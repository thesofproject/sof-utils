# SPDX-License-Identifier: BSD-3-Clause
"""ALSA UCM2 validator, serializer, and automatic UCM2 profile generator from graph."""

from __future__ import annotations
import re
from typing import Dict, List, Optional, Set, Tuple

from .graph_builder import trace_reachable_from_node
from .model import (
    GraphNode,
    KcontrolInfo,
    MachineAudioSnapshot,
    UcmDiagnostic,
    UcmProfile,
    UcmSectionDevice,
    UcmSequenceCmd,
    UcmVerb,
    UcmGraphViewModel,
)


# ---------------------------------------------------------------------------
# UCM2 Validation against Live Machine Snapshot
# ---------------------------------------------------------------------------

def validate_ucm_profile(
    profile: UcmProfile,
    snap: MachineAudioSnapshot,
    vm: Optional[UcmGraphViewModel] = None,
) -> List[UcmDiagnostic]:
    """Validate PCM references, kcontrols, cset ranges, and ConflictingDevice symmetry."""
    diags: List[UcmDiagnostic] = []
    pcm_keys = {(p.device_id, p.direction) for p in snap.pcms}
    kctls_by_name = snap.kcontrols_by_name

    def check_seq(cmds: List[UcmSequenceCmd], verb_nm: str, dev_nm: str, file_path: str) -> None:
        for cmd in cmds:
            if cmd.cmd in ("cset", "cset-tlv", "cset-new"):
                m = re.search(r"name='([^']+)'(?:\s+(.*))?", cmd.arg)
                if not m:
                    diags.append(
                        UcmDiagnostic(
                            severity="warning",
                            message=f"Could not parse kcontrol name in `{cmd.cmd} \"{cmd.arg}\"`",
                            file_path=file_path,
                            verb_name=verb_nm,
                            device_name=dev_nm,
                        )
                    )
                    continue
                cname = m.group(1)
                cval = (m.group(2) or "").strip()
                k = kctls_by_name.get(cname)
                if k is None:
                    diags.append(
                        UcmDiagnostic(
                            severity="error",
                            message=f"Missing kcontrol '{cname}' referenced in {dev_nm or verb_nm or 'BootSequence'}",
                            file_path=file_path,
                            verb_name=verb_nm,
                            device_name=dev_nm,
                            kcontrol_name=cname,
                        )
                    )
                elif cmd.cmd == "cset" and cval and k.ctl_type == "INTEGER" and not cval.endswith("%"):
                    for tok in cval.split(","):
                        t = tok.strip()
                        if t.lstrip("-").isdigit():
                            iv = int(t)
                            if iv < k.min_val or iv > k.max_val:
                                diags.append(
                                    UcmDiagnostic(
                                        severity="warning",
                                        message=f"cset value {iv} for '{cname}' outside [{k.min_val}, {k.max_val}]",
                                        file_path=file_path,
                                        verb_name=verb_nm,
                                        device_name=dev_nm,
                                        kcontrol_name=cname,
                                    )
                                )
                                break

    check_seq(profile.boot_seq, "", "BootSequence", profile.master_rel_path)

    for vname, verb in profile.verbs.items():
        check_seq(verb.enable_seq, vname, "", verb.file_path)
        check_seq(verb.disable_seq, vname, "", verb.file_path)

        for dname, dev in verb.devices.items():
            # Check PCM references
            for key, pdir in (("PlaybackPCM", "playback"), ("CapturePCM", "capture")):
                pval = dev.values.get(key, "")
                if pval:
                    m_dev = re.search(r",(\d+)", pval)
                    if m_dev:
                        did = int(m_dev.group(1))
                        if (did, pdir) not in pcm_keys:
                            diags.append(
                                UcmDiagnostic(
                                    severity="error",
                                    message=f"{dname}: {key}='{pval}' has no {pdir} PCM{did} on card",
                                    file_path=dev.source_file or verb.file_path,
                                    verb_name=vname,
                                    device_name=dname,
                                )
                            )

            # Check kcontrol Value.* bindings
            for vk in ("PlaybackVolume", "PlaybackSwitch", "CaptureVolume", "CaptureSwitch", "JackControl"):
                kv = dev.values.get(vk, "").strip()
                if kv and kv not in kctls_by_name:
                    diags.append(
                        UcmDiagnostic(
                            severity="warning",
                            message=f"{dname}: {vk} '{kv}' not found in card kcontrols",
                            file_path=dev.source_file or verb.file_path,
                            verb_name=vname,
                            device_name=dname,
                            kcontrol_name=kv,
                        )
                    )

            check_seq(dev.enable_seq, vname, dname, dev.source_file or verb.file_path)
            check_seq(dev.disable_seq, vname, dname, dev.source_file or verb.file_path)

            # ConflictingDevice checks
            for conf in dev.conflicting_devices:
                if conf not in verb.devices:
                    diags.append(
                        UcmDiagnostic(
                            severity="warning",
                            message=f"{dname}: ConflictingDevice '{conf}' not defined in verb '{vname}'",
                            file_path=dev.source_file or verb.file_path,
                            verb_name=vname,
                            device_name=dname,
                        )
                    )
                elif dname not in verb.devices[conf].conflicting_devices:
                    diags.append(
                        UcmDiagnostic(
                            severity="info",
                            message=f"Asymmetric ConflictingDevice: '{dname}' conflicts with '{conf}', but not vice-versa",
                            file_path=dev.source_file or verb.file_path,
                            verb_name=vname,
                            device_name=dname,
                        )
                    )

    profile.diagnostics = diags
    return diags


