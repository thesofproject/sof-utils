# SPDX-License-Identifier: BSD-3-Clause
"""Pure-Python ALSA binary topology (.tplg) parser for IPC4 and IPC3 topologies."""

from __future__ import annotations
from dataclasses import dataclass, field
import struct
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# ALSA Topology UAPI Constants (sound/asoc.h)
# ---------------------------------------------------------------------------

SND_SOC_TPLG_MAGIC = 0x41536F43  # "CoSA" little-endian

SND_SOC_TPLG_TYPE_MIXER = 1
SND_SOC_TPLG_TYPE_BYTES = 2
SND_SOC_TPLG_TYPE_ENUM = 3
SND_SOC_TPLG_TYPE_DAPM_GRAPH = 4
SND_SOC_TPLG_TYPE_DAPM_WIDGET = 5
SND_SOC_TPLG_TYPE_DAI_LINK = 6
SND_SOC_TPLG_TYPE_PCM = 7
SND_SOC_TPLG_TYPE_MANIFEST = 8
SND_SOC_TPLG_TYPE_CODEC_LINK = 9
SND_SOC_TPLG_TYPE_BACKEND_LINK = 10
SND_SOC_TPLG_TYPE_PDATA = 11
SND_SOC_TPLG_TYPE_DAI = 12

SND_SOC_TPLG_DAPM_INPUT = 0
SND_SOC_TPLG_DAPM_OUTPUT = 1
SND_SOC_TPLG_DAPM_MUX = 2
SND_SOC_TPLG_DAPM_MIXER = 3
SND_SOC_TPLG_DAPM_PGA = 4
SND_SOC_TPLG_DAPM_OUT_DRV = 5
SND_SOC_TPLG_DAPM_ADC = 6
SND_SOC_TPLG_DAPM_DAC = 7
SND_SOC_TPLG_DAPM_SWITCH = 8
SND_SOC_TPLG_DAPM_PRE = 9
SND_SOC_TPLG_DAPM_POST = 10
SND_SOC_TPLG_DAPM_AIF_IN = 11
SND_SOC_TPLG_DAPM_AIF_OUT = 12
SND_SOC_TPLG_DAPM_DAI_IN = 13
SND_SOC_TPLG_DAPM_DAI_OUT = 14
SND_SOC_TPLG_DAPM_DAI_LINK = 15
SND_SOC_TPLG_DAPM_BUFFER = 16
SND_SOC_TPLG_DAPM_SCHEDULER = 17
SND_SOC_TPLG_DAPM_EFFECT = 18
SND_SOC_TPLG_DAPM_SIGGEN = 19
SND_SOC_TPLG_DAPM_SRC = 20
SND_SOC_TPLG_DAPM_ASRC = 21

DAPM_TYPE_NAMES: Dict[int, str] = {
    SND_SOC_TPLG_DAPM_INPUT: "input",
    SND_SOC_TPLG_DAPM_OUTPUT: "output",
    SND_SOC_TPLG_DAPM_MUX: "mux",
    SND_SOC_TPLG_DAPM_MIXER: "mixer",
    SND_SOC_TPLG_DAPM_PGA: "pga",
    SND_SOC_TPLG_DAPM_OUT_DRV: "out_drv",
    SND_SOC_TPLG_DAPM_ADC: "adc",
    SND_SOC_TPLG_DAPM_DAC: "dac",
    SND_SOC_TPLG_DAPM_SWITCH: "switch",
    SND_SOC_TPLG_DAPM_PRE: "pre",
    SND_SOC_TPLG_DAPM_POST: "post",
    SND_SOC_TPLG_DAPM_AIF_IN: "aif_in",
    SND_SOC_TPLG_DAPM_AIF_OUT: "aif_out",
    SND_SOC_TPLG_DAPM_DAI_IN: "dai_in",
    SND_SOC_TPLG_DAPM_DAI_OUT: "dai_out",
    SND_SOC_TPLG_DAPM_DAI_LINK: "dai_link",
    SND_SOC_TPLG_DAPM_BUFFER: "buffer",
    SND_SOC_TPLG_DAPM_SCHEDULER: "scheduler",
    SND_SOC_TPLG_DAPM_EFFECT: "effect",
    SND_SOC_TPLG_DAPM_SIGGEN: "siggen",
    SND_SOC_TPLG_DAPM_SRC: "src",
    SND_SOC_TPLG_DAPM_ASRC: "asrc",
}

