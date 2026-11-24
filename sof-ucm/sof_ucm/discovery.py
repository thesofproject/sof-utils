# SPDX-License-Identifier: BSD-3-Clause
"""Local and SSH-remote audio stack discovery (cards, PCMs, .tplg, kcontrols, DAPM, UCM2)."""

from __future__ import annotations
import base64
import os
import re
import shlex
import subprocess
from typing import Any, Dict, List, Optional, Tuple

from .model import (
    DapmComponentInfo,
    DapmEdgeInfo,
    DapmWidgetInfo,
    DpcmLinkInfo,
    HdaCodecInfo,
    HdaCodecNodeInfo,
    KcontrolInfo,
    MachineAudioSnapshot,
    PcmDeviceInfo,
    SoundCardInfo,
)
from .tplg_loader import TplgTopology, parse_tplg_bytes


# ---------------------------------------------------------------------------
# Transport Abstraction: Local Machine or Remote Machine via SSH
# ---------------------------------------------------------------------------

class Transport:
    """Abstract shell/file transport for local or SSH-connected target machines."""

    @property
    def label(self) -> str:
        return "local"

    @property
    def is_remote(self) -> bool:
        return False

    def run(self, command: str, timeout: float = 10.0) -> Tuple[int, str, str]:
        raise NotImplementedError

    def read_text(self, path: str) -> Optional[str]:
        raise NotImplementedError

    def read_bytes(self, path: str) -> Optional[bytes]:
        raise NotImplementedError

    def list_dir(self, path: str) -> List[str]:
        raise NotImplementedError

    def write_text(self, path: str, content: str, use_sudo: bool = False) -> Tuple[bool, str]:
        raise NotImplementedError


class LocalTransport(Transport):
    """Direct local filesystem and subprocess transport."""

    def run(self, command: str, timeout: float = 10.0) -> Tuple[int, str, str]:
        try:
            proc = subprocess.run(
                ["bash", "-c", command],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired:
            return -1, "", f"Command timed out after {timeout}s"
        except Exception as exc:
            return -1, "", str(exc)

    def read_text(self, path: str) -> Optional[str]:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except Exception:
            return None

    def read_bytes(self, path: str) -> Optional[bytes]:
        try:
            with open(path, "rb") as f:
                return f.read()
        except Exception:
            return None

    def list_dir(self, path: str) -> List[str]:
        try:
            return sorted(os.listdir(path))
        except Exception:
            return []

    def write_text(self, path: str, content: str, use_sudo: bool = False) -> Tuple[bool, str]:
        if not use_sudo:
            try:
                parent = os.path.dirname(path)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)
                return True, f"Saved {path}"
            except Exception as exc:
                return False, str(exc)
        b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
        q_path = shlex.quote(path)
        q_dir = shlex.quote(os.path.dirname(path) or ".")
        cmd = f"pkexec bash -c 'mkdir -p {q_dir} && echo {b64} | base64 -d > {q_path}'"
        rc, _out, err = self.run(cmd, timeout=30.0)
        return (rc == 0), (err.strip() or f"Saved {path}" if rc == 0 else f"Failed (rc={rc}): {err}")