# ---------------------------------------------------------------------------
# UCM2 Serializer (AST -> Clean Formatted ALSA UCM2 .conf Text)
# ---------------------------------------------------------------------------

def _ucm_quote(val: object) -> str:
    s = str(val).replace("\\", "\\\\").replace('"', '\\"').replace("\r\n", " ").replace("\n", " ")
    return f'"{s}"'


def _rel_ucm_path(path: str, ucm_root_dir: str) -> str:
    if not path:
        return ""
    if ucm_root_dir:
        root = ucm_root_dir.rstrip("/")
        if root and (path == root or path.startswith(root + "/")):
            return path[len(root) + 1:].lstrip("/")
    return path.lstrip("/")


def _has_non_flat_ucm_syntax(text: str) -> bool:
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if re.match(r"^(?:If(?:\.|\s|\{|$)|Condition\b|DefineRegex\b|DefineMacro\b|Include\.|Macro\.)", s):
            return True
    return False


def _fmt_seq_block(name: str, cmds: List[UcmSequenceCmd], indent: str = "\t") -> List[str]:
    if not cmds:
        return []
    lines = [f"{indent}{name} ["]
    for c in cmds:
        lines.append(c.format_line(indent + "\t"))
    lines.append(f"{indent}]")
    return lines


def serialize_master_conf(profile: UcmProfile) -> str:
    lines: List[str] = [
        f"# ALSA UCM2 Master Configuration for {profile.card_longname or profile.card_name or 'SOF Card'}",
        "# Generated / Edited by sof-ucm",
        "",
        f"Syntax {profile.syntax_version}",
        "",
    ]
    if profile.comment:
        lines.append(f"Comment {_ucm_quote(profile.comment)}")
        lines.append("")

    if profile.defines:
        lines.append("Define {")
        for k, v in profile.defines.items():
            lines.append(f"\t{k} {_ucm_quote(v)}")
        lines.append("}")
        lines.append("")

    for inc_id, inc_ref in profile.includes:
        lines.append(f"Include.{inc_id}.File {_ucm_quote(inc_ref)}")
    if profile.includes:
        lines.append("")

    if profile.boot_seq:
        lines.extend(_fmt_seq_block("BootSequence", profile.boot_seq, indent=""))
        lines.append("")

    if profile.fixed_boot_seq:
        lines.extend(_fmt_seq_block("FixedBootSequence", profile.fixed_boot_seq, indent=""))
        lines.append("")

    for vname, verb in profile.verbs.items():
        vfile = verb.file_path or f"{vname}.conf"
        if profile.ucm_root_dir and vfile.startswith(profile.ucm_root_dir):
            vfile = vfile[len(profile.ucm_root_dir):]
        lines.append(f"SectionUseCase.{_ucm_quote(vname)} {{")
        lines.append(f"\tFile {_ucm_quote(vfile)}")
        if verb.comment:
            lines.append(f"\tComment {_ucm_quote(verb.comment)}")
        lines.append("}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def serialize_verb_conf(
    verb: UcmVerb,
    ucm_root_dir: str = "",
    target_rel_path: str = "",
) -> str:
    target_rel = target_rel_path.lstrip("/")
    lines: List[str] = [
        f"# Use case configuration for verb '{verb.name}'",
        "# Generated / Edited by sof-ucm",
        "",
        "SectionVerb {",
    ]
    if verb.enable_seq:
        lines.extend(_fmt_seq_block("EnableSequence", verb.enable_seq, indent="\t"))
    if verb.disable_seq:
        lines.extend(_fmt_seq_block("DisableSequence", verb.disable_seq, indent="\t"))
    if verb.values:
        lines.append("\tValue {")
        for k, v in verb.values.items():
            sv = str(v)
            lines.append(f"\t\t{k} {sv}" if sv.isdigit() else f"\t\t{k} {_ucm_quote(sv)}")
        lines.append("\t}")
    lines.append("}")
    lines.append("")

    for dname, dev in verb.devices.items():
        if target_rel and dev.source_file:
            dev_rel = _rel_ucm_path(dev.source_file, ucm_root_dir)
            if dev_rel and dev_rel != target_rel:
                continue
        lines.append(f"SectionDevice.{_ucm_quote(dname)} {{")
        if dev.comment:
            lines.append(f"\tComment {_ucm_quote(dev.comment)}")
        if dev.conflicting_devices:
            lines.append("\tConflictingDevice [")
            for cd in dev.conflicting_devices:
                lines.append(f"\t\t{_ucm_quote(cd)}")
            lines.append("\t]")
        if dev.supported_devices:
            lines.append("\tSupportedDevice [")
            for sd in dev.supported_devices:
                lines.append(f"\t\t{_ucm_quote(sd)}")
            lines.append("\t]")
        if dev.enable_seq:
            lines.extend(_fmt_seq_block("EnableSequence", dev.enable_seq, indent="\t"))
        if dev.disable_seq:
            lines.extend(_fmt_seq_block("DisableSequence", dev.disable_seq, indent="\t"))
        if dev.values:
            lines.append("\tValue {")
            for k, v in dev.values.items():
                sv = str(v)
                lines.append(f"\t\t{k} {sv}" if sv.isdigit() else f"\t\t{k} {_ucm_quote(sv)}")
            lines.append("\t}")
        lines.append("}")
        lines.append("")

    for mname, mod in verb.modifiers.items():
        if target_rel and mod.source_file:
            mod_rel = _rel_ucm_path(mod.source_file, ucm_root_dir)
            if mod_rel and mod_rel != target_rel:
                continue
        lines.append(f"SectionModifier.{_ucm_quote(mname)} {{")
        if mod.comment:
            lines.append(f"\tComment {_ucm_quote(mod.comment)}")
        if mod.conflicting_devices:
            lines.append("\tConflictingDevice [")
            for cd in mod.conflicting_devices:
                lines.append(f"\t\t{_ucm_quote(cd)}")
            lines.append("\t]")
        if mod.supported_devices:
            lines.append("\tSupportedDevice [")
            for sd in mod.supported_devices:
                lines.append(f"\t\t{_ucm_quote(sd)}")
            lines.append("\t]")
        if mod.enable_seq:
            lines.extend(_fmt_seq_block("EnableSequence", mod.enable_seq, indent="\t"))
        if mod.disable_seq:
            lines.extend(_fmt_seq_block("DisableSequence", mod.disable_seq, indent="\t"))
        if mod.values:
            lines.append("\tValue {")
            for k, v in mod.values.items():
                sv = str(v)
                lines.append(f"\t\t{k} {sv}" if sv.isdigit() else f"\t\t{k} {_ucm_quote(sv)}")
            lines.append("\t}")
        lines.append("}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def sync_profile_raw_files(profile: UcmProfile) -> None:
    """Regenerate raw_files entries without overwriting conditional/included UCM2 sources."""
    master_key = profile.master_rel_path or f"{profile.card_driver or 'card'}.conf"
    if profile.is_generated or master_key not in profile.raw_files:
        profile.raw_files[master_key] = serialize_master_conf(profile)
    elif profile.is_modified:
        existing_master = profile.raw_files[master_key]
        if not _has_non_flat_ucm_syntax(existing_master) and not profile.includes:
            profile.raw_files[master_key] = serialize_master_conf(profile)
        else:
            # Preserve existing conditional/Include structure; append any newly added verbs missing from raw text.
            additions: List[str] = []
            for vname, verb in profile.verbs.items():
                if f'"{vname}"' not in existing_master and f"'{vname}'" not in existing_master:
                    vfile = verb.file_path or f"{vname}.conf"
                    if profile.ucm_root_dir and vfile.startswith(profile.ucm_root_dir):
                        vfile = vfile[len(profile.ucm_root_dir):]
                    block = [f"SectionUseCase.{_ucm_quote(vname)} {{", f"\tFile {_ucm_quote(vfile)}"]
                    if verb.comment:
                        block.append(f"\tComment {_ucm_quote(verb.comment)}")
                    block.append("}")
                    additions.append("\n".join(block))
            if additions:
                profile.raw_files[master_key] = existing_master.rstrip() + "\n\n" + "\n\n".join(additions) + "\n"

    for vname, verb in profile.verbs.items():
        vkey = _rel_ucm_path(verb.file_path, profile.ucm_root_dir) if verb.file_path else f"{vname}.conf"
        if profile.is_generated or vkey not in profile.raw_files:
            profile.raw_files[vkey] = serialize_verb_conf(
                verb,
                ucm_root_dir=profile.ucm_root_dir,
                target_rel_path=vkey,
            )
            continue
        if not profile.is_modified:
            continue
        existing_verb = profile.raw_files[vkey]
        has_conditional_devs = any(
            d.is_conditional
            and (not d.source_file or _rel_ucm_path(d.source_file, profile.ucm_root_dir) == vkey)
            for d in verb.devices.values()
        )
        if _has_non_flat_ucm_syntax(existing_verb) or has_conditional_devs:
            # Keep raw conditional/include verb text intact rather than stripping If/Condition/Include blocks.
            continue
        profile.raw_files[vkey] = serialize_verb_conf(
            verb,
            ucm_root_dir=profile.ucm_root_dir,
            target_rel_path=vkey,
        )


# ---------------------------------------------------------------------------
# Automatic UCM2 Profile Generator from Discovered Audio Graph
# ---------------------------------------------------------------------------

def _sanitized_ucm_device_name(ep_title: str, direction: str) -> str:
    low = ep_title.lower()
    if "headphone" in low:
        return "Headphones"
    if "speaker" in low:
        return "Speaker"
    if "line out" in low:
        return "Line1"
    if "dmic" in low:
        return "Mic1"
    if "mic" in low:
        return "Mic"
    m_hdmi = re.search(r"(?:hdmi|idisp).*?(\d+)", low)
    if m_hdmi:
        return f"HDMI{m_hdmi.group(1)}"
    if "spdif" in low:
        return "SPDIF"
    clean = re.sub(r"[^A-Za-z0-9]+", "", ep_title)
    return clean or ("Output" if direction == "playback" else "Mic")


def generate_ucm_profile_from_graph(
    snap: MachineAudioSnapshot,
    vm: UcmGraphViewModel,
    ucm_root: str = "/usr/share/alsa/ucm2",
) -> UcmProfile:
    """Create a fresh UCM2 profile by tracing PCM -> DSP -> Endpoint paths in `vm`."""
    c = snap.card
    drv = (c.driver if c and c.driver else "sof-card").strip()
    longname = (c.longname if c and c.longname else drv).strip()
    master_rel = f"conf.d/{drv}/{longname}.conf"
    hifi_rel = f"/{drv}/HiFi.conf"

    profile = UcmProfile(
        card_name=c.name if c else drv,
        card_longname=longname,
        card_driver=drv,
        ucm_root_dir=ucm_root,
        master_rel_path=master_rel,
        syntax_version=6,
        comment=f"Auto-generated UCM2 profile for {longname}",
        is_modified=True,
        is_generated=True,
    )

    # BootSequence: initialize Master / DSP / Dmic default volumes & switches
    for k in snap.kcontrols:
        low = k.name.lower()
        if not k.writable:
            continue
        if any(t in low for t in ("master playback", "pcm playback", "dmic0 capture", "gain.")):
            if k.ctl_type == "BOOLEAN":
                profile.boot_seq.append(UcmSequenceCmd("cset", f"name='{k.name}' on"))
            elif k.ctl_type == "INTEGER" and k.max_val > 1:
                pct = "75%" if "capture" in low or "dmic" in low else "80%"
                profile.boot_seq.append(UcmSequenceCmd("cset", f"name='{k.name}' {pct}"))

    verb = UcmVerb(
        name="HiFi",
        file_path=hifi_rel,
        comment="Play HiFi quality Music",
        values={"TQ": "HiFi"},
        enable_seq=[UcmSequenceCmd("disdevall", "")],
    )

    # Trace from each Endpoint back to reachable PCMs & attached kcontrols
    ep_nodes = [n for n in vm.nodes.values() if n.class_category == "Endpoint"]
    if not ep_nodes:
        # Fallback: create one device per PCM
        for p in snap.pcms:
            dname = f"PCM{p.device_id}_{p.dir_short}"
            dev = UcmSectionDevice(name=dname, comment=p.name or dname, source_file=hifi_rel.lstrip("/"))
            if p.direction == "playback":
                dev.values["PlaybackPCM"] = p.ucm_pcm_ref
                dev.values["PlaybackPriority"] = "100"
            else:
                dev.values["CapturePCM"] = p.ucm_pcm_ref
                dev.values["CapturePriority"] = "100"
            verb.devices[dname] = dev
    else:
        used_names: Set[str] = set()
        for idx, ep in enumerate(ep_nodes):
            vis_nodes, vis_edges = trace_reachable_from_node(vm, ep.id)
            pcm_nodes = [
                vm.nodes[nid] for nid in vis_nodes
                if vm.nodes[nid].class_category == "PCM" and vm.nodes[nid].pcm_info is not None
            ]
            # Pick best matching PCM
            pcm_node = pcm_nodes[0] if pcm_nodes else None
            dname = _sanitized_ucm_device_name(ep.title, ep.direction)
            if dname in used_names:
                dname = f"{dname}{idx + 1}"
            used_names.add(dname)

            dev = UcmSectionDevice(
                name=dname,
                comment=ep.title,
                source_file=hifi_rel.lstrip("/"),
            )
            is_pb = ep.direction == "playback"
            prio = 200 if "headphone" in dname.lower() else (300 if "hdmi" in dname.lower() else 100)
            if pcm_node and pcm_node.pcm_info:
                pcm_ref = pcm_node.pcm_info.ucm_pcm_ref
                ch = pcm_node.pcm_info.active_channels or pcm_node.pcm_info.channels_max or 2
                if is_pb:
                    dev.values["PlaybackPCM"] = pcm_ref
                    dev.values["PlaybackPriority"] = str(prio)
                    dev.values["PlaybackChannels"] = str(ch)
                else:
                    dev.values["CapturePCM"] = pcm_ref
                    dev.values["CapturePriority"] = str(prio)
                    dev.values["CaptureChannels"] = str(ch)

            # Collect kcontrols anchored to the Endpoint or directly along the traced path
            ep_kctls: List[KcontrolInfo] = []
            for nid in vis_nodes:
                cn = vm.nodes[nid]
                if cn.class_category == "Control" and cn.kcontrol is not None:
                    if cn.parent_node_id == ep.id or dname.lower().rstrip("0123456789") in cn.kcontrol.name.lower():
                        ep_kctls.append(cn.kcontrol)

            for k in ep_kctls:
                low_k = k.name.lower()
                if "jack" in low_k:
                    dev.values.setdefault("JackControl", k.name)
                    continue
                if not k.writable:
                    continue
                if k.ctl_type == "INTEGER" and k.max_val > 1:
                    key_v = "PlaybackVolume" if is_pb else "CaptureVolume"
                    dev.values.setdefault(key_v, k.name)
                elif k.ctl_type == "BOOLEAN":
                    key_s = "PlaybackSwitch" if is_pb else "CaptureSwitch"
                    dev.values.setdefault(key_s, k.name)
                    dev.enable_seq.append(UcmSequenceCmd("cset", f"name='{k.name}' on"))
                    dev.disable_seq.append(UcmSequenceCmd("cset", f"name='{k.name}' off"))

            # Also add any DAPM route switch/mux kcontrols along vis_edges
            for eid in vis_edges:
                edge = next((e for e in vm.edges if e.id == eid), None)
                if edge and edge.kcontrol and edge.kcontrol.writable:
                    ek = edge.kcontrol
                    if ek.ctl_type == "BOOLEAN":
                        dev.enable_seq.append(UcmSequenceCmd("cset", f"name='{ek.name}' on"))
                        dev.disable_seq.append(UcmSequenceCmd("cset", f"name='{ek.name}' off"))

            verb.devices[dname] = dev

        # Add mutual ConflictingDevice for Speaker <-> Headphones
        if "Speaker" in verb.devices and "Headphones" in verb.devices:
            verb.devices["Speaker"].conflicting_devices.append("Headphones")
            verb.devices["Headphones"].conflicting_devices.append("Speaker")

    profile.verbs["HiFi"] = verb
    sync_profile_raw_files(profile)
    validate_ucm_profile(profile, snap, vm)
    return profile