SND_SOC_TPLG_CTL_VOLSW = 1
SND_SOC_TPLG_CTL_VOLSW_SX = 2
SND_SOC_TPLG_CTL_VOLSW_XR_SX = 3
SND_SOC_TPLG_CTL_ENUM = 4
SND_SOC_TPLG_CTL_BYTES = 5
SND_SOC_TPLG_CTL_ENUM_VALUE = 6
SND_SOC_TPLG_CTL_RANGE = 7
SND_SOC_TPLG_CTL_STROBE = 8
SND_SOC_TPLG_DAPM_CTL_VOLSW = 64
SND_SOC_TPLG_DAPM_CTL_ENUM_DOUBLE = 65
SND_SOC_TPLG_DAPM_CTL_ENUM_VIRT = 66
SND_SOC_TPLG_DAPM_CTL_ENUM_VALUE = 67
SND_SOC_TPLG_DAPM_CTL_PIN = 68

SND_SOC_TPLG_TUPLE_TYPE_UUID = 0
SND_SOC_TPLG_TUPLE_TYPE_STRING = 1
SND_SOC_TPLG_TUPLE_TYPE_BOOL = 2
SND_SOC_TPLG_TUPLE_TYPE_BYTE = 3
SND_SOC_TPLG_TUPLE_TYPE_WORD = 4
SND_SOC_TPLG_TUPLE_TYPE_SHORT = 5

# SOF Topology Tokens
SOF_TKN_SCHED_PRIORITY = 201
SOF_TKN_SCHED_CORE = 203
SOF_TKN_COMP_CORE_ID = 404
SOF_TKN_COMP_UUID = 405
SOF_TKN_COMP_CPC = 406
SOF_TKN_COMP_IS_PAGES = 409
SOF_TKN_COMP_NUM_INPUT_PINS = 411
SOF_TKN_COMP_NUM_OUTPUT_PINS = 412
SOF_TKN_COMP_INPUT_PIN_BINDING_WNAME = 413
SOF_TKN_COMP_OUTPUT_PIN_BINDING_WNAME = 414
SOF_TKN_COMP_NUM_INPUT_AUDIO_FORMATS = 415
SOF_TKN_COMP_NUM_OUTPUT_AUDIO_FORMATS = 416
SOF_TKN_COMP_SCHED_DOMAIN = 418

SOF_TKN_DAI_TYPE = 154
SOF_TKN_DAI_INDEX = 155

SOF_TKN_CAVS_AUDIO_FORMAT_IN_RATE = 1900
SOF_TKN_CAVS_AUDIO_FORMAT_IN_BIT_DEPTH = 1901
SOF_TKN_CAVS_AUDIO_FORMAT_IN_CH_CFG = 1905
SOF_TKN_CAVS_AUDIO_FORMAT_INPUT_PIN_INDEX = 1909
SOF_TKN_CAVS_AUDIO_FORMAT_OUT_RATE = 1930
SOF_TKN_CAVS_AUDIO_FORMAT_OUT_BIT_DEPTH = 1931
SOF_TKN_CAVS_AUDIO_FORMAT_OUT_CH_CFG = 1935
SOF_TKN_CAVS_AUDIO_FORMAT_OUTPUT_PIN_INDEX = 1939


def _cstr(raw: bytes) -> str:
    idx = raw.find(b"\x00")
    if idx >= 0:
        raw = raw[:idx]
    return raw.decode("utf-8", errors="replace").strip()


