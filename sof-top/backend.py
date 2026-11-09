#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright(c) 2026 Intel Corporation. All rights reserved.
"""
backend.py — Local (/dev/sof-dsp0 / debugfs) and Remote TCP Network
(sof_remote_server) backends, SOFZ wire decompressor, and TEL2/PMW2 binary
telemetry parsers for sof-top.
"""

import collections
import ctypes
import fcntl
import math
import mmap
import os
import socket
import struct
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# -----------------------------------------------------------------------------
# Protocol & IOCTL Constants matching sof_remote_proto.h
# -----------------------------------------------------------------------------
SOF_NET_MAGIC = 0x534F4652
SOF_NET_VERSION = 1
SOF_NET_DEFAULT_PORT = 9998

SOF_NET_OP_FLAG_COMPRESSED = 0x8000
SOF_NET_OP_MASK = 0x7FFF
SOF_NET_COMP_MAGIC = 0x5A464F53  # 'SOFZ'

SOF_NET_OP_PING = 0x0000
SOF_NET_OP_GET_INFO = 0x0001
SOF_NET_OP_PM_CTL = 0x0002
SOF_NET_OP_IPC_TX = 0x0010
SOF_NET_OP_IPC_LARGE_CFG = 0x0011
SOF_NET_OP_WIN_INFO = 0x0020
SOF_NET_OP_WIN_READ = 0x0021
SOF_NET_OP_WIN_WRITE = 0x0022
SOF_NET_OP_WINSTREAM_IO = 0x0023
SOF_NET_OP_TELEM_SNAPSHOT = 0x0028
SOF_NET_OP_LLEXT_LOAD = 0x0030
SOF_NET_OP_SHELL_EXEC = 0x0040
SOF_NET_OP_PROBE_CTL = 0x0050
SOF_NET_OP_PCM_CTL = 0x0060
SOF_NET_OP_CARD_LIST = 0x0070
SOF_NET_OP_PCM_CAPS = 0x0071
SOF_NET_OP_DAPM_GRAPH = 0x0072
SOF_NET_OP_COMPR_CTL = 0x0073
SOF_NET_OP_KCTL_LIST = 0x0074
SOF_NET_OP_KCTL_GET = 0x0075
SOF_NET_OP_KCTL_SET = 0x0076

SOF_AUDIO_API_ALSA = 1
SOF_AUDIO_API_TINYALSA = 2
SOF_AUDIO_API_TINYCOMPRESS = 3

SOF_TELEM_FL_FW_REGS = 1 << 0
SOF_TELEM_FL_TELEM_SLOT = 1 << 1
SOF_TELEM_FL_PERF_WIN3 = 1 << 2
SOF_TELEM_FL_MEM_STATE_TLV = 1 << 3
SOF_TELEM_FL_PWR_STATE_TLV = 1 << 4
SOF_TELEM_FL_EXT_PERF_TLV = 1 << 5
SOF_TELEM_FL_MODULES_INFO_TLV = 1 << 6
SOF_TELEM_FL_SYS_HOST_STATS = 1 << 7
SOF_TELEM_FL_TEL2_SLOT = 1 << 8
SOF_TELEM_FL_PMW2_WIN3 = 1 << 9
SOF_TELEM_FL_HOT_ONLY = 1 << 10
SOF_TELEM_FL_COMPRESS = 1 << 14
SOF_TELEM_FL_WAKE_DSP = 1 << 15

SOF_PM_OP_QUERY = 0
SOF_PM_OP_WAKE_HOLD = 1
SOF_PM_OP_RELEASE_HOLD = 2
SOF_PM_OP_BOOT_DSP = 3
SOF_PM_OP_CORE_GET = 4
SOF_PM_OP_CORE_PUT = 5

SOF_PCM_OP_STATUS = 0
SOF_PCM_OP_START_TONE = 1
SOF_PCM_OP_STOP_TONE = 2
SOF_PCM_OP_PLAY_BUFFER = 3
SOF_PCM_OP_CAPTURE_BUFFER = 4

SOF_PROBE_OP_INIT = 1
SOF_PROBE_OP_DEINIT = 2
SOF_PROBE_OP_INFO_ACTIVE = 3
SOF_PROBE_OP_INFO_AVAILABLE = 4
SOF_PROBE_OP_POINTS_ADD = 5
SOF_PROBE_OP_POINTS_REMOVE = 6

# IPC4 BaseFW Param IDs
IPC4_MODULES_INFO_GET = 9
IPC4_MEMORY_STATE_INFO_GET = 14
IPC4_POWER_STATE_INFO_GET = 15
IPC4_PERF_MEASUREMENTS_STATE = 17
IPC4_GLOBAL_PERF_DATA = 18
IPC4_EXTENDED_GLOBAL_PERF_DATA = 30

# Debug Slot Types
SOF_CDEV_SLOT_UNUSED = 0x00000000
SOF_CDEV_SLOT_CRITICAL_LOG = 0x54524300
SOF_CDEV_SLOT_DEBUG_LOG = 0x474F4C00
SOF_CDEV_SLOT_GDB_STUB = 0x42444700
SOF_CDEV_SLOT_TELEMETRY = 0x4C455400
SOF_CDEV_SLOT_BROKEN = 0x44414544
SOF_CDEV_SLOT_SHELL = 0x73686C6C
SOF_CDEV_SLOT_LLEXT_LOAD = 0x4C454C44

# Extended Telemetry v2 ("TEL2" & "PMW2") Constants
SOF_TEL_EXT_MAGIC = 0x324C4554  # "TEL2"
SOF_TEL_EXT_HDR_OFFSET = 0x0100
SOF_TEL_EXT_DEBUGFS_HDR_OFFSET = 0x00FC
SOF_TEL_OFF_HOT_LINE0 = 0x0140
SOF_TEL_OFF_GW_HOT = 0x0180
SOF_TEL_OFF_MEM_SEC = 0x0240
SOF_TEL_OFF_VREG_SEC = 0x0340
SOF_TEL_OFF_PPL_SEC = 0x0540
SOF_TEL_OFF_DAI_SEC = 0x06C0
SOF_TEL_OFF_THD_SEC = 0x07C0

SOF_TEL_MW3_MAGIC = 0x32574D50  # "PMW2"
SOF_TEL_MW3_MAX_COMP_ITEMS = 88
SOF_TEL_MW3_MAX_IO_ITEMS = 128
SOF_TEL_MW3_COMP_PERF_STRIDE = 48
SOF_TEL_MW3_MOD_HOT_OFFSET = 0x1080
SOF_TEL_MW3_MOD_COLD_OFFSET = 0x1200
SOF_TEL_MW3_IO_PERF_OFFSET = 0x1800
SOF_TEL_MW3_HDR_OFFSET = 0x1FC0

SOF_NET_TELEM_SNAPSHOT_V1_SIZE = 16356

HEAP_TYPE_NAMES = {
    0: "NONE",
    1: "SYS_HEAP (SRAM)",
    2: "SHARED_HEAP (SRAM)",
    3: "LL_MOD_HEAP (SRAM)",
    4: "L3_IMR (DRAM)",
    5: "DRV_BUF (SRAM)",
}

GW_TYPE_NAMES = {
    0: "NONE",
    1: "SSP/I2S",
    2: "DMIC",
    3: "ALH/SDW",
    4: "HDA",
    5: "HOST_DMA",
    6: "OTHER",
}

PPL_STATUS_NAMES = {
    0: "NOT_EXIST",
    1: "INIT",
    2: "READY",
    3: "SUSPEND",
    4: "PREPARE",
    5: "PAUSED",
    6: "ACTIVE",
    7: "PRE_ACTIVE",
}

FW_STATE_NAMES = {
    0: "NOT_STARTED",
    1: "BOOT_NEEDED",
    2: "BOOT_PREPARE",
    3: "BOOT_IN_PROGRESS",
    4: "BOOT_FAILED",
    5: "BOOT_READY",
    6: "BOOT_READY_FAILED",
    7: "BOOT_COMPLETE",
    8: "CRASHED",
}


def slot_type_name(stype: int) -> str:
    if stype == 0:
        return "UNUSED"
    if (stype & 0xFFFFFF00) == SOF_CDEV_SLOT_CRITICAL_LOG:
        return f"CRIT_LOG(C{stype & 0xFF})"
    if (stype & 0xFFFFFF00) == SOF_CDEV_SLOT_DEBUG_LOG:
        return f"DBG_LOG(C{stype & 0xFF})"
    if stype == SOF_CDEV_SLOT_TELEMETRY:
        return "TELEMETRY"
    if stype == SOF_CDEV_SLOT_GDB_STUB:
        return "GDB_STUB"
    if stype == SOF_CDEV_SLOT_SHELL:
        return "SHELL('shll')"
    if stype == SOF_CDEV_SLOT_LLEXT_LOAD:
        return "LLEXT('LELD')"
    if stype == SOF_CDEV_SLOT_BROKEN:
        return "DEAD/BROKEN"
    return f"0x{stype:08x}"


def sof_net_decompress(src: bytes) -> bytes:
    """Decompress a SOFZ ('SOFZ' magic) compressed network payload."""
    if len(src) < 8:
        return src
    magic, raw_size = struct.unpack_from("<II", src, 0)
    if magic != SOF_NET_COMP_MAGIC or raw_size > 16 * 1024 * 1024:
        return src

    dst = bytearray(raw_size)
    in_pos = 8
    out_pos = 0
    src_len = len(src)

    while in_pos < src_len and out_pos < raw_size:
        tok = src[in_pos]
        in_pos += 1
        tag = tok >> 6

        if tag == 0:
            zlen = tok
            if zlen == 0:
                if in_pos + 2 > src_len:
                    return b""
                zlen = src[in_pos] | (src[in_pos + 1] << 8)
                in_pos += 2
            if zlen == 0 or out_pos + zlen > raw_size:
                return b""
            out_pos += zlen
        elif tag == 1:
            llen = (tok & 0x3F) + 1
            if in_pos + llen > src_len or out_pos + llen > raw_size:
                return b""
            dst[out_pos : out_pos + llen] = src[in_pos : in_pos + llen]
            in_pos += llen
            out_pos += llen
        elif tag == 2:
            rlen = (tok & 0x3F) + 3
            if in_pos + 1 > src_len or out_pos + rlen > raw_size:
                return b""
            bval = src[in_pos]
            in_pos += 1
            dst[out_pos : out_pos + rlen] = bytes([bval]) * rlen
            out_pos += rlen
        else:
            mlen = (tok & 0x3F) + 4
            if in_pos + 2 > src_len:
                return b""
            dist = src[in_pos] | (src[in_pos + 1] << 8)
            in_pos += 2
            if dist == 0 or dist > out_pos or out_pos + mlen > raw_size:
                return b""
            mpos = out_pos - dist
            for k in range(mlen):
                dst[out_pos + k] = dst[mpos + k]
            out_pos += mlen

    return bytes(dst) if out_pos == raw_size else b""


# -----------------------------------------------------------------------------
# Structured Telemetry Models
# -----------------------------------------------------------------------------
@dataclass
class CoreTelemetry:
    core_id: int
    enabled: bool = False
    tick_count: int = 0
    ticks_per_sec: float = 0.0
    last_time_elapsed_cyc: int = 0
    max_time_elapsed_cyc: int = 0
    last_time_elapsed_us: float = 0.0
    max_time_elapsed_us: float = 0.0
    last_ccount: int = 0
    avg_kcps: int = 0
    peak_kcps: int = 0
    peak_4k_kcps: int = 0
    peak_8k_kcps: int = 0
    budget_kcps: int = 0
    cpu_load_pct: float = 0.0
    peak_load_pct: float = 0.0
    ll_overruns: int = 0
    dp_deadline_misses: int = 0
    active_ll_tasks: int = 0
    active_dp_tasks: int = 0
    active_ppl_count: int = 0


