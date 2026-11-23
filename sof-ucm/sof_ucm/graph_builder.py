# SPDX-License-Identifier: BSD-3-Clause
"""Merge ALSA PCMs, .tplg topology, kcontrols, DAPM, and HDA codecs into an end-to-end GraphViewModel."""

from __future__ import annotations
from collections import defaultdict, deque
import re
from typing import Dict, List, Optional, Set, Tuple

from .model import (
    DapmWidgetInfo,
    GraphEdge,
    GraphNode,
    GraphPort,
    HdaCodecInfo,
    HdaCodecNodeInfo,
    KcontrolInfo,
    MachineAudioSnapshot,
    PcmDeviceInfo,
    PipelineContainer,
    UcmGraphViewModel,
    UcmProfile,
)
from .tplg_loader import (
    SND_SOC_TPLG_DAPM_AIF_IN,
    SND_SOC_TPLG_DAPM_AIF_OUT,
    SND_SOC_TPLG_DAPM_ASRC,
    SND_SOC_TPLG_DAPM_BUFFER,
    SND_SOC_TPLG_DAPM_DAI_IN,
    SND_SOC_TPLG_DAPM_DAI_OUT,
    SND_SOC_TPLG_DAPM_EFFECT,
    SND_SOC_TPLG_DAPM_INPUT,
    SND_SOC_TPLG_DAPM_MIXER,
    SND_SOC_TPLG_DAPM_MUX,
    SND_SOC_TPLG_DAPM_OUT_DRV,
    SND_SOC_TPLG_DAPM_OUTPUT,
    SND_SOC_TPLG_DAPM_PGA,
    SND_SOC_TPLG_DAPM_SRC,
    TplgWidgetDesc,
)

# Check constants that might not be in tplg_loader imports (fallback numeric values)
_TPLG_MACHINE_EP_TYPES = {
    SND_SOC_TPLG_DAPM_INPUT,
    SND_SOC_TPLG_DAPM_OUTPUT,
    SND_SOC_TPLG_DAPM_OUT_DRV,
}


def _classify_tplg_widget(w: TplgWidgetDesc) -> Tuple[str, str]:
    nm = w.name.lower()
    if w.dapm_type in (SND_SOC_TPLG_DAPM_AIF_IN, SND_SOC_TPLG_DAPM_AIF_OUT) or nm.startswith("host-copier"):
        return "host", "HostCopier"
    if w.dapm_type in (SND_SOC_TPLG_DAPM_DAI_IN, SND_SOC_TPLG_DAPM_DAI_OUT) or nm.startswith(("dai-copier", "alh-copier")):
        return "dai", "DaiCopier"
    if w.dapm_type in (SND_SOC_TPLG_DAPM_MIXER, SND_SOC_TPLG_DAPM_MUX) or nm.startswith(("mixin", "mixout", "mixer", "mux")):
        return "mix", "Mixer"
    if w.dapm_type == SND_SOC_TPLG_DAPM_PGA or nm.startswith("gain"):
        return "widget", "Gain"
    if w.dapm_type in (SND_SOC_TPLG_DAPM_SRC, SND_SOC_TPLG_DAPM_ASRC):
        return "widget", "SRC"
    if w.dapm_type == SND_SOC_TPLG_DAPM_EFFECT:
        return "widget", "Effect"
    if w.dapm_type == SND_SOC_TPLG_DAPM_BUFFER:
        return "widget", "Copier/Buffer"
    if w.dapm_type in _TPLG_MACHINE_EP_TYPES:
        return "hw_endpoint", w.type_name
    return "widget", w.type_name


def _is_endpoint_dapm_widget(dw: DapmWidgetInfo) -> bool:
    wt = dw.widget_type.lower()
    # Codec / DSP DAI endpoints (dai_in, dai_out, aif_in, aif_out) are Codec DAIs, NOT terminal physical pins
    if wt in ("dai_in", "dai_out", "aif_in", "aif_out", "dac", "adc", "mixer", "mux", "pga", "effect", "src", "asrc", "buffer", "scheduler"):
        return False
    if wt in ("output", "input", "spk", "hp", "mic", "line", "out_drv"):
        return True
    return False


def _infer_dapm_direction(dw: DapmWidgetInfo, dsp_comp: str) -> str:
    wt = dw.widget_type.lower()
    nm = dw.name.lower()
    if wt in ("input", "mic", "adc", "aif_out"):
        return "capture"
    if wt == "dai_out" and dw.component == dsp_comp:
        return "capture"
    if any(t in nm for t in ("dmic", "mic", "capture", "line in", "adc")):
        return "capture"
    return "playback"


def _clean_ep_name(kname: str) -> Optional[Tuple[str, str]]:
    low = kname.lower()
    m_hdmi = re.match(r"HDMI/DP,pcm=(\d+)\s+Jack", kname)
    if m_hdmi:
        return f"HDMI/DP (pcm={m_hdmi.group(1)})", "playback"
    if "spdif" in low or "iec958" in low:
        if "con mask" in low or "pro mask" in low or "default" in low:
            return None
        return "S/PDIF Digital Out", "playback"
    for suffix in (
        " Playback Volume", " Playback Switch",
        " Capture Volume", " Capture Switch",
        " Jack", " Volume", " Switch",
    ):
        if kname.endswith(suffix):
            base = kname[:-len(suffix)].strip()
            if not base or base.lower() in ("master", "pcm", "capture", "auto-mute mode", "loopback mixing"):
                return None
            direction = "capture" if ("mic" in low or "capture" in low or "line in" in low) else "playback"
            base_low = base.lower()
            if "headphone" in base_low:
                base = "Headphones"
            elif "speaker" in base_low:
                base = "Speaker"
            return base, direction
    return None


def _is_hidden_noise_kctl(k: KcontrolInfo) -> bool:
    low = k.name.lower()
    if any(x in low for x in ("con mask", "pro mask", "eld", "chmap", "channel map")) and not k.writable:
        return True
    return False


