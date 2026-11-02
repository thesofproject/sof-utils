/* SPDX-License-Identifier: BSD-3-Clause
 *
 * Copyright(c) 2026 Intel Corporation. All rights reserved.
 *
 * Sound Open Firmware (SOF) Remote Server Protocol & Shared UAPI Definitions
 */

#ifndef _TESTBENCH_SOF_REMOTE_PROTO_H
#define _TESTBENCH_SOF_REMOTE_PROTO_H

#include <stdint.h>

#define SOF_CDEV_ABI_VERSION		0x00010000U

/*
 * SRAM Memory Window Selectors
 */
#define SOF_CDEV_WIN_FW_REGS		0	/* Window 0: FW Regs (0x0..0xfff) + Inbox (+0x1000) */
#define SOF_CDEV_WIN_OUTBOX		1	/* Window 1: Host Downlink Outbox */
#define SOF_CDEV_WIN_DEBUG		2	/* Window 2: Debug Window (16 x 4 KB pages) */
#define SOF_CDEV_WIN_PERF		3	/* Window 3: Performance Measurements Window (ADSP_PMW) */
#define SOF_CDEV_WIN_BY_SLOT_TYPE	0xFF	/* Select Window 2 slot by slot_type + slot_index */

#define SOF_CDEV_MAX_WINDOWS		4
#define SOF_CDEV_MAX_DEBUG_SLOTS	16

/* Standard IPC4 Window 2 Debug Slot Types */
#define SOF_CDEV_SLOT_UNUSED		0x00000000U
#define SOF_CDEV_SLOT_CRITICAL_LOG	0x54524300U
#define SOF_CDEV_SLOT_DEBUG_LOG		0x474f4c00U
#define SOF_CDEV_SLOT_GDB_STUB		0x42444700U
#define SOF_CDEV_SLOT_TELEMETRY		0x4c455400U
#define SOF_CDEV_SLOT_BROKEN		0x44414544U
#define SOF_CDEV_SLOT_SHELL		0x73686c6cU /* 'shll' Zephyr winstream shell */
#define SOF_CDEV_SLOT_LLEXT_LOAD	0x4c454c44U /* 'LELD' Shell LLEXT load shm */

struct sof_cdev_slot_desc {
	uint32_t slot_index;
	uint32_t res_id;
	uint32_t type;
	uint32_t vma;
	uint32_t win_offset;
	uint32_t bar_offset;
	uint32_t size;
	uint32_t reserved;
};

struct sof_cdev_win_desc {
	uint32_t win_id;
	uint32_t bar_offset;
	uint32_t size;
	uint32_t flags;
};

struct sof_cdev_info {
	uint32_t abi_version;
	uint32_t ipc_type;		/* 0 = IPC3, 1 = IPC4 */
	uint32_t fw_state;		/* enum sof_fw_state (7 = BOOT_COMPLETE) */
	uint32_t dsp_power_state;	/* 0=U0, 1=D0, 2=D1, 3=D2, 4=D3 */
	uint32_t pm_runtime_active;	/* 1 if active (D0) */
	uint32_t num_cores;		/* Total DSP cores on platform */
	uint32_t enabled_cores_mask;	/* Bitmask of currently enabled DSP cores */
	uint32_t max_payload_size;	/* Max IPC mailbox payload size in bytes */
	uint16_t fw_ver_major;
	uint16_t fw_ver_minor;
	uint16_t fw_ver_micro;
	uint16_t fw_ver_build;
	uint32_t hw_ip_version;
	char     platform[32];
	char     fw_filename[64];
	struct sof_cdev_win_desc windows[SOF_CDEV_MAX_WINDOWS];
	struct sof_cdev_slot_desc debug_slots[SOF_CDEV_MAX_DEBUG_SLOTS];
};

/*
 * Power Management Control Commands
 */
#define SOF_PM_OP_QUERY		0
#define SOF_PM_OP_WAKE_HOLD	1
#define SOF_PM_OP_RELEASE_HOLD	2
#define SOF_PM_OP_BOOT_DSP	3
#define SOF_PM_OP_CORE_GET	4
#define SOF_PM_OP_CORE_PUT	5

struct sof_cdev_pm_ctl {
	uint32_t op;
	uint32_t core_id;
	uint32_t fw_state;
	uint32_t dsp_power_state;
	uint32_t pm_runtime_active;
	uint32_t enabled_cores_mask;
	uint32_t fd_holding_d0;
	uint32_t reserved;
};

#define SOF_CDEV_IPC_FL_NO_REPLY	(1U << 0)

#define SOF_CDEV_LARGE_CFG_GET		0
#define SOF_CDEV_LARGE_CFG_SET		1

#define SOF_CDEV_WIN_FL_WAKE_DSP	(1U << 0)

struct sof_cdev_win_info {
	struct sof_cdev_win_desc windows[SOF_CDEV_MAX_WINDOWS];
	struct sof_cdev_slot_desc debug_slots[SOF_CDEV_MAX_DEBUG_SLOTS];
};

#define SOF_LLEXT_MODE_SHELL_SHM	0
#define SOF_LLEXT_MODE_IPC4_DIRECT	1

#define SOF_PROBE_OP_INIT		1
#define SOF_PROBE_OP_DEINIT		2
#define SOF_PROBE_OP_INFO_ACTIVE	3
#define SOF_PROBE_OP_INFO_AVAILABLE	4
#define SOF_PROBE_OP_POINTS_ADD		5
#define SOF_PROBE_OP_POINTS_REMOVE	6
#define SOF_PROBE_OP_READ_BUFFER	7

struct sof_cdev_probe_point {
	uint32_t buffer_id;
	uint32_t purpose;
	uint32_t stream_tag;
	uint32_t reserved;
	char     widget_name[32];
};

struct sof_cdev_ipc_msg {
	uint32_t primary;
	uint32_t extension;
	uint32_t tx_size;
	uint32_t rx_size;
	uint32_t reply_primary;
	uint32_t reply_extension;
	int32_t  ipc_status;
	uint32_t flags;
	uint64_t tx_data_ptr;
	uint64_t rx_data_ptr;
};

struct sof_cdev_large_cfg {
	uint32_t module_id;
	uint32_t instance_id;
	uint32_t param_id;
	uint32_t set_get;
	uint32_t data_size;
	uint32_t flags;
	uint64_t data_ptr;
};

struct sof_cdev_win_io {
	uint32_t win_id;
	uint32_t slot_type;
	uint32_t slot_index;
	uint32_t offset;
	uint32_t size;
	uint32_t flags;
	uint64_t data_ptr;
};

struct sof_cdev_winstream_io {
	uint32_t slot_type;
	uint32_t rx_win_size;
	uint32_t tx_len;
	uint32_t rx_buf_len;
	uint32_t rx_bytes_read;
	uint32_t seq_in_out;
	uint64_t tx_ptr;
	uint64_t rx_ptr;
};

struct sof_cdev_llext_load {
	uint32_t mode;
	uint32_t lib_id;
	uint32_t data_size;
	int32_t  fw_state;
	uint64_t data_ptr;
};

struct sof_cdev_probe_ctl {
	uint32_t op;
	uint32_t stream_tag;
	uint32_t dma_buffer_size;
	uint32_t num_points;
	uint64_t points_ptr;
};

#define SOF_CDEV_EVT_FW_STATE		1
#define SOF_CDEV_EVT_IPC_NOTIFICATION	2

struct sof_cdev_event {
	uint64_t timestamp_ns;
	uint32_t event_type;
	uint32_t fw_state;
	uint32_t primary;
	uint32_t extension;
};

