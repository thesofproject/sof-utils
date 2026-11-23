# SPDX-License-Identifier: BSD-3-Clause
"""Data models for machine audio discovery, end-to-end pipeline graph, and ALSA UCM2."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# 1. Machine Audio Discovery Models (Cards, PCMs, Kcontrols, DAPM, DPCM)
# ---------------------------------------------------------------------------

@dataclass
class SoundCardInfo:
    """Discovered ALSA sound card and UCM matching metadata."""
    card_id: int
    id: str = ""             # /proc/asound/cardN/id (e.g. "sofhdadsp")
    driver: str = ""         # e.g. "sof-hda-dsp", "USB-Audio"
    name: str = ""           # short card name
    longname: str = ""       # full card longname
    components: str = ""     # sysfs / proc components string (e.g. "HDA:... cfg-dmics:4")
    tplg_path: str = ""      # resolved .tplg path on target machine
    asoc_card_dir: str = ""  # directory name under /sys/kernel/debug/asoc/
    num_pcm_streams: int = 0
    num_kcontrols: int = 0
    dmi_vendor: str = ""
    dmi_product: str = ""


@dataclass
class PcmDeviceInfo:
    """ALSA PCM or Compress stream endpoint."""
    card_id: int
    device_id: int
    direction: str = "playback"  # "playback" | "capture"
    id: str = ""
    name: str = ""
    stream_name: str = ""
    is_compress: bool = False
    state_str: str = "CLOSED"
    is_running: bool = False
    active_format: str = ""
    active_channels: int = 0
    active_rate: int = 0
    period_size: int = 0
    buffer_size: int = 0
    formats: List[str] = field(default_factory=list)
    rates: List[int] = field(default_factory=list)
    channels_min: int = 0
    channels_max: int = 0
    subdevices_count: int = 1
    subdevices_avail: int = 1

    @property
    def alsa_dev(self) -> str:
        return f"hw:{self.card_id},{self.device_id}"

    @property
    def ucm_pcm_ref(self) -> str:
        return f"hw:${{CardId}},{self.device_id}"

    @property
    def dir_short(self) -> str:
        return "PB" if self.direction == "playback" else "CAP"


@dataclass
class KcontrolInfo:
    """ALSA mixer/card/PCM kcontrol with range, TLV dB scale, values, and graph anchor."""
    numid: int
    name: str
    iface: str = "MIXER"          # "MIXER", "PCM", "CARD"
    index: int = 0
    device: int = 0
    subdevice: int = 0
    ctl_type: str = "INTEGER"     # "BOOLEAN", "INTEGER", "INTEGER64", "ENUMERATED", "BYTES", "IEC958"
    count: int = 1
    min_val: int = 0
    max_val: int = 1
    step: int = 1
    values: List[Any] = field(default_factory=list)
    enum_items: List[str] = field(default_factory=list)
    db_min: Optional[float] = None
    db_step: Optional[float] = None
    db_mute: bool = False
    tlv_str: str = ""
    writable: bool = True
    readable: bool = True
    inactive: bool = False
    # Graph anchoring resolved from .tplg / DAPM / naming
    anchor_kind: str = "card"     # "widget", "edge", "pcm", "endpoint", "card"
    anchor_widget: str = ""
    anchor_component: str = ""
    anchor_edge_ctl: str = ""
    pipeline_id: Optional[int] = None

    @property
    def category(self) -> str:
        if self.ctl_type == "BOOLEAN":
            return "control_switch"
        if self.ctl_type == "ENUMERATED":
            return "control_enum"
        if self.ctl_type == "BYTES":
            return "control_bytes"
        if self.max_val <= 1 and self.min_val == 0:
            return "control_switch"
        return "control_mixer"

    def formatted_value(self) -> str:
        if not self.values:
            return "-"
        if self.ctl_type == "ENUMERATED":
            v0 = self.values[0]
            if isinstance(v0, int) and 0 <= v0 < len(self.enum_items):
                return self.enum_items[v0]
            return str(v0)
        if self.ctl_type == "BOOLEAN":
            return "on" if all(bool(v) for v in self.values) else (
                "off" if not any(bool(v) for v in self.values) else ",".join("on" if v else "off" for v in self.values)
            )
        if self.ctl_type == "BYTES":
            return f"{len(self.values)} bytes"
        if len(self.values) > 1 and all(v == self.values[0] for v in self.values):
            return str(self.values[0])
        return ",".join(str(v) for v in self.values[:4]) + ("…" if len(self.values) > 4 else "")


@dataclass
class DapmComponentInfo:
    name: str
    bias_level: str = "Off"
    widget_count: int = 0
    powered_count: int = 0


@dataclass
class DapmWidgetInfo:
    name: str
    component: str
    widget_type: str = "unknown"
    power: int = 0                # 0=Off, 1=On, 2=On(forced)
    in_ep: int = 0
    out_ep: int = 0
    stream_name: str = ""
    stream_active: bool = False
    pipeline_id: Optional[int] = None
    reg: Optional[int] = None
    mask: int = 0
    kcontrol_names: List[str] = field(default_factory=list)


@dataclass
class DapmEdgeInfo:
    src_name: str
    src_comp: str
    dst_name: str
    dst_comp: str
    control_name: str = "static"
    active: bool = False


@dataclass
class DpcmLinkInfo:
    fe_name: str
    direction: str = "playback"
    state_str: str = "close"
    fe_format: str = ""
    fe_channels: int = 0
    fe_rate: int = 0
    backends: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class HdaCodecNodeInfo:
    """Single widget node parsed from /proc/asound/cardN/codec#M."""
    nid: int
    wtype: str                    # "Audio Output", "Audio Input", "Audio Mixer", "Audio Selector", "Pin Complex", etc.
    pin_default: str = ""         # e.g. "[Jack] HP Out at Ext Front", "[Jack] Digital Out at Int HDMI"
    pin_ctls: str = ""            # e.g. "0x40: OUT", "0x20: IN"
    power: str = ""               # e.g. "D0", "D3"
    device_name: str = ""
    device_id: Optional[int] = None
    controls: List[str] = field(default_factory=list)
    connections: List[int] = field(default_factory=list)
    active_conn: Optional[int] = None
    converter_stream: int = 0