def build_end_to_end_graph(
    snap: MachineAudioSnapshot,
    include_readonly_masks: bool = False,
) -> UcmGraphViewModel:
    """Build a unified end-to-end graph from PCMs -> DSP Modules -> DAI -> Codec -> HW Endpoints."""
    card_title = snap.card.longname or snap.card.name or f"card{snap.card.card_id}" if snap.card else "No Card"
    vm = UcmGraphViewModel(title=f"{card_title} ({snap.target_label})")

    pcm_node_by_dev_dir: Dict[Tuple[int, str], str] = {}
    tplg_node_by_wname: Dict[str, str] = {}
    dapm_node_by_key: Dict[Tuple[str, str], str] = {}
    dapm_node_by_name: Dict[str, str] = {}
    dapm_by_name: Dict[str, DapmWidgetInfo] = {}
    for dw in snap.dapm_widgets:
        dapm_by_name.setdefault(dw.name, dw)

    # Identify the primary DSP PCI component (e.g. "0000:80:1f.3")
    dsp_comp = ""
    for dw in snap.dapm_widgets:
        if dw.name.startswith(("host-copier.", "dai-copier.", "pipeline.")):
            dsp_comp = dw.component
            break

    # 1. ALSA PCM Frontend Nodes
    pcm_by_stream: Dict[str, str] = {}
    for p in snap.pcms:
        nid = f"pcm:{p.direction}:{p.device_id}"
        fmt_str = ""
        if p.active_rate and p.active_format:
            fmt_str = f"{p.active_format} {p.active_rate // 1000}k {p.active_channels}ch"
        elif p.formats:
            fmt_str = f"{p.formats[0]}"
        sub_parts = [p.alsa_dev, p.state_str]
        if p.name and p.name != p.id:
            sub_parts.insert(0, p.name[:24])
        node = GraphNode(
            id=nid,
            title=f"PCM{p.device_id} {p.dir_short}: {p.id or p.name or p.alsa_dev}",
            subtitle=" • ".join(sub_parts),
            category="pcm",
            class_category="PCM",
            class_name="pcm",
            direction=p.direction,
            powered=p.is_running,
            stream_active=p.is_running,
            badges=[p.alsa_dev, p.state_str],
            out_fmt_badge=fmt_str if p.direction == "playback" else "",
            in_fmt_badge=fmt_str if p.direction == "capture" else "",
            in_ports=[GraphPort("in0", "in", "in")] if p.direction == "capture" else [],
            out_ports=[GraphPort("out0", "out", "out")] if p.direction == "playback" else [],
            pcm_info=p,
        )
        vm.nodes[nid] = node
        pcm_node_by_dev_dir[(p.device_id, p.direction)] = nid
        for alias in (p.id, p.name, p.stream_name):
            if alias:
                pcm_by_stream[alias.lower()] = nid

    # 2. DSP Pipeline Containers & Topology Widgets (from .tplg)
    tplg = snap.tplg
    fe_stream_to_pcm_nid: Dict[str, str] = {}
    if tplg is not None:
        pipe_dir: Dict[int, str] = {}
        for w in tplg.widgets:
            if w.dapm_type == SND_SOC_TPLG_DAPM_AIF_OUT or w.dapm_type == SND_SOC_TPLG_DAPM_DAI_OUT or ".capture" in w.name.lower():
                pipe_dir[w.pipeline_id] = "capture"
            elif w.pipeline_id not in pipe_dir and w.dapm_type not in _TPLG_MACHINE_EP_TYPES:
                pipe_dir[w.pipeline_id] = "playback"

        for pid, pdesc in sorted(tplg.pipelines.items()):
            dw_sched = dapm_by_name.get(pdesc.name)
            vm.containers[pid] = PipelineContainer(
                id=pid,
                title=f"Pipeline {pid}: {pdesc.name} (C{pdesc.core_id} P{pdesc.priority})",
                direction=pipe_dir.get(pid, "playback"),
                core_id=pdesc.core_id,
                priority=pdesc.priority,
                powered=bool(dw_sched and dw_sched.power > 0),
            )

        # Precompute routed widgets in .tplg so unrouted legacy machine pin stubs don't clutter pipelines
        routed_tplg_names: Set[str] = set()
        for r in tplg.routes:
            routed_tplg_names.add(r.source_name)
            routed_tplg_names.add(r.sink_name)
        for de in snap.dapm_edges:
            routed_tplg_names.add(de.src_name)
            routed_tplg_names.add(de.dst_name)

        for w in tplg.widgets:
            is_machine_ep = w.dapm_type in _TPLG_MACHINE_EP_TYPES
            # Skip unrouted legacy machine output/out_drv stubs (e.g. iDisp1_out / iDisp1 Tx) when real codec DAPM/HDA exists
            if is_machine_ep and w.name not in routed_tplg_names and (snap.dapm_available or snap.hda_codecs):
                continue

            cat, role_lbl = _classify_tplg_widget(w)
            nid = f"tplg:{w.name}"
            dw = dapm_by_name.get(w.name)
            eff_pid: Optional[int] = None if is_machine_ep else (w.pipeline_id if w.pipeline_id in vm.containers else None)
            is_cap = (pipe_dir.get(w.pipeline_id, "playback") == "capture") if eff_pid is not None else False
            in_fmt = w.input_pin_fmts[0].short_str() if w.input_pin_fmts else ""
            out_fmt = w.output_pin_fmts[0].short_str() if w.output_pin_fmts else ""
            sub_items = [role_lbl]
            if eff_pid is not None:
                sub_items.append(f"P{eff_pid}")
            sub_items.append(w.proc_domain)
            if dw:
                sub_items.append("ON" if dw.power > 0 else "OFF")
            node = GraphNode(
                id=nid,
                title=w.name,
                subtitle=" • ".join(sub_items),
                category=cat,
                class_category="Endpoint" if is_machine_ep else "Widget",
                class_name=w.type_name,
                pipeline_id=eff_pid,
                component=dw.component if dw else (dsp_comp or "sof-dsp"),
                direction="capture" if is_cap else "playback",
                powered=bool(dw and dw.power > 0),
                stream_active=bool(dw and dw.stream_active),
                in_fmt_badge=in_fmt,
                out_fmt_badge=out_fmt,
                in_ports=[GraphPort(f"in{i}", f"in{i}", "in") for i in range(max(1, w.num_input_pins))],
                out_ports=[GraphPort(f"out{i}", f"out{i}", "out") for i in range(max(1, w.num_output_pins))],
                dapm_widget=dw,
                tplg_widget=w,
            )
            vm.nodes[nid] = node
            tplg_node_by_wname[w.name] = nid
            if dw:
                dapm_node_by_key[(dw.name, dw.component)] = nid
            dapm_node_by_name[w.name] = nid

        # Connect PCM nodes to host-copiers & record FE stream names -> PCM node mapping
        for tp in tplg.pcms:
            if tp.playback_host_name and tp.playback_host_name in tplg_node_by_wname:
                pcm_nid = pcm_node_by_dev_dir.get((tp.pcm_id, "playback"))
                if pcm_nid:
                    _add_edge(vm, pcm_nid, tplg_node_by_wname[tp.playback_host_name], active=vm.nodes[pcm_nid].powered)
                    if tp.playback_stream_name:
                        fe_stream_to_pcm_nid[tp.playback_stream_name] = pcm_nid
            if tp.capture_host_name and tp.capture_host_name in tplg_node_by_wname:
                pcm_nid = pcm_node_by_dev_dir.get((tp.pcm_id, "capture"))
                if pcm_nid:
                    _add_edge(vm, tplg_node_by_wname[tp.capture_host_name], pcm_nid, active=vm.nodes[pcm_nid].powered)
                    if tp.capture_stream_name:
                        fe_stream_to_pcm_nid[tp.capture_stream_name] = pcm_nid

        for w in tplg.widgets:
            m_hc = re.match(r"host-copier\.(\d+)\.(playback|capture)", w.name)
            if m_hc:
                dev_id = int(m_hc.group(1))
                wdir = m_hc.group(2)
                pcm_nid = pcm_node_by_dev_dir.get((dev_id, wdir))
                w_nid = tplg_node_by_wname.get(w.name)
                if pcm_nid and w_nid:
                    if w.stream_name:
                        fe_stream_to_pcm_nid[w.stream_name] = pcm_nid
                    if wdir == "playback":
                        _add_edge(vm, pcm_nid, w_nid, active=vm.nodes[pcm_nid].powered)
                    else:
                        _add_edge(vm, w_nid, pcm_nid, active=vm.nodes[pcm_nid].powered)

        # Topology routes
        for r in tplg.routes:
            src_nid = tplg_node_by_wname.get(r.source_name)
            dst_nid = tplg_node_by_wname.get(r.sink_name)
            if src_nid and dst_nid:
                u = vm.nodes[src_nid]
                v = vm.nodes[dst_nid]
                _add_edge(
                    vm,
                    src_nid,
                    dst_nid,
                    control_name=r.control_name,
                    active=(u.powered and v.powered),
                    inter_pipe=(u.pipeline_id != v.pipeline_id),
                )

    # 3. Merge Codec & Card DAPM Widgets and Edges
    if snap.dapm_available:
        for dw in snap.dapm_widgets:
            key = (dw.name, dw.component)
            if key in dapm_node_by_key or dw.name in tplg_node_by_wname:
                continue
            if dw.widget_type == "scheduler":
                continue

            # Map FE DAI stream stubs on DSP component directly to their PCM node!
            if dw.name in fe_stream_to_pcm_nid:
                pcm_nid = fe_stream_to_pcm_nid[dw.name]
                dapm_node_by_key[key] = pcm_nid
                dapm_node_by_name.setdefault(dw.name, pcm_nid)
                continue
            if dw.component == dsp_comp and (
                dw.name.lower() in pcm_by_stream
                or (dw.stream_name and dw.stream_name in fe_stream_to_pcm_nid)
            ):
                pcm_nid = fe_stream_to_pcm_nid.get(dw.stream_name) or pcm_by_stream[dw.name.lower()]
                dapm_node_by_key[key] = pcm_nid
                dapm_node_by_name.setdefault(dw.name, pcm_nid)
                continue
            if dw.name.startswith("PCM") and any(dw.name == f"PCM{p.device_id}" for p in snap.pcms):
                continue

            is_ep = _is_endpoint_dapm_widget(dw)
            cat = "hw_endpoint" if is_ep else "codec"
            cls_cat = "Endpoint" if is_ep else "Codec"
            nid = f"dapm:{dw.component}:{dw.name}"
            direction = _infer_dapm_direction(dw, dsp_comp)
            sub = f"{dw.widget_type} • {dw.component} • {'ON' if dw.power > 0 else 'OFF'}"
            node = GraphNode(
                id=nid,
                title=dw.name,
                subtitle=sub,
                category=cat,
                class_category=cls_cat,
                class_name=dw.widget_type,
                component=dw.component,
                direction=direction,
                powered=dw.power > 0,
                stream_active=dw.stream_active,
                badges=[dw.widget_type.upper(), "ON" if dw.power > 0 else "OFF"],
                in_ports=[GraphPort("in0", "in", "in")] if (not is_ep or direction == "playback") else [],
                out_ports=[GraphPort("out0", "out", "out")] if (not is_ep or direction == "capture") else [],
                dapm_widget=dw,
            )
            vm.nodes[nid] = node
            dapm_node_by_key[key] = nid
            dapm_node_by_name.setdefault(dw.name, nid)

        for de in snap.dapm_edges:
            src_nid = (
                dapm_node_by_key.get((de.src_name, de.src_comp))
                or tplg_node_by_wname.get(de.src_name)
                or dapm_node_by_name.get(de.src_name)
            )
            dst_nid = (
                dapm_node_by_key.get((de.dst_name, de.dst_comp))
                or tplg_node_by_wname.get(de.dst_name)
                or dapm_node_by_name.get(de.dst_name)
            )
            if src_nid and dst_nid and src_nid != dst_nid:
                ctl = "" if de.control_name == "static" else de.control_name
                _add_edge(vm, src_nid, dst_nid, control_name=ctl, active=de.active)

    # 4. Merge HDA Codec Internal Nodes (/proc/asound/cardN/codec#*) connected to Codec DAIs or PCMs
    hda_ctl_to_nid: Dict[str, str] = {}
    _merge_hda_codecs(snap, vm, pcm_node_by_dev_dir, dapm_node_by_key, hda_ctl_to_nid)

    # 5. Synthesize Hardware Endpoints only for remaining dangling DAI/PCM paths
    _synthesize_missing_endpoints(snap, vm, pcm_node_by_dev_dir)

    # 6. Attach Kcontrols to Edges and Owning Nodes
    _attach_kcontrols(snap, vm, pcm_node_by_dev_dir, tplg_node_by_wname, hda_ctl_to_nid, include_readonly_masks)

    # 7. Prune completely isolated non-PCM nodes that have zero edges and zero child kcontrols
    if vm.edges:
        connected: Set[str] = set()
        for e in vm.edges:
            connected.add(e.source_node)
            connected.add(e.sink_node)
        for nid, n in list(vm.nodes.items()):
            if n.class_category not in ("PCM", "Control") and nid not in connected:
                has_children = any(c.parent_node_id == nid for c in vm.nodes.values())
                if not has_children:
                    del vm.nodes[nid]

    return vm