#ifdef __linux__
#include <sys/ioctl.h>
#define SOF_CDEV_IOC_MAGIC	'S'
#define SOF_IOC_GET_INFO	_IOR(SOF_CDEV_IOC_MAGIC, 0x01, struct sof_cdev_info)
#define SOF_IOC_PM_CTL		_IOWR(SOF_CDEV_IOC_MAGIC, 0x02, struct sof_cdev_pm_ctl)
#define SOF_IOC_IPC_TX		_IOWR(SOF_CDEV_IOC_MAGIC, 0x10, struct sof_cdev_ipc_msg)
#define SOF_IOC_IPC_LARGE_CFG	_IOWR(SOF_CDEV_IOC_MAGIC, 0x11, struct sof_cdev_large_cfg)
#define SOF_IOC_WIN_INFO	_IOR(SOF_CDEV_IOC_MAGIC, 0x20, struct sof_cdev_win_info)
#define SOF_IOC_WIN_READ	_IOWR(SOF_CDEV_IOC_MAGIC, 0x21, struct sof_cdev_win_io)
#define SOF_IOC_WIN_WRITE	_IOW(SOF_CDEV_IOC_MAGIC, 0x22, struct sof_cdev_win_io)
#define SOF_IOC_WINSTREAM_IO	_IOWR(SOF_CDEV_IOC_MAGIC, 0x23, struct sof_cdev_winstream_io)
#define SOF_IOC_LLEXT_LOAD	_IOWR(SOF_CDEV_IOC_MAGIC, 0x30, struct sof_cdev_llext_load)
#define SOF_IOC_PROBE_CTL	_IOWR(SOF_CDEV_IOC_MAGIC, 0x40, struct sof_cdev_probe_ctl)
#endif

/*
 * Network Protocol Definitions (TCP port 9998)
 */
#define SOF_NET_MAGIC			0x534F4652U /* 'SOFR' */
#define SOF_NET_VERSION			1
#define SOF_NET_DEFAULT_PORT		9998

/*
 * High bit in sof_net_hdr.opcode indicates SOFZ-compressed payload.
 * Clients set this bit in request opcode (or SOF_TELEM_FL_COMPRESS in
 * telemetry flags) to advertise decompression support; servers set it
 * in reply opcode when the reply payload is SOFZ-compressed.
 */
#define SOF_NET_OP_FLAG_COMPRESSED	0x8000U
#define SOF_NET_OP_MASK			0x7FFFU
#define SOF_NET_COMP_MAGIC		0x5A464F53U /* 'SOFZ' */

struct sof_net_comp_hdr {
	uint32_t magic;		/* SOF_NET_COMP_MAGIC ('SOFZ') */
	uint32_t raw_size;	/* Uncompressed payload byte length */
} __attribute__((packed));

enum sof_net_opcode {
	SOF_NET_OP_PING			= 0x0000,
	SOF_NET_OP_GET_INFO		= 0x0001,
	SOF_NET_OP_PM_CTL		= 0x0002,
	SOF_NET_OP_IPC_TX		= 0x0010,
	SOF_NET_OP_IPC_LARGE_CFG	= 0x0011,
	SOF_NET_OP_WIN_INFO		= 0x0020,
	SOF_NET_OP_WIN_READ		= 0x0021,
	SOF_NET_OP_WIN_WRITE		= 0x0022,
	SOF_NET_OP_WINSTREAM_IO		= 0x0023,
	SOF_NET_OP_TELEM_SNAPSHOT	= 0x0028,
	SOF_NET_OP_WORKLOAD_SIM_CFG	= 0x0029,
	SOF_NET_OP_SCHED_TRACE		= 0x002B,
	SOF_NET_OP_LLEXT_LOAD		= 0x0030,
	SOF_NET_OP_SHELL_EXEC		= 0x0040,
	SOF_NET_OP_PROBE_CTL		= 0x0050,
	SOF_NET_OP_PCM_CTL		= 0x0060,
	/* Extended Audio Card, PCM/Compress Capabilities, DAPM Graph & Kcontrol Opcodes */
	SOF_NET_OP_CARD_LIST		= 0x0070,
	SOF_NET_OP_PCM_CAPS		= 0x0071,
	SOF_NET_OP_DAPM_GRAPH		= 0x0072,
	SOF_NET_OP_COMPR_CTL		= 0x0073,
	SOF_NET_OP_KCTL_LIST		= 0x0074,
	SOF_NET_OP_KCTL_GET		= 0x0075,
	SOF_NET_OP_KCTL_SET		= 0x0076,
};

/* Selectable Userspace Audio API Backend */
enum sof_audio_api {
	SOF_AUDIO_API_AUTO		= 0,
	SOF_AUDIO_API_ALSA		= 1,
	SOF_AUDIO_API_TINYALSA		= 2,
	SOF_AUDIO_API_TINYCOMPRESS	= 3,
};

/* Sample Format IDs across ALSA and TinyALSA */
enum sof_pcm_fmt_id {
	SOF_PCM_FMT_S16_LE		= 0,
	SOF_PCM_FMT_S24_LE		= 1,
	SOF_PCM_FMT_S24_3LE		= 2,
	SOF_PCM_FMT_S32_LE		= 3,
	SOF_PCM_FMT_S8			= 4,
	SOF_PCM_FMT_FLOAT_LE		= 5,
};

struct sof_net_hdr {
	uint32_t magic;
	uint16_t version;
	uint16_t opcode;
	uint32_t req_id;
	uint32_t payload_len;
	int32_t  status;
} __attribute__((packed));

struct sof_net_ipc_tx_req {
	uint32_t primary;
	uint32_t extension;
	uint32_t tx_size;
	uint32_t rx_size;
	uint32_t flags;
} __attribute__((packed));

struct sof_net_ipc_tx_rsp {
	uint32_t reply_primary;
	uint32_t reply_extension;
	int32_t  ipc_status;
	uint32_t rx_size;
} __attribute__((packed));

struct sof_net_large_cfg_req {
	uint32_t module_id;
	uint32_t instance_id;
	uint32_t param_id;
	uint32_t set_get;
	uint32_t data_size;
	uint32_t flags;
} __attribute__((packed));

struct sof_net_win_io_req {
	uint32_t win_id;
	uint32_t slot_type;
	uint32_t slot_index;
	uint32_t offset;
	uint32_t size;
	uint32_t flags;
} __attribute__((packed));

struct sof_net_winstream_req {
	uint32_t slot_type;
	uint32_t rx_win_size;
	uint32_t tx_len;
	uint32_t rx_buf_len;
	uint32_t seq_in;
} __attribute__((packed));

struct sof_net_winstream_rsp {
	uint32_t rx_bytes_read;
	uint32_t seq_out;
} __attribute__((packed));

#define SOF_TELEM_FL_FW_REGS		(1U << 0)
#define SOF_TELEM_FL_TELEM_SLOT		(1U << 1)
#define SOF_TELEM_FL_PERF_WIN3		(1U << 2)
#define SOF_TELEM_FL_MEM_STATE_TLV	(1U << 3)
#define SOF_TELEM_FL_PWR_STATE_TLV	(1U << 4)
#define SOF_TELEM_FL_EXT_PERF_TLV	(1U << 5)
#define SOF_TELEM_FL_MODULES_INFO_TLV	(1U << 6)
#define SOF_TELEM_FL_SYS_HOST_STATS	(1U << 7)
#define SOF_TELEM_FL_TEL2_SLOT		(1U << 8)
#define SOF_TELEM_FL_PMW2_WIN3		(1U << 9)
#define SOF_TELEM_FL_HOT_ONLY		(1U << 10)
#define SOF_TELEM_FL_COMPRESS		(1U << 14)
#define SOF_TELEM_FL_WAKE_DSP		(1U << 15)