@dataclass
class HdaCodecInfo:
    """Parsed HDA codec (/proc/asound/cardN/codec#M)."""
    address: int
    name: str
    vendor_id: str = ""
    nodes: List[HdaCodecNodeInfo] = field(default_factory=list)


@dataclass
class MachineAudioSnapshot:
    """Complete snapshot of a machine's audio topology, PCMs, kcontrols, and DAPM graph."""
    target_label: str = "local"
    card: Optional[SoundCardInfo] = None
    cards: List[SoundCardInfo] = field(default_factory=list)
    pcms: List[PcmDeviceInfo] = field(default_factory=list)
    kcontrols: List[KcontrolInfo] = field(default_factory=list)
    kcontrols_by_name: Dict[str, KcontrolInfo] = field(default_factory=dict)
    tplg: Any = None  # Optional[TplgTopology]
    dapm_components: List[DapmComponentInfo] = field(default_factory=list)
    dapm_widgets: List[DapmWidgetInfo] = field(default_factory=list)
    dapm_edges: List[DapmEdgeInfo] = field(default_factory=list)
    dpcm_links: List[DpcmLinkInfo] = field(default_factory=list)
    hda_codecs: List[HdaCodecInfo] = field(default_factory=list)
    dapm_available: bool = False
    tplg_available: bool = False
    warnings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 2. Visual Graph Models (Matching sof-builder GraphNode/GraphEdge/Container)
# ---------------------------------------------------------------------------

@dataclass
class GraphPort:
    id: str
    label: str
    direction: str          # "in" | "out"
    rel_x: float = 0.0
    rel_y: float = 0.0


@dataclass
class GraphNode:
    id: str
    title: str
    subtitle: str
    category: str           # "pcm", "host", "widget", "mix", "dai", "codec", "hw_endpoint", "control_*"
    class_category: str     # "PCM", "Widget", "Codec", "Endpoint", "Control"
    class_name: str = ""
    pipeline_id: Optional[int] = None
    component: str = ""
    direction: str = "playback"   # "playback" | "capture"
    powered: bool = False
    stream_active: bool = False
    badges: List[str] = field(default_factory=list)
    in_fmt_badge: str = ""
    out_fmt_badge: str = ""
    in_ports: List[GraphPort] = field(default_factory=list)
    out_ports: List[GraphPort] = field(default_factory=list)
    parent_node_id: Optional[str] = None
    # Back-references to discovered domain objects
    pcm_info: Optional[PcmDeviceInfo] = None
    kcontrol: Optional[KcontrolInfo] = None
    dapm_widget: Optional[DapmWidgetInfo] = None
    tplg_widget: Any = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    # Layout geometry
    x: float = 0.0
    y: float = 0.0
    width: float = 200.0
    height: float = 86.0
    # UCM & diagnostic annotations
    ucm_badges: List[str] = field(default_factory=list)
    has_error: bool = False
    has_warning: bool = False
    diag_message: str = ""


@dataclass
class GraphEdge:
    id: str
    source_node: str
    sink_node: str
    source_port: str = "out0"
    sink_port: str = "in0"
    control_name: str = ""      # non-empty for DAPM route switch/mux edges
    kcontrol: Optional[KcontrolInfo] = None
    is_active: bool = False
    is_inter_pipeline: bool = False
    is_ucm_highlighted: bool = False


@dataclass
class PipelineContainer:
    id: int
    title: str
    direction: str = "playback"  # "playback" | "capture" | "codec"
    core_id: int = 0
    priority: int = 0
    x: float = 0.0
    y: float = 0.0
    width: float = 260.0
    height: float = 160.0
    powered: bool = False
    warning_text: str = ""