def _merge_hda_codecs(
    snap: MachineAudioSnapshot,
    vm: UcmGraphViewModel,
    pcm_node_by_dev_dir: Dict[Tuple[int, str], str],
    dapm_node_by_key: Dict[Tuple[str, str], str],
    hda_ctl_to_nid: Dict[str, str],
) -> None:
    """Build HDA codec converter -> mixer/selector -> pin complex chains from /proc/asound/cardN/codec#*."""
    if not snap.hda_codecs:
        return

    for codec in snap.hda_codecs:
        comp_id = f"ehdaudio0D{codec.address}"
        by_nid: Dict[int, HdaCodecNodeInfo] = {hn.nid: hn for hn in codec.nodes}
        is_hdmi = "hdmi" in codec.name.lower() or "display" in codec.name.lower()

        if is_hdmi:
            _merge_hdmi_hda_codec(codec, comp_id, by_nid, snap, vm, dapm_node_by_key, hda_ctl_to_nid)
        else:
            _merge_analog_hda_codec(codec, comp_id, by_nid, snap, vm, pcm_node_by_dev_dir, dapm_node_by_key, hda_ctl_to_nid)


def _merge_hdmi_hda_codec(
    codec: HdaCodecInfo,
    comp_id: str,
    by_nid: Dict[int, HdaCodecNodeInfo],
    snap: MachineAudioSnapshot,
    vm: UcmGraphViewModel,
    dapm_node_by_key: Dict[Tuple[str, str], str],
    hda_ctl_to_nid: Dict[str, str],
) -> None:
    """Connect ehdaudio0D2 hifi1..N -> HDA Audio Output converters -> active/connected HDMI/DP Pin Complexes."""
    # Find Audio Output converters (e.g. 0x03, 0x05, 0x07, 0x09)
    converters = [hn for hn in codec.nodes if hn.wtype == "Audio Output"]
    pins = [
        hn for hn in codec.nodes
        if hn.wtype == "Pin Complex" and (hn.connections or "OUT" in hn.pin_ctls)
    ]

    # Map hifi1..N DAPM Codec DAI nodes to converter nodes
    hifi_nodes: List[Tuple[int, str]] = []
    for idx in range(1, 9):
        dnid = dapm_node_by_key.get((f"hifi{idx}", comp_id))
        if dnid and dnid in vm.nodes:
            hifi_nodes.append((idx - 1, dnid))

    hdmi_pcms = sorted(
        [p for p in snap.pcms if p.direction == "playback" and "hdmi" in (p.id + " " + p.name).lower()],
        key=lambda p: p.device_id,
    )

    conv_vm_nids: Dict[int, str] = {}
    for c_idx, conv in enumerate(converters):
        connected_to_pin = any(conv.nid in p.connections for p in pins)
        has_hifi = any(hi == c_idx for hi, _ in hifi_nodes)
        has_hdmi_pcm = (not snap.tplg_available) and (c_idx < len(hdmi_pcms))
        if not connected_to_pin and not has_hifi and not has_hdmi_pcm:
            continue
        nid = f"hda:{comp_id}:0x{conv.nid:02x}"
        vm.nodes[nid] = GraphNode(
            id=nid,
            title=f"{codec.name} Conv 0x{conv.nid:02x}",
            subtitle=f"HDA Audio Output • {conv.power or 'D0'} • stream={conv.converter_stream}",
            category="codec",
            class_category="Codec",
            class_name="hda_dac",
            component=comp_id,
            direction="playback",
            powered=(conv.converter_stream > 0 and conv.power == "D0"),
            badges=["HDA DAC", f"0x{conv.nid:02x}", conv.power or "D0"],
            in_ports=[GraphPort("in0", "in", "in")],
            out_ports=[GraphPort("out0", "out", "out")],
        )
        conv_vm_nids[conv.nid] = nid
        for hi, h_nid in hifi_nodes:
            if hi == c_idx:
                _add_edge(vm, h_nid, nid, active=vm.nodes[h_nid].powered)
                if not vm.nodes[h_nid].out_ports:
                    vm.nodes[h_nid].out_ports = [GraphPort("out0", "out", "out")]
        if has_hdmi_pcm and not hifi_nodes:
            pcm_nid = f"pcm:playback:{hdmi_pcms[c_idx].device_id}"
            if pcm_nid in vm.nodes:
                _add_edge(vm, pcm_nid, nid, active=vm.nodes[pcm_nid].powered)

    for p in pins:
        pin_nid = f"hda:{comp_id}:0x{p.nid:02x}"
        p_lbl = p.pin_default.replace("[Jack]", "").strip() or f"HDMI/DP Pin 0x{p.nid:02x}"
        vm.nodes[pin_nid] = GraphNode(
            id=pin_nid,
            title=f"{p_lbl} (0x{p.nid:02x})",
            subtitle=f"HDA Pin Complex • {comp_id} • {p.pin_ctls or p.power or 'D0'}",
            category="hw_endpoint",
            class_category="Endpoint",
            class_name="hda_pin",
            component=comp_id,
            direction="playback",
            powered=("OUT" in p.pin_ctls and p.power == "D0"),
            badges=["HDMI/DP PIN", f"0x{p.nid:02x}"],
            in_ports=[GraphPort("in0", "in", "in")],
        )
        for c_nid_hex in p.connections:
            src_vm_nid = conv_vm_nids.get(c_nid_hex)
            if src_vm_nid:
                is_sel = (p.active_conn == c_nid_hex)
                _add_edge(vm, src_vm_nid, pin_nid, active=is_sel and vm.nodes[pin_nid].powered)

    # When HDA HDMI pins have no static Connection list (e.g. non-SOF HDA desktop without connected monitor),
    # still create per-PCM HDMI/DP Jack endpoints on the right so every HDMI PCM terminates cleanly.
    if not pins and not snap.tplg_available and hdmi_pcms:
        for c_idx, hp in enumerate(hdmi_pcms):
            ep_nid = f"ep:hdmi:pcm{hp.device_id}"
            vm.nodes[ep_nid] = GraphNode(
                id=ep_nid,
                title=f"HDMI/DP {c_idx} Jack (pcm={hp.device_id})",
                subtitle=f"HDMI/DP Endpoint • {comp_id}",
                category="hw_endpoint",
                class_category="Endpoint",
                class_name="hda_pin",
                component=comp_id,
                direction="playback",
                badges=["HDMI/DP JACK", f"pcm={hp.device_id}"],
                in_ports=[GraphPort("in0", "in", "in")],
            )
            if c_idx < len(converters) and converters[c_idx].nid in conv_vm_nids:
                _add_edge(vm, conv_vm_nids[converters[c_idx].nid], ep_nid)
            else:
                pcm_nid = f"pcm:playback:{hp.device_id}"
                if pcm_nid in vm.nodes:
                    _add_edge(vm, pcm_nid, ep_nid)

    # Also ensure any remaining hifiN (e.g. hifi1..3) without a pin connection links to HDMI/DP Pin if pins exist
    if pins and not any(
        e.sink_node.startswith(f"hda:{comp_id}:") for e in vm.edges
        if e.source_node.startswith(f"hda:{comp_id}:")
    ):
        for hi, h_nid in hifi_nodes:
            if hi < len(converters) and converters[hi].nid in conv_vm_nids:
                _add_edge(vm, conv_vm_nids[converters[hi].nid], f"hda:{comp_id}:0x{pins[0].nid:02x}")