/* Hot-zone byte length in MW2 Slot 1 (0x0000..0x023F: ticks + TEL2 hdr + hot_line0 + gw_hot[16]) */
#define SOF_TEL_HOT_ZONE_BYTES		0x0240U

/* Base v1 snapshot size before TEL2/PMW2 extension fields */
#define SOF_NET_TELEM_SNAPSHOT_V1_SIZE	16356U

struct sof_net_telem_req {
	uint32_t query_mask;
	uint32_t flags;
} __attribute__((packed));

struct sof_net_telem_snapshot {
	struct sof_cdev_info dev_info;
	uint32_t flags_present;
	uint32_t active_pcm_streams;
	uint8_t  fw_regs[512];
	uint8_t  telem_slot[512];
	uint8_t  perf_win3[4096];
	uint32_t mem_state_len;
	uint8_t  mem_state_tlv[2048];
	uint32_t pwr_state_len;
	uint8_t  pwr_state_tlv[256];
	uint32_t ext_perf_len;
	uint8_t  ext_perf_tlv[4096];
	uint32_t modules_info_len;
	uint8_t  modules_info_tlv[4096];
	/* Extended TEL2 (MW2 Slot 1, 4 KB) & PMW2 (MW3, 8 KB) windows */
	uint32_t tel2_slot_len;
	uint8_t  tel2_slot[4096];
	uint32_t pmw2_win3_len;
	uint8_t  pmw2_win3[8192];
};

/* -------------------------------------------------------------------------
 * Extended Memory Window 2 ("TEL2") & Memory Window 3 ("PMW2") Wire Format
 * Matches SOF DSP telemetry.h & performance_monitor.h cache-line layout.
 * Note: /sys/kernel/debug/sof/exception skips the first 4-byte separator_1,
 * placing sof_tel_ext_hdr at file offset 0x00FC instead of slot offset 0x0100.
 * -------------------------------------------------------------------------
 */
#define SOF_TEL_EXT_MAGIC		0x324C4554U /* "TEL2" */
#define SOF_TEL_EXT_VERSION		1U
#define SOF_TEL_EXT_HDR_OFFSET		0x0100U
#define SOF_TEL_EXT_DEBUGFS_HDR_OFFSET	0x00FCU
#define SOF_TEL_MAX_CORES		5U
#define SOF_TEL_MAX_SECTIONS		6U
#define SOF_TEL_MAX_HEAPS		6U
#define SOF_TEL_MAX_VMH_BUNDLES		10U
#define SOF_TEL_MAX_VPAGE_ELEMS		16U
#define SOF_TEL_MAX_VREGIONS		16U
#define SOF_TEL_MAX_PIPELINES		16U
#define SOF_TEL_MAX_GATEWAYS		16U
#define SOF_TEL_MAX_THREADS		32U

/* Canonical MW2 Slot 1 section offsets (64-byte cache-line aligned) */
#define SOF_TEL_OFF_HOT_LINE0		0x0140U
#define SOF_TEL_OFF_GW_HOT		0x0180U
#define SOF_TEL_OFF_MEM_SEC		0x0240U
#define SOF_TEL_OFF_VREG_SEC		0x0340U
#define SOF_TEL_OFF_PPL_SEC		0x0540U
#define SOF_TEL_OFF_DAI_SEC		0x06C0U
#define SOF_TEL_OFF_THD_SEC		0x07C0U

enum sof_tel_section_id {
	SOF_TEL_SEC_HOT_ZONE	= 1,
	SOF_TEL_SEC_MEMORY	= 2,
	SOF_TEL_SEC_VREGION	= 3,
	SOF_TEL_SEC_PIPELINES	= 4,
	SOF_TEL_SEC_DAIS	= 5,
	SOF_TEL_SEC_THREADS	= 6,
};

enum sof_tel_heap_type {
	SOF_TEL_HEAP_NONE	= 0,
	SOF_TEL_HEAP_SYS	= 1,
	SOF_TEL_HEAP_SHARED	= 2,
	SOF_TEL_HEAP_LL_USER	= 3,
	SOF_TEL_HEAP_L3_IMR	= 4,
	SOF_TEL_HEAP_DRV_USER	= 5,
};

enum sof_tel_gw_type {
	SOF_TEL_GW_NONE		= 0,
	SOF_TEL_GW_SSP		= 1,
	SOF_TEL_GW_DMIC		= 2,
	SOF_TEL_GW_ALH		= 3,
	SOF_TEL_GW_HDA		= 4,
	SOF_TEL_GW_HOST_DMA	= 5,
	SOF_TEL_GW_OTHER	= 6,
};

struct sof_tel_sec_desc {
	uint8_t  id;
	uint8_t  elem_size;
	uint16_t offset;
	uint16_t max_count;
	uint16_t active_mask;
} __attribute__((packed));

struct sof_tel_ext_hdr {
	uint32_t magic;
	uint8_t  version;
	uint8_t  max_cores;
	uint16_t num_secs;
	uint32_t seq;
	uint32_t timestamp_us;
	struct sof_tel_sec_desc secs[SOF_TEL_MAX_SECTIONS];
} __attribute__((packed));

struct sof_tel_hot_line0 {
	uint16_t ll_overrun_count[SOF_TEL_MAX_CORES];
	uint16_t dp_deadline_misses[SOF_TEL_MAX_CORES];
	uint16_t active_ll_tasks[SOF_TEL_MAX_CORES];
	uint16_t total_xrun_events;
	uint16_t ppl_xrun_count[SOF_TEL_MAX_PIPELINES];
} __attribute__((packed));

struct sof_tel_gw_hot {
	uint32_t total_kb_or_frames;
	uint16_t dma_buf_avail_bytes;
	uint16_t xrun_or_skip_count;
} __attribute__((packed));

struct sof_tel_sram_info {
	uint16_t hp_sram_free_pages;
	uint16_t hp_sram_total_pages;
	uint16_t lp_sram_free_pages;
	uint16_t lp_sram_total_pages;
	uint32_t ebb_power_mask_lo;
	uint32_t ebb_power_mask_hi;
	uint32_t lpsram_ebb_mask;
	uint32_t rsvd;
} __attribute__((packed));

struct sof_tel_heap_info {
	uint16_t heap_type;
	uint16_t alloc_fail_count;
	uint32_t total_bytes;
	uint32_t allocated_bytes;
	uint32_t free_bytes;
	uint32_t max_allocated_bytes;
} __attribute__((packed));

struct sof_tel_vmh_bundle {
	uint8_t  blk_sz_shift;
	uint8_t  rsvd;
	uint16_t total_blocks;
	uint16_t used_blocks;
	uint16_t max_blocks_or_oom;
} __attribute__((packed));

struct sof_tel_fast_get_info {
	uint16_t active_entries;
	uint16_t fail_count;
	uint32_t total_sram_bytes;
	uint32_t peak_sram_bytes;
	uint32_t total_get_calls;
} __attribute__((packed));

struct sof_tel_mem_section {
	struct sof_tel_sram_info sram;
	struct sof_tel_heap_info heaps[SOF_TEL_MAX_HEAPS];
	uint32_t vmh_region_size;
	uint16_t vmh_bundle_count;
	uint16_t vmh_oom_total;
	struct sof_tel_vmh_bundle vmh_bundles[SOF_TEL_MAX_VMH_BUNDLES];
	struct sof_tel_fast_get_info fast_get;
	uint8_t  pad[8];
} __attribute__((packed));