@dataclass
class ComponentPerfItem:
    resource_id: int
    module_id: int
    instance_id: int
    module_name: str
    core_id: int = 0
    power_mode: str = "LL"
    is_removed: bool = False
    avg_kcps: int = 0
    peak_kcps: int = 0
    iterations: int = 0
    total_cycles: int = 0
    restricted_peak_cycles: int = 0
    restricted_iterations: int = 0
    restricted_cycles: int = 0
    cpc_violations: int = 0
    xrun_count: int = 0
    heap_usage_bytes: int = 0
    heap_hwm_bytes: int = 0
    configured_cpc: int = 0


@dataclass
class IoPerfItem:
    resource_id: int
    power_mode: int
    perf_usage: int

    @property
    def utilization_kcps(self) -> int:
        return self.perf_usage


@dataclass
class SramSegmentState:
    name: str
    total_banks: int = 0
    powered_banks: int = 0
    gated_banks: int = 0
    free_phys_pages: int = 0
    total_phys_pages: int = 0
    bank_powered: List[bool] = field(default_factory=list)
    bank_alloc_mask: List[int] = field(default_factory=list)


@dataclass
class HeapTelemetry:
    heap_type: int
    name: str
    is_dram: bool = False
    total_bytes: int = 0
    allocated_bytes: int = 0
    free_bytes: int = 0
    max_allocated_bytes: int = 0
    alloc_fail_count: int = 0


@dataclass
class VmhBundleInfo:
    blk_sz_shift: int
    block_size_bytes: int
    total_blocks: int
    used_blocks: int
    max_blocks_or_oom: int


@dataclass
class FastGetInfo:
    active_entries: int = 0
    fail_count: int = 0
    total_sram_bytes: int = 0
    peak_sram_bytes: int = 0
    total_get_calls: int = 0


@dataclass
class VregionInfo:
    base_va: int
    total_pages: int
    use_count: int
    type_and_flags: int
    lifetime_size_bytes: int
    lifetime_used_bytes: int
    interim_alloc_bytes: int
    interim_free_kb: int
    lifetime_free_count: int


@dataclass
class VregionSectionInfo:
    total_pages: int = 0
    free_pages: int = 0
    min_free_pages: int = 0
    num_elems_in_use: int = 0
    alloc_fail_count: int = 0
    region_base_va: int = 0
    active_vregion_count: int = 0
    total_vregions_created: int = 0
    velems: List[Tuple[int, int]] = field(default_factory=list)
    vregions: List[VregionInfo] = field(default_factory=list)


@dataclass
class PipelineInfo:
    pipeline_id: int
    core: int
    priority: int
    status: int
    status_name: str
    comp_count: int
    attributes: int
    period_us: int
    sched_comp_id: int
    last_xrun_bytes: int
    xrun_count: int = 0


@dataclass
class GatewayInfo:
    slot_idx: int
    gw_type: int
    type_name: str
    index: int
    direction: int  # 0=playback/tx, 1=capture/rx
    dma_chan: int
    comp_id: int
    state: int
    group_id: int
    period_bytes: int
    dma_buf_size: int
    total_kb_or_frames: int = 0
    dma_buf_avail_bytes: int = 0
    xrun_or_skip_count: int = 0


@dataclass
class ThreadInfo:
    thread_id: int
    name: str
    core: int
    prio: int
    state: int
    cpu_load_pct: float
    stack_usage_pct: float
    flags: int
    stack_size_bytes: int
    total_exec_kcycles: int


@dataclass
class DspTaskItem:
    """Unified DSP Task entry for the User Edition Task List and Developer views."""
    task_id: str
    name: str
    task_type: str  # "LL Module", "DP Module", "RTOS Thread", "Pipeline", "Core Sched", "DAI Gateway"
    core_id: int
    status: str     # "RUNNING", "ACTIVE", "READY", "IDLE", "PAUSED", "XRUN!", "CPC_VIOL", "GATED"
    cpu_pct: float
    avg_kcps: int
    peak_kcps: int
    mem_used_kb: float
    mem_limit_kb: float
    mem_desc: str
    iterations: int
    xruns: int


@dataclass
class DebugSlotInfo:
    slot_index: int
    res_id: int
    slot_type: int
    type_name: str
    vma: int
    win_offset: int
    bar_offset: int
    size: int


@dataclass
class WindowInfo:
    win_id: int
    bar_offset: int
    size: int
    mapped: bool


@dataclass
class TelemetrySnapshot:
    timestamp: float = 0.0
    connected: bool = False
    platform: str = "unknown"
    fw_filename: str = ""
    fw_version: str = "0.0.0.0"
    ipc_type: int = 1
    fw_state: int = 0
    fw_state_name: str = "NOT_STARTED"
    dsp_power_state: int = 0
    pm_runtime_active: bool = False
    num_cores: int = 2
    enabled_cores_mask: int = 1
    dsp_clock_hz: int = 400_000_000
    active_pcm_streams: int = 0
    wire_bytes: int = 0
    raw_bytes: int = 0
    # Window 0 FW Regs
    fw_status: int = 0
    lec: int = 0
    fps: int = 0
    lnec: int = 0
    ltr: int = 0
    # Windows & Debug Slots
    windows: List[WindowInfo] = field(default_factory=list)
    debug_slots: List[DebugSlotInfo] = field(default_factory=list)
    # Cores, Components, Pipelines, Gateways, Threads, Unified Tasks
    cores: List[CoreTelemetry] = field(default_factory=list)
    components: List[ComponentPerfItem] = field(default_factory=list)
    io_perf_items: List[IoPerfItem] = field(default_factory=list)
    pipelines: List[PipelineInfo] = field(default_factory=list)
    gateways: List[GatewayInfo] = field(default_factory=list)
    threads: List[ThreadInfo] = field(default_factory=list)
    tasks: List[DspTaskItem] = field(default_factory=list)
    total_xrun_events: int = 0
    tel2_present: bool = False
    pmw2_present: bool = False
    tel2_seq: int = 0
    tel2_timestamp_us: int = 0
    # Memory State (SRAM + DRAM + Heaps + VMH + VRegions)
    hpsram: SramSegmentState = field(default_factory=lambda: SramSegmentState("HPSRAM"))
    lpsram: SramSegmentState = field(default_factory=lambda: SramSegmentState("LPSRAM"))
    heaps: List[HeapTelemetry] = field(default_factory=list)
    sram_used_kb: float = 0.0
    sram_powered_kb: float = 0.0
    sram_total_kb: float = 0.0
    dram_used_kb: float = 0.0
    dram_hwm_kb: float = 0.0
    dram_total_kb: float = 0.0
    heap_allocated_bytes: int = 0
    heap_free_bytes: int = 0
    heap_max_bytes: int = 0
    heap_total_bytes: int = 0
    vmh_region_size: int = 0
    vmh_bundle_count: int = 0
    vmh_oom_total: int = 0
    vmh_bundles: List[VmhBundleInfo] = field(default_factory=list)
    fast_get: FastGetInfo = field(default_factory=FastGetInfo)
    vregion_sec: VregionSectionInfo = field(default_factory=VregionSectionInfo)
    loaded_modules: Dict[int, str] = field(default_factory=dict)


# -----------------------------------------------------------------------------
# Binary Parsers for Firmware Windows & IPC4 BaseFW TLVs
# -----------------------------------------------------------------------------
WELL_KNOWN_IPC4_MODULES = {
    0x0000: "basefw",
    0x0001: "mixin",
    0x0002: "mixout",
    0x0003: "copier",
    0x0004: "peakvol",
    0x0005: "src",
    0x0006: "asrc",
    0x0007: "probe",
    0x0008: "updown_mix",
    0x0009: "smart_amp",
    0x000A: "dts",
    0x000B: "dolby",
    0x000C: "eq_iir",
    0x000D: "eq_fir",
    0x000E: "drc",
    0x000F: "tdfb",
    0x0010: "crossover",
    0x0011: "multiband_drc",
    0x0012: "aria",
    0x0013: "igo_nr",
    0x0094: "copier",
    0x0095: "mixin",
    0x0096: "mixout",
    0x0097: "gain",
    0x0098: "src",
    0x0099: "wov_detect",
}


def sof_tel2_slot_bias(buf: bytes) -> int:
    if len(buf) >= SOF_TEL_EXT_HDR_OFFSET + 64:
        (magic,) = struct.unpack_from("<I", buf, SOF_TEL_EXT_HDR_OFFSET)
        if magic == SOF_TEL_EXT_MAGIC:
            return 0
    if len(buf) >= SOF_TEL_EXT_DEBUGFS_HDR_OFFSET + 64:
        (magic,) = struct.unpack_from("<I", buf, SOF_TEL_EXT_DEBUGFS_HDR_OFFSET)
        if magic == SOF_TEL_EXT_MAGIC:
            return 4
    return -1