def _merge_analog_hda_codec(
    codec: HdaCodecInfo,
    comp_id: str,
    by_nid: Dict[int, HdaCodecNodeInfo],
    snap: MachineAudioSnapshot,
    vm: UcmGraphViewModel,
    pcm_node_by_dev_dir: Dict[Tuple[int, str], str],
    dapm_node_by_key: Dict[Tuple[str, str], str],
    hda_ctl_to_nid: Dict[str, str],
) -> None:
    """Build connected HDA Audio Output/Input -> Mixer/Selector -> Pin Complex nodes."""
    # Only include non-vendor nodes that are part of connected paths or hold controls/pins
    relevant_types = {"Audio Output", "Audio Input", "Audio Mixer", "Audio Selector", "Pin Complex"}
    active_pins = [
        hn for hn in codec.nodes
        if hn.wtype == "Pin Complex"
        and "[N/A]" not in hn.pin_default
        and (hn.connections or "IN" in hn.pin_ctls or "OUT" in hn.pin_ctls or hn.controls)
    ]
    if not active_pins:
        return

    # Walk backward from active playback pins, skipping [N/A] unpopulated pins
    keep_nids: Set[int] = set()
    q = deque([p.nid for p in active_pins])
    while q:
        nid_hex = q.popleft()
        if nid_hex in keep_nids:
            continue
        hn = by_nid.get(nid_hex)
        if not hn or hn.wtype not in relevant_types:
            continue
        if hn.wtype == "Pin Complex" and "[N/A]" in hn.pin_default:
            continue
        keep_nids.add(nid_hex)
        for src_hex in hn.connections:
            if src_hex not in keep_nids:
                q.append(src_hex)

    # Also include Audio Inputs and any Selector/Mixer chains upstream of them (skipping [N/A] pins)
    cap_q = deque([hn.nid for hn in codec.nodes if hn.wtype == "Audio Input" and hn.connections])
    while cap_q:
        nid_hex = cap_q.popleft()
        if nid_hex in keep_nids:
            continue
        hn = by_nid.get(nid_hex)
        if not hn or hn.wtype not in relevant_types:
            continue
        if hn.wtype == "Pin Complex" and "[N/A]" in hn.pin_default:
            continue
        keep_nids.add(nid_hex)
        for src_hex in hn.connections:
            if src_hex not in keep_nids:
                cap_q.append(src_hex)

    if not keep_nids:
        return

    vm_nid_of_hex: Dict[int, str] = {}
    for nid_hex in sorted(keep_nids):
        hn = by_nid[nid_hex]
        is_pin = hn.wtype == "Pin Complex"
        is_cap = (
            hn.wtype == "Audio Input"
            or "IN" in hn.pin_ctls
            or any(t in hn.pin_default.lower() for t in ("mic", "line in"))
        )
        direction = "capture" if is_cap else "playback"
        if is_pin:
            pin_lbl = re.sub(r"\[[^\]]+\]\s*", "", hn.pin_default).split(" at ")[0].strip() or f"Pin 0x{hn.nid:02x}"
            title = f"{pin_lbl} (0x{hn.nid:02x})"
            cat = "hw_endpoint"
            cls_cat = "Endpoint"
        else:
            title = f"{codec.name.split()[0]} {hn.wtype} 0x{hn.nid:02x}"
            cat = "mix" if hn.wtype in ("Audio Mixer", "Audio Selector") else "codec"
            cls_cat = "Codec"

        vm_nid = f"hda:{comp_id}:0x{hn.nid:02x}"
        vm.nodes[vm_nid] = GraphNode(
            id=vm_nid,
            title=title,
            subtitle=f"{hn.wtype} • {comp_id} • {hn.power or 'D0'}",
            category=cat,
            class_category=cls_cat,
            class_name=hn.wtype.lower().replace(" ", "_"),
            component=comp_id,
            direction=direction,
            powered=(hn.power == "D0"),
            badges=[hn.wtype.upper(), f"0x{hn.nid:02x}"],
            in_ports=[GraphPort("in0", "in", "in")] if (not is_pin or direction == "playback") else [],
            out_ports=[GraphPort("out0", "out", "out")] if (not is_pin or direction == "capture") else [],
        )
        vm_nid_of_hex[nid_hex] = vm_nid
        for cname in hn.controls:
            hda_ctl_to_nid.setdefault(cname, vm_nid)

    # Connect HDA internal connections (for capture Pin -> Selector -> ADC, edge direction is Pin -> ADC;
    # for playback DAC -> Mixer -> Pin, edge direction is DAC -> Pin)
    for nid_hex in keep_nids:
        hn = by_nid[nid_hex]
        dst_vm = vm_nid_of_hex[nid_hex]
        is_cap = vm.nodes[dst_vm].direction == "capture"
        for src_hex in hn.connections:
            src_vm = vm_nid_of_hex.get(src_hex)
            if not src_vm or src_vm == dst_vm:
                continue
            if is_cap:
                # In HDA, Audio Input lists connection to ADC mux/pin: signal flows Pin/Mux -> Audio Input
                _add_edge(vm, src_vm, dst_vm, active=(hn.active_conn == src_hex))
            else:
                _add_edge(vm, src_vm, dst_vm, active=(hn.active_conn == src_hex))

    # Connect Audio Output / Input converters to PCM nodes (when no SOF .tplg already handled them)
    if not snap.tplg_available:
        for nid_hex in keep_nids:
            hn = by_nid[nid_hex]
            if hn.wtype == "Audio Output":
                dev_id = hn.device_id if hn.device_id is not None else 0
                pcm_nid = pcm_node_by_dev_dir.get((dev_id, "playback")) or pcm_node_by_dev_dir.get((0, "playback"))
                if pcm_nid:
                    _add_edge(vm, pcm_nid, vm_nid_of_hex[nid_hex], active=vm.nodes[pcm_nid].powered)
            elif hn.wtype == "Audio Input":
                dev_id = hn.device_id if hn.device_id is not None else 0
                pcm_nid = pcm_node_by_dev_dir.get((dev_id, "capture")) or pcm_node_by_dev_dir.get((0, "capture"))
                if pcm_nid:
                    _add_edge(vm, vm_nid_of_hex[nid_hex], pcm_nid, active=vm.nodes[pcm_nid].powered)