struct sof_tel_valloc_elem {
	uint16_t pages;
	uint16_t vpage;
} __attribute__((packed));

struct sof_tel_vregion_info {
	uint32_t base_va;
	uint16_t total_pages;
	uint8_t  use_count;
	uint8_t  type_and_flags;
	uint32_t lifetime_size_bytes;
	uint32_t lifetime_used_bytes;
	uint32_t interim_alloc_bytes;
	uint16_t interim_free_kb;
	uint16_t lifetime_free_count;
} __attribute__((packed));

struct sof_tel_vregion_section {
	uint16_t total_pages;
	uint16_t free_pages;
	uint16_t min_free_pages;
	uint16_t num_elems_in_use;
	uint32_t alloc_fail_count;
	uint32_t region_base_va;
	struct sof_tel_valloc_elem velems[SOF_TEL_MAX_VPAGE_ELEMS];
	uint16_t active_vregion_count;
	uint16_t total_vregions_created;
	uint8_t  pad[44];
	struct sof_tel_vregion_info vregions[SOF_TEL_MAX_VREGIONS];
} __attribute__((packed));

struct sof_tel_core_sched {
	uint8_t  core_id;
	uint8_t  ll_task_count;
	uint8_t  dp_task_count;
	uint8_t  active_ppl_count;
	uint32_t active_kcps_budget;
	uint32_t rsvd;
} __attribute__((packed));

struct sof_tel_ppl_info {
	uint16_t pipeline_id;
	uint8_t  core;
	uint8_t  priority;
	uint8_t  status;
	uint8_t  comp_count;
	uint16_t attributes;
	uint32_t period_us;
	uint32_t sched_comp_id;
	int32_t  last_xrun_bytes;
} __attribute__((packed));

struct sof_tel_ppl_section {
	struct sof_tel_core_sched core_sched[SOF_TEL_MAX_CORES];
	uint16_t active_ppl_count;
	uint16_t total_comp_count;
	struct sof_tel_ppl_info pipelines[SOF_TEL_MAX_PIPELINES];
} __attribute__((packed));

struct sof_tel_gw_cold {
	uint8_t  gw_type;
	uint8_t  index;
	uint8_t  direction;
	int8_t   dma_chan;
	uint16_t comp_id;
	uint8_t  state;
	uint8_t  group_id;
	uint32_t period_bytes;
	uint32_t dma_buf_size;
} __attribute__((packed));

struct sof_tel_dai_section {
	struct sof_tel_gw_cold gateways[SOF_TEL_MAX_GATEWAYS];
} __attribute__((packed));

#define SOF_TEL_THD_FLG_IDLE	(1U << 0)
#define SOF_TEL_THD_FLG_USER	(1U << 1)
#define SOF_TEL_THD_FLG_DP	(1U << 2)
#define SOF_TEL_THD_FLG_LL	(1U << 3)

struct sof_tel_thread_info {
	uint32_t thread_id;
	uint8_t  core;
	int8_t   prio;
	uint8_t  state;
	uint8_t  cpu_load;
	uint8_t  stack_usage;
	uint8_t  flags;
	uint16_t stack_size_chunks;
	uint32_t total_exec_kcycles;
	char     name[8];
} __attribute__((packed));

struct sof_tel_thread_section {
	uint8_t  core_load[8];
	uint16_t thread_count;
	uint16_t dropped_threads;
	uint32_t sample_seq;
	uint32_t rsvd[4];
	struct sof_tel_thread_info threads[SOF_TEL_MAX_THREADS];
	uint8_t  pad[32];
} __attribute__((packed));

/* Memory Window 3 ("PMW2") constants & structures (8,192 B window) */
#define SOF_TEL_MW3_MAGIC		0x32574D50U /* "PMW2" */
#define SOF_TEL_MW3_VERSION		1U
#define SOF_TEL_MW3_MAX_COMP_ITEMS	88U
#define SOF_TEL_MW3_MAX_IO_ITEMS	128U
#define SOF_TEL_MW3_COMP_PERF_STRIDE	48U
#define SOF_TEL_MW3_MOD_HOT_OFFSET	0x1080U
#define SOF_TEL_MW3_MOD_COLD_OFFSET	0x1200U
#define SOF_TEL_MW3_IO_PERF_OFFSET	0x1800U
#define SOF_TEL_MW3_HDR_OFFSET		0x1FC0U

/* Native 48-byte DSP MW3 component performance record (88 * 48 = 0x1080) */
struct sof_tel_mw3_comp_perf {
	uint32_t resource_id;
	uint32_t power_mode : 1;
	uint32_t rsvd       : 30;
	uint32_t is_removed : 1;
	uint32_t peak_kcps;
	uint32_t avg_kcps;
	uint32_t total_iteration_count;
	uint64_t total_cycles_consumed;
	uint32_t restricted_peak_cycles;
	uint64_t restricted_total_iterations;
	uint64_t restricted_total_cycles;
} __attribute__((packed));

struct sof_tel_mod_hot {
	uint16_t cpc_violation_count;
	uint16_t xrun_count;
} __attribute__((packed));

struct sof_tel_mod_cold {
	uint32_t resource_id;
	uint32_t heap_usage;
	uint32_t heap_high_water_mark;
	uint32_t configured_cpc;
} __attribute__((packed));

struct sof_tel_io_perf_item {
	uint32_t resource_id;
	uint32_t power_mode;
	uint32_t perf_usage;
} __attribute__((packed));

struct sof_tel_mw3_hdr {
	uint32_t magic;
	uint16_t version;
	uint16_t max_comp_entries;
	uint16_t max_io_entries;
	uint16_t mod_hot_offset;
	uint16_t mod_cold_offset;
	uint16_t io_perf_offset;
	uint8_t  rsvd[48];
} __attribute__((packed));

/**
 * Locate the TEL2 header inside a MW2 Slot 1 or /sys/kernel/debug/sof/exception buffer.
 * Returns the byte delta to subtract from canonical slot offsets (0 for raw 4096B slot,
 * 4 for /sys/kernel/debug/sof/exception which skips separator_1, or -1 if not present).
 */
static inline int sof_tel2_slot_bias(const uint8_t *buf, uint32_t len)
{
	if (!buf)
		return -1;
	if (len >= SOF_TEL_EXT_HDR_OFFSET + sizeof(struct sof_tel_ext_hdr)) {
		const struct sof_tel_ext_hdr *h =
			(const struct sof_tel_ext_hdr *)(buf + SOF_TEL_EXT_HDR_OFFSET);
		if (h->magic == SOF_TEL_EXT_MAGIC)
			return 0;
	}
	if (len >= SOF_TEL_EXT_DEBUGFS_HDR_OFFSET + sizeof(struct sof_tel_ext_hdr)) {
		const struct sof_tel_ext_hdr *h =
			(const struct sof_tel_ext_hdr *)(buf + SOF_TEL_EXT_DEBUGFS_HDR_OFFSET);
		if (h->magic == SOF_TEL_EXT_MAGIC)
			return 4;
	}
	return -1;
}

struct sof_net_llext_req {
	uint32_t mode;
	uint32_t lib_id;
	uint32_t data_size;
	uint32_t issue_shell_cmd;
	char     mod_name[64];
} __attribute__((packed));

struct sof_net_llext_rsp {
	uint32_t lib_id;
	int32_t  fw_state;
	char     shell_out[512];
} __attribute__((packed));

struct sof_net_shell_req {
	uint32_t timeout_ms;
	uint32_t cmd_len;
} __attribute__((packed));

struct sof_net_probe_req {
	uint32_t op;
	uint32_t stream_tag;
	uint32_t dma_buffer_size;
	uint32_t num_points;
} __attribute__((packed));