@dataclass
class UcmGraphViewModel:
    title: str = "SOF End-to-End Audio Pipelines"
    nodes: Dict[str, GraphNode] = field(default_factory=dict)
    edges: List[GraphEdge] = field(default_factory=list)
    containers: Dict[int, PipelineContainer] = field(default_factory=dict)
    # Active UCM overlay state
    highlighted_node_ids: Set[str] = field(default_factory=set)
    highlighted_edge_ids: Set[str] = field(default_factory=set)
    active_ucm_devices: Set[str] = field(default_factory=set)
    has_active_trace: bool = False


# ---------------------------------------------------------------------------
# 3. ALSA UCM2 AST & Profile Models
# ---------------------------------------------------------------------------

@dataclass
class UcmSequenceCmd:
    """Single command inside BootSequence / EnableSequence / DisableSequence."""
    cmd: str                # "cset", "cset-tlv", "cset-new", "disdevall", "exec", "msleep", etc.
    arg: str = ""           # raw argument string (e.g. "name='Master Playback Switch' on")
    comment: str = ""
    raw_line: str = ""

    def format_line(self, indent: str = "\t\t") -> str:
        if self.cmd == "comment":
            return f"{indent}# {self.arg}"
        q_arg = self.arg.replace('"', '\\"')
        c_part = f"  # {self.comment}" if self.comment else ""
        return f'{indent}{self.cmd} "{q_arg}"{c_part}'


@dataclass
class UcmSectionDevice:
    name: str
    comment: str = ""
    values: Dict[str, str] = field(default_factory=dict)
    enable_seq: List[UcmSequenceCmd] = field(default_factory=list)
    disable_seq: List[UcmSequenceCmd] = field(default_factory=list)
    conflicting_devices: List[str] = field(default_factory=list)
    supported_devices: List[str] = field(default_factory=list)
    source_file: str = ""
    is_conditional: bool = False
    condition_note: str = ""


@dataclass
class UcmSectionModifier:
    name: str
    comment: str = ""
    values: Dict[str, str] = field(default_factory=dict)
    enable_seq: List[UcmSequenceCmd] = field(default_factory=list)
    disable_seq: List[UcmSequenceCmd] = field(default_factory=list)
    supported_devices: List[str] = field(default_factory=list)
    conflicting_devices: List[str] = field(default_factory=list)
    source_file: str = ""


@dataclass
class UcmVerb:
    name: str
    file_path: str = ""
    comment: str = ""
    values: Dict[str, str] = field(default_factory=dict)
    enable_seq: List[UcmSequenceCmd] = field(default_factory=list)
    disable_seq: List[UcmSequenceCmd] = field(default_factory=list)
    devices: Dict[str, UcmSectionDevice] = field(default_factory=dict)
    modifiers: Dict[str, UcmSectionModifier] = field(default_factory=dict)


@dataclass
class UcmDiagnostic:
    severity: str = "warning"   # "error" | "warning" | "info"
    message: str = ""
    file_path: str = ""
    line: int = 0
    verb_name: str = ""
    device_name: str = ""
    kcontrol_name: str = ""
    node_id: str = ""


@dataclass
class UcmProfile:
    """Loaded or newly created ALSA UCM2 profile for a sound card."""
    card_name: str = ""
    card_longname: str = ""
    card_driver: str = ""
    ucm_root_dir: str = ""
    master_rel_path: str = ""
    syntax_version: int = 6
    comment: str = ""
    defines: Dict[str, str] = field(default_factory=dict)
    includes: List[Tuple[str, str]] = field(default_factory=list)  # (include_id, file_ref)
    boot_seq: List[UcmSequenceCmd] = field(default_factory=list)
    fixed_boot_seq: List[UcmSequenceCmd] = field(default_factory=list)
    verbs: Dict[str, UcmVerb] = field(default_factory=dict)
    raw_files: Dict[str, str] = field(default_factory=dict)       # rel_path -> text
    diagnostics: List[UcmDiagnostic] = field(default_factory=list)
    is_modified: bool = False
    is_generated: bool = False
    eval_notes: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 4. Undo / Redo Manager for UCM Profile Edits
# ---------------------------------------------------------------------------

class UndoRedoManager:
    def __init__(self, max_depth: int = 50, on_changed: Optional[Callable[[], None]] = None):
        self._undo: List[Tuple[str, Any]] = []
        self._redo: List[Tuple[str, Any]] = []
        self._max_depth = max_depth
        self._on_changed = on_changed

    def push(self, label: str, snapshot: Any) -> None:
        self._undo.append((label, snapshot))
        if len(self._undo) > self._max_depth:
            self._undo.pop(0)
        self._redo.clear()
        if self._on_changed:
            self._on_changed()

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self, current_snapshot: Any) -> Optional[Tuple[str, Any]]:
        if not self._undo:
            return None
        label, prev = self._undo.pop()
        self._redo.append((label, current_snapshot))
        if self._on_changed:
            self._on_changed()
        return label, prev

    def redo(self, current_snapshot: Any) -> Optional[Tuple[str, Any]]:
        if not self._redo:
            return None
        label, nxt = self._redo.pop()
        self._undo.append((label, current_snapshot))
        if self._on_changed:
            self._on_changed()
        return label, nxt

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()
        if self._on_changed:
            self._on_changed()