def _add_edge(
    vm: UcmGraphViewModel,
    src_nid: str,
    dst_nid: str,
    control_name: str = "",
    active: bool = False,
    inter_pipe: bool = False,
) -> GraphEdge:
    for existing in vm.edges:
        if (
            existing.source_node == src_nid
            and existing.sink_node == dst_nid
            and existing.control_name == control_name
        ):
            return existing
    eid = f"e{len(vm.edges)}:{src_nid}->{dst_nid}"
    if control_name:
        eid += f":{control_name}"
    edge = GraphEdge(
        id=eid,
        source_node=src_nid,
        sink_node=dst_nid,
        control_name=control_name,
        is_active=active,
        is_inter_pipeline=inter_pipe,
    )
    vm.edges.append(edge)
    return edge


def _synthesize_missing_endpoints(
    snap: MachineAudioSnapshot,
    vm: UcmGraphViewModel,
    pcm_node_by_dev_dir: Dict[Tuple[int, str], str],
) -> None:
    """Ensure every PCM / DAI Copier reaches a Hardware Endpoint node if not already routed to Codec/Endpoint."""
    existing_eps = [n for n in vm.nodes.values() if n.class_category == "Endpoint"]
    ep_by_key: Dict[str, str] = {n.title.lower(): n.id for n in existing_eps}

    def get_or_create_ep(ep_name: str, direction: str, comp: str = "hw-endpoint") -> str:
        key = ep_name.lower()
        if key in ep_by_key:
            return ep_by_key[key]
        nid = f"ep:synth:{re.sub(r'[^a-z0-9]+', '_', key).strip('_')}"
        node = GraphNode(
            id=nid,
            title=ep_name,
            subtitle=f"HW Endpoint • {direction.title()}",
            category="hw_endpoint",
            class_category="Endpoint",
            class_name="hw_endpoint",
            component=comp,
            direction=direction,
            badges=["ENDPOINT", direction.upper()],
            in_ports=[GraphPort("in0", "in", "in")] if direction == "playback" else [],
            out_ports=[GraphPort("out0", "out", "out")] if direction == "capture" else [],
        )
        vm.nodes[nid] = node
        ep_by_key[key] = nid
        return nid

    out_adj: Dict[str, int] = defaultdict(int)
    in_adj: Dict[str, int] = defaultdict(int)
    for e in vm.edges:
        out_adj[e.source_node] += 1
        in_adj[e.sink_node] += 1

    # Link dangling DAI copiers (e.g. SSP2-BT or non-debugfs platforms) to HW Endpoints
    for dn in [n for n in vm.nodes.values() if n.category == "dai"]:
        low = dn.title.lower()
        if dn.direction == "playback" and out_adj[dn.id] == 0:
            if "ssp" in low and "bt" in low:
                ep_nid = get_or_create_ep("Bluetooth SCO Out", "playback", "bt-codec")
                _add_edge(vm, dn.id, ep_nid, active=dn.powered)
            elif "idisp" in low or "hdmi" in low:
                m = re.search(r"idisp(\d+)|hdmi(\d+)", low)
                idx = (m.group(1) or m.group(2)) if m else "1"
                ep_nid = get_or_create_ep(f"HDMI/DP {idx}", "playback", "hdmi-codec")
                _add_edge(vm, dn.id, ep_nid, active=dn.powered)
            elif "hda" in low or "analog" in low or "ssp" in low or "sdw" in low:
                has_hp = any("headphone" in k.name.lower() for k in snap.kcontrols)
                has_spk = any("speaker" in k.name.lower() for k in snap.kcontrols)
                if has_spk:
                    _add_edge(vm, dn.id, get_or_create_ep("Speaker", "playback", "codec"), active=dn.powered)
                if has_hp:
                    _add_edge(vm, dn.id, get_or_create_ep("Headphones", "playback", "codec"), active=dn.powered)
                if not has_spk and not has_hp:
                    _add_edge(vm, dn.id, get_or_create_ep(f"DAI Out ({dn.title})", "playback", "codec"), active=dn.powered)
        elif dn.direction == "capture" and in_adj[dn.id] == 0:
            if "ssp" in low and "bt" in low:
                ep_nid = get_or_create_ep("Bluetooth SCO In", "capture", "bt-codec")
                _add_edge(vm, ep_nid, dn.id, active=dn.powered)
            elif "dmic" in low:
                ep_nid = get_or_create_ep("Internal DMIC", "capture", "dmic-codec")
                _add_edge(vm, ep_nid, dn.id, active=dn.powered)
            else:
                ep_nid = get_or_create_ep("Mic", "capture", "codec")
                _add_edge(vm, ep_nid, dn.id, active=dn.powered)

    # Also ensure Codec DAIs (e.g. dmic-codec:Capture) that have no PCM edge don't dangle silently,
    # and if there is no .tplg and no HDA codec graph and no DAPM, bridge PCMs to synthesized Endpoints
    if not snap.tplg_available and not snap.dapm_available and not snap.hda_codecs:
        ep_kctl_names: List[Tuple[str, str]] = []
        for k in snap.kcontrols:
            res = _clean_ep_name(k.name)
            if res and res not in ep_kctl_names:
                ep_kctl_names.append(res)

        pb_pcms = [p for p in snap.pcms if p.direction == "playback"]
        cap_pcms = [p for p in snap.pcms if p.direction == "capture"]
        analog_pb = next((p for p in pb_pcms if "hdmi" not in p.name.lower() and "spdif" not in p.name.lower() and "digital" not in p.name.lower()), None)
        if analog_pb is not None:
            pcm_nid = pcm_node_by_dev_dir[(analog_pb.device_id, "playback")]
            codec_nid = f"codec:synth:{snap.card.driver if snap.card else 'alsa'}"
            vm.nodes[codec_nid] = GraphNode(
                id=codec_nid,
                title=f"{snap.card.name if snap.card else 'Codec'} Mixer",
                subtitle="ALSA Codec / Mixer Path",
                category="codec",
                class_category="Codec",
                class_name="mixer",
                direction="playback",
                in_ports=[GraphPort("in0", "in", "in")],
                out_ports=[GraphPort("out0", "out", "out")],
            )
            _add_edge(vm, pcm_nid, codec_nid, active=analog_pb.is_running)
            analog_eps = [(nm, dr) for nm, dr in ep_kctl_names if dr == "playback" and "hdmi" not in nm.lower() and "s/pdif" not in nm.lower()]
            if not analog_eps:
                analog_eps = [("Speaker / Line Out", "playback")]
            for nm, dr in analog_eps:
                _add_edge(vm, codec_nid, get_or_create_ep(nm, dr, "codec"), active=analog_pb.is_running)