#define SOF_PCM_OP_STATUS		0
#define SOF_PCM_OP_START_TONE		1
#define SOF_PCM_OP_STOP_TONE		2
#define SOF_PCM_OP_PLAY_BUFFER		3
#define SOF_PCM_OP_CAPTURE_BUFFER	4

struct sof_net_pcm_req {
	uint32_t op;
	char     device[32];
	uint32_t rate;
	uint32_t channels;
	uint32_t freq_hz;
	uint32_t duration_ms;
	uint32_t data_size;
	/* Optional extended fields (if payload_len >= sizeof(struct sof_net_pcm_req)) */
	uint32_t api_backend;
	uint32_t format;
	uint32_t period_size;
	uint32_t period_count;
} __attribute__((packed));

struct sof_net_pcm_rsp {
	uint32_t tone_running;
	char     active_device[32];
	uint32_t rate;
	uint32_t channels;
	uint32_t freq_hz;
	uint64_t frames_played;
	uint32_t rms_level;
	uint32_t peak_level;
	uint32_t data_size;
	uint32_t api_used;
	uint32_t format;
	uint32_t xruns;
} __attribute__((packed));

/* -------------------------------------------------------------------------
 * SOF_NET_OP_CARD_LIST: Sound Cards, PCM Streams & Compress Streams
 * -------------------------------------------------------------------------
 */
#define SOF_STREAM_DIR_PLAYBACK		0
#define SOF_STREAM_DIR_CAPTURE		1

struct sof_net_card_list_hdr {
	uint32_t num_cards;
	uint32_t num_pcm_nodes;
} __attribute__((packed));

struct sof_net_card_info {
	uint32_t card_id;
	char     id[32];
	char     driver[32];
	char     name[64];
	char     longname[96];
	char     mixername[64];
	char     tplg_file[96];
	uint32_t num_pcm_streams;
	uint32_t num_compr_streams;
	uint32_t num_kcontrols;
	uint32_t active_streams;
} __attribute__((packed));

struct sof_net_pcm_node_info {
	uint32_t card_id;
	uint32_t device_id;
	uint32_t direction;
	uint32_t is_compress;
	char     id[64];
	char     name[64];
	uint32_t subdevices_count;
	uint32_t subdevices_avail;
	char     state_str[16];
	uint32_t is_running;
	uint32_t owner_pid;
	char     active_format[16];
	uint32_t active_rate;
	uint32_t active_channels;
	uint32_t period_size;
	uint32_t buffer_size;
	uint64_t hw_ptr;
	uint64_t appl_ptr;
	uint32_t delay_frames;
	uint32_t avail_frames;
} __attribute__((packed));

/* -------------------------------------------------------------------------
 * SOF_NET_OP_PCM_CAPS: Per-PCM & Compress Capabilities
 * -------------------------------------------------------------------------
 */
#define SOF_CAPS_DIR_PLAYBACK		(1U << 0)
#define SOF_CAPS_DIR_CAPTURE		(1U << 1)
#define SOF_CAPS_DIR_COMPRESS		(1U << 2)
#define SOF_CAPS_DIR_ALL		0x07U

struct sof_net_pcm_caps_req {
	uint32_t card_id;
	uint32_t device_id;
	uint32_t dir_mask;
	uint32_t api_backend;
} __attribute__((packed));

struct sof_net_pcm_caps_hdr {
	uint32_t num_entries;
	uint32_t api_used;
} __attribute__((packed));

#define SOF_FMTBIT_S8			(1ULL << 0)
#define SOF_FMTBIT_U8			(1ULL << 1)
#define SOF_FMTBIT_S16_LE		(1ULL << 2)
#define SOF_FMTBIT_S16_BE		(1ULL << 3)
#define SOF_FMTBIT_S24_LE		(1ULL << 6)
#define SOF_FMTBIT_S24_BE		(1ULL << 7)
#define SOF_FMTBIT_S32_LE		(1ULL << 10)
#define SOF_FMTBIT_S32_BE		(1ULL << 11)
#define SOF_FMTBIT_FLOAT_LE		(1ULL << 14)
#define SOF_FMTBIT_S24_3LE		(1ULL << 32)
#define SOF_FMTBIT_S24_3BE		(1ULL << 33)

#define SOF_RATEBIT_8000		(1U << 0)
#define SOF_RATEBIT_11025		(1U << 1)
#define SOF_RATEBIT_16000		(1U << 2)
#define SOF_RATEBIT_22050		(1U << 3)
#define SOF_RATEBIT_32000		(1U << 4)
#define SOF_RATEBIT_44100		(1U << 5)
#define SOF_RATEBIT_48000		(1U << 6)
#define SOF_RATEBIT_88200		(1U << 7)
#define SOF_RATEBIT_96000		(1U << 8)
#define SOF_RATEBIT_176400		(1U << 9)
#define SOF_RATEBIT_192000		(1U << 10)

struct sof_net_pcm_caps {
	uint32_t card_id;
	uint32_t device_id;
	uint32_t direction;
	uint32_t is_compress;
	uint32_t api_used;
	int32_t  status;
	char     id[64];
	char     name[64];
	uint64_t formats_mask;
	uint32_t access_mask;
	uint32_t rates_mask;
	uint32_t rate_min;
	uint32_t rate_max;
	uint32_t channels_min;
	uint32_t channels_max;
	uint32_t sample_bits_min;
	uint32_t sample_bits_max;
	uint32_t period_size_min;
	uint32_t period_size_max;
	uint32_t period_bytes_min;
	uint32_t period_bytes_max;
	uint32_t periods_min;
	uint32_t periods_max;
	uint32_t buffer_size_min;
	uint32_t buffer_size_max;
	uint32_t buffer_bytes_min;
	uint32_t buffer_bytes_max;
	uint32_t min_fragment_size;
	uint32_t max_fragment_size;
	uint32_t min_fragments;
	uint32_t max_fragments;
	uint32_t num_codecs;
	uint32_t codecs[16];
	char     formats_str[96];
	char     rates_str[96];
} __attribute__((packed));

/* -------------------------------------------------------------------------
 * SOF_NET_OP_DAPM_GRAPH: DAPM Nodes, Directed Connections, Layout & DPCM Status
 * -------------------------------------------------------------------------
 */
struct sof_net_dapm_req {
	uint32_t card_id;
	uint32_t flags;
} __attribute__((packed));

struct sof_net_dapm_graph_hdr {
	uint32_t card_id;
	char     card_name[64];
	char     tplg_file[96];
	uint32_t num_components;
	uint32_t num_nodes;
	uint32_t num_powered_nodes;
	uint32_t num_edges;
	uint32_t num_active_edges;
	uint32_t num_dpcm_links;
} __attribute__((packed));

struct sof_net_dapm_comp_status {
	char     name[48];
	char     bias_str[16];
	uint32_t bias_level;
	uint32_t widget_count;
	uint32_t powered_count;
} __attribute__((packed));

struct sof_net_dapm_node {
	uint32_t node_id;
	char     name[64];
	char     component[48];
	char     widget_type[24];
	uint32_t power;
	uint32_t in_ep;
	uint32_t out_ep;
	uint32_t stream_active;
	char     stream_name[48];
	int32_t  reg;
	uint32_t mask;
	int32_t  pipeline_id;
	uint16_t layout_col;
	uint16_t layout_row;
	uint16_t num_kcontrols;
	uint16_t reserved;
	uint32_t kcontrol_numids[4];
	char     kcontrol_names[2][48];
} __attribute__((packed));