class SshTransport(Transport):
    """Remote machine transport over OpenSSH (supports ControlMaster multiplexing)."""

    def __init__(
        self,
        target: str,
        port: int = 22,
        identity_file: Optional[str] = None,
        extra_opts: Optional[List[str]] = None,
    ):
        self.target = target.strip()
        self.port = int(port)
        self.identity_file = identity_file
        self.extra_opts = list(extra_opts or [])

    @property
    def label(self) -> str:
        if self.port != 22:
            return f"ssh:{self.target}:{self.port}"
        return f"ssh:{self.target}"

    @property
    def is_remote(self) -> bool:
        return True

    @staticmethod
    def _control_path() -> str:
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR", "").strip()
        if runtime_dir and os.path.isdir(runtime_dir):
            ctl_dir = os.path.join(runtime_dir, "sof-ucm")
        else:
            ctl_dir = os.path.join(os.path.expanduser("~"), ".cache", "sof-ucm")
        try:
            os.makedirs(ctl_dir, mode=0o700, exist_ok=True)
            os.chmod(ctl_dir, 0o700)
        except OSError:
            pass
        return os.path.join(ctl_dir, "ssh-%C")

    def _base_ssh_cmd(self) -> List[str]:
        cmd = [
            "ssh",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=6",
            "-o", "ControlMaster=auto",
            "-o", "ControlPersist=60",
            "-o", f"ControlPath={self._control_path()}",
        ]
        if self.port != 22:
            cmd.extend(["-p", str(self.port)])
        if self.identity_file:
            cmd.extend(["-i", self.identity_file])
        cmd.extend(self.extra_opts)
        cmd.extend(["--", self.target])
        return cmd

    def run(self, command: str, timeout: float = 12.0) -> Tuple[int, str, str]:
        full_cmd = self._base_ssh_cmd() + [f"bash -c {shlex.quote(command)}"]
        try:
            proc = subprocess.run(
                full_cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired:
            return -1, "", f"SSH command timed out after {timeout}s"
        except Exception as exc:
            return -1, "", str(exc)

    def read_text(self, path: str) -> Optional[str]:
        rc, out, _ = self.run(f"cat {shlex.quote(path)} 2>/dev/null", timeout=8.0)
        return out if rc == 0 else None

    def read_bytes(self, path: str) -> Optional[bytes]:
        rc, out, _ = self.run(
            f"base64 -w0 {shlex.quote(path)} 2>/dev/null || base64 {shlex.quote(path)} 2>/dev/null",
            timeout=15.0,
        )
        if rc != 0 or not out.strip():
            return None
        try:
            return base64.b64decode("".join(out.split()))
        except Exception:
            return None

    def list_dir(self, path: str) -> List[str]:
        rc, out, _ = self.run(f"ls -1 {shlex.quote(path)} 2>/dev/null", timeout=8.0)
        if rc != 0:
            return []
        return sorted([ln.strip() for ln in out.splitlines() if ln.strip()])

    def write_text(self, path: str, content: str, use_sudo: bool = False) -> Tuple[bool, str]:
        b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
        q_path = shlex.quote(path)
        q_dir = shlex.quote(os.path.dirname(path) or ".")
        prefix = "sudo -n " if use_sudo else ""
        cmd = f"{prefix}mkdir -p {q_dir} && echo {b64} | base64 -d | {prefix}tee {q_path} >/dev/null"
        rc, _out, err = self.run(cmd, timeout=15.0)
        return (rc == 0), (err.strip() or f"Saved {path} on {self.target}" if rc == 0 else f"Failed (rc={rc}): {err}")


# ---------------------------------------------------------------------------
# Card & PCM Discovery
# ---------------------------------------------------------------------------

def _parse_cards_text(cards_txt: str) -> List[SoundCardInfo]:
    cards: List[SoundCardInfo] = []
    lines = cards_txt.splitlines()
    i = 0
    while i < len(lines):
        m = re.match(r"^\s*(\d+)\s+\[([^\]]+)\]:\s+(\S+)\s+-\s+(.*)$", lines[i])
        if m:
            cid = int(m.group(1))
            card_id_str = m.group(2).strip()
            drv = m.group(3).strip()
            name = m.group(4).strip()
            longname = ""
            if i + 1 < len(lines) and lines[i + 1].startswith(" "):
                longname = lines[i + 1].strip()
                i += 1
            cards.append(
                SoundCardInfo(
                    card_id=cid,
                    id=card_id_str,
                    driver=drv,
                    name=name,
                    longname=longname or name,
                )
            )
        i += 1
    return cards


def discover_cards(transport: Transport) -> List[SoundCardInfo]:
    """Enumerate ALSA sound cards on local or SSH remote target."""
    probe = (
        "echo '===CARDS==='; cat /proc/asound/cards 2>/dev/null; "
        "echo '===SYS==='; "
        "for d in /sys/class/sound/card[0-9]*; do "
        "  [ -d \"$d\" ] || continue; "
        "  bn=$(basename \"$d\"); "
        "  cid=$(cat \"$d/id\" 2>/dev/null); "
        "  comp=$(cat \"$d/components\" 2>/dev/null); "
        "  drv=$(basename $(readlink -f \"$d/device/driver\" 2>/dev/null) 2>/dev/null); "
        "  echo \"$bn|$cid|$drv|$comp\"; "
        "done; "
        "echo '===DMI==='; "
        "echo \"vendor=$(cat /sys/class/dmi/id/sys_vendor 2>/dev/null)\"; "
        "echo \"product=$(cat /sys/class/dmi/id/product_name 2>/dev/null)\""
    )
    rc, out, _ = transport.run(probe, timeout=10.0)
    if rc != 0 and "===CARDS===" not in out:
        return []

    sections = _split_markers(out)
    cards = _parse_cards_text(sections.get("CARDS", ""))
    by_id = {c.card_id: c for c in cards}

    dmi_vendor = ""
    dmi_product = ""
    for line in sections.get("DMI", "").splitlines():
        if line.startswith("vendor="):
            dmi_vendor = line.split("=", 1)[1].strip()
        elif line.startswith("product="):
            dmi_product = line.split("=", 1)[1].strip()

    for line in sections.get("SYS", "").splitlines():
        parts = line.split("|", 3)
        if len(parts) < 4:
            continue
        bn, cid_str, sys_drv, comp = parts
        m = re.match(r"card(\d+)$", bn.strip())
        if not m:
            continue
        num = int(m.group(1))
        if num in by_id:
            c = by_id[num]
            if cid_str.strip():
                c.id = cid_str.strip()
            if comp.strip():
                c.components = comp.strip()
            if not c.driver and sys_drv.strip():
                c.driver = sys_drv.strip()
            c.dmi_vendor = dmi_vendor
            c.dmi_product = dmi_product

    return cards


def _split_markers(text: str) -> Dict[str, str]:
    result: Dict[str, str] = {}
    cur_key: Optional[str] = None
    buf: List[str] = []
    for line in text.splitlines():
        m = re.match(r"^===([A-Z0-9_:/-]+)===$", line.strip())
        if m:
            if cur_key is not None:
                result[cur_key] = "\n".join(buf)
            cur_key = m.group(1)
            buf = []
        else:
            buf.append(line)
    if cur_key is not None:
        result[cur_key] = "\n".join(buf)
    return result


# ---------------------------------------------------------------------------
# Kcontrol Parser (amixer -c N contents)
# ---------------------------------------------------------------------------

def parse_amixer_contents(text: str) -> List[KcontrolInfo]:
    """Parse `amixer -c N contents` output into structured KcontrolInfo objects."""
    kctls: List[KcontrolInfo] = []
    cur: Optional[KcontrolInfo] = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r")
        if line.startswith("numid="):
            if cur is not None:
                kctls.append(cur)
            numid = 0
            iface = "MIXER"
            name = ""
            index = 0
            device = 0
            subdevice = 0

            m_num = re.search(r"numid=(\d+)", line)
            if m_num:
                numid = int(m_num.group(1))
            m_if = re.search(r"iface=([A-Za-z0-9_]+)", line)
            if m_if:
                iface = m_if.group(1)
            m_nm = re.search(r"name='((?:[^'\\]|\\.)*)'", line)
            if m_nm:
                name = m_nm.group(1)
            else:
                m_nm2 = re.search(r"name=([^,]+)", line)
                if m_nm2:
                    name = m_nm2.group(1).strip("'\"")
            m_idx = re.search(r"index=(\d+)", line)
            if m_idx:
                index = int(m_idx.group(1))
            m_dev = re.search(r"device=(\d+)", line)
            if m_dev:
                device = int(m_dev.group(1))
            m_sub = re.search(r"subdevice=(\d+)", line)
            if m_sub:
                subdevice = int(m_sub.group(1))

            cur = KcontrolInfo(
                numid=numid,
                name=name,
                iface=iface,
                index=index,
                device=device,
                subdevice=subdevice,
            )
            continue

        if cur is None:
            continue

        s = line.strip()
        if s.startswith("; type="):
            m_tp = re.search(r"type=([A-Za-z0-9_]+)", s)
            if m_tp:
                cur.ctl_type = m_tp.group(1)
            m_acc = re.search(r"access=([rwRW\-]+)", s)
            if m_acc:
                acc = m_acc.group(1)
                cur.readable = "r" in acc.lower()
                cur.writable = "w" in acc.lower()
            m_cnt = re.search(r"values=(\d+)", s)
            if m_cnt:
                cur.count = int(m_cnt.group(1))
            m_min = re.search(r"min=(-?\d+)", s)
            if m_min:
                cur.min_val = int(m_min.group(1))
            m_max = re.search(r"max=(-?\d+)", s)
            if m_max:
                cur.max_val = int(m_max.group(1))
            m_step = re.search(r"step=(\d+)", s)
            if m_step:
                cur.step = int(m_step.group(1))
        elif s.startswith("; Item #"):
            m_it = re.match(r";\s*Item\s*#(\d+)\s+'(.*)'", s)
            if m_it:
                cur.enum_items.append(m_it.group(2))
        elif s.startswith(": values="):
            val_str = s[len(": values="):].strip()
            vals: List[Any] = []
            if val_str.startswith("[") and val_str.endswith("]"):
                vals = [val_str]
            elif val_str:
                for tok in val_str.split(","):
                    t = tok.strip()
                    if t.lower() in ("on", "true"):
                        vals.append(1)
                    elif t.lower() in ("off", "false"):
                        vals.append(0)
                    else:
                        try:
                            vals.append(int(t, 0))
                        except ValueError:
                            vals.append(t)
            cur.values = vals
        elif s.startswith("| "):
            cur.tlv_str = s[2:].strip()
            m_db = re.search(r"dBscale-min=([-\d.]+)dB,step=([-\d.]+)dB,mute=(\d+)", cur.tlv_str)
            if m_db:
                cur.db_min = float(m_db.group(1))
                cur.db_step = float(m_db.group(2))
                cur.db_mute = m_db.group(3) == "1"

    if cur is not None:
        kctls.append(cur)
    return kctls


# ---------------------------------------------------------------------------
# DAPM & DPCM Parsers (matching dapm_inspector.c)
# ---------------------------------------------------------------------------

def _parse_dapm_widget_file(comp_name: str, widget_name: str, content: str) -> Tuple[DapmWidgetInfo, List[DapmEdgeInfo]]:
    w = DapmWidgetInfo(name=widget_name, component=comp_name)
    edges: List[DapmEdgeInfo] = []
    lines = content.splitlines()
    for idx, line in enumerate(lines):
        if idx == 0:
            rest = line
            prefix = f"{widget_name}:"
            if line.startswith(prefix):
                rest = line[len(prefix):].strip()
            if rest.startswith("On"):
                w.power = 2 if "(forced)" in rest else 1
            else:
                w.power = 0
            m_ep = re.search(r"\bin\s+(\d+)\s+out\s+(\d+)", rest)
            if m_ep:
                w.in_ep = int(m_ep.group(1))
                w.out_ep = int(m_ep.group(2))
            m_reg = re.search(r"-\s+R(\d+)\(0x[0-9a-fA-F]+\)\s+mask\s+0x([0-9a-fA-F]+)", rest)
            if m_reg:
                w.reg = int(m_reg.group(1))
                w.mask = int(m_reg.group(2), 16)
        elif line.startswith(" stream "):
            sp = line[len(" stream "):]
            if sp.endswith(" active"):
                w.stream_active = True
                w.stream_name = sp[:-len(" active")].strip()
            elif sp.endswith(" inactive"):
                w.stream_active = False
                w.stream_name = sp[:-len(" inactive")].strip()
        elif line.startswith(" widget-type "):
            w.widget_type = line[len(" widget-type "):].strip()
        elif line.startswith(" in  \"") or line.startswith(" out  \""):
            is_in = line.startswith(" in  \"")
            quoted = re.findall(r'"([^"]*)"', line)
            if len(quoted) >= 2:
                ctl_nm = quoted[0] or "static"
                peer_nm = quoted[1]
                raw_comp = quoted[2] if len(quoted) >= 3 else ""
                if raw_comp == "(null)":
                    peer_comp = "card"
                elif raw_comp:
                    peer_comp = raw_comp
                else:
                    peer_comp = comp_name
                if is_in:
                    edges.append(
                        DapmEdgeInfo(
                            src_name=peer_nm,
                            src_comp=peer_comp,
                            dst_name=widget_name,
                            dst_comp=comp_name,
                            control_name=ctl_nm,
                        )
                    )
                else:
                    edges.append(
                        DapmEdgeInfo(
                            src_name=widget_name,
                            src_comp=comp_name,
                            dst_name=peer_nm,
                            dst_comp=peer_comp,
                            control_name=ctl_nm,
                        )
                    )
    m_pid = re.match(r"^[^.]+\.(\d+)\.", widget_name)
    if m_pid and not widget_name.startswith(("host-copier.", "dai-copier.")):
        w.pipeline_id = int(m_pid.group(1))
    return w, edges


def _parse_dpcm_state_text(fe_name: str, content: str) -> List[DpcmLinkInfo]:
    links: List[DpcmLinkInfo] = []
    cur: Optional[DpcmLinkInfo] = None
    in_backends = False
    cur_be: Optional[Dict[str, Any]] = None

    for line in content.splitlines():
        if line.startswith("["):
            cur = DpcmLinkInfo(
                fe_name=fe_name,
                direction="capture" if "Capture" in line else "playback",
            )
            links.append(cur)
            in_backends = False
            cur_be = None
        elif cur is not None:
            s = line.strip()
            if s.startswith("State: "):
                val = s[len("State: "):].strip()
                if in_backends and cur_be is not None:
                    cur_be["state"] = val
                else:
                    cur.state_str = val
            elif s.startswith("Hardware Params: "):
                m = re.search(r"Format\s*=\s*([^,]+),\s*Channels\s*=\s*(\d+),\s*Rate\s*=\s*(\d+)", s)
                if m:
                    if in_backends and cur_be is not None:
                        cur_be["format"] = m.group(1).strip()
                        cur_be["channels"] = int(m.group(2))
                        cur_be["rate"] = int(m.group(3))
                    else:
                        cur.fe_format = m.group(1).strip()
                        cur.fe_channels = int(m.group(2))
                        cur.fe_rate = int(m.group(3))
            elif s.startswith("Backends:"):
                in_backends = True
            elif in_backends and line.startswith("- "):
                cur_be = {"name": line[2:].strip(), "state": "close", "format": "", "channels": 0, "rate": 0}
                cur.backends.append(cur_be)
    return links


# ---------------------------------------------------------------------------
# Full Machine Audio Discovery
# ---------------------------------------------------------------------------

def _detect_tplg_path(transport: Transport, card: SoundCardInfo, dmesg_out: str) -> str:
    for line in reversed(dmesg_out.splitlines()):
        if "loading topology:" in line.lower():
            idx = line.lower().find("loading topology:")
            cand = line[idx + len("loading topology:"):].strip()
            if cand:
                for p in (cand, f"/lib/firmware/{cand}", f"/vendor/firmware/{cand}"):
                    rc, _, _ = transport.run(f"test -r {shlex.quote(p)}", timeout=4.0)
                    if rc == 0:
                        return p

    # Fallback search by card driver / SOF topology dirs
    if "sof" in card.driver.lower() or "sof" in card.longname.lower():
        rc, out, _ = transport.run(
            "ls -1 /lib/firmware/intel/sof-ipc4-tplg/*.tplg /lib/firmware/intel/sof-tplg/*.tplg "
            "/vendor/firmware/intel/sof-ipc4-tplg/*.tplg 2>/dev/null | head -n 40",
            timeout=5.0,
        )
        files = [ln.strip() for ln in out.splitlines() if ln.strip()]
        if files:
            # Prefer exact driver/card name match if present
            for f in files:
                bn = os.path.basename(f).lower()
                if card.driver.lower().replace("-dsp", "") in bn:
                    return f
            for f in files:
                if "sof-hda-generic.tplg" in f:
                    return f
            return files[0]
    return ""


def _parse_pcm_blocks(pcm_section: str, card_id: int) -> List[PcmDeviceInfo]:
    pcms: List[PcmDeviceInfo] = []
    # Blocks separated by @@PCM:<dir_entry>@@
    cur_entry = ""
    cur_sub = ""
    blocks: Dict[str, Dict[str, str]] = {}

    for line in pcm_section.splitlines():
        if line.startswith("@@PCM:") and line.endswith("@@"):
            tag = line[6:-2]
            if ":" in tag:
                cur_entry, cur_sub = tag.split(":", 1)
            else:
                cur_entry, cur_sub = tag, "info"
            blocks.setdefault(cur_entry, {}).setdefault(cur_sub, "")
        elif cur_entry:
            blocks[cur_entry][cur_sub] = (blocks[cur_entry].get(cur_sub, "") + line + "\n")

    for entry, parts in sorted(blocks.items()):
        m = re.match(r"^pcm(\d+)([pc])$", entry)
        if not m:
            continue
        dev_id = int(m.group(1))
        direction = "capture" if m.group(2) == "c" else "playback"
        p = PcmDeviceInfo(card_id=card_id, device_id=dev_id, direction=direction)

        for ln in parts.get("info", "").splitlines():
            if ln.startswith("id: "):
                p.id = ln[4:].strip()
            elif ln.startswith("name: "):
                p.name = ln[6:].strip()
            elif ln.startswith("subname: ") and not p.stream_name:
                p.stream_name = ln[9:].strip()
            elif ln.startswith("subdevices_count: "):
                p.subdevices_count = int(ln[18:].strip() or 1)
            elif ln.startswith("subdevices_avail: "):
                p.subdevices_avail = int(ln[18:].strip() or 0)

        status_txt = parts.get("status", "")
        if status_txt and not status_txt.strip().startswith("closed"):
            for ln in status_txt.splitlines():
                if ln.startswith("state: "):
                    p.state_str = ln[7:].strip()
                    p.is_running = p.state_str == "RUNNING"

        hw_txt = parts.get("hw_params", "")
        if hw_txt and not hw_txt.strip().startswith("closed"):
            for ln in hw_txt.splitlines():
                if ln.startswith("format: "):
                    p.active_format = ln[8:].strip()
                elif ln.startswith("channels: "):
                    p.active_channels = int(ln[10:].strip() or 0)
                elif ln.startswith("rate: "):
                    p.active_rate = int(ln[6:].split()[0] or 0)
                elif ln.startswith("period_size: "):
                    p.period_size = int(ln[13:].strip() or 0)
                elif ln.startswith("buffer_size: "):
                    p.buffer_size = int(ln[13:].strip() or 0)

        # Optional dump-hw-params output
        caps_txt = parts.get("caps", "")
        for ln in caps_txt.splitlines():
            if ln.startswith("FORMAT:"):
                p.formats = ln.split(":", 1)[1].strip().split()
            elif ln.startswith("CHANNELS:"):
                nums = [int(x) for x in re.findall(r"\d+", ln.split(":", 1)[1])]
                if nums:
                    p.channels_min = min(nums)
                    p.channels_max = max(nums)
            elif ln.startswith("RATE:"):
                nums = [int(x) for x in re.findall(r"\d+", ln.split(":", 1)[1])]
                if len(nums) == 2 and "[" in ln:
                    p.rates = [nums[0], nums[1]]
                elif nums:
                    p.rates = nums

        pcms.append(p)
    return pcms


def parse_hda_codec_text(text: str) -> Optional[HdaCodecInfo]:
    """Parse /proc/asound/cardN/codec#M into HdaCodecInfo with nodes & connections."""
    name = ""
    address = 0
    vendor_id = ""
    nodes: List[HdaCodecNodeInfo] = []
    cur: Optional[HdaCodecNodeInfo] = None
    in_conn = False

    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r")
        if line.startswith("Codec: "):
            name = line[7:].strip()
        elif line.startswith("Address: "):
            try:
                address = int(line[9:].strip())
            except ValueError:
                pass
        elif line.startswith("Vendor Id: "):
            vendor_id = line[11:].strip()
        elif line.startswith("Node 0x"):
            in_conn = False
            m = re.match(r"^Node\s+(0x[0-9a-fA-F]+)\s+\[([^\]]+)\]", line)
            if m:
                cur = HdaCodecNodeInfo(nid=int(m.group(1), 16), wtype=m.group(2).strip())
                nodes.append(cur)
        elif cur is not None:
            s = line.strip()
            if in_conn:
                if line.startswith("     ") or line.startswith("\t"):
                    for tok in s.split():
                        is_act = tok.endswith("*")
                        hx = tok.rstrip("*")
                        if hx.startswith("0x"):
                            try:
                                cid = int(hx, 16)
                                cur.connections.append(cid)
                                if is_act or cur.active_conn is None:
                                    cur.active_conn = cid
                            except ValueError:
                                pass
                    continue
                in_conn = False

            if s.startswith("Control: name="):
                mc = re.search(r'name="([^"]+)"', s)
                if mc and mc.group(1) not in cur.controls:
                    cur.controls.append(mc.group(1))
            elif s.startswith("Device: "):
                md = re.search(r'name="([^"]*)".*?device=(\d+)', s)
                if md:
                    cur.device_name = md.group(1)
                    cur.device_id = int(md.group(2))
            elif s.startswith("Pin Default "):
                cur.pin_default = s.split(":", 1)[1].strip() if ":" in s else s
            elif s.startswith("Pin-ctls: "):
                cur.pin_ctls = s[len("Pin-ctls: "):].strip()
            elif s.startswith("Power: "):
                mp = re.search(r"actual=([A-Za-z0-9]+)", s)
                if mp:
                    cur.power = mp.group(1)
            elif s.startswith("Converter: "):
                ms = re.search(r"stream=(\d+)", s)
                if ms:
                    cur.converter_stream = int(ms.group(1))
            elif s.startswith("Connection: "):
                in_conn = True

    if not name and not nodes:
        return None
    return HdaCodecInfo(address=address, name=name, vendor_id=vendor_id, nodes=nodes)


def _parse_codecs_section(section_text: str) -> List[HdaCodecInfo]:
    codecs: List[HdaCodecInfo] = []
    cur_buf: List[str] = []
    cur_file = ""

    def flush() -> None:
        if cur_file and cur_buf:
            c = parse_hda_codec_text("\n".join(cur_buf))
            if c is not None:
                codecs.append(c)

    for line in section_text.splitlines():
        if line.startswith("@@CODEC:") and line.endswith("@@"):
            flush()
            cur_file = line[8:-2]
            cur_buf = []
        elif cur_file:
            cur_buf.append(line)
    flush()
    return codecs


def discover_machine_audio(
    transport: Transport,
    card_id: int,
    tplg_override: Optional[str] = None,
    use_sudo_debugfs: bool = False,
) -> MachineAudioSnapshot:
    """Gather cards, PCMs, kcontrols, topology (.tplg), and DAPM/DPCM graph from target."""
    cards = discover_cards(transport)
    card = next((c for c in cards if c.card_id == card_id), None)
    if card is None:
        card = cards[0] if cards else SoundCardInfo(card_id=card_id, id=f"card{card_id}")
        card_id = card.card_id

    snap = MachineAudioSnapshot(target_label=transport.label, card=card, cards=cards)

    # Single batched shell probe for PCMs, amixer, dmesg, and debugfs DAPM
    sudo_pfx = "sudo -n " if use_sudo_debugfs else ""
    probe = f"""
echo '===PCMS==='
for pdir in /proc/asound/card{card_id}/pcm[0-9]*[pc]; do
  [ -d "$pdir" ] || continue
  bn=$(basename "$pdir")
  echo "@@PCM:$bn:info@@"
  cat "$pdir/info" 2>/dev/null
  echo "@@PCM:$bn:status@@"
  cat "$pdir/sub0/status" 2>/dev/null
  echo "@@PCM:$bn:hw_params@@"
  cat "$pdir/sub0/hw_params" 2>/dev/null
done
echo '===AMIXER==='
amixer -c {card_id} contents 2>/dev/null
echo '===CODECS==='
for cf in /proc/asound/card{card_id}/codec#[0-9]*; do
  [ -r "$cf" ] || continue
  echo "@@CODEC:$(basename "$cf")@@"
  cat "$cf" 2>/dev/null
done
echo '===DMESG==='
dmesg 2>/dev/null | grep -i 'loading topology:' | tail -n 5
echo '===ASOC_LIST==='
for root in /sys/kernel/debug/asoc /d/asoc; do
  if {sudo_pfx}test -r "$root" 2>/dev/null; then
    echo "ROOT=$root"
    {sudo_pfx}ls -1 "$root" 2>/dev/null
    break
  fi
done
"""
    rc, out, err = transport.run(probe, timeout=15.0)
    if rc != 0 and not out.strip():
        snap.warnings.append(f"Discovery failed ({transport.label}): {err or 'no output'}")
        return snap

    sections = _split_markers(out)
    snap.pcms = _parse_pcm_blocks(sections.get("PCMS", ""), card_id)
    card.num_pcm_streams = len(snap.pcms)

    snap.kcontrols = parse_amixer_contents(sections.get("AMIXER", ""))
    snap.kcontrols_by_name = {}
    for k in snap.kcontrols:
        snap.kcontrols_by_name.setdefault(k.name, k)
    card.num_kcontrols = len(snap.kcontrols)
    snap.hda_codecs = _parse_codecs_section(sections.get("CODECS", ""))

    # Topology resolution & parsing
    tplg_path = tplg_override or _detect_tplg_path(transport, card, sections.get("DMESG", ""))
    if tplg_path:
        card.tplg_path = tplg_path
        tplg_bytes: Optional[bytes] = None
        if tplg_override and os.path.isfile(tplg_override):
            try:
                with open(tplg_override, "rb") as f:
                    tplg_bytes = f.read()
            except Exception:
                tplg_bytes = None
        if tplg_bytes is None:
            tplg_bytes = transport.read_bytes(tplg_path)
        if tplg_bytes:
            try:
                snap.tplg = parse_tplg_bytes(tplg_bytes, source_path=tplg_path)
                snap.tplg_available = True
            except Exception as exc:
                snap.warnings.append(f"Failed parsing topology {tplg_path}: {exc}")
        else:
            snap.warnings.append(f"Topology file not readable: {tplg_path}")

    # Resolve ASoC debugfs card directory and dump DAPM + DPCM state
    asoc_lines = [ln.strip() for ln in sections.get("ASOC_LIST", "").splitlines() if ln.strip()]
    asoc_root = ""
    asoc_entries: List[str] = []
    for ln in asoc_lines:
        if ln.startswith("ROOT="):
            asoc_root = ln[5:]
        else:
            asoc_entries.append(ln)

    if asoc_root and asoc_entries:
        cands = [card.name, card.driver, card.id]
        card_dir = ""
        for cand in cands:
            if cand and cand in asoc_entries:
                card_dir = cand
                break
        if not card_dir:
            for e in asoc_entries:
                if e in ("components", "dais", "dapm_pop_time"):
                    continue
                if (card.name and card.name in e) or (card.driver and card.driver in e):
                    card_dir = e
                    break
        if not card_dir:
            for e in asoc_entries:
                if e not in ("components", "dais", "dapm_pop_time"):
                    card_dir = e
                    break
        if card_dir:
            card.asoc_card_dir = card_dir
            _collect_dapm_graph(transport, f"{asoc_root}/{card_dir}", snap, use_sudo=use_sudo_debugfs)
    else:
        snap.warnings.append(
            "ASoC debugfs (/sys/kernel/debug/asoc) not readable without root; "
            "graph synthesized from topology + ALSA PCMs + kcontrols."
        )

    # Anchor kcontrols to .tplg widgets, DAPM edges, PCMs, and endpoints
    _resolve_kcontrol_anchors(snap)
    return snap


def _collect_dapm_graph(
    transport: Transport,
    card_base: str,
    snap: MachineAudioSnapshot,
    use_sudo: bool = False,
) -> None:
    sudo_pfx = "sudo -n " if use_sudo else ""
    q_base = shlex.quote(card_base)
    script = f"""
dump_dapm_dir() {{
  local comp="$1"
  local dir="$2"
  echo "@@COMP:$comp@@"
  {sudo_pfx}cat "$dir/bias_level" 2>/dev/null || echo "Off"
  for wf in "$dir"/*; do
    [ -f "$wf" ] || continue
    wbn=$(basename "$wf")
    [ "$wbn" = "bias_level" ] && continue
    echo "@@WIDGET:$comp::$wbn@@"
    {sudo_pfx}cat "$wf" 2>/dev/null
  done
}}
if {sudo_pfx}test -d {q_base}/dapm 2>/dev/null; then
  dump_dapm_dir "card" {q_base}/dapm
fi
for sub in {q_base}/*; do
  [ -d "$sub" ] || continue
  sbn=$(basename "$sub")
  [ "$sbn" = "dapm" ] && continue
  if {sudo_pfx}test -d "$sub/dapm" 2>/dev/null; then
    dump_dapm_dir "$sbn" "$sub/dapm"
  fi
  if {sudo_pfx}test -f "$sub/state" 2>/dev/null; then
    echo "@@DPCM:$sbn@@"
    {sudo_pfx}cat "$sub/state" 2>/dev/null
  fi
done
"""
    rc, out, _ = transport.run(script, timeout=15.0)
    if rc != 0 and "@@WIDGET:" not in out:
        return

    cur_kind = ""
    cur_comp = ""
    cur_name = ""
    buf: List[str] = []
    comp_map: Dict[str, DapmComponentInfo] = {}
    edge_dedup: set[Tuple[str, str, str, str, str]] = set()

    def flush() -> None:
        nonlocal buf
        text = "\n".join(buf)
        buf = []
        if not text.strip():
            return
        if cur_kind == "COMP":
            ci = comp_map.setdefault(cur_comp, DapmComponentInfo(name=cur_comp))
            ci.bias_level = text.strip().splitlines()[0]
        elif cur_kind == "WIDGET":
            w, edges = _parse_dapm_widget_file(cur_comp, cur_name, text)
            snap.dapm_widgets.append(w)
            ci = comp_map.setdefault(cur_comp, DapmComponentInfo(name=cur_comp))
            ci.widget_count += 1
            if w.power > 0:
                ci.powered_count += 1
            for e in edges:
                key = (e.src_name, e.src_comp, e.dst_name, e.dst_comp, e.control_name)
                if key not in edge_dedup:
                    edge_dedup.add(key)
                    snap.dapm_edges.append(e)
        elif cur_kind == "DPCM":
            snap.dpcm_links.extend(_parse_dpcm_state_text(cur_name, text))

    for line in out.splitlines():
        if line.startswith("@@COMP:") and line.endswith("@@"):
            flush()
            cur_kind = "COMP"
            cur_comp = line[7:-2]
            cur_name = cur_comp
        elif line.startswith("@@WIDGET:") and line.endswith("@@"):
            flush()
            cur_kind = "WIDGET"
            rest = line[9:-2]
            if "::" in rest:
                cur_comp, cur_name = rest.split("::", 1)
            else:
                cur_comp, cur_name = "card", rest
        elif line.startswith("@@DPCM:") and line.endswith("@@"):
            flush()
            cur_kind = "DPCM"
            cur_name = line[7:-2]
        else:
            buf.append(line)
    flush()

    snap.dapm_components = list(comp_map.values())
    if snap.dapm_widgets:
        snap.dapm_available = True
        w_pwr = {(w.name, w.component): w.power for w in snap.dapm_widgets}
        for e in snap.dapm_edges:
            if w_pwr.get((e.src_name, e.src_comp), 0) > 0 and w_pwr.get((e.dst_name, e.dst_comp), 0) > 0:
                e.active = True


def _resolve_kcontrol_anchors(snap: MachineAudioSnapshot) -> None:
    """Link each ALSA kcontrol to its owning .tplg widget, DAPM edge, PCM, or HW endpoint."""
    tplg_wkctl: Dict[str, Tuple[str, int]] = {}
    if snap.tplg is not None:
        for wname, ctls in snap.tplg.widget_kcontrol_map.items():
            for c in ctls:
                tplg_wkctl[c.name] = (wname, c.pipeline_id)

    edge_ctls: set[str] = {
        e.control_name for e in snap.dapm_edges if e.control_name and e.control_name != "static"
    }
    dapm_w_by_name: Dict[str, DapmWidgetInfo] = {w.name: w for w in snap.dapm_widgets}

    for k in snap.kcontrols:
        # 1. Topology widget-bound kcontrol
        if k.name in tplg_wkctl:
            wname, pid = tplg_wkctl[k.name]
            k.anchor_kind = "widget"
            k.anchor_widget = wname
            k.pipeline_id = pid
            dw = dapm_w_by_name.get(wname)
            if dw:
                k.anchor_component = dw.component
                if k.name not in dw.kcontrol_names:
                    dw.kcontrol_names.append(k.name)
            continue

        # 2. DAPM route switch / mux control
        if k.name in edge_ctls or any(k.name.startswith(ec) for ec in edge_ctls if len(ec) > 4):
            k.anchor_kind = "edge"
            k.anchor_edge_ctl = k.name
            continue

        # 3. PCM interface controls
        if k.iface == "PCM":
            k.anchor_kind = "pcm"
            continue

        # 4. Jack / codec endpoint controls
        low = k.name.lower()
        if "jack" in low or any(
            ep in low for ep in ("headphone", "speaker", "mic", "line out", "line in", "spdif", "iec958", "hdmi", "master")
        ):
            k.anchor_kind = "endpoint"
            continue


# ---------------------------------------------------------------------------
# Live Kcontrol Set & UCM/Audio Verification Helpers
# ---------------------------------------------------------------------------

def set_kcontrol_value(
    transport: Transport,
    card_id: int,
    kctl: KcontrolInfo,
    new_val_str: str,
) -> Tuple[bool, str]:
    """Apply a live kcontrol change using amixer -c <card> cset."""
    cmd = f"amixer -c {card_id} cset numid={kctl.numid} {shlex.quote(new_val_str)}"
    rc, out, err = transport.run(cmd, timeout=6.0)
    if rc == 0:
        parsed = parse_amixer_contents(out)
        if parsed:
            kctl.values = parsed[0].values
        return True, out.strip() or f"Set numid={kctl.numid} ({kctl.name}) = {new_val_str}"
    return False, err.strip() or out.strip() or f"amixer failed (rc={rc})"


def query_pcm_hw_params(transport: Transport, card_id: int, pcm: PcmDeviceInfo) -> PcmDeviceInfo:
    """Query detailed hw_params capabilities for a single PCM via aplay/arecord --dump-hw-params."""
    tool = "arecord" if pcm.direction == "capture" else "aplay"
    cmd = f"{tool} -D hw:{card_id},{pcm.device_id} --dump-hw-params /dev/zero 2>&1 | head -n 35"
    _rc, out, _ = transport.run(cmd, timeout=5.0)
    for ln in out.splitlines():
        if ln.startswith("FORMAT:"):
            pcm.formats = ln.split(":", 1)[1].strip().split()
        elif ln.startswith("CHANNELS:"):
            nums = [int(x) for x in re.findall(r"\d+", ln.split(":", 1)[1])]
            if nums:
                pcm.channels_min = min(nums)
                pcm.channels_max = max(nums)
        elif ln.startswith("RATE:"):
            nums = [int(x) for x in re.findall(r"\d+", ln.split(":", 1)[1])]
            if nums:
                pcm.rates = nums
    return pcm


def run_alsaucm_command(
    transport: Transport,
    card_id: int,
    ucm_dir: Optional[str],
    commands: List[str],
) -> Tuple[int, str, str]:
    """Run alsaucm against card_id with optional ALSA_CONFIG_UCM2 staging directory."""
    env_pfx = f"ALSA_CONFIG_UCM2={shlex.quote(ucm_dir)} " if ucm_dir else ""
    joined = " ".join(f"-c hw:{card_id}" if i == 0 else "" for i in range(1))
    cmd_args = " ".join(shlex.quote(c) for c in commands)
    full = f"{env_pfx}alsaucm {joined} {cmd_args}"
    return transport.run(full, timeout=10.0)