def _attach_kcontrols(
    snap: MachineAudioSnapshot,
    vm: UcmGraphViewModel,
    pcm_node_by_dev_dir: Dict[Tuple[int, str], str],
    tplg_node_by_wname: Dict[str, str],
    hda_ctl_to_nid: Dict[str, str],
    include_readonly_masks: bool,
) -> None:
    ep_nodes = [n for n in vm.nodes.values() if n.class_category == "Endpoint"]
    codec_nodes = [n for n in vm.nodes.values() if n.class_category == "Codec"]
    edges_by_ctl: Dict[str, List[GraphEdge]] = defaultdict(list)
    for e in vm.edges:
        if e.control_name:
            edges_by_ctl[e.control_name].append(e)

    for k in snap.kcontrols:
        if not include_readonly_masks and _is_hidden_noise_kctl(k):
            continue

        if k.name in edges_by_ctl:
            for e in edges_by_ctl[k.name]:
                e.kcontrol = k

        parent_nid: Optional[str] = None
        is_hdmi_iec958 = (
            "iec958" in k.name.lower() and k.index < 16 and "default pcm" not in k.name.lower()
        )
        if k.anchor_widget and k.anchor_widget in tplg_node_by_wname:
            parent_nid = tplg_node_by_wname[k.anchor_widget]
        elif not is_hdmi_iec958 and k.name in hda_ctl_to_nid and hda_ctl_to_nid[k.name] in vm.nodes:
            parent_nid = hda_ctl_to_nid[k.name]
        elif k.anchor_kind == "pcm":
            pdir = "capture" if "capture" in k.name.lower() else "playback"
            parent_nid = pcm_node_by_dev_dir.get((k.device, pdir)) or pcm_node_by_dev_dir.get((k.device, "playback"))
        else:
            low_k = k.name.lower()
            m_hdmi = re.match(r"HDMI/DP,pcm=(\d+)\s+Jack", k.name)
            if m_hdmi:
                pcm_num = int(m_hdmi.group(1))
                for ep in ep_nodes:
                    if f"pcm={pcm_num}" in ep.title.lower():
                        parent_nid = ep.id
                        break
                if parent_nid is None:
                    for ep in ep_nodes:
                        if "hdmi" in ep.title.lower():
                            parent_nid = ep.id
                            break
                if parent_nid is None:
                    parent_nid = pcm_node_by_dev_dir.get((pcm_num, "playback"))
            if parent_nid is None:
                for ep in ep_nodes:
                    ep_tok = ep.title.lower().split("(")[0].strip()
                    if ep_tok and (ep_tok in low_k or (ep_tok == "headphones" and "headphone" in low_k)):
                        parent_nid = ep.id
                        break
            if parent_nid is None and "iec958" in low_k and k.index < 16 and "default pcm" not in low_k:
                # Attach IEC958 HDMI controls (index 0..3) to the matching HDMI endpoint / PCM node
                hdmi_eps = sorted(
                    (ep for ep in ep_nodes if "hdmi" in ep.title.lower()),
                    key=lambda e: e.title,
                )
                idx_pcm_map = {0: 3, 1: 4, 2: 5}
                if 0 <= k.index < len(hdmi_eps):
                    parent_nid = hdmi_eps[k.index].id
                elif hdmi_eps:
                    parent_nid = hdmi_eps[0].id
                elif k.index in idx_pcm_map and (idx_pcm_map[k.index], "playback") in pcm_node_by_dev_dir:
                    parent_nid = pcm_node_by_dev_dir[(idx_pcm_map[k.index], "playback")]
            if parent_nid is None:
                for cn in codec_nodes:
                    if cn.dapm_widget and k.name in cn.dapm_widget.kcontrol_names:
                        parent_nid = cn.id
                        break
            if parent_nid is None and "iec958" in low_k:
                hdmi_eps = sorted(
                    (ep for ep in ep_nodes if "hdmi" in ep.title.lower()),
                    key=lambda e: e.title,
                )
                if hdmi_eps:
                    parent_nid = hdmi_eps[0].id
            if parent_nid is None and codec_nodes and any(t in low_k for t in ("master", "pcm", "capture", "auto-mute", "loopback", "input source", "mic boost")):
                parent_nid = codec_nodes[0].id

        if parent_nid is None or parent_nid not in vm.nodes:
            continue

        parent = vm.nodes[parent_nid]
        ctl_nid = f"kctl:{k.numid}:{k.name}"
        cat = k.category
        ctl_node = GraphNode(
            id=ctl_nid,
            title=k.name,
            subtitle=f"{k.ctl_type} = {k.formatted_value()}",
            category=cat,
            class_category="Control",
            class_name=cat,
            pipeline_id=parent.pipeline_id,
            component=parent.component,
            direction=parent.direction,
            parent_node_id=parent_nid,
            kcontrol=k,
            in_ports=[GraphPort("in0", "ctl", "in")],
            out_ports=[GraphPort("out0", "ctl", "out")],
        )
        vm.nodes[ctl_nid] = ctl_node