struct sof_net_dapm_edge {
	uint32_t edge_id;
	int32_t  src_node_id;
	int32_t  dst_node_id;
	char     src_name[48];
	char     dst_name[48];
	char     src_comp[32];
	char     dst_comp[32];
	char     control_name[48];
	uint32_t kcontrol_numid;
	uint32_t active;
} __attribute__((packed));

struct sof_net_dpcm_link_state {
	char     fe_name[48];
	uint32_t direction;
	char     state_str[16];
	char     fe_format[16];
	uint32_t fe_channels;
	uint32_t fe_rate;
	uint32_t num_backends;
	char     be_names[4][32];
	char     be_states[4][16];
	char     be_formats[4][16];
	uint32_t be_channels[4];
	uint32_t be_rates[4];
} __attribute__((packed));

/* -------------------------------------------------------------------------
 * SOF_NET_OP_COMPR_CTL: Compressed PCM Playback & Capture
 * -------------------------------------------------------------------------
 */
#define SOF_COMPR_OP_GET_CAPS		0
#define SOF_COMPR_OP_PLAY_BUFFER	1
#define SOF_COMPR_OP_CAPTURE_BUFFER	2

struct sof_net_compr_req {
	uint32_t op;
	uint32_t card_id;
	uint32_t device_id;
	uint32_t direction;
	uint32_t codec_id;
	uint32_t rate;
	uint32_t channels;
	uint32_t format;
	uint32_t fragment_size;
	uint32_t fragments;
	uint32_t duration_ms;
	uint32_t freq_hz;
	uint32_t data_size;
} __attribute__((packed));

struct sof_net_compr_rsp {
	int32_t  status;
	uint32_t card_id;
	uint32_t device_id;
	uint32_t direction;
	uint32_t codec_id;
	uint32_t fragment_size;
	uint32_t fragments;
	uint32_t min_fragment_size;
	uint32_t max_fragment_size;
	uint32_t num_codecs;
	uint32_t codecs[16];
	uint32_t bytes_transferred;
	uint64_t rendered_samples;
	uint32_t sampling_rate;
	uint32_t data_size;
} __attribute__((packed));

/* -------------------------------------------------------------------------
 * SOF_NET_OP_KCTL_LIST / KCTL_GET / KCTL_SET
 * -------------------------------------------------------------------------
 */
enum sof_kctl_type {
	SOF_KCTL_TYPE_NONE		= 0,
	SOF_KCTL_TYPE_BOOLEAN		= 1,
	SOF_KCTL_TYPE_INTEGER		= 2,
	SOF_KCTL_TYPE_ENUMERATED	= 3,
	SOF_KCTL_TYPE_BYTES		= 4,
	SOF_KCTL_TYPE_IEC958		= 5,
	SOF_KCTL_TYPE_INTEGER64		= 6,
};

enum sof_kctl_anchor_type {
	SOF_KCTL_ANCHOR_CARD		= 0,
	SOF_KCTL_ANCHOR_WIDGET		= 1,
	SOF_KCTL_ANCHOR_EDGE		= 2,
	SOF_KCTL_ANCHOR_PCM_FE		= 3,
	SOF_KCTL_ANCHOR_DAI_BE		= 4,
};

#define SOF_KCTL_ACC_READ		(1U << 0)
#define SOF_KCTL_ACC_WRITE		(1U << 1)
#define SOF_KCTL_ACC_VOLATILE		(1U << 2)
#define SOF_KCTL_ACC_TLV_READ		(1U << 3)
#define SOF_KCTL_ACC_TLV_WRITE		(1U << 4)
#define SOF_KCTL_ACC_INACTIVE		(1U << 5)
#define SOF_KCTL_ACC_LOCK		(1U << 6)

struct sof_net_kctl_list_req {
	uint32_t card_id;
	uint32_t api_backend;
} __attribute__((packed));

struct sof_net_kctl_list_hdr {
	uint32_t card_id;
	uint32_t api_used;
	uint32_t num_kcontrols;
} __attribute__((packed));

struct sof_net_kctl_info {
	uint32_t card_id;
	uint32_t numid;
	uint32_t iface;
	char     iface_str[12];
	uint32_t device;
	uint32_t subdevice;
	uint32_t index;
	char     name[64];
	uint32_t type;
	char     type_str[12];
	uint32_t access_mask;
	char     access_str[12];
	uint32_t count;
	int64_t  min_val;
	int64_t  max_val;
	int64_t  step_val;
	uint32_t has_db_scale;
	int32_t  db_min_cdb;
	int32_t  db_step_cdb;
	uint32_t db_mute;
	uint32_t num_enums;
	char     enum_names[8][24];
	int64_t  values[8];
	uint32_t anchor_type;
	int32_t  target_node_id;
	int32_t  target_edge_id;
	char     target_widget[64];
	char     target_component[48];
	int32_t  pipeline_id;
	uint16_t layout_col;
	uint16_t layout_row;
} __attribute__((packed));

struct sof_net_kctl_io_req {
	uint32_t card_id;
	uint32_t api_backend;
	uint32_t numid;
	uint32_t iface;
	uint32_t device;
	uint32_t index;
	char     name[64];
	uint32_t count;
	int64_t  values[16];
	uint32_t bytes_len;
	uint8_t  bytes_data[512];
} __attribute__((packed));

struct sof_net_kctl_io_rsp {
	int32_t  status;
	uint32_t api_used;
	struct sof_net_kctl_info info;
	uint32_t bytes_len;
	uint8_t  bytes_data[512];
} __attribute__((packed));

/* -------------------------------------------------------------------------
 * SOF_NET_OP_WORKLOAD_SIM_CFG (0x0029)
 * Configure and query accurate CPC/Frequency/SRAM-DRAM workload simulation
 * -------------------------------------------------------------------------
 */
#define SOF_WORKLOAD_SIM_OP_GET			0
#define SOF_WORKLOAD_SIM_OP_SET			1
#define SOF_WORKLOAD_SIM_OP_MOD_SET		2
#define SOF_WORKLOAD_SIM_OP_RESET_OVERRIDES	3

struct sof_net_workload_sim_req {
	uint32_t op;			/* SOF_WORKLOAD_SIM_OP_* */
	uint32_t mode;			/* 0=OFF, 1=VIRTUAL, 2=REALTIME */
	uint32_t default_mem_domain;	/* 0=SRAM, 1=DRAM */
	uint32_t dram_factor_x100;	/* e.g. 160 = 1.60x */
	uint32_t dram_cycles_per_word;	/* e.g. 2 cycles per 32-bit word */
	uint32_t core_freq_hz[8];	/* 0 = unchanged, else target core frequency in Hz */
	/* For SOF_WORKLOAD_SIM_OP_MOD_SET */
	uint32_t target_comp_id;
	uint32_t cpc_override;
	uint32_t mem_domain_override;	/* 0=SRAM, 1=DRAM, 0xFF=AUTO */
} __attribute__((packed));

struct sof_net_workload_core_sim_stat {
	uint32_t active;
	uint32_t freq_hz;
	uint32_t total_util_x100;
	uint32_t ll_deadline_violations;
	uint32_t ll_avg_kcps;
	uint32_t ll_peak_kcps;
	uint64_t ll_avg_exec_ns;
	uint64_t ll_peak_exec_ns;
	uint32_t dp_avg_kcps;
	uint32_t dp_peak_kcps;
	uint64_t dp_avg_exec_ns;
	uint64_t dp_peak_exec_ns;
} __attribute__((packed));

struct sof_net_workload_sim_rsp {
	int32_t  status;
	uint32_t mode;
	uint32_t default_mem_domain;
	uint32_t sram_factor_x100;
	uint32_t dram_factor_x100;
	uint32_t dram_cycles_per_word;
	uint32_t num_cores;
	uint32_t num_overrides;
	struct sof_net_workload_core_sim_stat cores[8];
} __attribute__((packed));