def _parse_vendor_arrays(priv_bytes: bytes) -> List[Tuple[int, List[Tuple[int, object]]]]:
    arrays: List[Tuple[int, List[Tuple[int, object]]]] = []
    off = 0
    total = len(priv_bytes)
    while off + 12 <= total:
        arr_size, arr_type, num_elems = struct.unpack_from("<3I", priv_bytes, off)
        if arr_size < 12 or off + arr_size > total:
            break
        elems: List[Tuple[int, object]] = []
        eoff = off + 12
        end = off + arr_size
        if arr_type == SND_SOC_TPLG_TUPLE_TYPE_UUID:
            for _ in range(num_elems):
                if eoff + 20 > end:
                    break
                tkn = struct.unpack_from("<I", priv_bytes, eoff)[0]
                uval = priv_bytes[eoff + 4:eoff + 20]
                elems.append((tkn, uval))
                eoff += 20
        elif arr_type == SND_SOC_TPLG_TUPLE_TYPE_STRING:
            for _ in range(num_elems):
                if eoff + 48 > end:
                    break
                tkn = struct.unpack_from("<I", priv_bytes, eoff)[0]
                sval = _cstr(priv_bytes[eoff + 4:eoff + 48])
                elems.append((tkn, sval))
                eoff += 48
        else:
            for _ in range(num_elems):
                if eoff + 8 > end:
                    break
                tkn, val = struct.unpack_from("<2I", priv_bytes, eoff)
                elems.append((tkn, val))
                eoff += 8
        arrays.append((arr_type, elems))
        off = end
    return arrays


def _parse_token_sets_word(
    arrays: List[Tuple[int, List[Tuple[int, object]]]],
    target_tokens: set[int],
    expected_count: int,
) -> List[Dict[int, int]]:
    sets: List[Dict[int, int]] = []
    current: Dict[int, int] = {}
    for atype, elems in arrays:
        if atype in (SND_SOC_TPLG_TUPLE_TYPE_UUID, SND_SOC_TPLG_TUPLE_TYPE_STRING):
            continue
        for tkn, val in elems:
            if tkn not in target_tokens:
                continue
            if tkn in current:
                sets.append(current)
                current = {}
            current[tkn] = int(val)
    if current:
        sets.append(current)
    return sets[:expected_count] if expected_count > 0 else sets


def _ch_cfg_to_channels(ch_cfg: int) -> int:
    # IPC4 channel config enum: 0=MONO, 1=STEREO, 2=2.1, 3=3.0, 4=3.1, 5=QUATRO, 6=4.0, 7=5.0, 8=5.1, 9=DUAL_MONO, 10=7.1
    mapping = {0: 1, 1: 2, 2: 3, 3: 3, 4: 4, 5: 4, 6: 4, 7: 5, 8: 6, 9: 2, 10: 8}
    return mapping.get(ch_cfg, 2)


@dataclass
class TplgPinFormatDesc:
    pin_index: int = 0
    rate: int = 48000
    bit_depth: int = 32
    channels: int = 2

    def short_str(self) -> str:
        r_khz = self.rate // 1000 if self.rate and self.rate % 1000 == 0 else self.rate
        unit = "k" if (self.rate and self.rate % 1000 == 0) else "Hz"
        return f"s{self.bit_depth} {r_khz}{unit} {self.channels}ch"


@dataclass
class TplgKcontrolDesc:
    name: str
    ctl_type: int
    ops_info: int
    index: int = 0
    widget_name: str = ""
    pipeline_id: int = 0
    max_val: int = 1
    num_channels: int = 2
    scale_min: int = 0
    scale_step: int = 0
    scale_mute: int = 0
    enum_texts: List[str] = field(default_factory=list)


@dataclass
class TplgPipelineDesc:
    pipeline_id: int
    name: str
    core_id: int = 0
    priority: int = 0


@dataclass
class TplgWidgetDesc:
    comp_id: int
    pipeline_id: int
    dapm_type: int
    name: str
    stream_name: str = ""
    core_id: int = 0
    proc_domain: str = "LL"
    uuid_hex: str = ""
    num_input_pins: int = 1
    num_output_pins: int = 1
    input_pin_fmts: List[TplgPinFormatDesc] = field(default_factory=list)
    output_pin_fmts: List[TplgPinFormatDesc] = field(default_factory=list)
    kcontrols: List[TplgKcontrolDesc] = field(default_factory=list)
    dai_type: str = ""
    dai_index: int = -1

    @property
    def type_name(self) -> str:
        return DAPM_TYPE_NAMES.get(self.dapm_type, f"type_{self.dapm_type}")