# ---------------------------------------------------------------------------
# End-to-End Signal Path Tracing & UCM Overlay
# ---------------------------------------------------------------------------

def trace_reachable_from_node(vm: UcmGraphViewModel, start_nid: str) -> Tuple[Set[str], Set[str]]:
    if start_nid not in vm.nodes:
        return set(), set()

    fwd: Dict[str, List[GraphEdge]] = defaultdict(list)
    rev: Dict[str, List[GraphEdge]] = defaultdict(list)
    for e in vm.edges:
        fwd[e.source_node].append(e)
        rev[e.sink_node].append(e)

    vis_nodes: Set[str] = {start_nid}
    vis_edges: Set[str] = set()

    for adj, is_fwd in ((fwd, True), (rev, False)):
        q = deque([start_nid])
        seen = {start_nid}
        while q:
            u = q.popleft()
            for e in adj[u]:
                vis_edges.add(e.id)
                nxt = e.sink_node if is_fwd else e.source_node
                if nxt not in seen:
                    seen.add(nxt)
                    vis_nodes.add(nxt)
                    q.append(nxt)

    for nid, n in vm.nodes.items():
        if n.class_category == "Control" and n.parent_node_id in vis_nodes:
            vis_nodes.add(nid)

    return vis_nodes, vis_edges