/* -------------------------------------------------------------------------
 * SOF_NET_OP_SCHED_TRACE (0x002B)
 * Query per-iteration LL/DP module scheduling timeline (start/complete/deadline)
 * -------------------------------------------------------------------------
 */
#define SOF_SCHED_TRACE_OP_READ		0
#define SOF_SCHED_TRACE_OP_CLEAR	1

struct sof_net_sched_trace_req {
	uint32_t op;			/* SOF_SCHED_TRACE_OP_READ or CLEAR */
	uint32_t max_events;		/* Max events to return (0 = default 256, max 4096) */
	uint32_t since_seq_id;		/* Return only events with seq_id > since_seq_id */
	uint32_t filter_core_id;	/* 0xFFFFFFFF = all cores, else DSP core 0..7 */
	uint32_t filter_pipe_id;	/* 0xFFFFFFFF = all pipelines, else pipeline ID */
	uint32_t filter_comp_id;	/* 0xFFFFFFFF = all modules, else IPC4 comp_id */
	uint32_t filter_domain;		/* 0xFFFFFFFF = all, 0 = LL_COPY, 1 = DP_SYNC, 2 = DP_RUN */
} __attribute__((packed));

struct sof_net_sched_trace_hdr {
	int32_t  status;
	uint32_t total_recorded_events;	/* Total scheduling events emitted since reset */
	uint32_t avail_in_ring;		/* Events currently stored in trace ring buffer */
	uint32_t returned_events;	/* Number of sof_net_sched_trace_entry items following */
	uint32_t current_ll_tick;	/* Total 1ms LL ticks executed in current run */
	uint32_t num_cores;		/* Configured DSP core count */
	uint32_t sim_mode;		/* 0=HOST-MEASURED, 1=VIRTUAL-SIM, 2=REALTIME-PACED */
	uint32_t core_freq_hz[8];	/* Active core clock frequencies in Hz */
	char     platform_name[32];	/* Platform preset name (e.g. ptl, arl, tgl, nvl) */
} __attribute__((packed));

struct sof_net_sched_trace_entry {
	uint32_t seq_id;
	uint32_t ll_tick_nr;
	uint32_t mod_iter_nr;
	uint32_t comp_id;
	uint16_t pipeline_id;
	uint8_t  core_id;
	uint8_t  event_type;		/* 0=LL_COPY, 1=DP_SYNC, 2=DP_RUN */
	uint8_t  mem_domain;		/* 0=SRAM, 1=DRAM */
	uint8_t  cpc_violation;
	uint16_t period_us;
	uint64_t start_cycles;
	uint64_t complete_cycles;
	uint32_t duration_cycles;
	uint32_t cpc_budget;
	uint64_t start_ns;
	uint64_t complete_ns;
	uint64_t deadline_ns;
	uint64_t wall_start_ns;
	uint64_t wall_complete_ns;
	uint32_t bytes_in;
	uint32_t bytes_out;
	int32_t  ret_status;
	char     widget_name[32];
} __attribute__((packed));

/*
 * Resolve an SSH Host alias (e.g. "damsel-fly", "aphid", "dragon-fly", "spider")
 * from ~/.ssh/config to its HostName when DNS/getaddrinfo does not resolve it.
 */
static inline const char *sof_resolve_ssh_host(const char *host, char *buf, size_t cap)
{
	if (!host || !buf || cap < 2)
		return host;

	const char *home = getenv("HOME");
	if (!home || !home[0])
		return host;

	char path[512];
	snprintf(path, sizeof(path), "%s/.ssh/config", home);
	FILE *fp = fopen(path, "r");
	if (!fp)
		return host;

	bool in_match = false;
	char line[512];
	while (fgets(line, sizeof(line), fp)) {
		char *p = line;
		while (*p == ' ' || *p == '\t')
			p++;
		if (*p == '#' || *p == '\n' || *p == '\r' || *p == '\0')
			continue;

		if (strncasecmp(p, "Host ", 5) == 0 || strncasecmp(p, "Host\t", 5) == 0) {
			in_match = false;
			char *tok_ctx = NULL;
			char *tok = strtok_r(p + 5, " \t\r\n", &tok_ctx);
			while (tok) {
				if (strcmp(tok, host) == 0) {
					in_match = true;
					break;
				}
				tok = strtok_r(NULL, " \t\r\n", &tok_ctx);
			}
		} else if (in_match &&
			   (strncasecmp(p, "HostName ", 9) == 0 ||
			    strncasecmp(p, "HostName\t", 9) == 0)) {
			char *tok_ctx = NULL;
			char *hn = strtok_r(p + 9, " \t\r\n", &tok_ctx);
			if (hn && hn[0]) {
				snprintf(buf, cap, "%s", hn);
				fclose(fp);
				return buf;
			}
		}
	}

	fclose(fp);
	return host;
}

/*
 * SOFZ Wire Payload Compressor & Decompressor
 *
 * Token encoding after 8-byte struct sof_net_comp_hdr ('SOFZ' + raw_size):
 *   0x00 + u16_le(len) : Long zero run (1..65535 zero bytes)
 *   0x01..0x3F         : Short zero run (1..63 zero bytes, length = token)
 *   0x40..0x7F + bytes : Literal run (1..64 bytes, length = (token & 0x3F) + 1)
 *   0x80..0xBF + byte  : Repeat byte run (3..66 copies, length = (token & 0x3F) + 3)
 *   0xC0..0xFF + u16_le: LZ77 back-reference (4..67 bytes, length = (token & 0x3F) + 4,
 *                        distance = 1..65535 bytes back in output buffer)
 *
 * Returns compressed byte size (including 8B header) on success, or 0 if output
 * does not fit in dst_cap.
 */