@dataclass
class TplgRouteDesc:
    source_name: str
    sink_name: str
    control_name: str = ""


@dataclass
class TplgPcmDesc:
    pcm_id: int
    name: str
    playback_stream_name: str = ""
    capture_stream_name: str = ""
    playback_host_name: str = ""
    capture_host_name: str = ""


@dataclass
class TplgLinkDesc:
    link_id: int
    name: str
    stream_name: str = ""
    dai_type: str = ""


@dataclass
class TplgTopology:
    source_path: str = ""
    pipelines: Dict[int, TplgPipelineDesc] = field(default_factory=dict)
    widgets: List[TplgWidgetDesc] = field(default_factory=list)
    widgets_by_name: Dict[str, TplgWidgetDesc] = field(default_factory=dict)
    routes: List[TplgRouteDesc] = field(default_factory=list)
    pcms: List[TplgPcmDesc] = field(default_factory=list)
    links: List[TplgLinkDesc] = field(default_factory=list)
    kcontrols: List[TplgKcontrolDesc] = field(default_factory=list)
    widget_kcontrol_map: Dict[str, List[TplgKcontrolDesc]] = field(default_factory=dict)


def _parse_single_control(
    data: bytes,
    off: int,
    block_end: int,
    widget_name: str,
    pipeline_id: int,
    idx_hint: int = 0,
) -> Tuple[int, Optional[TplgKcontrolDesc]]:
    if off + 204 > block_end:
        return block_end, None
    csize, ctype = struct.unpack_from("<2I", data, off)
    cname = _cstr(data[off + 8:off + 52])
    ops_info = struct.unpack_from("<I", data, off + 64)[0]

    if ops_info in (
        SND_SOC_TPLG_CTL_VOLSW,
        SND_SOC_TPLG_CTL_STROBE,
        SND_SOC_TPLG_CTL_VOLSW_SX,
        SND_SOC_TPLG_CTL_VOLSW_XR_SX,
        SND_SOC_TPLG_CTL_RANGE,
        SND_SOC_TPLG_DAPM_CTL_VOLSW,
        SND_SOC_TPLG_DAPM_CTL_PIN,
    ):
        hdr_sz = csize if csize >= 360 else 360
        if off + hdr_sz > block_end:
            return block_end, None
        max_val = struct.unpack_from("<i", data, off + 212)[0]
        num_ch = struct.unpack_from("<i", data, off + 224)[0]
        cpriv_sz = struct.unpack_from("<I", data, off + 356)[0]
        scale_min, scale_step, scale_mute = struct.unpack_from("<iII", data, off + 76)
        next_off = off + hdr_sz + cpriv_sz
        if next_off > block_end:
            return block_end, None
        kctl = TplgKcontrolDesc(
            name=cname,
            ctl_type=ctype,
            ops_info=ops_info,
            index=idx_hint,
            widget_name=widget_name,
            pipeline_id=pipeline_id,
            max_val=max_val,
            num_channels=max(1, num_ch),
            scale_min=scale_min,
            scale_step=scale_step,
            scale_mute=scale_mute,
        )
        return next_off, kctl

    if ops_info in (
        SND_SOC_TPLG_CTL_ENUM,
        SND_SOC_TPLG_CTL_ENUM_VALUE,
        SND_SOC_TPLG_DAPM_CTL_ENUM_DOUBLE,
        SND_SOC_TPLG_DAPM_CTL_ENUM_VIRT,
        SND_SOC_TPLG_DAPM_CTL_ENUM_VALUE,
    ):
        hdr_sz = csize if csize >= 1764 else 1764
        if off + hdr_sz > block_end:
            return block_end, None
        num_items = struct.unpack_from("<i", data, off + 208)[0]
        texts: List[str] = []
        for ti in range(max(0, min(num_items, 16))):
            t_str = _cstr(data[off + 352 + ti * 64:off + 352 + (ti + 1) * 64])
            if t_str:
                texts.append(t_str)
        cpriv_sz = struct.unpack_from("<I", data, off + 1760)[0]
        next_off = off + hdr_sz + cpriv_sz
        if next_off > block_end:
            return block_end, None
        kctl = TplgKcontrolDesc(
            name=cname,
            ctl_type=ctype,
            ops_info=ops_info,
            index=idx_hint,
            widget_name=widget_name,
            pipeline_id=pipeline_id,
            enum_texts=texts,
        )
        return next_off, kctl

    if ops_info == SND_SOC_TPLG_CTL_BYTES:
        hdr_sz = csize if csize >= 240 else 240
        if off + hdr_sz > block_end:
            return block_end, None
        cpriv_sz = struct.unpack_from("<I", data, off + 236)[0]
        next_off = off + hdr_sz + cpriv_sz
        if next_off > block_end:
            return block_end, None
        kctl = TplgKcontrolDesc(
            name=cname,
            ctl_type=ctype,
            ops_info=ops_info,
            index=idx_hint,
            widget_name=widget_name,
            pipeline_id=pipeline_id,
        )
        return next_off, kctl

    return block_end, None