class TelemetryParser:
    def __init__(self):
        self.prev_ts: float = 0.0
        self.prev_ticks: Dict[int, int] = {}
        self.prev_ccount: Dict[int, int] = {}
        self.module_name_lookup: Dict[int, str] = dict(WELL_KNOWN_IPC4_MODULES)
        self.cached_modules: Dict[int, str] = {}
        self.cached_hpsram: Optional[SramSegmentState] = None
        self.cached_lpsram: Optional[SramSegmentState] = None
        self.cached_core_kcps: Dict[int, int] = {}
        self.cached_ext_perf: List[ComponentPerfItem] = []

    def lookup_module_name(self, mod_id: int) -> str:
        return (
            self.cached_modules.get(mod_id)
            or self.module_name_lookup.get(mod_id, f"mod_0x{mod_id:02x}")
        )

    def parse_cdev_info_into(self, raw_info: bytes, snap: TelemetrySnapshot):
        if len(raw_info) < 140:
            return
        (
            abi_ver,
            ipc_type,
            fw_state,
            dsp_power_state,
            pm_active,
            num_cores,
            enabled_cores_mask,
            max_payload,
            ver_maj,
            ver_min,
            ver_mic,
            ver_bld,
            hw_ip,
            plat_bytes,
            fw_bytes,
        ) = struct.unpack_from("<IIIIIIIIHHHHI32s64s", raw_info, 0)

        snap.ipc_type = ipc_type
        snap.fw_state = fw_state
        snap.fw_state_name = FW_STATE_NAMES.get(fw_state, f"STATE_{fw_state}")
        snap.dsp_power_state = dsp_power_state
        snap.pm_runtime_active = bool(pm_active)
        snap.num_cores = max(1, min(8, num_cores))
        snap.enabled_cores_mask = enabled_cores_mask
        snap.fw_version = f"{ver_maj}.{ver_min}.{ver_mic}.{ver_bld}"
        snap.platform = plat_bytes.split(b"\x00", 1)[0].decode("ascii", errors="replace")
        snap.fw_filename = fw_bytes.split(b"\x00", 1)[0].decode("ascii", errors="replace")

        off = 140
        snap.windows = []
        for _ in range(4):
            if off + 16 <= len(raw_info):
                wid, boff, wsz, wfl = struct.unpack_from("<IIII", raw_info, off)
                snap.windows.append(WindowInfo(wid, boff, wsz, bool(wfl)))
                off += 16

        snap.debug_slots = []
        for _ in range(16):
            if off + 32 <= len(raw_info):
                sidx, resid, stype, vma, woff, boff, ssz, _ = struct.unpack_from(
                    "<IIIIIIII", raw_info, off
                )
                snap.debug_slots.append(
                    DebugSlotInfo(
                        sidx, resid, stype, slot_type_name(stype), vma, woff, boff, ssz
                    )
                )
                off += 32

    def parse_modules_info_tlv(self, data: bytes):
        if len(data) < 4:
            return
        (mod_count,) = struct.unpack_from("<I", data, 0)
        mod_count = min(mod_count, 128)
        off = 4
        for idx in range(mod_count):
            if off + 116 > len(data):
                break
            mod_id_rt, _ = struct.unpack_from("<HH", data, off)
            if data[off : off + 4] == b"$AME":
                mod_id_rt = idx
            name_raw = data[off + 4 : off + 12]
            mname = name_raw.split(b"\x00", 1)[0].decode("ascii", errors="replace").strip()
            if mname:
                self.cached_modules[mod_id_rt] = mname
                self.cached_modules[idx] = mname
                self.module_name_lookup[mod_id_rt] = mname
                self.module_name_lookup[idx] = mname
            off += 116

    def parse_memory_state_tlv(self, data: bytes):
        off = 0
        while off + 8 <= len(data):
            ttype, tlen = struct.unpack_from("<II", data, off)
            off += 8
            if tlen == 0 or off + tlen > len(data):
                break
            val = data[off : off + tlen]
            off += tlen

            if len(val) < 12:
                continue
            free_pages, ebb_dwords = struct.unpack_from("<II", val, 0)
            if ebb_dwords > 8 or 8 + ebb_dwords * 4 + 4 > len(val):
                continue
            pgctl_masks = struct.unpack_from(f"<{ebb_dwords}I", val, 8) if ebb_dwords > 0 else ()
            (bank_count,) = struct.unpack_from("<I", val, 8 + ebb_dwords * 4)
            if ebb_dwords > 0:
                bank_count = min(bank_count, ebb_dwords * 32, 64)
            else:
                bank_count = 0

            seg = SramSegmentState("HPSRAM" if ttype == 1 else "LPSRAM")
            seg.total_banks = bank_count
            seg.total_phys_pages = bank_count * (32 if ttype == 1 else 16)
            seg.free_phys_pages = free_pages
            for b in range(bank_count):
                dword_idx = b // 32
                bit_idx = b % 32
                is_gated = bool((pgctl_masks[dword_idx] >> bit_idx) & 1)
                is_powered = not is_gated
                seg.bank_powered.append(is_powered)
                if is_powered:
                    seg.powered_banks += 1
                else:
                    seg.gated_banks += 1

            alloc_off = 12 + ebb_dwords * 4
            for _ in range(bank_count):
                if alloc_off + 2 <= len(val):
                    (amask,) = struct.unpack_from("<H", val, alloc_off)
                    seg.bank_alloc_mask.append(amask)
                    alloc_off += 2

            if ttype == 1 and seg.total_banks > 0:
                self.cached_hpsram = seg
            elif ttype == 0 and seg.total_banks > 0:
                self.cached_lpsram = seg

    def parse_power_state_tlv(self, data: bytes, snap: TelemetrySnapshot):
        off = 0
        while off + 8 <= len(data):
            ttype, tlen = struct.unpack_from("<II", data, off)
            off += 8
            if off + tlen > len(data):
                break
            val = data[off : off + tlen]
            off += tlen
            if ttype == 0 and tlen >= 4:
                (mask,) = struct.unpack_from("<I", val, 0)
                snap.enabled_cores_mask = mask
            elif ttype == 1 and tlen >= 4:
                n = tlen // 4
                kcps_arr = struct.unpack_from(f"<{n}I", val, 0)
                for cid, kv in enumerate(kcps_arr):
                    self.cached_core_kcps[cid] = kv

    def parse_ext_perf_tlv(self, data: bytes):
        if len(data) < 4:
            return
        (count,) = struct.unpack_from("<I", data, 0)
        if count == 0:
            self.cached_ext_perf = []
            return
        if count > 256:
            return
        items: List[ComponentPerfItem] = []
        off = 4
        rem = len(data) - 4
        if rem >= count * 48:
            stride = 48
        elif rem >= count * 32:
            stride = 32
        else:
            stride = 16

        for _ in range(count):
            if off + stride > len(data):
                break
            if stride == 48:
                (
                    res_id,
                    flags,
                    peak_cyc,
                    total_cyc,
                    iters,
                    _r_peak,
                    _r_tot,
                    _r_it,
                ) = struct.unpack_from("<IIIQQIQQ", data, off)
                avg_cyc = (total_cyc // max(1, iters)) if iters > 0 else (peak_cyc // 1000)
                peak_kcps = peak_cyc // 1000 if peak_cyc >= 1000 else peak_cyc
                avg_kcps = int(avg_cyc)
                cycles = total_cyc
            elif stride == 32:
                res_id, flags, peak_kcps, avg_kcps, iters, cycles = struct.unpack_from(
                    "<IIIIQQ", data, off
                )
            else:
                res_id, flags, peak_kcps, avg_kcps = struct.unpack_from(
                    "<IIII", data, off
                )
                iters, cycles = 0, 0
            off += stride
            if res_id == 0 and peak_kcps == 0 and avg_kcps == 0 and cycles == 0:
                continue
            if res_id == 0xFFFFFFFF:
                continue
            mod_id = res_id & 0xFFFF
            inst_id = (res_id >> 16) & 0xFFFF
            mname = (
                f"Core {inst_id} Summary"
                if mod_id == 0
                else self.lookup_module_name(mod_id)
            )
            pmode = "DP" if (flags & 1) else "LL"
            removed = bool((flags >> 31) & 1)
            items.append(
                ComponentPerfItem(
                    resource_id=res_id,
                    module_id=mod_id,
                    instance_id=inst_id,
                    module_name=mname,
                    core_id=inst_id if mod_id == 0 else 0,
                    power_mode=pmode,
                    is_removed=removed,
                    avg_kcps=avg_kcps,
                    peak_kcps=peak_kcps,
                    iterations=iters,
                    total_cycles=cycles,
                )
            )
        self.cached_ext_perf = items

    def parse_tel2_and_pmw2(self, tel2: bytes, pmw2: bytes, snap: TelemetrySnapshot):
        """Parse Extended Telemetry v2 (TEL2 4KB slot & PMW2 8KB window)."""
        bias = sof_tel2_slot_bias(tel2)
        if bias >= 0:
            snap.tel2_present = True
            hdr_off = SOF_TEL_EXT_HDR_OFFSET - bias
            if hdr_off + 16 <= len(tel2):
                _, ver, max_cores, num_secs, seq, ts_us = struct.unpack_from(
                    "<IBBHII", tel2, hdr_off
                )
                snap.tel2_seq = seq
                snap.tel2_timestamp_us = ts_us

            # 1. Hot Line 0 (0x0140)
            hl_off = SOF_TEL_OFF_HOT_LINE0 - bias
            ppl_xruns: List[int] = [0] * 16
            if hl_off + 64 <= len(tel2):
                vals = struct.unpack_from("<5H5H5HH16H", tel2, hl_off)
                ll_ov = vals[0:5]
                dp_miss = vals[5:10]
                act_ll = vals[10:15]
                snap.total_xrun_events = vals[15]
                ppl_xruns = list(vals[16:32])
                for c in range(min(5, len(snap.cores))):
                    snap.cores[c].ll_overruns = ll_ov[c]
                    snap.cores[c].dp_deadline_misses = dp_miss[c]
                    snap.cores[c].active_ll_tasks = act_ll[c]

            # 2. Gateway Hot (0x0180, 16 x 8B)
            gw_hot_off = SOF_TEL_OFF_GW_HOT - bias
            gw_hot_list = []
            if gw_hot_off + 128 <= len(tel2):
                for g in range(16):
                    tot_kb_f, avail_b, xrun_c = struct.unpack_from(
                        "<IHH", tel2, gw_hot_off + g * 8
                    )
                    gw_hot_list.append((tot_kb_f, avail_b, xrun_c))

            # 3. Memory Section (0x0240, 256B)
            mem_off = SOF_TEL_OFF_MEM_SEC - bias
            if mem_off + 256 <= len(tel2):
                (
                    hp_free,
                    hp_tot,
                    lp_free,
                    lp_tot,
                    ebb_lo,
                    ebb_hi,
                    lp_ebb,
                    _rsvd,
                ) = struct.unpack_from("<HHHHIIII", tel2, mem_off)

                if hp_tot > 0:
                    snap.hpsram.total_phys_pages = hp_tot
                    snap.hpsram.free_phys_pages = hp_free
                    if snap.hpsram.total_banks == 0:
                        hp_banks = (hp_tot + 31) // 32
                        pwr_hp = bin(ebb_lo).count("1") + bin(ebb_hi).count("1")
                        snap.hpsram.total_banks = hp_banks
                        snap.hpsram.powered_banks = (
                            pwr_hp
                            if pwr_hp >= hp_banks
                            else (hp_banks if hp_free < hp_tot else pwr_hp)
                        )
                        snap.hpsram.gated_banks = max(
                            0, snap.hpsram.total_banks - snap.hpsram.powered_banks
                        )
                        snap.hpsram.bank_powered = [
                            b < snap.hpsram.powered_banks for b in range(hp_banks)
                        ]
                        self.cached_hpsram = snap.hpsram
                if lp_tot > 0:
                    snap.lpsram.total_phys_pages = lp_tot
                    snap.lpsram.free_phys_pages = lp_free
                    if snap.lpsram.total_banks == 0:
                        lp_banks = (lp_tot + 15) // 16
                        pwr_lp = bin(lp_ebb).count("1")
                        snap.lpsram.total_banks = lp_banks
                        snap.lpsram.powered_banks = (
                            min(lp_banks, pwr_lp)
                            if pwr_lp > 0
                            else (lp_banks if lp_free < lp_tot else 0)
                        )
                        snap.lpsram.gated_banks = max(
                            0, snap.lpsram.total_banks - snap.lpsram.powered_banks
                        )
                        snap.lpsram.bank_powered = [
                            b < snap.lpsram.powered_banks for b in range(lp_banks)
                        ]
                        self.cached_lpsram = snap.lpsram

                # Heaps[6] (each 20B at mem_off + 24)
                snap.heaps = []
                tot_alloc = 0
                tot_free = 0
                tot_max = 0
                tot_cap = 0
                for h_idx in range(6):
                    hoff = mem_off + 24 + h_idx * 20
                    htype, hfail, htot, halloc, hfree, hmax = struct.unpack_from(
                        "<HHIIII", tel2, hoff
                    )
                    if htype == 0 and htot == 0 and halloc == 0:
                        continue
                    is_dram = (htype == 4)  # SOF_TEL_HEAP_L3_IMR
                    hname = HEAP_TYPE_NAMES.get(htype, f"HEAP_{htype}")
                    snap.heaps.append(
                        HeapTelemetry(
                            heap_type=htype,
                            name=hname,
                            is_dram=is_dram,
                            total_bytes=htot,
                            allocated_bytes=halloc,
                            free_bytes=hfree,
                            max_allocated_bytes=hmax,
                            alloc_fail_count=hfail,
                        )
                    )
                    if is_dram:
                        snap.dram_used_kb += halloc / 1024.0
                        snap.dram_hwm_kb += hmax / 1024.0
                        snap.dram_total_kb += htot / 1024.0
                    else:
                        tot_alloc += halloc
                        tot_free += hfree
                        tot_max += hmax
                        tot_cap += htot

                snap.heap_allocated_bytes = tot_alloc
                snap.heap_free_bytes = tot_free
                snap.heap_max_bytes = tot_max
                snap.heap_total_bytes = tot_cap

                # VMH & fast_get (at mem_off + 144)
                vmh_off = mem_off + 144
                vmh_reg_sz, vmh_cnt, vmh_oom = struct.unpack_from("<IHH", tel2, vmh_off)
                snap.vmh_region_size = vmh_reg_sz
                snap.vmh_bundle_count = vmh_cnt
                snap.vmh_oom_total = vmh_oom
                snap.vmh_bundles = []
                for b_idx in range(10):
                    boff = vmh_off + 8 + b_idx * 8
                    bshift, _, btot, bused, bmax = struct.unpack_from("<BBHHH", tel2, boff)
                    if btot > 0 or bused > 0:
                        snap.vmh_bundles.append(
                            VmhBundleInfo(
                                blk_sz_shift=bshift,
                                block_size_bytes=(1 << bshift) if bshift < 31 else 0,
                                total_blocks=btot,
                                used_blocks=bused,
                                max_blocks_or_oom=bmax,
                            )
                        )
                fg_off = vmh_off + 88
                f_act, f_fail, f_tot, f_peak, f_calls = struct.unpack_from(
                    "<HHIII", tel2, fg_off
                )
                snap.fast_get = FastGetInfo(
                    active_entries=f_act,
                    fail_count=f_fail,
                    total_sram_bytes=f_tot,
                    peak_sram_bytes=f_peak,
                    total_get_calls=f_calls,
                )

            # 4. VRegion Section (0x0340, 512B)
            vr_off = SOF_TEL_OFF_VREG_SEC - bias
            if vr_off + 512 <= len(tel2):
                vt_p, vf_p, vmin_p, ve_in, vfail, vbase = struct.unpack_from(
                    "<HHHHII", tel2, vr_off
                )
                vsec = VregionSectionInfo(
                    total_pages=vt_p,
                    free_pages=vf_p,
                    min_free_pages=vmin_p,
                    num_elems_in_use=ve_in,
                    alloc_fail_count=vfail,
                    region_base_va=vbase,
                )
                for e_i in range(16):
                    epages, evpage = struct.unpack_from("<HH", tel2, vr_off + 16 + e_i * 4)
                    if epages > 0:
                        vsec.velems.append((epages, evpage))
                act_vr, tot_vr = struct.unpack_from("<HH", tel2, vr_off + 80)
                vsec.active_vregion_count = act_vr
                vsec.total_vregions_created = tot_vr
                for r_i in range(16):
                    roff = vr_off + 128 + r_i * 24
                    r_va, r_pg, r_uc, r_tf, r_lsz, r_lused, r_ialloc, r_ifree, r_lfc = (
                        struct.unpack_from("<IHBBIIIHH", tel2, roff)
                    )
                    if r_va != 0 or r_pg > 0:
                        vsec.vregions.append(
                            VregionInfo(
                                base_va=r_va,
                                total_pages=r_pg,
                                use_count=r_uc,
                                type_and_flags=r_tf,
                                lifetime_size_bytes=r_lsz,
                                lifetime_used_bytes=r_lused,
                                interim_alloc_bytes=r_ialloc,
                                interim_free_kb=r_ifree,
                                lifetime_free_count=r_lfc,
                            )
                        )
                snap.vregion_sec = vsec

            # 5. Pipelines Section (0x0540, 384B)
            ppl_off = SOF_TEL_OFF_PPL_SEC - bias
            if ppl_off + 384 <= len(tel2):
                for c_i in range(5):
                    coff = ppl_off + c_i * 12
                    cid, ll_c, dp_c, ap_c, kcps_b, _ = struct.unpack_from("<BBBBII", tel2, coff)
                    if c_i < len(snap.cores):
                        snap.cores[c_i].active_ll_tasks = max(snap.cores[c_i].active_ll_tasks, ll_c)
                        snap.cores[c_i].active_dp_tasks = dp_c
                        snap.cores[c_i].active_ppl_count = ap_c
                        if snap.cores[c_i].budget_kcps == 0 and kcps_b > 0:
                            snap.cores[c_i].budget_kcps = kcps_b
                act_ppls, tot_comps = struct.unpack_from("<HH", tel2, ppl_off + 60)
                snap.pipelines = []
                for p_i in range(16):
                    poff = ppl_off + 64 + p_i * 20
                    pid, pcore, pprio, pst, pcomp, pattr, pper, psched, pxrun_b = (
                        struct.unpack_from("<HBBBBHIIi", tel2, poff)
                    )
                    if pst != 0 or pcomp > 0:
                        snap.pipelines.append(
                            PipelineInfo(
                                pipeline_id=pid,
                                core=pcore,
                                priority=pprio,
                                status=pst,
                                status_name=PPL_STATUS_NAMES.get(pst, f"ST_{pst}"),
                                comp_count=pcomp,
                                attributes=pattr,
                                period_us=pper,
                                sched_comp_id=psched,
                                last_xrun_bytes=pxrun_b,
                                xrun_count=ppl_xruns[p_i] if p_i < len(ppl_xruns) else 0,
                            )
                        )

            # 6. DAI Gateways Section (0x06C0, 256B)
            dai_off = SOF_TEL_OFF_DAI_SEC - bias
            if dai_off + 256 <= len(tel2):
                snap.gateways = []
                for g_i in range(16):
                    goff = dai_off + g_i * 16
                    gtype, gidx, gdir, gdma, gcomp, gst, ggrp, gper, gbuf = (
                        struct.unpack_from("<BBBbIBBHI", tel2, goff)
                    )
                    tot_kbf, avail_b, xrun_c = (
                        gw_hot_list[g_i] if g_i < len(gw_hot_list) else (0, 0, 0)
                    )
                    if gtype != 0 or gst != 0 or tot_kbf > 0:
                        snap.gateways.append(
                            GatewayInfo(
                                slot_idx=g_i,
                                gw_type=gtype,
                                type_name=GW_TYPE_NAMES.get(gtype, f"GW_{gtype}"),
                                index=gidx,
                                direction=gdir,
                                dma_chan=gdma,
                                comp_id=gcomp,
                                state=gst,
                                group_id=ggrp,
                                period_bytes=gper,
                                dma_buf_size=gbuf,
                                total_kb_or_frames=tot_kbf,
                                dma_buf_avail_bytes=avail_b,
                                xrun_or_skip_count=xrun_c,
                            )
                        )

            # 7. Zephyr RTOS Threads Section (0x07C0)
            thd_off = SOF_TEL_OFF_THD_SEC - bias
            if thd_off + 32 + 32 * 24 <= len(tel2):
                t_cnt, t_drop, t_seq = struct.unpack_from("<HHI", tel2, thd_off + 8)
                snap.threads = []
                for t_i in range(min(32, t_cnt if t_cnt > 0 else 32)):
                    toff = thd_off + 32 + t_i * 24
                    tid, tcore, tprio, tst, tload, tstk, tflg, tchunks, tkcyc, tname_b = (
                        struct.unpack_from("<IBbBBBBHI8s", tel2, toff)
                    )
                    if tid == 0 and tload == 0 and tkcyc == 0 and tname_b[0] == 0:
                        continue
                    tname = tname_b.split(b"\x00", 1)[0].decode("ascii", errors="replace").strip()
                    snap.threads.append(
                        ThreadInfo(
                            thread_id=tid,
                            name=tname or f"thd_{t_i}",
                            core=tcore,
                            prio=tprio,
                            state=tst,
                            cpu_load_pct=tload / 2.55,
                            stack_usage_pct=tstk / 2.55,
                            flags=tflg,
                            stack_size_bytes=tchunks * 64,
                            total_exec_kcycles=tkcyc,
                        )
                    )

        # Parse PMW2 (MW3 8KB window) only when the PMW2 header is valid
        if len(pmw2) >= 8192:
            (mw3_magic,) = struct.unpack_from("<I", pmw2, SOF_TEL_MW3_HDR_OFFSET)
            if mw3_magic == SOF_TEL_MW3_MAGIC:
                snap.pmw2_present = True
                # Parse mod_cold[88] (16B each at 0x1200) and mod_hot[88] (4B each at 0x1080)
                cold_by_res: Dict[int, Tuple[int, int, int]] = {}
                for i in range(SOF_TEL_MW3_MAX_COMP_ITEMS):
                    coff = SOF_TEL_MW3_MOD_COLD_OFFSET + i * 16
                    c_res, c_heap, c_hwm, c_cpc = struct.unpack_from("<IIII", pmw2, coff)
                    if c_res not in (0, 0xFFFFFFFF):
                        cold_by_res[c_res] = (c_heap, c_hwm, c_cpc)

                pmw_comps: List[ComponentPerfItem] = []
                for i in range(SOF_TEL_MW3_MAX_COMP_ITEMS):
                    poff = i * SOF_TEL_MW3_COMP_PERF_STRIDE
                    (
                        res_id,
                        flags,
                        peak_k,
                        avg_k,
                        iters,
                        tot_cyc,
                        r_peak,
                        r_iters,
                        r_cyc,
                    ) = struct.unpack_from("<IIIIIQIQQ", pmw2, poff)
                    if res_id in (0, 0xFFFFFFFF):
                        continue
                    hoff = SOF_TEL_MW3_MOD_HOT_OFFSET + i * 4
                    cpc_v, xrun_v = struct.unpack_from("<HH", pmw2, hoff)
                    c_heap, c_hwm, c_cpc = cold_by_res.get(res_id, (0, 0, 0))
                    mod_id = res_id & 0xFFFF
                    inst_id = (res_id >> 16) & 0xFFFF
                    mname = self.lookup_module_name(mod_id)
                    pmw_comps.append(
                        ComponentPerfItem(
                            resource_id=res_id,
                            module_id=mod_id,
                            instance_id=inst_id,
                            module_name=mname,
                            core_id=0,
                            power_mode="DP" if (flags & 1) else "LL",
                            is_removed=bool((flags >> 31) & 1),
                            avg_kcps=avg_k,
                            peak_kcps=peak_k,
                            iterations=iters,
                            total_cycles=tot_cyc,
                            restricted_peak_cycles=r_peak,
                            restricted_iterations=r_iters,
                            restricted_cycles=r_cyc,
                            cpc_violations=cpc_v,
                            xrun_count=xrun_v,
                            heap_usage_bytes=c_heap,
                            heap_hwm_bytes=c_hwm,
                            configured_cpc=c_cpc,
                        )
                    )
                if pmw_comps:
                    snap.components = pmw_comps

                # Parse io_perf[128] at 0x1800 (12B each)
                snap.io_perf_items = []
                for i in range(SOF_TEL_MW3_MAX_IO_ITEMS):
                    ioff = SOF_TEL_MW3_IO_PERF_OFFSET + i * 12
                    ires, ipm, iuse = struct.unpack_from("<III", pmw2, ioff)
                    if ires not in (0, 0xFFFFFFFF) or iuse > 0:
                        snap.io_perf_items.append(IoPerfItem(ires, ipm, iuse))

    def synthesize_fallback_memory_and_tasks(self, snap: TelemetrySnapshot):
        """Populate SRAM totals from measured telemetry and build Unified DSP Task List."""
        hp = snap.hpsram
        lp = snap.lpsram
        hp_tot_kb = hp.total_banks * 128.0
        hp_pwr_kb = hp.powered_banks * 128.0
        hp_used_kb = (
            max(0.0, (hp.total_phys_pages - hp.free_phys_pages) * 4.0)
            if hp.total_phys_pages >= hp.free_phys_pages and hp.total_phys_pages > 0
            else hp_pwr_kb
        )
        lp_tot_kb = lp.total_banks * 64.0
        lp_pwr_kb = lp.powered_banks * 64.0
        lp_used_kb = (
            max(0.0, (lp.total_phys_pages - lp.free_phys_pages) * 4.0)
            if lp.total_phys_pages >= hp.free_phys_pages and lp.total_phys_pages > 0
            else lp_pwr_kb
        )

        snap.sram_used_kb = hp_used_kb + lp_used_kb
        snap.sram_powered_kb = hp_pwr_kb + lp_pwr_kb
        snap.sram_total_kb = hp_tot_kb + lp_tot_kb

        # Build Unified DSP Task List (snap.tasks)
        clock_khz = max(1.0, snap.dsp_clock_hz / 1000.0)
        tasks: List[DspTaskItem] = []

        # 1) Add Per-Core Scheduler / BaseFW tasks
        for ct in snap.cores:
            if not ct.enabled and ct.tick_count == 0 and ct.avg_kcps == 0:
                continue
            eff_kcps = ct.avg_kcps if ct.avg_kcps > 0 else ct.budget_kcps
            st_str = (
                "RUNNING"
                if (ct.ticks_per_sec > 0 or eff_kcps > 0)
                else ("IDLE" if ct.enabled else "GATED")
            )
            if ct.ll_overruns > 0 or ct.dp_deadline_misses > 0:
                st_str = "XRUN!"
            tasks.append(
                DspTaskItem(
                    task_id=f"core:{ct.core_id}",
                    name=f"core{ct.core_id}_ll_sched",
                    task_type="Core Sched",
                    core_id=ct.core_id,
                    status=st_str,
                    cpu_pct=ct.cpu_load_pct,
                    avg_kcps=eff_kcps,
                    peak_kcps=max(ct.peak_kcps, eff_kcps),
                    mem_used_kb=64.0 if ct.core_id == 0 else 16.0,
                    mem_limit_kb=128.0,
                    mem_desc=f"Systick {ct.last_time_elapsed_us:.1f}us (max {ct.max_time_elapsed_us:.1f}us)",
                    iterations=ct.tick_count,
                    xruns=ct.ll_overruns + ct.dp_deadline_misses,
                )
            )

        # 2) Add Active Audio Pipeline Component Tasks
        comp_by_res_id: Dict[int, ComponentPerfItem] = {}
        for comp in snap.components:
            comp_by_res_id[comp.resource_id] = comp
            if comp.module_id == 0:
                continue  # Already covered by core scheduler summary
            st_str = "REMOVED" if comp.is_removed else ("RUNNING" if comp.avg_kcps > 0 or comp.iterations > 0 else "READY")
            if comp.xrun_count > 0:
                st_str = "XRUN!"
            elif comp.cpc_violations > 0:
                st_str = "CPC_VIOL"
            mem_kb = comp.heap_usage_bytes / 1024.0 if comp.heap_usage_bytes > 0 else 0.0
            hwm_kb = comp.heap_hwm_bytes / 1024.0 if comp.heap_hwm_bytes > 0 else mem_kb
            mem_desc = f"Heap {mem_kb:.1f}/{hwm_kb:.1f}KB" if hwm_kb > 0 else "Heap N/A"
            tasks.append(
                DspTaskItem(
                    task_id=f"0x{comp.module_id:02x}:{comp.instance_id}",
                    name=f"{comp.module_name}:{comp.instance_id}",
                    task_type=f"{comp.power_mode} Module",
                    core_id=comp.core_id,
                    status=st_str,
                    cpu_pct=min(100.0, (comp.avg_kcps / clock_khz) * 100.0),
                    avg_kcps=comp.avg_kcps,
                    peak_kcps=comp.peak_kcps,
                    mem_used_kb=mem_kb,
                    mem_limit_kb=hwm_kb,
                    mem_desc=mem_desc,
                    iterations=comp.iterations,
                    xruns=comp.xrun_count + comp.cpc_violations,
                )
            )

        # 3) Add Zephyr RTOS Threads (if reported via TEL2)
        for thd in snap.threads:
            st_map = {0: "READY", 1: "RUNNING", 2: "WAITING", 3: "SUSPENDED", 4: "IDLE"}
            st_str = "IDLE" if (thd.flags & 1) else st_map.get(thd.state, "ACTIVE")
            stk_total_kb = max(1.0, thd.stack_size_bytes / 1024.0)
            stk_used_kb = stk_total_kb * (thd.stack_usage_pct / 100.0)
            kcps_est = int((thd.cpu_load_pct / 100.0) * clock_khz)
            tasks.append(
                DspTaskItem(
                    task_id=f"thd:{thd.thread_id}",
                    name=thd.name,
                    task_type="RTOS Thread",
                    core_id=thd.core,
                    status=st_str,
                    cpu_pct=thd.cpu_load_pct,
                    avg_kcps=kcps_est,
                    peak_kcps=kcps_est,
                    mem_used_kb=stk_used_kb,
                    mem_limit_kb=stk_total_kb,
                    mem_desc=f"Stack {thd.stack_usage_pct:.0f}% ({stk_total_kb:.1f}KB)",
                    iterations=thd.total_exec_kcycles,
                    xruns=0,
                )
            )

        # 4) Add Active DAI Gateways (using measured performance counters when available)
        io_kcps_by_id: Dict[int, int] = {
            io.resource_id: io.perf_usage for io in snap.io_perf_items
        }
        for gw in snap.gateways:
            dir_s = "RX" if gw.direction else "TX"
            st_str = "XRUN!" if gw.xrun_or_skip_count > 0 else ("RUNNING" if gw.state > 0 else "IDLE")
            buf_kb = gw.dma_buf_size / 1024.0 if gw.dma_buf_size > 0 else 0.0
            avail_kb = gw.dma_buf_avail_bytes / 1024.0
            gw_comp = comp_by_res_id.get(gw.comp_id)
            gw_avg_kcps = gw_comp.avg_kcps if gw_comp else io_kcps_by_id.get(gw.comp_id, 0)
            gw_peak_kcps = gw_comp.peak_kcps if gw_comp else gw_avg_kcps
            gw_cpu_pct = min(100.0, (gw_avg_kcps / clock_khz) * 100.0)
            tasks.append(
                DspTaskItem(
                    task_id=f"dai:{gw.slot_idx}",
                    name=f"{gw.type_name}{gw.index}_{dir_s}",
                    task_type="DAI Gateway",
                    core_id=0,
                    status=st_str,
                    cpu_pct=gw_cpu_pct,
                    avg_kcps=gw_avg_kcps,
                    peak_kcps=gw_peak_kcps,
                    mem_used_kb=avail_kb,
                    mem_limit_kb=buf_kb,
                    mem_desc=f"DMA ch{gw.dma_chan} ({avail_kb:.1f}/{buf_kb:.1f}KB)",
                    iterations=gw.total_kb_or_frames,
                    xruns=gw.xrun_or_skip_count,
                )
            )

        # 5) If no pipeline components or threads are active yet, show target-enumerated firmware modules
        if len(tasks) <= len(snap.cores) and snap.loaded_modules:
            for mid, mname in sorted(snap.loaded_modules.items())[:14]:
                if mid == 0:
                    continue
                tasks.append(
                    DspTaskItem(
                        task_id=f"mod:0x{mid:02x}",
                        name=mname,
                        task_type="FW Module",
                        core_id=0,
                        status="READY",
                        cpu_pct=0.0,
                        avg_kcps=0,
                        peak_kcps=0,
                        mem_used_kb=0.0,
                        mem_limit_kb=0.0,
                        mem_desc="Loaded",
                        iterations=0,
                        xruns=0,
                    )
                )

        snap.tasks = tasks

    def parse_composite_snapshot(self, raw_snap: bytes, wire_len: int = 0) -> TelemetrySnapshot:
        now = time.time()
        dt = max(0.001, now - self.prev_ts) if self.prev_ts > 0 else 0.1
        snap = TelemetrySnapshot(
            timestamp=now,
            connected=True,
            wire_bytes=wire_len if wire_len > 0 else len(raw_snap),
            raw_bytes=len(raw_snap),
        )

        cdev_info_sz = 716
        if len(raw_snap) < cdev_info_sz + 8:
            return snap

        self.parse_cdev_info_into(raw_snap[:cdev_info_sz], snap)
        flags_present, active_pcm = struct.unpack_from("<II", raw_snap, cdev_info_sz)
        snap.active_pcm_streams = active_pcm

        off = cdev_info_sz + 8
        fw_regs = raw_snap[off : off + 512]
        off += 512
        telem_slot = raw_snap[off : off + 512]
        off += 512
        perf_win3 = raw_snap[off : off + 4096]
        off += 4096

        (mem_len,) = struct.unpack_from("<I", raw_snap, off)
        off += 4
        mem_tlv = raw_snap[off : off + min(2048, mem_len)]
        off += 2048

        (pwr_len,) = struct.unpack_from("<I", raw_snap, off)
        off += 4
        pwr_tlv = raw_snap[off : off + min(256, pwr_len)]
        off += 256

        (ext_perf_len,) = struct.unpack_from("<I", raw_snap, off)
        off += 4
        ext_perf_tlv = raw_snap[off : off + min(4096, ext_perf_len)]
        off += 4096

        (mod_info_len,) = struct.unpack_from("<I", raw_snap, off)
        off += 4
        mod_info_tlv = raw_snap[off : off + min(4096, mod_info_len)]
        off += 4096

        tel2_slot = b""
        pmw2_win3 = b""
        if len(raw_snap) >= off + 4 + 4096 + 4 + 8192:
            (tel2_len,) = struct.unpack_from("<I", raw_snap, off)
            off += 4
            tel2_slot = raw_snap[off : off + min(4096, tel2_len)]
            off += 4096
            (pmw2_len,) = struct.unpack_from("<I", raw_snap, off)
            off += 4
            pmw2_win3 = raw_snap[off : off + min(8192, pmw2_len)]

        if (flags_present & SOF_TELEM_FL_MODULES_INFO_TLV) and mod_info_len > 0:
            self.parse_modules_info_tlv(mod_info_tlv)
        snap.loaded_modules = dict(self.cached_modules)

        if (flags_present & SOF_TELEM_FL_MEM_STATE_TLV) and mem_len > 0:
            self.parse_memory_state_tlv(mem_tlv)

        if (flags_present & SOF_TELEM_FL_PWR_STATE_TLV) and pwr_len > 0:
            self.parse_power_state_tlv(pwr_tlv, snap)

        if (flags_present & SOF_TELEM_FL_EXT_PERF_TLV) and ext_perf_len > 0:
            self.parse_ext_perf_tlv(ext_perf_tlv)

        if self.cached_hpsram:
            snap.hpsram = self.cached_hpsram
        if self.cached_lpsram:
            snap.lpsram = self.cached_lpsram

        # Parse Window 0 FW Regs
        if flags_present & SOF_TELEM_FL_FW_REGS:
            snap.fw_status, snap.lec, snap.fps, snap.lnec, snap.ltr = struct.unpack_from(
                "<IIIII", fw_regs, 0
            )

        # Parse Window 2 Telemetry Slot
        clock_khz = max(1, snap.dsp_clock_hz // 1000)
        snap.cores = []
        for cid in range(snap.num_cores):
            en = bool((snap.enabled_cores_mask >> cid) & 1)
            ct = CoreTelemetry(
                core_id=cid,
                enabled=en,
                budget_kcps=self.cached_core_kcps.get(cid, 0),
            )
            if (flags_present & SOF_TELEM_FL_TELEM_SLOT) and len(telem_slot) >= 4 + (cid + 1) * 32:
                (sep,) = struct.unpack_from("<I", telem_slot, 0)
                if sep in (0x0000C0DE, 0xDEC0AD0B) or sep != 0xFFFFFFFF:
                    (
                        t_cnt,
                        last_el,
                        max_el,
                        last_cc,
                        avg_u,
                        peak_u,
                        peak_4k,
                        peak_8k,
                    ) = struct.unpack_from("<IIIIIIII", telem_slot, 4 + cid * 32)
                    if t_cnt != 0xFFFFFFFF:
                        ct.tick_count = t_cnt
                        has_prev = cid in self.prev_ticks
                        prev_t = self.prev_ticks.get(cid, t_cnt)
                        if has_prev and t_cnt >= prev_t and self.prev_ts > 0:
                            ct.ticks_per_sec = (t_cnt - prev_t) / dt
                        self.prev_ticks[cid] = t_cnt

                        # Guard against idle-gap ccount wrap and stopped LL timer in SOF telemetry.c
                        if max_el > clock_khz:
                            max_el = last_el if last_el <= clock_khz else 0
                        if peak_u > clock_khz * 2 or (max_el > 0 and peak_u > max_el * 2):
                            peak_u = max_el
                        if peak_4k > clock_khz * 2 or (max_el > 0 and peak_4k > max_el * 2):
                            peak_4k = peak_u
                        if peak_8k > clock_khz * 2 or (max_el > 0 and peak_8k > max_el * 2):
                            peak_8k = peak_u

                        if has_prev:
                            is_idle = ct.ticks_per_sec == 0.0
                        else:
                            is_idle = (snap.active_pcm_streams == 0) and (ct.ticks_per_sec == 0.0)
                        if is_idle:
                            avg_u = 0
                            last_el = 0
                            ct.budget_kcps = 0
                        elif (
                            avg_u > clock_khz * 2
                            or (max_el > 0 and avg_u > max_el)
                            or (last_el > 0 and avg_u > last_el * 4)
                        ):
                            avg_u = last_el if last_el <= clock_khz else 0

                        if ct.budget_kcps > clock_khz * 2 or (max_el > 0 and ct.budget_kcps > max_el):
                            ct.budget_kcps = avg_u

                        ct.last_time_elapsed_cyc = last_el
                        ct.max_time_elapsed_cyc = max_el
                        ct.last_time_elapsed_us = last_el / (snap.dsp_clock_hz / 1e6)
                        ct.max_time_elapsed_us = max_el / (snap.dsp_clock_hz / 1e6)
                        ct.last_ccount = last_cc

                        ct.avg_kcps = avg_u
                        ct.peak_kcps = peak_u
                        ct.peak_4k_kcps = peak_4k
                        ct.peak_8k_kcps = peak_8k

                        eff_kcps = avg_u if avg_u > 0 else ct.budget_kcps
                        ct.cpu_load_pct = min(100.0, (eff_kcps / clock_khz) * 100.0)
                        ct.peak_load_pct = min(
                            100.0, (max(peak_u, eff_kcps) / clock_khz) * 100.0
                        )
            elif en and ct.budget_kcps > 0:
                if ct.budget_kcps > clock_khz * 2:
                    ct.budget_kcps = 0
                ct.cpu_load_pct = min(100.0, (ct.budget_kcps / clock_khz) * 100.0)
                ct.peak_load_pct = ct.cpu_load_pct

            snap.cores.append(ct)

        # Parse legacy Window 3 Performance Measurements Window (ADSP_PMW)
        win3_items: List[ComponentPerfItem] = []
        if flags_present & SOF_TELEM_FL_PERF_WIN3:
            off3 = 0
            while off3 + 32 <= len(perf_win3):
                res_id, flags, peak_kcps, avg_kcps, iters, cycles = struct.unpack_from(
                    "<IIIIQQ", perf_win3, off3
                )
                off3 += 32
                if res_id in (0, 0xFFFFFFFF):
                    break
                mod_id = res_id & 0xFFFF
                inst_id = (res_id >> 16) & 0xFFFF
                mname = self.lookup_module_name(mod_id)
                win3_items.append(
                    ComponentPerfItem(
                        resource_id=res_id,
                        module_id=mod_id,
                        instance_id=inst_id,
                        module_name=mname,
                        core_id=0,
                        power_mode="DP" if (flags & 1) else "LL",
                        is_removed=bool((flags >> 1) & 1),
                        avg_kcps=avg_kcps if avg_kcps <= clock_khz * 2 else 0,
                        peak_kcps=peak_kcps if peak_kcps <= clock_khz * 2 else 0,
                        iterations=iters,
                        total_cycles=cycles,
                    )
                )

        snap.components = win3_items if win3_items else list(self.cached_ext_perf)

        # Parse Extended TEL2 (MW2 Slot 1) and PMW2 (MW3) if present
        if tel2_slot or pmw2_win3:
            self.parse_tel2_and_pmw2(tel2_slot, pmw2_win3, snap)

        for it in snap.components:
            if it.avg_kcps > clock_khz * 2:
                it.avg_kcps = snap.cores[0].avg_kcps if snap.cores else 0
            if it.peak_kcps > clock_khz * 2:
                it.peak_kcps = snap.cores[0].peak_kcps if snap.cores else 0
            if it.module_id == 0 and it.instance_id < len(snap.cores):
                ct = snap.cores[it.instance_id]
                if it.avg_kcps == 0:
                    it.avg_kcps = ct.avg_kcps if ct.avg_kcps > 0 else ct.budget_kcps
                if it.peak_kcps == 0:
                    it.peak_kcps = ct.peak_kcps if ct.peak_kcps > 0 else it.avg_kcps
                if it.iterations == 0:
                    it.iterations = ct.tick_count
                if it.total_cycles <= 1:
                    it.total_cycles = ct.last_ccount

        self.synthesize_fallback_memory_and_tasks(snap)
        self.prev_ts = now
        return snap


# -----------------------------------------------------------------------------
# Remote TCP Network Backend (connects to sof_remote_server)
# -----------------------------------------------------------------------------
class RemoteTcpBackend:
    def __init__(self, host: str, port: int = SOF_NET_DEFAULT_PORT):
        self.host = host
        self.port = port
        self.sock: Optional[socket.socket] = None
        self.req_id = 1
        self.parser = TelemetryParser()
        self.poll_counter = 0
        self.last_wire_len = 0

    def connect(self) -> bool:
        self.close()
        try:
            s = socket.create_connection((self.host, self.port), timeout=2.5)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.sock = s
            return True
        except Exception:
            self.sock = None
            return False

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    def _recv_exact(self, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("Remote socket closed")
            buf.extend(chunk)
        return bytes(buf)

    def rpc(self, opcode: int, payload: bytes = b"") -> Tuple[int, bytes]:
        if not self.sock and not self.connect():
            return -107, b""
        rid = self.req_id
        self.req_id += 1
        hdr = struct.pack(
            "<IHHIIi",
            SOF_NET_MAGIC,
            SOF_NET_VERSION,
            opcode | SOF_NET_OP_FLAG_COMPRESSED,
            rid,
            len(payload),
            0,
        )
        try:
            self.sock.sendall(hdr + payload)
            rhdr = self._recv_exact(20)
            magic, ver, rop, rrid, plen, status = struct.unpack("<IHHIIi", rhdr)
            rdata = self._recv_exact(plen) if plen > 0 else b""
            self.last_wire_len = plen
            if (rop & SOF_NET_OP_FLAG_COMPRESSED) and len(rdata) >= 8:
                decomp = sof_net_decompress(rdata)
                if decomp:
                    rdata = decomp
            return status, rdata
        except Exception:
            self.close()
            return -104, b""

    def poll_telemetry(self, force_full: bool = False, wake_dsp: bool = False) -> TelemetrySnapshot:
        qmask = (
            SOF_TELEM_FL_FW_REGS
            | SOF_TELEM_FL_TELEM_SLOT
            | SOF_TELEM_FL_EXT_PERF_TLV
            | SOF_TELEM_FL_TEL2_SLOT
            | SOF_TELEM_FL_PMW2_WIN3
        )
        if force_full or (self.poll_counter % 10 == 0):
            qmask |= (
                SOF_TELEM_FL_MEM_STATE_TLV
                | SOF_TELEM_FL_PWR_STATE_TLV
                | SOF_TELEM_FL_EXT_PERF_TLV
                | SOF_TELEM_FL_MODULES_INFO_TLV
            )
        self.poll_counter += 1
        flags = SOF_TELEM_FL_COMPRESS | (SOF_TELEM_FL_WAKE_DSP if wake_dsp else 0)
        req = struct.pack("<II", qmask, flags)
        status, data = self.rpc(SOF_NET_OP_TELEM_SNAPSHOT, req)
        if status != 0 or not data:
            return TelemetrySnapshot(timestamp=time.time(), connected=False)
        return self.parser.parse_composite_snapshot(data, wire_len=self.last_wire_len)

    def win_read(
        self,
        win_id: int,
        offset: int = 0,
        size: int = 256,
        slot_type: int = 0,
        slot_index: int = 0,
        wake_dsp: bool = False,
    ) -> Tuple[int, bytes]:
        flags = 1 if wake_dsp else 0
        req = struct.pack("<IIIIII", win_id, slot_type, slot_index, offset, size, flags)
        return self.rpc(SOF_NET_OP_WIN_READ, req)

    def pm_control(self, op: int, core_id: int = 0) -> Tuple[int, dict]:
        req = struct.pack("<IIIIIIII", op, core_id, 0, 0, 0, 0, 0, 0)
        st, data = self.rpc(SOF_NET_OP_PM_CTL, req)
        if len(data) >= 32:
            _, _, fw_st, pwr_st, pm_act, cmask, hold, _ = struct.unpack_from(
                "<IIIIIIII", data, 0
            )
            return st, {
                "fw_state": fw_st,
                "dsp_power_state": pwr_st,
                "pm_runtime_active": bool(pm_act),
                "enabled_cores_mask": cmask,
                "fd_holding_d0": bool(hold),
            }
        return st, {}

    def set_perf_measurement_state(self, state_val: int) -> int:
        if state_val == 2:
            p0 = struct.pack("<IIIIII", 0, 0, IPC4_PERF_MEASUREMENTS_STATE, 1, 4, 0) + struct.pack("<I", 0)
            self.rpc(SOF_NET_OP_IPC_LARGE_CFG, p0)
        payload = struct.pack("<IIIIII", 0, 0, IPC4_PERF_MEASUREMENTS_STATE, 1, 4, 0) + struct.pack(
            "<I", state_val
        )
        st, _ = self.rpc(SOF_NET_OP_IPC_LARGE_CFG, payload)
        return st

    def load_llext(self, file_bytes: bytes, lib_id: int = 0, mode: int = 1, mod_name: str = "mod") -> Tuple[int, int, int, str]:
        mname_b = mod_name.encode("ascii", errors="ignore")[:63].ljust(64, b"\x00")
        issue_shell = 1 if mode == 0 else 0
        req_hdr = struct.pack("<IIII64s", mode, lib_id, len(file_bytes), issue_shell, mname_b)
        st, rdata = self.rpc(SOF_NET_OP_LLEXT_LOAD, req_hdr + file_bytes)
        if len(rdata) >= 520:
            out_lib_id, fw_st, sh_out = struct.unpack_from("<Ii512s", rdata, 0)
            return st, out_lib_id, fw_st, sh_out.split(b"\x00", 1)[0].decode("utf-8", errors="replace")
        return st, lib_id, -1, ""

    def exec_shell(self, cmd: str, timeout_ms: int = 800) -> Tuple[int, str]:
        cmd_b = cmd.encode("utf-8") + b"\x00"
        req = struct.pack("<II", timeout_ms, len(cmd_b)) + cmd_b
        st, rdata = self.rpc(SOF_NET_OP_SHELL_EXEC, req)
        return st, rdata.decode("utf-8", errors="replace")

    def query_probes(self, active_only: bool = False) -> Tuple[int, List[dict]]:
        op = SOF_PROBE_OP_INFO_ACTIVE if active_only else SOF_PROBE_OP_INFO_AVAILABLE
        req = struct.pack("<IIII", op, 0, 0, 64)
        st, rdata = self.rpc(SOF_NET_OP_PROBE_CTL, req)
        pts = []
        if st == 0 and len(rdata) >= 16:
            _, _, _, count = struct.unpack_from("<IIII", rdata, 0)
            off = 16
            for _ in range(min(count, 64)):
                if off + 48 > len(rdata):
                    break
                bid, purp, stag, _, wname = struct.unpack_from("<IIII32s", rdata, off)
                off += 48
                pts.append(
                    {
                        "buffer_id": bid,
                        "purpose": purp,
                        "stream_tag": stag,
                        "widget_name": wname.split(b"\x00", 1)[0].decode("ascii", errors="replace"),
                    }
                )
        return st, pts

    def pcm_control(
        self,
        op: int,
        device: str = "hw:0,0",
        freq_or_ms: int = 1000,
        api_backend: int = SOF_AUDIO_API_ALSA,
        fmt_id: int = 0,
    ) -> Tuple[int, dict, bytes]:
        dev_b = device.encode("ascii", errors="ignore")[:31].ljust(32, b"\x00")
        req = struct.pack(
            "<I32sIIIIIIIII",
            op,
            dev_b,
            48000,
            2,
            freq_or_ms,
            freq_or_ms,
            0,
            api_backend,
            fmt_id,
            1024,
            4,
        )
        st, rdata = self.rpc(SOF_NET_OP_PCM_CTL, req)
        if len(rdata) >= 80:
            tone_run, adev, rate, ch, freq, frames, rms, peak, dsz, api_u, fmt_u, xruns = (
                struct.unpack_from("<I32sIIIQIIIIII", rdata, 0)
            )
            pcm_bytes = rdata[80 : 80 + dsz] if dsz > 0 else b""
            return st, {
                "tone_running": bool(tone_run),
                "device": adev.split(b"\x00", 1)[0].decode("ascii", errors="replace"),
                "rate": rate,
                "channels": ch,
                "freq_hz": freq,
                "frames_played": frames,
                "rms_level": rms,
                "peak_level": peak,
                "api_used": api_u,
                "format": fmt_u,
                "xruns": xruns,
            }, pcm_bytes
        return st, {}, b""

    def query_audio_graph(self, card_id: int = 0, api_backend: int = SOF_AUDIO_API_ALSA) -> dict:
        result = {
            "cards": [],
            "pcm_nodes": [],
            "pcm_caps": {},
            "dapm_hdr": {},
            "components": [],
            "dapm_nodes": [],
            "dapm_edges": [],
            "dpcm_links": [],
            "kcontrols": [],
        }

        # 1. Card & PCM stream list
        st, cdata = self.rpc(SOF_NET_OP_CARD_LIST)
        if st == 0 and len(cdata) >= 8:
            num_cards, num_nodes = struct.unpack_from("<II", cdata, 0)
            off = 8
            card_sz = 404
            for _ in range(num_cards):
                if off + card_sz > len(cdata):
                    break
                cid, id_b, drv_b, nm_b, lnm_b, mix_b, tplg_b, npcm, ncomp, nkctl, nact = (
                    struct.unpack_from("<I32s32s64s96s64s96sIIII", cdata, off)
                )
                off += card_sz
                result["cards"].append({
                    "card_id": cid,
                    "id": id_b.split(b"\x00", 1)[0].decode("utf-8", errors="replace"),
                    "driver": drv_b.split(b"\x00", 1)[0].decode("utf-8", errors="replace"),
                    "name": nm_b.split(b"\x00", 1)[0].decode("utf-8", errors="replace"),
                    "longname": lnm_b.split(b"\x00", 1)[0].decode("utf-8", errors="replace"),
                    "tplg_file": tplg_b.split(b"\x00", 1)[0].decode("utf-8", errors="replace"),
                    "num_pcm_streams": npcm,
                    "num_compr_streams": ncomp,
                    "num_kcontrols": nkctl,
                    "active_streams": nact,
                })
            node_sz = 232
            for _ in range(num_nodes):
                if off + node_sz > len(cdata):
                    break
                (
                    cid,
                    did,
                    sdir,
                    is_comp,
                    id_b,
                    nm_b,
                    scnt,
                    savail,
                    st_b,
                    is_run,
                    opid,
                    afmt_b,
                    arate,
                    ach,
                    psize,
                    bsize,
                    hw_ptr,
                    appl_ptr,
                    dly,
                    avail,
                ) = struct.unpack_from("<IIII64s64sII16sII16sIIIIQQII", cdata, off)
                off += node_sz
                result["pcm_nodes"].append({
                    "card_id": cid,
                    "device_id": did,
                    "direction": sdir,
                    "is_compress": bool(is_comp),
                    "id": id_b.split(b"\x00", 1)[0].decode("utf-8", errors="replace"),
                    "name": nm_b.split(b"\x00", 1)[0].decode("utf-8", errors="replace"),
                    "state": st_b.split(b"\x00", 1)[0].decode("utf-8", errors="replace"),
                    "is_running": bool(is_run),
                    "active_format": afmt_b.split(b"\x00", 1)[0].decode("utf-8", errors="replace"),
                    "active_rate": arate,
                    "active_channels": ach,
                    "period_size": psize,
                    "buffer_size": bsize,
                    "delay_frames": dly,
                    "hw_ptr": hw_ptr,
                })

        # 2. PCM Capabilities
        creq = struct.pack("<IIII", card_id, 0xFFFFFFFF, 0x07, api_backend)
        st, cap_data = self.rpc(SOF_NET_OP_PCM_CAPS, creq)
        if st == 0 and len(cap_data) >= 8:
            num_entries, api_u = struct.unpack_from("<II", cap_data, 0)
            off = 8
            cap_sz = 508
            for _ in range(num_entries):
                if off + cap_sz > len(cap_data):
                    break
                (
                    cid,
                    did,
                    sdir,
                    is_comp,
                    api_used,
                    cst,
                    id_b,
                    nm_b,
                    fmask,
                    amask,
                    rmask,
                    rmin,
                    rmax,
                    chmin,
                    chmax,
                    sbmin,
                    sbmax,
                    psmin,
                    psmax,
                    pbmin,
                    pbmax,
                    pmin,
                    pmax,
                    bsmin,
                    bsmax,
                    bbmin,
                    bbmax,
                ) = struct.unpack_from("<IIIIIi64s64sQIIIIIIIIIIIIIIIIII", cap_data, off)
                fmt_s, rate_s = struct.unpack_from("<96s96s", cap_data, off + cap_sz - 192)
                off += cap_sz
                result["pcm_caps"][(cid, did, sdir)] = {
                    "formats_str": fmt_s.split(b"\x00", 1)[0].decode("utf-8", errors="replace"),
                    "rates_str": rate_s.split(b"\x00", 1)[0].decode("utf-8", errors="replace"),
                    "rate_min": rmin,
                    "rate_max": rmax,
                    "channels_min": chmin,
                    "channels_max": chmax,
                    "period_size_min": psmin,
                    "period_size_max": psmax,
                    "buffer_size_min": bsmin,
                    "buffer_size_max": bsmax,
                    "api_used": api_used,
                }

        # 3. DAPM Graph
        dreq = struct.pack("<II", card_id, 0)
        st, gdata = self.rpc(SOF_NET_OP_DAPM_GRAPH, dreq)
        if st == 0 and len(gdata) >= 188:
            cid, cname_b, tplg_b, ncomp, nnodes, npow, nedges, nact_e, ndpcm = (
                struct.unpack_from("<I64s96sIIIIII", gdata, 0)
            )
            result["dapm_hdr"] = {
                "card_id": cid,
                "card_name": cname_b.split(b"\x00", 1)[0].decode("utf-8", errors="replace"),
                "tplg_file": tplg_b.split(b"\x00", 1)[0].decode("utf-8", errors="replace"),
                "num_components": ncomp,
                "num_nodes": nnodes,
                "num_powered_nodes": npow,
                "num_edges": nedges,
                "num_active_edges": nact_e,
                "num_dpcm_links": ndpcm,
            }
            off = 188
            comp_sz = 76
            for _ in range(ncomp):
                if off + comp_sz > len(gdata):
                    break
                nm_b, bias_b, b_lvl, wcnt, pcnt = struct.unpack_from("<48s16sIII", gdata, off)
                off += comp_sz
                result["components"].append({
                    "name": nm_b.split(b"\x00", 1)[0].decode("utf-8", errors="replace"),
                    "bias_str": bias_b.split(b"\x00", 1)[0].decode("utf-8", errors="replace"),
                    "bias_level": b_lvl,
                    "widget_count": wcnt,
                    "powered_count": pcnt,
                })

            dnode_fmt = "<I64s48s24sIIII48siIiHHHHIIII48s48s"
            dnode_sz = struct.calcsize(dnode_fmt)
            for _ in range(nnodes):
                if off + dnode_sz > len(gdata):
                    break
                (
                    nid,
                    nm_b,
                    cmp_b,
                    wt_b,
                    pwr,
                    in_ep,
                    out_ep,
                    sact,
                    snm_b,
                    reg,
                    mask,
                    pipe_id,
                    lcol,
                    lrow,
                    nkctl,
                    _rsvd,
                    kn0,
                    kn1,
                    kn2,
                    kn3,
                    kname0_b,
                    kname1_b,
                ) = struct.unpack_from(dnode_fmt, gdata, off)
                off += dnode_sz
                result["dapm_nodes"].append({
                    "node_id": nid,
                    "name": nm_b.split(b"\x00", 1)[0].decode("utf-8", errors="replace"),
                    "component": cmp_b.split(b"\x00", 1)[0].decode("utf-8", errors="replace"),
                    "widget_type": wt_b.split(b"\x00", 1)[0].decode("utf-8", errors="replace"),
                    "power": pwr,
                    "in_ep": in_ep,
                    "out_ep": out_ep,
                    "stream_active": bool(sact),
                    "stream_name": snm_b.split(b"\x00", 1)[0].decode("utf-8", errors="replace"),
                    "pipeline_id": pipe_id,
                    "layout_col": lcol,
                    "layout_row": lrow,
                    "num_kcontrols": nkctl,
                    "kcontrol_numids": [x for x in (kn0, kn1, kn2, kn3) if x > 0],
                    "kcontrol_names": [
                        x.split(b"\x00", 1)[0].decode("utf-8", errors="replace")
                        for x in (kname0_b, kname1_b)
                        if x.split(b"\x00", 1)[0]
                    ],
                })

            dedge_fmt = "<Iii48s48s32s32s48sII"
            dedge_sz = struct.calcsize(dedge_fmt)
            for _ in range(nedges):
                if off + dedge_sz > len(gdata):
                    break
                eid, sid, did, snm_b, dnm_b, sc_b, dc_b, ctl_b, knid, act = (
                    struct.unpack_from(dedge_fmt, gdata, off)
                )
                off += dedge_sz
                result["dapm_edges"].append({
                    "edge_id": eid,
                    "src_node_id": sid,
                    "dst_node_id": did,
                    "src_name": snm_b.split(b"\x00", 1)[0].decode("utf-8", errors="replace"),
                    "dst_name": dnm_b.split(b"\x00", 1)[0].decode("utf-8", errors="replace"),
                    "control_name": ctl_b.split(b"\x00", 1)[0].decode("utf-8", errors="replace"),
                    "kcontrol_numid": knid,
                    "active": bool(act),
                })

            dpcm_sz = 384
            for _ in range(ndpcm):
                if off + dpcm_sz > len(gdata):
                    break
                fe_b, fdir, st_b, fmt_b, fch, frt, nbe = struct.unpack_from(
                    "<48sI16s16sIII", gdata, off
                )
                off += dpcm_sz
                result["dpcm_links"].append({
                    "fe_name": fe_b.split(b"\x00", 1)[0].decode("utf-8", errors="replace"),
                    "direction": fdir,
                    "state": st_b.split(b"\x00", 1)[0].decode("utf-8", errors="replace"),
                    "format": fmt_b.split(b"\x00", 1)[0].decode("utf-8", errors="replace"),
                    "channels": fch,
                    "rate": frt,
                    "num_backends": nbe,
                })

        # 4. Kcontrol List with Graph Anchors
        kreq = struct.pack("<II", card_id, api_backend)
        st, kdata = self.rpc(SOF_NET_OP_KCTL_LIST, kreq)
        if st == 0 and len(kdata) >= 12:
            _, api_u, nk = struct.unpack_from("<III", kdata, 0)
            off = 12
            khead_fmt = "<III12sIII64sI12sI12sIqqqIiiII"
            khead_sz = struct.calcsize(khead_fmt)
            kanc_fmt = "<Iii64s48siHH"
            kanc_sz = struct.calcsize(kanc_fmt)
            kinfo_sz = khead_sz + 192 + 64 + kanc_sz
            for _ in range(nk):
                if off + kinfo_sz > len(kdata):
                    break
                (
                    cid,
                    numid,
                    iface,
                    if_b,
                    dev,
                    subdev,
                    idx,
                    nm_b,
                    ktype,
                    tstr_b,
                    amask,
                    astr_b,
                    cnt,
                    min_v,
                    max_v,
                    step_v,
                    has_db,
                    db_min,
                    db_step,
                    db_mute,
                    nenums,
                ) = struct.unpack_from(khead_fmt, kdata, off)
                v_off = off + khead_sz + 192
                v0, v1, v2, v3, v4, v5, v6, v7 = struct.unpack_from("<qqqqqqqq", kdata, v_off)
                a_off = v_off + 64
                anc_type, t_nid, t_eid, tw_b, tc_b, pipe_id, lcol, lrow = struct.unpack_from(
                    kanc_fmt, kdata, a_off
                )
                off += kinfo_sz
                result["kcontrols"].append({
                    "numid": numid,
                    "iface_str": if_b.split(b"\x00", 1)[0].decode("utf-8", errors="replace"),
                    "device": dev,
                    "index": idx,
                    "name": nm_b.split(b"\x00", 1)[0].decode("utf-8", errors="replace"),
                    "type": ktype,
                    "type_str": tstr_b.split(b"\x00", 1)[0].decode("utf-8", errors="replace"),
                    "access_str": astr_b.split(b"\x00", 1)[0].decode("utf-8", errors="replace"),
                    "count": cnt,
                    "min_val": min_v,
                    "max_val": max_v,
                    "has_db_scale": bool(has_db),
                    "db_min_cdb": db_min,
                    "db_step_cdb": db_step,
                    "values": [v0, v1, v2, v3, v4, v5, v6, v7][: max(1, min(cnt, 8))],
                    "anchor_type": anc_type,
                    "target_widget": tw_b.split(b"\x00", 1)[0].decode("utf-8", errors="replace"),
                    "target_component": tc_b.split(b"\x00", 1)[0].decode("utf-8", errors="replace"),
                    "pipeline_id": pipe_id,
                    "layout_col": lcol,
                    "layout_row": lrow,
                })

        return result


# -----------------------------------------------------------------------------
# Local Character Device Backend (/dev/sof-dsp0 direct ioctl or debugfs/sysfs)
# -----------------------------------------------------------------------------
def _ioc(dir_bits: int, magic: int, nr: int, size: int) -> int:
    return (dir_bits << 30) | (size << 16) | (magic << 8) | nr


SOF_CDEV_IOC_MAGIC = ord("S")
SOF_IOC_GET_INFO = _ioc(2, SOF_CDEV_IOC_MAGIC, 0x01, 716)
SOF_IOC_PM_CTL = _ioc(3, SOF_CDEV_IOC_MAGIC, 0x02, 32)
SOF_IOC_IPC_LARGE_CFG = _ioc(3, SOF_CDEV_IOC_MAGIC, 0x11, 32)
SOF_IOC_WIN_READ = _ioc(3, SOF_CDEV_IOC_MAGIC, 0x21, 32)


class LocalCdevBackend(RemoteTcpBackend):
    """Direct local character-device (/dev/sof-dsp0) ioctl and debugfs/sysfs backend."""

    def __init__(self, cdev_path: str = "/dev/sof-dsp0", port: int = SOF_NET_DEFAULT_PORT):
        super().__init__("127.0.0.1", port)
        self.cdev_path = cdev_path
        self.cdev_fd: int = -1

    def connect(self) -> bool:
        self.close()
        try:
            self.cdev_fd = os.open(self.cdev_path, os.O_RDWR)
            return True
        except OSError:
            self.cdev_fd = -1
            return False

    def close(self):
        if self.cdev_fd >= 0:
            try:
                os.close(self.cdev_fd)
            except OSError:
                pass
            self.cdev_fd = -1
        super().close()

    def _ioctl_win_read(
        self,
        win_id: int,
        offset: int,
        size: int,
        flags: int = 0,
        slot_type: int = 0,
        slot_index: int = 0,
    ) -> Optional[bytes]:
        if self.cdev_fd < 0:
            return None
        buf = (ctypes.c_uint8 * size)()
        wio = bytearray(
            struct.pack(
                "<IIIIIIQ",
                win_id,
                slot_type,
                slot_index,
                offset,
                size,
                flags,
                ctypes.addressof(buf),
            )
        )
        try:
            fcntl.ioctl(self.cdev_fd, SOF_IOC_WIN_READ, wio, True)
            return bytes(buf)
        except OSError:
            return None

    def _ioctl_large_cfg_get(self, param_id: int, cap: int) -> Optional[bytes]:
        if self.cdev_fd < 0:
            return None
        buf = (ctypes.c_uint8 * cap)()
        cfg = bytearray(
            struct.pack("<IIIIIIQ", 0, 0, param_id, 0, cap, 0, ctypes.addressof(buf))
        )
        try:
            fcntl.ioctl(self.cdev_fd, SOF_IOC_IPC_LARGE_CFG, cfg, True)
            _, _, _, _, out_len, _, _ = struct.unpack("<IIIIIIQ", cfg)
            return bytes(buf)[: min(cap, out_len)]
        except OSError:
            return None

    def pm_control(self, op: int, core_id: int = 0) -> Tuple[int, dict]:
        if self.cdev_fd < 0 and not self.connect():
            return -19, {}
        ctl = bytearray(struct.pack("<IIIIIIII", op, core_id, 0, 0, 0, 0, 0, 0))
        try:
            fcntl.ioctl(self.cdev_fd, SOF_IOC_PM_CTL, ctl, True)
            _, _, fw_st, pwr_st, pm_act, cmask, hold, _ = struct.unpack("<IIIIIIII", ctl)
            return 0, {
                "fw_state": fw_st,
                "dsp_power_state": pwr_st,
                "pm_runtime_active": bool(pm_act),
                "enabled_cores_mask": cmask,
                "fd_holding_d0": bool(hold),
            }
        except OSError as e:
            return -(e.errno or 5), {}

    def set_perf_measurement_state(self, state_val: int) -> int:
        if self.cdev_fd < 0 and not self.connect():
            return -19
        try:
            for val in ((0, state_val) if state_val == 2 else (state_val,)):
                val_buf = (ctypes.c_uint32 * 1)(val)
                cfg = bytearray(
                    struct.pack(
                        "<IIIIIIQ",
                        0,
                        0,
                        IPC4_PERF_MEASUREMENTS_STATE,
                        1,
                        4,
                        0,
                        ctypes.addressof(val_buf),
                    )
                )
                fcntl.ioctl(self.cdev_fd, SOF_IOC_IPC_LARGE_CFG, cfg, True)
            return 0
        except OSError as e:
            return -(e.errno or 5)

    def poll_telemetry(self, force_full: bool = False, wake_dsp: bool = False) -> TelemetrySnapshot:
        if self.cdev_fd < 0 and not self.connect():
            return TelemetrySnapshot(timestamp=time.time(), connected=False)

        temp_hold = False
        if wake_dsp:
            st_q, q_res = self.pm_control(SOF_PM_OP_QUERY)
            if st_q == 0 and not q_res.get("fd_holding_d0", False):
                st_w, _ = self.pm_control(SOF_PM_OP_WAKE_HOLD)
                if st_w == 0:
                    temp_hold = True

        try:
            info_buf = bytearray(716)
            try:
                fcntl.ioctl(self.cdev_fd, SOF_IOC_GET_INFO, info_buf, True)
            except OSError:
                return TelemetrySnapshot(timestamp=time.time(), connected=False)

            full_poll = force_full or (self.poll_counter % 10 == 0)
            self.poll_counter += 1
            wflags = 1 if wake_dsp else 0
            flags_present = 0

            fw_regs = self._ioctl_win_read(SOF_CDEV_WIN_FW_REGS, 0, 512, wflags)
            if fw_regs:
                flags_present |= SOF_TELEM_FL_FW_REGS
            else:
                fw_regs = b"\x00" * 512

            tel2_slot = self._ioctl_win_read(
                SOF_CDEV_WIN_BY_SLOT_TYPE,
                0,
                4096,
                wflags,
                SOF_CDEV_SLOT_TELEMETRY,
                0,
            )
            if not tel2_slot:
                tel2_slot = self._ioctl_win_read(SOF_CDEV_WIN_DEBUG, 4096, 4096, wflags)
            if tel2_slot:
                flags_present |= SOF_TELEM_FL_TELEM_SLOT | SOF_TELEM_FL_TEL2_SLOT
                telem_slot = bytearray(512)
                if sof_tel2_slot_bias(tel2_slot) == 0:
                    telem_slot[:4] = tel2_slot[:4]
                    for c in range(5):
                        telem_slot[4 + c * 32 : 4 + (c + 1) * 32] = tel2_slot[
                            4 + c * 40 : 4 + c * 40 + 32
                        ]
                else:
                    telem_slot[:] = tel2_slot[:512]
                telem_slot_b = bytes(telem_slot)
            else:
                tel2_slot = b"\x00" * 4096
                telem_slot_b = b"\x00" * 512

            pmw2_win3 = self._ioctl_win_read(SOF_CDEV_WIN_PERF, 0, 8192, wflags)
            if not pmw2_win3:
                w3_4k = self._ioctl_win_read(SOF_CDEV_WIN_PERF, 0, 4096, wflags)
                pmw2_win3 = (w3_4k + b"\x00" * 4096) if w3_4k else b""
            if pmw2_win3:
                flags_present |= SOF_TELEM_FL_PERF_WIN3 | SOF_TELEM_FL_PMW2_WIN3
                perf_win3 = pmw2_win3[:4096]
            else:
                pmw2_win3 = b"\x00" * 8192
                perf_win3 = b"\x00" * 4096

            mem_tlv = b""
            pwr_tlv = b""
            ext_perf_tlv = b""
            mod_info_tlv = b""
            if full_poll:
                m_b = self._ioctl_large_cfg_get(IPC4_MEMORY_STATE_INFO_GET, 2048)
                if m_b:
                    mem_tlv = m_b
                    flags_present |= SOF_TELEM_FL_MEM_STATE_TLV
                p_b = self._ioctl_large_cfg_get(IPC4_POWER_STATE_INFO_GET, 256)
                if p_b:
                    pwr_tlv = p_b
                    flags_present |= SOF_TELEM_FL_PWR_STATE_TLV
                e_b = self._ioctl_large_cfg_get(
                    IPC4_EXTENDED_GLOBAL_PERF_DATA, 4096
                ) or self._ioctl_large_cfg_get(IPC4_GLOBAL_PERF_DATA, 4096)
                if e_b:
                    ext_perf_tlv = e_b
                    flags_present |= SOF_TELEM_FL_EXT_PERF_TLV
                mi_b = self._ioctl_large_cfg_get(IPC4_MODULES_INFO_GET, 4096)
                if mi_b:
                    mod_info_tlv = mi_b
                    flags_present |= SOF_TELEM_FL_MODULES_INFO_TLV

            raw_snap = (
                bytes(info_buf)
                + struct.pack("<II", flags_present, 0)
                + fw_regs[:512].ljust(512, b"\x00")
                + telem_slot_b[:512].ljust(512, b"\x00")
                + perf_win3[:4096].ljust(4096, b"\x00")
                + struct.pack("<I", len(mem_tlv))
                + mem_tlv[:2048].ljust(2048, b"\x00")
                + struct.pack("<I", len(pwr_tlv))
                + pwr_tlv[:256].ljust(256, b"\x00")
                + struct.pack("<I", len(ext_perf_tlv))
                + ext_perf_tlv[:4096].ljust(4096, b"\x00")
                + struct.pack("<I", len(mod_info_tlv))
                + mod_info_tlv[:4096].ljust(4096, b"\x00")
                + struct.pack("<I", len(tel2_slot))
                + tel2_slot[:4096].ljust(4096, b"\x00")
                + struct.pack("<I", len(pmw2_win3))
                + pmw2_win3[:8192].ljust(8192, b"\x00")
            )
            return self.parser.parse_composite_snapshot(raw_snap, wire_len=len(raw_snap))
        finally:
            if temp_hold:
                self.pm_control(SOF_PM_OP_RELEASE_HOLD)