def apply_ucm_overlay_to_graph(
    vm: UcmGraphViewModel,
    profile: Optional[UcmProfile],
    verb_name: Optional[str],
    selected_devices: Set[str],
) -> None:
    vm.highlighted_node_ids.clear()
    vm.highlighted_edge_ids.clear()
    vm.active_ucm_devices = set(selected_devices)
    vm.has_active_trace = False
    for n in vm.nodes.values():
        n.ucm_badges.clear()
        n.has_error = False
        n.has_warning = False
        n.diag_message = ""
    for e in vm.edges:
        e.is_ucm_highlighted = False

    if profile is None or not verb_name or verb_name not in profile.verbs:
        return

    verb = profile.verbs[verb_name]

    for diag in profile.diagnostics:
        target_nid = diag.node_id
        if not target_nid and diag.kcontrol_name:
            for nid, n in vm.nodes.items():
                if n.kcontrol and n.kcontrol.name == diag.kcontrol_name:
                    target_nid = nid
                    break
        if target_nid and target_nid in vm.nodes:
            tn = vm.nodes[target_nid]
            if diag.severity == "error":
                tn.has_error = True
            else:
                tn.has_warning = True
            tn.diag_message = diag.message

    if not selected_devices:
        return

    vm.has_active_trace = True
    for dev_name in selected_devices:
        dev = verb.devices.get(dev_name)
        if not dev:
            continue

        matched_pcms: List[str] = []
        for val_key, direction in (("PlaybackPCM", "playback"), ("CapturePCM", "capture")):
            pcm_ref = dev.values.get(val_key, "")
            m = re.search(r",(\d+)", pcm_ref)
            if m:
                dev_id = int(m.group(1))
                pcm_nid = f"pcm:{direction}:{dev_id}"
                if pcm_nid in vm.nodes:
                    matched_pcms.append(pcm_nid)
                    vm.nodes[pcm_nid].ucm_badges.append(f"UCM:{dev_name}")

        claimed_kctl_names: Set[str] = set()
        for vk in (
            "PlaybackVolume", "PlaybackSwitch", "PlaybackMixerElem",
            "CaptureVolume", "CaptureSwitch", "CaptureMixerElem",
            "JackControl", "JackHWMixer",
        ):
            v = dev.values.get(vk, "").strip()
            if v:
                claimed_kctl_names.add(v)
        for seq_cmd in dev.enable_seq + dev.disable_seq:
            m_cset = re.search(r"name='([^']+)'", seq_cmd.arg)
            if m_cset:
                claimed_kctl_names.add(m_cset.group(1))

        matched_ep_nids: Set[str] = set()
        for nid, n in vm.nodes.items():
            if n.kcontrol:
                if (
                    n.kcontrol.name in claimed_kctl_names
                    or any(n.kcontrol.name.startswith(c) for c in claimed_kctl_names if len(c) > 3)
                ):
                    n.ucm_badges.append(f"UCM:{dev_name}")
                    vm.highlighted_node_ids.add(nid)
                    if n.parent_node_id and n.parent_node_id in vm.nodes:
                        vm.highlighted_node_ids.add(n.parent_node_id)
                        if vm.nodes[n.parent_node_id].class_category == "Endpoint":
                            matched_ep_nids.add(n.parent_node_id)
            elif n.class_category == "Endpoint":
                dn_low = dev_name.lower()
                nt_low = n.title.lower()
                if dn_low in nt_low or (dn_low.startswith("headphone") and "headphone" in nt_low) or (dn_low.startswith("mic") and "mic" in nt_low):
                    matched_ep_nids.add(nid)
                    n.ucm_badges.append(f"UCM:{dev_name}")

        for p_nid in matched_pcms:
            if matched_ep_nids:
                p_nodes, p_edges = _trace_between(vm, p_nid, matched_ep_nids)
                if not p_nodes:
                    p_nodes, p_edges = trace_reachable_from_node(vm, p_nid)
            else:
                p_nodes, p_edges = trace_reachable_from_node(vm, p_nid)
            vm.highlighted_node_ids.update(p_nodes)
            vm.highlighted_edge_ids.update(p_edges)

        for ep_nid in matched_ep_nids:
            vm.highlighted_node_ids.add(ep_nid)

    for e in vm.edges:
        if e.id in vm.highlighted_edge_ids:
            e.is_ucm_highlighted = True


def _trace_between(
    vm: UcmGraphViewModel,
    start_nid: str,
    target_nids: Set[str],
) -> Tuple[Set[str], Set[str]]:
    undirected_fwd: Dict[str, List[GraphEdge]] = defaultdict(list)
    undirected_rev: Dict[str, List[GraphEdge]] = defaultdict(list)
    for e in vm.edges:
        undirected_fwd[e.source_node].append(e)
        undirected_rev[e.sink_node].append(e)

    for adj, is_fwd in ((undirected_fwd, True), (undirected_rev, False)):
        reach_from_start: Set[str] = {start_nid}
        q = deque([start_nid])
        while q:
            u = q.popleft()
            for e in adj[u]:
                nxt = e.sink_node if is_fwd else e.source_node
                if nxt not in reach_from_start:
                    reach_from_start.add(nxt)
                    q.append(nxt)

        hit_targets = reach_from_start & target_nids
        if not hit_targets:
            continue

        rev_adj = undirected_rev if is_fwd else undirected_fwd
        reach_to_target: Set[str] = set(hit_targets)
        q2 = deque(hit_targets)
        while q2:
            u = q2.popleft()
            for e in rev_adj[u]:
                prev_n = e.source_node if is_fwd else e.sink_node
                if prev_n not in reach_to_target:
                    reach_to_target.add(prev_n)
                    q2.append(prev_n)

        path_nodes = reach_from_start & reach_to_target
        path_edges: Set[str] = {
            e.id for e in vm.edges if e.source_node in path_nodes and e.sink_node in path_nodes
        }
        for nid, n in vm.nodes.items():
            if n.class_category == "Control" and n.parent_node_id in path_nodes:
                path_nodes.add(nid)
        return path_nodes, path_edges

    return set(), set()