def parse_tplg_file(tplg_path: str) -> TplgTopology:
    with open(tplg_path, "rb") as f:
        data = f.read()
    tplg = parse_tplg_bytes(data)
    tplg.source_path = tplg_path
    return tplg


def parse_tplg_bytes(data: bytes, source_path: str = "") -> TplgTopology:
    """Parse an ALSA binary topology (.tplg) buffer into TplgTopology."""
    tplg = TplgTopology(source_path=source_path)
    comp_id = 1
    off = 0
    size = len(data)

    while off + 36 <= size:
        (
            magic,
            _abi,
            _version,
            hdr_type,
            hdr_size,
            _vendor_type,
            payload_size,
            hdr_index,
            hdr_count,
        ) = struct.unpack_from("<9I", data, off)
        if magic != SND_SOC_TPLG_MAGIC or hdr_size < 36 or off + hdr_size + payload_size > size:
            break
        block_end = off + hdr_size + payload_size
        off += hdr_size

        if hdr_type == SND_SOC_TPLG_TYPE_DAPM_WIDGET:
            pipeline_id = hdr_index
            for _ in range(hdr_count):
                if off + 132 > block_end:
                    break
                w_size, w_id = struct.unpack_from("<2I", data, off)
                w_hdr_sz = w_size if w_size >= 132 else 132
                w_name = _cstr(data[off + 8:off + 52])
                w_sname = _cstr(data[off + 52:off + 96])
                num_kctls, priv_size = struct.unpack_from("<2I", data, off + 124)
                priv_start = off + w_hdr_sz
                priv_end = priv_start + priv_size
                if priv_end > block_end:
                    break
                priv_bytes = data[priv_start:priv_end]
                off = priv_end

                arrays = _parse_vendor_arrays(priv_bytes)
                word_map: Dict[int, int] = {}
                str_map: Dict[int, str] = {}
                uuid_hex = ""
                for atype, elems in arrays:
                    if atype == SND_SOC_TPLG_TUPLE_TYPE_UUID:
                        for tkn, uval in elems:
                            if tkn == SOF_TKN_COMP_UUID and isinstance(uval, (bytes, bytearray)):
                                uuid_hex = bytes(uval).hex()
                    elif atype == SND_SOC_TPLG_TUPLE_TYPE_STRING:
                        for tkn, sval in elems:
                            str_map[tkn] = str(sval)
                    else:
                        for tkn, vval in elems:
                            word_map[tkn] = int(vval)

                if w_id == SND_SOC_TPLG_DAPM_SCHEDULER:
                    tplg.pipelines[pipeline_id] = TplgPipelineDesc(
                        pipeline_id=pipeline_id,
                        name=w_name,
                        core_id=word_map.get(SOF_TKN_SCHED_CORE, 0),
                        priority=word_map.get(SOF_TKN_SCHED_PRIORITY, 0),
                    )
                    for ki in range(num_kctls):
                        off, kctl = _parse_single_control(data, off, block_end, w_name, pipeline_id, ki)
                        if kctl and kctl.name:
                            tplg.kcontrols.append(kctl)
                            tplg.widget_kcontrol_map.setdefault(w_name, []).append(kctl)
                    comp_id += 1
                    continue

                proc_dom = "DP" if str_map.get(SOF_TKN_COMP_SCHED_DOMAIN, "LL").upper() == "DP" else "LL"
                wdesc = TplgWidgetDesc(
                    comp_id=comp_id,
                    pipeline_id=pipeline_id,
                    dapm_type=w_id,
                    name=w_name,
                    stream_name=w_sname,
                    core_id=word_map.get(SOF_TKN_COMP_CORE_ID, 0),
                    proc_domain=proc_dom,
                    uuid_hex=uuid_hex,
                    num_input_pins=word_map.get(SOF_TKN_COMP_NUM_INPUT_PINS, 1) or 1,
                    num_output_pins=word_map.get(SOF_TKN_COMP_NUM_OUTPUT_PINS, 1) or 1,
                    dai_type=str_map.get(SOF_TKN_DAI_TYPE, ""),
                    dai_index=word_map.get(SOF_TKN_DAI_INDEX, -1),
                )

                num_in_fmts = word_map.get(SOF_TKN_COMP_NUM_INPUT_AUDIO_FORMATS, 0)
                num_out_fmts = word_map.get(SOF_TKN_COMP_NUM_OUTPUT_AUDIO_FORMATS, 0)
                for fset in _parse_token_sets_word(
                    arrays,
                    {
                        SOF_TKN_CAVS_AUDIO_FORMAT_IN_RATE,
                        SOF_TKN_CAVS_AUDIO_FORMAT_IN_BIT_DEPTH,
                        SOF_TKN_CAVS_AUDIO_FORMAT_IN_CH_CFG,
                        SOF_TKN_CAVS_AUDIO_FORMAT_INPUT_PIN_INDEX,
                    },
                    num_in_fmts,
                ):
                    wdesc.input_pin_fmts.append(
                        TplgPinFormatDesc(
                            pin_index=fset.get(SOF_TKN_CAVS_AUDIO_FORMAT_INPUT_PIN_INDEX, 0),
                            rate=fset.get(SOF_TKN_CAVS_AUDIO_FORMAT_IN_RATE, 48000),
                            bit_depth=fset.get(SOF_TKN_CAVS_AUDIO_FORMAT_IN_BIT_DEPTH, 32),
                            channels=_ch_cfg_to_channels(fset.get(SOF_TKN_CAVS_AUDIO_FORMAT_IN_CH_CFG, 1)),
                        )
                    )
                for fset in _parse_token_sets_word(
                    arrays,
                    {
                        SOF_TKN_CAVS_AUDIO_FORMAT_OUT_RATE,
                        SOF_TKN_CAVS_AUDIO_FORMAT_OUT_BIT_DEPTH,
                        SOF_TKN_CAVS_AUDIO_FORMAT_OUT_CH_CFG,
                        SOF_TKN_CAVS_AUDIO_FORMAT_OUTPUT_PIN_INDEX,
                    },
                    num_out_fmts,
                ):
                    wdesc.output_pin_fmts.append(
                        TplgPinFormatDesc(
                            pin_index=fset.get(SOF_TKN_CAVS_AUDIO_FORMAT_OUTPUT_PIN_INDEX, 0),
                            rate=fset.get(SOF_TKN_CAVS_AUDIO_FORMAT_OUT_RATE, 48000),
                            bit_depth=fset.get(SOF_TKN_CAVS_AUDIO_FORMAT_OUT_BIT_DEPTH, 32),
                            channels=_ch_cfg_to_channels(fset.get(SOF_TKN_CAVS_AUDIO_FORMAT_OUT_CH_CFG, 1)),
                        )
                    )

                for ki in range(num_kctls):
                    off, kctl = _parse_single_control(data, off, block_end, w_name, pipeline_id, ki)
                    if kctl and kctl.name:
                        wdesc.kcontrols.append(kctl)
                        tplg.kcontrols.append(kctl)
                        tplg.widget_kcontrol_map.setdefault(w_name, []).append(kctl)

                tplg.widgets.append(wdesc)
                tplg.widgets_by_name[wdesc.name] = wdesc
                comp_id += 1

        elif hdr_type == SND_SOC_TPLG_TYPE_DAPM_GRAPH:
            for _ in range(hdr_count):
                if off + 132 > block_end:
                    break
                sink_name = _cstr(data[off:off + 44])
                ctl_name = _cstr(data[off + 44:off + 88])
                source_name = _cstr(data[off + 88:off + 132])
                off += 132
                if source_name and sink_name:
                    tplg.routes.append(
                        TplgRouteDesc(
                            source_name=source_name,
                            sink_name=sink_name,
                            control_name=ctl_name,
                        )
                    )

        elif hdr_type == SND_SOC_TPLG_TYPE_PCM:
            for _ in range(hdr_count):
                if off + 912 > block_end:
                    break
                pcm_name = _cstr(data[off + 4:off + 48])
                pcm_id = struct.unpack_from("<I", data, off + 92)[0]
                pb_sname = _cstr(data[off + 692 + 4:off + 692 + 48])
                cap_sname = _cstr(data[off + 692 + 104 + 4:off + 692 + 104 + 48])
                pcm_priv_size = struct.unpack_from("<I", data, off + 908)[0]
                next_off = off + 912 + pcm_priv_size
                if next_off > block_end:
                    break
                off = next_off
                pdesc = TplgPcmDesc(
                    pcm_id=pcm_id,
                    name=pcm_name,
                    playback_stream_name=pb_sname,
                    capture_stream_name=cap_sname,
                )
                tplg.pcms.append(pdesc)

        elif hdr_type in (
            SND_SOC_TPLG_TYPE_DAI_LINK,
            SND_SOC_TPLG_TYPE_BACKEND_LINK,
            SND_SOC_TPLG_TYPE_CODEC_LINK,
        ):
            for _ in range(hdr_count):
                if off + 200 > block_end:
                    break
                l_size = struct.unpack_from("<I", data, off)[0]
                if l_size < 16 or off + l_size > block_end:
                    break
                l_name = _cstr(data[off + 8:off + 52]) if l_size >= 52 else ""
                l_sname = _cstr(data[off + 52:off + 96]) if l_size >= 96 else ""
                l_id = struct.unpack_from("<I", data, off + 4)[0]
                priv_sz = struct.unpack_from("<I", data, off + l_size - 4)[0]
                next_off = off + l_size + priv_sz
                if next_off > block_end:
                    break
                tplg.links.append(TplgLinkDesc(link_id=l_id, name=l_name, stream_name=l_sname))
                off = next_off

        elif hdr_type in (SND_SOC_TPLG_TYPE_MIXER, SND_SOC_TPLG_TYPE_ENUM, SND_SOC_TPLG_TYPE_BYTES):
            for ki in range(hdr_count):
                off, kctl = _parse_single_control(data, off, block_end, "", hdr_index, ki)
                if kctl and kctl.name:
                    tplg.kcontrols.append(kctl)

        off = block_end

    # Link PCM stream names to AIF_IN / AIF_OUT widgets
    for pdesc in tplg.pcms:
        for w in tplg.widgets:
            if w.dapm_type in (SND_SOC_TPLG_DAPM_AIF_IN, SND_SOC_TPLG_DAPM_AIF_OUT):
                if pdesc.playback_stream_name and w.stream_name == pdesc.playback_stream_name:
                    pdesc.playback_host_name = w.name
                if pdesc.capture_stream_name and w.stream_name == pdesc.capture_stream_name:
                    pdesc.capture_host_name = w.name

    # Inherit pipeline core_id for LL widgets
    for w in tplg.widgets:
        p = tplg.pipelines.get(w.pipeline_id)
        if p is not None and w.proc_domain == "LL":
            w.core_id = p.core_id

    return tplg