static inline uint32_t sof_net_compress(const uint8_t *src, uint32_t src_len,
					uint8_t *dst, uint32_t dst_cap)
{
	if (!src || !dst || src_len == 0 || src_len > (16U * 1024U * 1024U) ||
	    dst_cap <= sizeof(struct sof_net_comp_hdr))
		return 0;

	struct sof_net_comp_hdr chdr = {
		.magic    = SOF_NET_COMP_MAGIC,
		.raw_size = src_len,
	};
	memcpy(dst, &chdr, sizeof(chdr));

	uint32_t out = (uint32_t)sizeof(chdr);
	uint32_t htab[1024];
	memset(htab, 0, sizeof(htab));

	uint32_t lit_start = 0;
	uint32_t lit_len = 0;
	uint32_t i = 0;

	while (i < src_len) {
		/* 1. Check zero run */
		if (src[i] == 0) {
			uint32_t zlen = 1;
			while (i + zlen < src_len && src[i + zlen] == 0 && zlen < 65535U)
				zlen++;
			if (zlen >= 3 || (zlen >= 1 && lit_len == 0)) {
				if (lit_len > 0) {
					if (out + 1 + lit_len > dst_cap)
						return 0;
					dst[out++] = (uint8_t)(0x40U | (lit_len - 1U));
					memcpy(dst + out, src + lit_start, lit_len);
					out += lit_len;
					lit_len = 0;
				}
				if (zlen <= 63U) {
					if (out + 1 > dst_cap)
						return 0;
					dst[out++] = (uint8_t)zlen;
				} else {
					if (out + 3 > dst_cap)
						return 0;
					dst[out++] = 0x00U;
					dst[out++] = (uint8_t)(zlen & 0xFFU);
					dst[out++] = (uint8_t)((zlen >> 8) & 0xFFU);
				}
				i += zlen;
				continue;
			}
		}

		/* 2. Check repeat non-zero byte run (3..66) */
		if (src[i] != 0 && i + 3 <= src_len &&
		    src[i] == src[i + 1] && src[i] == src[i + 2]) {
			uint32_t rlen = 3;
			while (i + rlen < src_len && src[i + rlen] == src[i] && rlen < 66U)
				rlen++;
			if (lit_len > 0) {
				if (out + 1 + lit_len > dst_cap)
					return 0;
				dst[out++] = (uint8_t)(0x40U | (lit_len - 1U));
				memcpy(dst + out, src + lit_start, lit_len);
				out += lit_len;
				lit_len = 0;
			}
			if (out + 2 > dst_cap)
				return 0;
			dst[out++] = (uint8_t)(0x80U | (rlen - 3U));
			dst[out++] = src[i];
			i += rlen;
			continue;
		}

		/* 3. Check 4-byte LZ77 back-reference (4..67 bytes) */
		if (i + 4 <= src_len) {
			uint32_t v4;
			memcpy(&v4, src + i, 4);
			uint32_t h = (v4 * 2654435761U) >> 22;
			uint32_t ref = htab[h];
			htab[h] = i + 1U;
			if (ref > 0) {
				uint32_t mpos = ref - 1U;
				uint32_t dist = i - mpos;
				if (dist > 0 && dist <= 65535U &&
				    memcmp(src + mpos, src + i, 4) == 0) {
					uint32_t mlen = 4;
					while (mlen < 67U && i + mlen < src_len &&
					       src[mpos + mlen] == src[i + mlen])
						mlen++;
					if (lit_len > 0) {
						if (out + 1 + lit_len > dst_cap)
							return 0;
						dst[out++] = (uint8_t)(0x40U | (lit_len - 1U));
						memcpy(dst + out, src + lit_start, lit_len);
						out += lit_len;
						lit_len = 0;
					}
					if (out + 3 > dst_cap)
						return 0;
					dst[out++] = (uint8_t)(0xC0U | (mlen - 4U));
					dst[out++] = (uint8_t)(dist & 0xFFU);
					dst[out++] = (uint8_t)((dist >> 8) & 0xFFU);
					i += mlen;
					continue;
				}
			}
		}

		/* 4. Accumulate literal byte */
		if (lit_len == 0)
			lit_start = i;
		lit_len++;
		i++;
		if (lit_len == 64U) {
			if (out + 1 + lit_len > dst_cap)
				return 0;
			dst[out++] = (uint8_t)(0x40U | (lit_len - 1U));
			memcpy(dst + out, src + lit_start, lit_len);
			out += lit_len;
			lit_len = 0;
		}
	}

	if (lit_len > 0) {
		if (out + 1 + lit_len > dst_cap)
			return 0;
		dst[out++] = (uint8_t)(0x40U | (lit_len - 1U));
		memcpy(dst + out, src + lit_start, lit_len);
		out += lit_len;
	}

	return out;
}

static inline int sof_net_decompress(const uint8_t *src, uint32_t src_len,
				     uint8_t **out_buf, uint32_t *out_len)
{
	if (!src || !out_buf || !out_len || src_len < sizeof(struct sof_net_comp_hdr))
		return -1;

	struct sof_net_comp_hdr chdr;
	memcpy(&chdr, src, sizeof(chdr));
	if (chdr.magic != SOF_NET_COMP_MAGIC || chdr.raw_size > (16U * 1024U * 1024U))
		return -1;

	uint8_t *dst = (uint8_t *)calloc(1, chdr.raw_size + 1U);
	if (!dst)
		return -1;

	uint32_t in = (uint32_t)sizeof(chdr);
	uint32_t out = 0;

	while (in < src_len && out < chdr.raw_size) {
		uint8_t tok = src[in++];
		uint8_t tag = (uint8_t)(tok >> 6);

		if (tag == 0) {
			uint32_t zlen = tok;
			if (zlen == 0) {
				if (in + 2 > src_len) {
					free(dst);
					return -1;
				}
				zlen = (uint32_t)src[in] | ((uint32_t)src[in + 1] << 8);
				in += 2;
			}
			if (zlen == 0 || out + zlen > chdr.raw_size) {
				free(dst);
				return -1;
			}
			/* dst is already zero-initialized by calloc */
			out += zlen;
		} else if (tag == 1) {
			uint32_t llen = (uint32_t)(tok & 0x3FU) + 1U;
			if (in + llen > src_len || out + llen > chdr.raw_size) {
				free(dst);
				return -1;
			}
			memcpy(dst + out, src + in, llen);
			in += llen;
			out += llen;
		} else if (tag == 2) {
			uint32_t rlen = (uint32_t)(tok & 0x3FU) + 3U;
			if (in + 1 > src_len || out + rlen > chdr.raw_size) {
				free(dst);
				return -1;
			}
			memset(dst + out, src[in++], rlen);
			out += rlen;
		} else {
			uint32_t mlen = (uint32_t)(tok & 0x3FU) + 4U;
			if (in + 2 > src_len) {
				free(dst);
				return -1;
			}
			uint32_t dist = (uint32_t)src[in] | ((uint32_t)src[in + 1] << 8);
			in += 2;
			if (dist == 0 || dist > out || out + mlen > chdr.raw_size) {
				free(dst);
				return -1;
			}
			uint32_t mpos = out - dist;
			for (uint32_t k = 0; k < mlen; k++)
				dst[out + k] = dst[mpos + k];
			out += mlen;
		}
	}

	if (out != chdr.raw_size) {
		free(dst);
		return -1;
	}

	*out_buf = dst;
	*out_len = chdr.raw_size;
	return 0;
}

/**
 * Reconstruct the 4,096 B legacy perf_win3 (32-byte records with 64-bit
 * iteration counts and bit-1 is_removed) from a PMW2 Window 3 buffer
 * (88 x 48-byte native struct sof_tel_mw3_comp_perf records).
 */
static inline void sof_tel_pmw2_to_legacy_win3(uint8_t *dst_win3,
					       const uint8_t *src_pmw2)
{
	if (!dst_win3 || !src_pmw2)
		return;
	for (uint32_t b = 0; b < 4096U; b++) {
		if (dst_win3[b] != 0)
			return;
	}
	memset(dst_win3, 0, 4096);
	for (uint32_t i = 0; i < SOF_TEL_MW3_MAX_COMP_ITEMS; i++) {
		const struct sof_tel_mw3_comp_perf *src =
			(const struct sof_tel_mw3_comp_perf *)
			(src_pmw2 + i * SOF_TEL_MW3_COMP_PERF_STRIDE);
		struct {
			uint32_t resource_id;
			uint32_t power_mode : 1;
			uint32_t is_removed : 1;
			uint32_t rsvd       : 30;
			uint32_t peak_kcps;
			uint32_t avg_kcps;
			uint64_t total_iteration_count;
			uint64_t total_cycles_consumed;
		} __attribute__((packed)) *dst =
			(void *)(dst_win3 + i * 32U);

		dst->resource_id = src->resource_id;
		dst->power_mode = src->power_mode;
		dst->is_removed = src->is_removed;
		dst->rsvd = 0;
		dst->peak_kcps = src->peak_kcps;
		dst->avg_kcps = src->avg_kcps;
		dst->total_iteration_count =
			(((uint64_t)src->rsvd) << 32) | src->total_iteration_count;
		dst->total_cycles_consumed = src->total_cycles_consumed;
	}
}

#endif /* _TESTBENCH_SOF_REMOTE_PROTO_H */
