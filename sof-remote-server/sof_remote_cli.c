// SPDX-License-Identifier: (GPL-2.0-only OR BSD-3-Clause)
/*
 * sof_remote_cli.c — Command-line client for sof_remote_server
 */

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <netdb.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <arpa/inet.h>

#include "sof_remote_proto.h"

struct system_tick_info_wire {
	uint32_t count;
	uint32_t last_time_elapsed;
	uint32_t max_time_elapsed;
	uint32_t last_ccount;
	uint32_t avg_utilization;
	uint32_t peak_utilization;
	uint32_t peak_utilization_4k;
	uint32_t peak_utilization_8k;
} __attribute__((packed));

struct perf_data_item_comp_wire {
	uint32_t resource_id;
	uint32_t power_mode : 1;
	uint32_t is_removed : 1;
	uint32_t rsvd       : 30;
	uint32_t peak_kcps;
	uint32_t avg_kcps;
	uint64_t total_iteration_count;
	uint64_t total_cycles_consumed;
} __attribute__((packed));

static int connect_server(const char *host, int port)
{
	struct addrinfo hints, *res = NULL, *rp;
	char port_str[16];
	int sock = -1;

	memset(&hints, 0, sizeof(hints));
	hints.ai_family = AF_INET;
	hints.ai_socktype = SOCK_STREAM;
	snprintf(port_str, sizeof(port_str), "%d", port);

	if (getaddrinfo(host, port_str, &hints, &res) != 0) {
		char ssh_host[128];
		const char *alt = sof_resolve_ssh_host(host, ssh_host, sizeof(ssh_host));
		if (alt == host || getaddrinfo(alt, port_str, &hints, &res) != 0) {
			perror("getaddrinfo");
			return -1;
		}
	}

	for (rp = res; rp != NULL; rp = rp->ai_next) {
		sock = socket(rp->ai_family, rp->ai_socktype, rp->ai_protocol);
		if (sock < 0)
			continue;
		if (connect(sock, rp->ai_addr, rp->ai_addrlen) == 0)
			break;
		close(sock);
		sock = -1;
	}

	freeaddrinfo(res);
	return sock;
}

static int read_exact(int fd, void *buf, size_t len)
{
	uint8_t *p = (uint8_t *)buf;
	size_t rem = len;

	while (rem > 0) {
		ssize_t n = recv(fd, p, rem, 0);
		if (n <= 0)
			return -1;
		p += n;
		rem -= (size_t)n;
	}
	return 0;
}

static int write_exact(int fd, const void *buf, size_t len)
{
	const uint8_t *p = (const uint8_t *)buf;
	size_t rem = len;

	while (rem > 0) {
		ssize_t n = send(fd, p, rem, MSG_NOSIGNAL);
		if (n <= 0)
			return -1;
		p += n;
		rem -= (size_t)n;
	}
	return 0;
}

static uint32_t g_last_wire_bytes;
static uint32_t g_last_raw_bytes;
static bool g_server_no_compress;

static int rpc_call(int sock, uint16_t opcode, const void *req_payload,
		    uint32_t req_len, struct sof_net_hdr *rsp_hdr,
		    void **rsp_payload)
{
	static uint32_t s_req_id = 1;
	uint16_t wire_op = g_server_no_compress ? opcode :
			   (uint16_t)(opcode | SOF_NET_OP_FLAG_COMPRESSED);
	struct sof_net_hdr hdr = {
		.magic       = SOF_NET_MAGIC,
		.version     = SOF_NET_VERSION,
		.opcode      = wire_op,
		.req_id      = s_req_id++,
		.payload_len = req_payload ? req_len : 0,
		.status      = 0,
	};

	*rsp_payload = NULL;
	if (write_exact(sock, &hdr, sizeof(hdr)) < 0)
		return -1;
	if (req_payload && req_len > 0) {
		if (write_exact(sock, req_payload, req_len) < 0)
			return -1;
	}

	if (read_exact(sock, rsp_hdr, sizeof(*rsp_hdr)) < 0)
		return -1;

	if (rsp_hdr->status == -ENOSYS && !g_server_no_compress && rsp_hdr->payload_len == 0) {
		g_server_no_compress = true;
		return rpc_call(sock, opcode, req_payload, req_len, rsp_hdr, rsp_payload);
	}

	g_last_wire_bytes = rsp_hdr->payload_len;
	g_last_raw_bytes = rsp_hdr->payload_len;

	if (rsp_hdr->payload_len > 0) {
		uint8_t *buf = calloc(1, rsp_hdr->payload_len + 1);
		if (!buf)
			return -1;
		if (read_exact(sock, buf, rsp_hdr->payload_len) < 0) {
			free(buf);
			return -1;
		}
		if ((rsp_hdr->opcode & SOF_NET_OP_FLAG_COMPRESSED) &&
		    rsp_hdr->payload_len >= sizeof(struct sof_net_comp_hdr)) {
			uint8_t *dbuf = NULL;
			uint32_t dlen = 0;
			if (sof_net_decompress(buf, rsp_hdr->payload_len, &dbuf, &dlen) != 0) {
				free(buf);
				return -1;
			}
			free(buf);
			buf = dbuf;
			rsp_hdr->payload_len = dlen;
			rsp_hdr->opcode &= SOF_NET_OP_MASK;
			g_last_raw_bytes = dlen;
			if (rsp_hdr->opcode == SOF_NET_OP_TELEM_SNAPSHOT &&
			    dlen >= sizeof(struct sof_net_telem_snapshot)) {
				struct sof_net_telem_snapshot *sn =
					(struct sof_net_telem_snapshot *)buf;
				if (sn->flags_present & SOF_TELEM_FL_PMW2_WIN3)
					sof_tel_pmw2_to_legacy_win3(sn->perf_win3, sn->pmw2_win3);
			}
		}
		*rsp_payload = buf;
	} else {
		rsp_hdr->opcode &= SOF_NET_OP_MASK;
	}
	return 0;
}

static void print_slot_type(uint32_t type, char *out, size_t cap)
{
	if (type == 0) {
		snprintf(out, cap, "UNUSED");
	} else if ((type & 0xffffff00U) == SOF_CDEV_SLOT_CRITICAL_LOG) {
		snprintf(out, cap, "CRITICAL_LOG(c%u)", type & 0xff);
	} else if ((type & 0xffffff00U) == SOF_CDEV_SLOT_DEBUG_LOG) {
		snprintf(out, cap, "DEBUG_LOG(c%u)", type & 0xff);
	} else if (type == SOF_CDEV_SLOT_TELEMETRY) {
		snprintf(out, cap, "TELEMETRY");
	} else if (type == SOF_CDEV_SLOT_GDB_STUB) {
		snprintf(out, cap, "GDB_STUB");
	} else if (type == SOF_CDEV_SLOT_SHELL) {
		snprintf(out, cap, "SHELL('shll')");
	} else if (type == SOF_CDEV_SLOT_LLEXT_LOAD) {
		snprintf(out, cap, "LLEXT_LOAD('LELD')");
	} else {
		snprintf(out, cap, "0x%08x", type);
	}
}

static void print_info(const struct sof_cdev_info *info)
{
	printf("=== SOF DSP Device Info ===\n");
	printf("Platform:         %s\n", info->platform);
	printf("Firmware File:    %s\n", info->fw_filename);
	printf("Firmware Version: v%u.%u.%u.%u\n",
	       info->fw_ver_major, info->fw_ver_minor,
	       info->fw_ver_micro, info->fw_ver_build);
	printf("IPC Type:         %s (%u)\n",
	       info->ipc_type == 1 ? "IPC4" : "IPC3", info->ipc_type);
	printf("FW State:         %u (%s)\n", info->fw_state,
	       (info->fw_state == 6 || info->fw_state == 7) ? "BOOT_COMPLETE" :
	       info->fw_state == 8 ? "CRASHED" :
	       info->fw_state == 0 ? "NOT_STARTED" : "OTHER");
	printf("Runtime PM:       %s (DSP Power State: %u)\n",
	       info->pm_runtime_active ? "ACTIVE (D0)" : "SUSPENDED (D3)",
	       info->dsp_power_state);
	printf("DSP Cores:        %u total (enabled_mask=0x%x)\n",
	       info->num_cores, info->enabled_cores_mask);
	printf("Max IPC Payload:  %u bytes\n\n", info->max_payload_size);

	printf("=== SRAM Memory Windows ===\n");
	for (int i = 0; i < SOF_CDEV_MAX_WINDOWS; i++) {
		printf("  Window %d: BAR offset 0x%06x, size %6u bytes [%s]\n",
		       info->windows[i].win_id,
		       info->windows[i].bar_offset,
		       info->windows[i].size,
		       info->windows[i].flags ? "MAPPED" : "UNMAPPED");
	}

	printf("\n=== Window 2 Debug Slots ===\n");
	for (int i = 0; i < SOF_CDEV_MAX_DEBUG_SLOTS; i++) {
		const struct sof_cdev_slot_desc *s = &info->debug_slots[i];
		if (!s->type)
			continue;
		char tname[32];
		print_slot_type(s->type, tname, sizeof(tname));
		printf("  Slot %2u: %-18s res_id=0x%08x vma=0x%08x bar_off=0x%06x size=%u\n",
		       s->slot_index, tname, s->res_id, s->vma, s->bar_offset, s->size);
	}
}

int main(int argc, char **argv)
{
	const char *host = "127.0.0.1";
	int port = SOF_NET_DEFAULT_PORT;
	int opt;

	while ((opt = getopt(argc, argv, "+H:p:h")) != -1) {
		switch (opt) {
		case 'H':
			host = optarg;
			break;
		case 'p':
			port = atoi(optarg);
			break;
		default:
			fprintf(stderr,
				"Usage: %s [-H host] [-p port] <command> [args...]\n"
				"Commands:\n"
				"  info                                 Show DSP & window info\n"
				"  telem                                Fetch composite telemetry snapshot\n"
				"  pm <query|wake|release|boot>         Control DSP power state\n"
				"  ipc <primary_hex> <ext_hex>          Send atomic IPC4 message\n"
				"  large-cfg <param_id>                 Query BaseFW LARGE_CONFIG_GET\n"
				"  win-read <win_id> <offset> <bytes>   Read from SRAM window\n"
				"  llext-load <file.ri> [lib_id] [mode] Load LLEXT module remotely\n"
				"  shell \"<cmd>\"                        Execute DSP or host: shell command\n"
				"  probes [active|avail]                Query SOF probe points\n"
				"  cards                                List sound cards, PCMs & Compress streams\n"
				"  pcm-caps [card] [dev] [alsa|tinyalsa] Show per-PCM & Compress capabilities\n"
				"  dapm [card] [out.dot]                Show DAPM graph, connections & DPCM status\n"
				"  kctl list [card] [alsa|tinyalsa]     List kcontrols, values & graph anchors\n"
				"  kctl get <numid|name> [alsa|tinyalsa] Read kcontrol value & graph anchor\n"
				"  kctl set <numid|name> <v0> [v1] [api] Set kcontrol value(s) via ALSA or TinyALSA\n"
				"  pcm <status|start-tone|stop-tone|play|capture> [dev] [freq_or_ms] [alsa|tinyalsa] [S16_LE|S24_LE|S32_LE]\n"
				"  compr <caps|play|capture> [card] [dev] [ms]\n",
				argv[0]);
			return 1;
		}
	}

	if (optind >= argc) {
		fprintf(stderr, "Error: missing command (try -h)\n");
		return 1;
	}

	const char *cmd = argv[optind++];
	int sock = connect_server(host, port);
	if (sock < 0) {
		fprintf(stderr, "Failed to connect to %s:%d\n", host, port);
		return 1;
	}

	struct sof_net_hdr rhdr;
	void *rpayload = NULL;

	if (strcmp(cmd, "info") == 0) {
		if (rpc_call(sock, SOF_NET_OP_GET_INFO, NULL, 0, &rhdr, &rpayload) == 0 &&
		    rhdr.status == 0 && rpayload) {
			print_info((const struct sof_cdev_info *)rpayload);
		} else {
			fprintf(stderr, "OP_GET_INFO failed: status=%d\n", rhdr.status);
		}
	} else if (strcmp(cmd, "pm") == 0) {
		const char *sub = (optind < argc) ? argv[optind++] : "query";
		struct sof_cdev_pm_ctl ctl;
		memset(&ctl, 0, sizeof(ctl));
		if (strcmp(sub, "wake") == 0 || strcmp(sub, "hold") == 0)
			ctl.op = SOF_PM_OP_WAKE_HOLD;
		else if (strcmp(sub, "release") == 0)
			ctl.op = SOF_PM_OP_RELEASE_HOLD;
		else if (strcmp(sub, "boot") == 0)
			ctl.op = SOF_PM_OP_BOOT_DSP;
		else
			ctl.op = SOF_PM_OP_QUERY;

		if (rpc_call(sock, SOF_NET_OP_PM_CTL, &ctl, sizeof(ctl), &rhdr, &rpayload) == 0 &&
		    rpayload) {
			struct sof_cdev_pm_ctl *out = (struct sof_cdev_pm_ctl *)rpayload;
			printf("PM Result (status=%d): fw_state=%u, dsp_power=%u, runtime_active=%u, cores_mask=0x%x\n",
			       rhdr.status, out->fw_state, out->dsp_power_state,
			       out->pm_runtime_active, out->enabled_cores_mask);
		}
	} else if (strcmp(cmd, "telem") == 0) {
		bool hot_req = (optind < argc &&
				(strcmp(argv[optind], "--hot") == 0 ||
				 strcmp(argv[optind], "hot") == 0));
		struct sof_net_telem_req req = {
			.query_mask = SOF_TELEM_FL_FW_REGS |
				      SOF_TELEM_FL_TELEM_SLOT |
				      SOF_TELEM_FL_PERF_WIN3 |
				      SOF_TELEM_FL_TEL2_SLOT |
				      SOF_TELEM_FL_PMW2_WIN3 |
				      (hot_req ? SOF_TELEM_FL_HOT_ONLY :
				       (SOF_TELEM_FL_MEM_STATE_TLV |
					SOF_TELEM_FL_PWR_STATE_TLV |
					SOF_TELEM_FL_EXT_PERF_TLV |
					SOF_TELEM_FL_MODULES_INFO_TLV)),
			.flags = SOF_TELEM_FL_WAKE_DSP | SOF_TELEM_FL_COMPRESS |
				 (hot_req ? SOF_TELEM_FL_HOT_ONLY : 0),
		};
		if (rpc_call(sock, SOF_NET_OP_TELEM_SNAPSHOT, &req, sizeof(req),
			     &rhdr, &rpayload) == 0 && rhdr.status == 0 && rpayload) {
			const struct sof_net_telem_snapshot *s = (const struct sof_net_telem_snapshot *)rpayload;
			double saved_pct = g_last_raw_bytes > 0 ?
					   100.0 * (1.0 - (double)g_last_wire_bytes /
							  (double)g_last_raw_bytes) : 0.0;
			print_info(&s->dev_info);
			printf("\n=== Composite Telemetry Blocks (flags=0x%x, wire=%uB / raw=%uB, %.1f%% saved) ===\n",
			       s->flags_present, g_last_wire_bytes, g_last_raw_bytes, saved_pct);
			if (s->flags_present & SOF_TELEM_FL_FW_REGS) {
				const uint32_t *r32 = (const uint32_t *)s->fw_regs;
				printf("  [Window 0 FW Regs] fw_status=0x%08x lec=0x%08x fps=0x%08x lnec=0x%08x ltr=0x%08x\n",
				       r32[0], r32[1], r32[2], r32[3], r32[4]);
			}
			if (s->flags_present & SOF_TELEM_FL_TELEM_SLOT) {
				uint32_t sep = *(const uint32_t *)s->telem_slot;
				printf("  [Window 2 Telemetry Slot] separator=0x%08x\n", sep);
				const struct system_tick_info_wire *ticks =
					(const struct system_tick_info_wire *)(s->telem_slot + 4);
				for (uint32_t c = 0; c < s->dev_info.num_cores &&
						     4U + (size_t)(c + 1) * sizeof(*ticks) <=
						     sizeof(s->telem_slot); c++) {
					printf("    Core %u: ticks=%u, last_elapsed=%u, max_elapsed=%u, avg_kcps=%u, peak_kcps=%u\n",
					       c, ticks[c].count, ticks[c].last_time_elapsed,
					       ticks[c].max_time_elapsed,
					       ticks[c].avg_utilization, ticks[c].peak_utilization);
				}
			}
			if (rhdr.payload_len >= sizeof(struct sof_net_telem_snapshot) &&
			    (s->flags_present & SOF_TELEM_FL_TEL2_SLOT) &&
			    s->tel2_slot_len > 0) {
				int bias = sof_tel2_slot_bias(s->tel2_slot, s->tel2_slot_len);
				if (bias >= 0) {
					uint32_t t2_len = s->tel2_slot_len <= sizeof(s->tel2_slot) ?
							  s->tel2_slot_len : (uint32_t)sizeof(s->tel2_slot);
					const uint8_t *tbase = s->tel2_slot - bias;
					const struct sof_tel_ext_hdr *eh =
						(const struct sof_tel_ext_hdr *)
						(tbase + SOF_TEL_EXT_HDR_OFFSET);
					const struct sof_tel_hot_line0 *h0 =
						(const struct sof_tel_hot_line0 *)
						(tbase + SOF_TEL_OFF_HOT_LINE0);
					const struct sof_tel_gw_hot *gwh =
						(const struct sof_tel_gw_hot *)
						(tbase + SOF_TEL_OFF_GW_HOT);
					static const char * const hnames[] = {
						"NONE", "SYS", "SHARED", "LL_USER", "L3_IMR", "DRV_USER"
					};
					static const char * const gwnames[] = {
						"NONE", "SSP", "DMIC", "ALH", "HDA", "HOST_DMA", "OTHER"
					};
					if (SOF_TEL_OFF_THD_SEC - (uint32_t)bias +
					    sizeof(struct sof_tel_thread_section) <= t2_len) {
						const struct sof_tel_mem_section *ms =
							(const struct sof_tel_mem_section *)
							(tbase + SOF_TEL_OFF_MEM_SEC);
						const struct sof_tel_vregion_section *vs =
							(const struct sof_tel_vregion_section *)
							(tbase + SOF_TEL_OFF_VREG_SEC);
						const struct sof_tel_ppl_section *ps =
							(const struct sof_tel_ppl_section *)
							(tbase + SOF_TEL_OFF_PPL_SEC);
						const struct sof_tel_dai_section *ds =
							(const struct sof_tel_dai_section *)
							(tbase + SOF_TEL_OFF_DAI_SEC);
						const struct sof_tel_thread_section *ts =
							(const struct sof_tel_thread_section *)
							(tbase + SOF_TEL_OFF_THD_SEC);
						printf("  [MW2 Slot 1 'TEL2' v%u] secs=%u seq=%u ts=%u us | xruns=%u | HP SRAM free=%u/%u pages | active_ppls=%u comps=%u | threads=%u\n",
						       eh->version, eh->num_secs, eh->seq, eh->timestamp_us,
						       h0->total_xrun_events,
						       ms->sram.hp_sram_free_pages,
						       ms->sram.hp_sram_total_pages,
						       ps->active_ppl_count, ps->total_comp_count,
						       ts->thread_count);
						for (int h = 0; h < (int)SOF_TEL_MAX_HEAPS; h++) {
							const struct sof_tel_heap_info *hp = &ms->heaps[h];
							if (!hp->heap_type && !hp->total_bytes)
								continue;
							const char *hn = hp->heap_type < 6 ?
									 hnames[hp->heap_type] : "OTHER";
							printf("    Heap[%d] %-8s alloc=%7uB free=%8uB peak=%7uB total=%8uB fails=%u\n",
							       h, hn, hp->allocated_bytes, hp->free_bytes,
							       hp->max_allocated_bytes, hp->total_bytes,
							       hp->alloc_fail_count);
						}
						if (vs->total_pages > 0 || vs->active_vregion_count > 0) {
							printf("    Vregions: vpages free=%u/%u (min_free=%u, base_va=0x%08x, active=%u/%u)\n",
							       vs->free_pages, vs->total_pages,
							       vs->min_free_pages, vs->region_base_va,
							       vs->active_vregion_count, vs->total_vregions_created);
						}
						for (int p = 0; p < (int)ps->active_ppl_count &&
								p < (int)SOF_TEL_MAX_PIPELINES; p++) {
							const struct sof_tel_ppl_info *pe = &ps->pipelines[p];
							printf("    PPL[%d] id=%u core=%u prio=%u status=%u comps=%u period=%u us sched_comp=0x%x\n",
							       p, pe->pipeline_id, pe->core, pe->priority,
							       pe->status, pe->comp_count, pe->period_us,
							       pe->sched_comp_id);
						}
						for (int g = 0; g < (int)SOF_TEL_MAX_GATEWAYS; g++) {
							const struct sof_tel_gw_cold *dc = &ds->gateways[g];
							const struct sof_tel_gw_hot *gh = &gwh[g];
							if (!dc->gw_type)
								continue;
							const char *gname = (dc->gw_type < 7) ?
									    gwnames[dc->gw_type] : "OTHER";
							printf("    Gateway[%d] %-8s idx=%u dir=%s chan=%d comp=0x%04x state=%u period=%uB buf=%uB | hot: xfer=%uKB avail=%uB xruns=%u\n",
							       g, gname, dc->index,
							       dc->direction ? "CAP" : "PLAY",
							       dc->dma_chan, dc->comp_id, dc->state,
							       dc->period_bytes, dc->dma_buf_size,
							       gh->total_kb_or_frames,
							       gh->dma_buf_avail_bytes,
							       gh->xrun_or_skip_count);
						}
						uint8_t n_thds = ts->thread_count <= SOF_TEL_MAX_THREADS ?
								 ts->thread_count : SOF_TEL_MAX_THREADS;
						for (uint8_t t = 0; t < n_thds; t++) {
							const struct sof_tel_thread_info *te = &ts->threads[t];
							char tname[9];
							memcpy(tname, te->name, 8);
							tname[8] = '\0';
							printf("    Thd[%2u] %-8s id=0x%08x core=%u prio=%3d cpu=%5.1f%% stack_hwm=%5.1f%% (%4uB) kcyc=%u\n",
							       t, tname, te->thread_id, te->core, te->prio,
							       (te->cpu_load * 100.0) / 255.0,
							       (te->stack_usage * 100.0) / 255.0,
							       (unsigned int)te->stack_size_chunks * 64U,
							       te->total_exec_kcycles);
						}
					} else {
						printf("  [MW2 Slot 1 'TEL2' v%u HOT_ONLY] seq=%u ts=%u us | xruns=%u | LL tasks=[%u,%u,%u,%u,%u]\n",
						       eh->version, eh->seq, eh->timestamp_us,
						       h0->total_xrun_events,
						       h0->active_ll_tasks[0], h0->active_ll_tasks[1],
						       h0->active_ll_tasks[2], h0->active_ll_tasks[3],
						       h0->active_ll_tasks[4]);
						for (int g = 0; g < (int)SOF_TEL_MAX_GATEWAYS; g++) {
							const struct sof_tel_gw_hot *gh = &gwh[g];
							if (!gh->total_kb_or_frames &&
							    !gh->dma_buf_avail_bytes &&
							    !gh->xrun_or_skip_count)
								continue;
							printf("    Gateway[%d] hot: xfer=%uKB avail=%uB xruns=%u\n",
							       g, gh->total_kb_or_frames,
							       gh->dma_buf_avail_bytes,
							       gh->xrun_or_skip_count);
						}
					}
				}
			}
			if (rhdr.payload_len >= sizeof(struct sof_net_telem_snapshot) &&
			    (s->flags_present & SOF_TELEM_FL_PMW2_WIN3) &&
			    s->pmw2_win3_len >= SOF_TEL_MW3_HDR_OFFSET + sizeof(struct sof_tel_mw3_hdr)) {
				uint32_t w3_avail = s->pmw2_win3_len <= sizeof(s->pmw2_win3) ?
						    s->pmw2_win3_len :
						    (uint32_t)sizeof(s->pmw2_win3);
				const struct sof_tel_mw3_hdr *mh =
					(const struct sof_tel_mw3_hdr *)
					(s->pmw2_win3 + SOF_TEL_MW3_HDR_OFFSET);
				if (mh->magic == SOF_TEL_MW3_MAGIC) {
					uint32_t max_comp = mh->max_comp_entries <= SOF_TEL_MW3_MAX_COMP_ITEMS ?
							    mh->max_comp_entries : SOF_TEL_MW3_MAX_COMP_ITEMS;
					bool has_hot = (size_t)mh->mod_hot_offset +
						       (size_t)max_comp * sizeof(struct sof_tel_mod_hot) <= w3_avail;
					bool has_cold = (size_t)mh->mod_cold_offset +
							(size_t)max_comp * sizeof(struct sof_tel_mod_cold) <= w3_avail;

					printf("  [MW3 'PMW2' v%u] max_comp=%u max_io=%u (mod_hot=0x%x, mod_cold=0x%x, io=0x%x)\n",
					       mh->version, mh->max_comp_entries,
					       mh->max_io_entries, mh->mod_hot_offset,
					       mh->mod_cold_offset, mh->io_perf_offset);
					if (has_hot) {
						const struct sof_tel_mod_hot *mhot =
							(const struct sof_tel_mod_hot *)
							(s->pmw2_win3 + mh->mod_hot_offset);
						const struct sof_tel_mod_cold *mcold = has_cold ?
							(const struct sof_tel_mod_cold *)
							(s->pmw2_win3 + mh->mod_cold_offset) : NULL;

						for (int m = 0; m < (int)max_comp; m++) {
							uint32_t res_id = mcold ? mcold[m].resource_id : 0;
							uint32_t heap_u = mcold ? mcold[m].heap_usage : 0;
							uint32_t heap_hwm = mcold ? mcold[m].heap_high_water_mark : 0;
							uint32_t cfg_cpc = mcold ? mcold[m].configured_cpc : 0;

							if (!res_id &&
							    (size_t)(m + 1) * SOF_TEL_MW3_COMP_PERF_STRIDE <= mh->mod_hot_offset &&
							    (size_t)(m + 1) * SOF_TEL_MW3_COMP_PERF_STRIDE <= w3_avail) {
								const struct sof_tel_mw3_comp_perf *cp =
									(const struct sof_tel_mw3_comp_perf *)s->pmw2_win3;

								res_id = cp[m].resource_id;
							}
							if ((!res_id || res_id == 0xFFFFFFFFU) &&
							    !heap_u && !cfg_cpc &&
							    !mhot[m].cpc_violation_count &&
							    !mhot[m].xrun_count)
								continue;
							printf("    Mod[%2d] res_id=0x%08x (mod=0x%04x,inst=%u) heap=%uB hwm=%uB cpc=%u viol=%u xruns=%u\n",
							       m, res_id,
							       res_id & 0xFFFFU,
							       (res_id >> 16) & 0xFFFFU,
							       heap_u, heap_hwm, cfg_cpc,
							       mhot[m].cpc_violation_count,
							       mhot[m].xrun_count);
						}
					}
				}
			}
			if (s->flags_present & SOF_TELEM_FL_MEM_STATE_TLV)
				printf("  [BaseFW Memory State TLV] %u bytes received\n", s->mem_state_len);
			if (s->flags_present & SOF_TELEM_FL_PWR_STATE_TLV)
				printf("  [BaseFW Power State TLV]  %u bytes received\n", s->pwr_state_len);
			if (s->flags_present & SOF_TELEM_FL_EXT_PERF_TLV)
				printf("  [BaseFW Global Perf TLV]  %u bytes received\n", s->ext_perf_len);
			if (s->flags_present & SOF_TELEM_FL_MODULES_INFO_TLV)
				printf("  [BaseFW Modules Info TLV] %u bytes received\n", s->modules_info_len);
		} else {
			fprintf(stderr, "OP_TELEM_SNAPSHOT failed: status=%d\n", rhdr.status);
		}
	} else if (strcmp(cmd, "ipc") == 0) {
		if (optind + 2 > argc) {
			fprintf(stderr, "Usage: ipc <primary_hex> <ext_hex>\n");
			return 1;
		}
		struct sof_net_ipc_tx_req req = {
			.primary   = (uint32_t)strtoul(argv[optind], NULL, 0),
			.extension = (uint32_t)strtoul(argv[optind + 1], NULL, 0),
			.tx_size   = 0,
			.rx_size   = 4096,
			.flags     = 0,
		};
		if (rpc_call(sock, SOF_NET_OP_IPC_TX, &req, sizeof(req), &rhdr, &rpayload) == 0 &&
		    rpayload) {
			struct sof_net_ipc_tx_rsp *rsp = (struct sof_net_ipc_tx_rsp *)rpayload;
			printf("IPC Reply (ioctl_status=%d): primary=0x%08x ext=0x%08x ipc_status=%d rx_size=%u\n",
			       rhdr.status, rsp->reply_primary, rsp->reply_extension,
			       rsp->ipc_status, rsp->rx_size);
		}
	} else if (strcmp(cmd, "large-cfg") == 0) {
		uint32_t param_id = (optind < argc) ? (uint32_t)strtoul(argv[optind], NULL, 0) : 14;
		struct sof_net_large_cfg_req req = {
			.module_id   = 0,
			.instance_id = 0,
			.param_id    = param_id,
			.set_get     = SOF_CDEV_LARGE_CFG_GET,
			.data_size   = 16384,
		};
		if (rpc_call(sock, SOF_NET_OP_IPC_LARGE_CFG, &req, sizeof(req), &rhdr, &rpayload) == 0 &&
		    rhdr.status == 0 && rpayload) {
			printf("LARGE_CONFIG_GET(param_id=%u) returned %u bytes:\n",
			       param_id, rhdr.payload_len);
			const uint8_t *b = (const uint8_t *)rpayload;
			for (uint32_t i = 0; i < rhdr.payload_len && i < 256; i++) {
				if (i % 16 == 0)
					printf("  %04x: ", i);
				printf("%02x ", b[i]);
				if (i % 16 == 15 || i + 1 == rhdr.payload_len)
					printf("\n");
			}
		} else {
			fprintf(stderr, "LARGE_CONFIG_GET failed: status=%d\n", rhdr.status);
		}
	} else if (strcmp(cmd, "win-read") == 0) {
		uint32_t win_id = (optind < argc) ? (uint32_t)strtoul(argv[optind++], NULL, 0) : 0;
		uint32_t offset = (optind < argc) ? (uint32_t)strtoul(argv[optind++], NULL, 0) : 0;
		uint32_t size   = (optind < argc) ? (uint32_t)strtoul(argv[optind++], NULL, 0) : 64;
		struct sof_net_win_io_req req = {
			.win_id = win_id,
			.offset = offset,
			.size   = size,
			.flags  = SOF_CDEV_WIN_FL_WAKE_DSP,
		};
		if (rpc_call(sock, SOF_NET_OP_WIN_READ, &req, sizeof(req), &rhdr, &rpayload) == 0 &&
		    rhdr.status == 0 && rpayload) {
			printf("Window %u [offset 0x%x, %u bytes]:\n", win_id, offset, rhdr.payload_len);
			const uint8_t *b = (const uint8_t *)rpayload;
			for (uint32_t i = 0; i < rhdr.payload_len; i++) {
				if (i % 16 == 0)
					printf("  %04x: ", offset + i);
				printf("%02x ", b[i]);
				if (i % 16 == 15 || i + 1 == rhdr.payload_len)
					printf("\n");
			}
		} else {
			fprintf(stderr, "WIN_READ failed: status=%d\n", rhdr.status);
		}
	} else if (strcmp(cmd, "llext-load") == 0) {
		if (optind >= argc) {
			fprintf(stderr, "Usage: llext-load <file.ri> [lib_id] [direct|shm]\n");
			return 1;
		}
		const char *filepath = argv[optind++];
		uint32_t lib_id = (optind < argc) ? (uint32_t)strtoul(argv[optind++], NULL, 0) : 0;
		const char *mode_s = (optind < argc) ? argv[optind++] : "direct";

		FILE *fp = fopen(filepath, "rb");
		if (!fp) {
			perror("fopen");
			return 1;
		}
		fseek(fp, 0, SEEK_END);
		long fsize = ftell(fp);
		fseek(fp, 0, SEEK_SET);
		if (fsize <= 0) {
			fclose(fp);
			return 1;
		}

		uint32_t total_len = sizeof(struct sof_net_llext_req) + (uint32_t)fsize;
		uint8_t *buf = calloc(1, total_len);
		struct sof_net_llext_req *lreq = (struct sof_net_llext_req *)buf;
		lreq->mode = (strcmp(mode_s, "shm") == 0) ?
			     SOF_LLEXT_MODE_SHELL_SHM : SOF_LLEXT_MODE_IPC4_DIRECT;
		lreq->lib_id = lib_id;
		lreq->data_size = (uint32_t)fsize;
		lreq->issue_shell_cmd = (lreq->mode == SOF_LLEXT_MODE_SHELL_SHM) ? 1 : 0;
		if (fread(buf + sizeof(*lreq), 1, (size_t)fsize, fp) != (size_t)fsize) {
			perror("fread");
			free(buf);
			fclose(fp);
			return 1;
		}
		fclose(fp);

		if (rpc_call(sock, SOF_NET_OP_LLEXT_LOAD, buf, total_len, &rhdr, &rpayload) == 0 &&
		    rpayload) {
			struct sof_net_llext_rsp *rsp = (struct sof_net_llext_rsp *)rpayload;
			printf("LLEXT Load status=%d, lib_id=%u, fw_state=%d\n",
			       rhdr.status, rsp->lib_id, rsp->fw_state);
			if (rsp->shell_out[0])
				printf("Shell output:\n%s\n", rsp->shell_out);
		} else {
			fprintf(stderr, "LLEXT Load failed: status=%d\n", rhdr.status);
		}
		free(buf);
	} else if (strcmp(cmd, "shell") == 0) {
		const char *scmd = (optind < argc) ? argv[optind++] : "version";
		uint32_t slen = (uint32_t)strlen(scmd) + 1;
		uint32_t total = sizeof(struct sof_net_shell_req) + slen;
		uint8_t *buf = calloc(1, total);
		struct sof_net_shell_req *sreq = (struct sof_net_shell_req *)buf;
		sreq->timeout_ms = 1000;
		sreq->cmd_len = slen;
		memcpy(buf + sizeof(*sreq), scmd, slen);

		if (rpc_call(sock, SOF_NET_OP_SHELL_EXEC, buf, total, &rhdr, &rpayload) == 0) {
			if (rhdr.status == 0 && rpayload)
				printf("%s\n", (char *)rpayload);
			else
				fprintf(stderr, "Shell exec status=%d\n", rhdr.status);
		}
		free(buf);
	} else if (strcmp(cmd, "probes") == 0) {
		const char *sub = (optind < argc) ? argv[optind++] : "avail";
		struct sof_net_probe_req req = {
			.op = (strcmp(sub, "active") == 0) ?
			      SOF_PROBE_OP_INFO_ACTIVE : SOF_PROBE_OP_INFO_AVAILABLE,
			.num_points = 64,
		};
		if (rpc_call(sock, SOF_NET_OP_PROBE_CTL, &req, sizeof(req), &rhdr, &rpayload) == 0 &&
		    rhdr.status == 0 && rpayload) {
			struct sof_net_probe_req *rsp = (struct sof_net_probe_req *)rpayload;
			struct sof_cdev_probe_point *pts =
				(struct sof_cdev_probe_point *)((uint8_t *)rpayload + sizeof(*rsp));
			printf("SOF Probe Points (%s): %u points\n", sub, rsp->num_points);
			for (uint32_t i = 0; i < rsp->num_points; i++) {
				printf("  [%2u] buffer_id=0x%08x purpose=%u stream_tag=%u widget=%s\n",
				       i, pts[i].buffer_id, pts[i].purpose,
				       pts[i].stream_tag, pts[i].widget_name);
			}
		} else {
			fprintf(stderr, "Probes query status=%d\n", rhdr.status);
		}
	} else if (strcmp(cmd, "cards") == 0) {
		if (rpc_call(sock, SOF_NET_OP_CARD_LIST, NULL, 0, &rhdr, &rpayload) == 0 &&
		    rhdr.status == 0 && rpayload) {
			const struct sof_net_card_list_hdr *chdr =
				(const struct sof_net_card_list_hdr *)rpayload;
			const struct sof_net_card_info *cards =
				(const struct sof_net_card_info *)((const uint8_t *)rpayload + sizeof(*chdr));
			const struct sof_net_pcm_node_info *nodes =
				(const struct sof_net_pcm_node_info *)
				((const uint8_t *)cards + chdr->num_cards * sizeof(*cards));

			printf("=== Sound Cards (%u total) ===\n", chdr->num_cards);
			for (uint32_t i = 0; i < chdr->num_cards; i++) {
				const struct sof_net_card_info *c = &cards[i];
				printf("  Card %u [%s]: driver=%s name='%s'\n"
				       "    Longname:  %s\n"
				       "    Topology:  %s\n"
				       "    Resources: %u PCM streams, %u Compress streams, %u Kcontrols, %u Active\n",
				       c->card_id, c->id, c->driver, c->name,
				       c->longname, c->tplg_file[0] ? c->tplg_file : "N/A",
				       c->num_pcm_streams, c->num_compr_streams,
				       c->num_kcontrols, c->active_streams);
			}

			printf("\n=== PCM & Compress Streams (%u total) ===\n", chdr->num_pcm_nodes);
			printf("  %-10s %-10s %-22s %-10s %-8s %-8s %-4s %-12s %s\n",
			       "Device", "Type/Dir", "ID", "State", "Format", "Rate", "Ch", "Period/Buf", "Delay/HwPtr");
			for (uint32_t i = 0; i < chdr->num_pcm_nodes; i++) {
				const struct sof_net_pcm_node_info *n = &nodes[i];
				char dev_s[24], type_s[16], pb_s[24];
				snprintf(dev_s, sizeof(dev_s), "%s:%u,%u",
					 n->is_compress ? "compr" : "hw",
					 n->card_id, n->device_id);
				snprintf(type_s, sizeof(type_s), "%s-%s",
					 n->is_compress ? "COMPR" : "PCM",
					 n->direction == SOF_STREAM_DIR_CAPTURE ? "CAP" : "PLAY");
				snprintf(pb_s, sizeof(pb_s), "%u/%u",
					 n->period_size, n->buffer_size);
				printf("  %-10s %-10s %-22.22s %-10s %-8s %-8u %-4u %-12s delay=%u hw=%lu\n",
				       dev_s, type_s, n->id, n->state_str,
				       n->active_format[0] ? n->active_format : "-",
				       n->active_rate, n->active_channels, pb_s,
				       n->delay_frames, (unsigned long)n->hw_ptr);
			}
		} else {
			fprintf(stderr, "OP_CARD_LIST failed: status=%d\n", rhdr.status);
		}
	} else if (strcmp(cmd, "pcm-caps") == 0) {
		struct sof_net_pcm_caps_req req = {
			.card_id     = 0xFFFFFFFFU,
			.device_id   = 0xFFFFFFFFU,
			.dir_mask    = SOF_CAPS_DIR_ALL,
			.api_backend = SOF_AUDIO_API_ALSA,
		};
		for (int i = optind; i < argc; i++) {
			if (strcmp(argv[i], "tinyalsa") == 0 || strcmp(argv[i], "--api=tinyalsa") == 0)
				req.api_backend = SOF_AUDIO_API_TINYALSA;
			else if (strcmp(argv[i], "alsa") == 0 || strcmp(argv[i], "--api=alsa") == 0)
				req.api_backend = SOF_AUDIO_API_ALSA;
			else if (req.card_id == 0xFFFFFFFFU && argv[i][0] >= '0' && argv[i][0] <= '9')
				req.card_id = (uint32_t)atoi(argv[i]);
			else if (req.device_id == 0xFFFFFFFFU && argv[i][0] >= '0' && argv[i][0] <= '9')
				req.device_id = (uint32_t)atoi(argv[i]);
		}

		if (rpc_call(sock, SOF_NET_OP_PCM_CAPS, &req, sizeof(req), &rhdr, &rpayload) == 0 &&
		    rhdr.status == 0 && rpayload) {
			const struct sof_net_pcm_caps_hdr *chdr =
				(const struct sof_net_pcm_caps_hdr *)rpayload;
			const struct sof_net_pcm_caps *caps =
				(const struct sof_net_pcm_caps *)((const uint8_t *)rpayload + sizeof(*chdr));

			printf("=== PCM & Compress Hardware Capabilities (%u entries, API=%s) ===\n",
			       chdr->num_entries,
			       chdr->api_used == SOF_AUDIO_API_TINYALSA ? "TinyALSA" : "alsa-lib");
			for (uint32_t i = 0; i < chdr->num_entries; i++) {
				const struct sof_net_pcm_caps *c = &caps[i];
				printf("  [%s %u,%u %s] '%s' (%s) status=%d\n",
				       c->is_compress ? "COMPR" : "PCM",
				       c->card_id, c->device_id,
				       c->direction == SOF_STREAM_DIR_CAPTURE ? "CAPTURE" : "PLAYBACK",
				       c->id, c->name, c->status);
				if (c->is_compress) {
					printf("    Codecs:     %s\n"
					       "    Fragments:  %u..%u bytes x %u..%u fragments\n",
					       c->formats_str, c->min_fragment_size,
					       c->max_fragment_size, c->min_fragments,
					       c->max_fragments);
				} else {
					printf("    Formats:    %s (mask=0x%llx, bits=%u..%u)\n"
					       "    Rates:      %u..%u Hz [%s]\n"
					       "    Channels:   %u..%u ch | AccessMask=0x%x\n"
					       "    Periods:    %u..%u frames (%u..%u B) x %u..%u periods\n"
					       "    Buffer:     %u..%u frames (%u..%u B)\n",
					       c->formats_str, (unsigned long long)c->formats_mask,
					       c->sample_bits_min, c->sample_bits_max,
					       c->rate_min, c->rate_max, c->rates_str,
					       c->channels_min, c->channels_max, c->access_mask,
					       c->period_size_min, c->period_size_max,
					       c->period_bytes_min, c->period_bytes_max,
					       c->periods_min, c->periods_max,
					       c->buffer_size_min, c->buffer_size_max,
					       c->buffer_bytes_min, c->buffer_bytes_max);
				}
			}
		} else {
			fprintf(stderr, "OP_PCM_CAPS failed: status=%d\n", rhdr.status);
		}
	} else if (strcmp(cmd, "dapm") == 0) {
		struct sof_net_dapm_req dreq = { .card_id = 0, .flags = 0 };
		const char *dot_file = NULL;
		for (int i = optind; i < argc; i++) {
			if (strstr(argv[i], ".dot"))
				dot_file = argv[i];
			else if (argv[i][0] >= '0' && argv[i][0] <= '9')
				dreq.card_id = (uint32_t)atoi(argv[i]);
		}

		if (rpc_call(sock, SOF_NET_OP_DAPM_GRAPH, &dreq, sizeof(dreq), &rhdr, &rpayload) == 0 &&
		    rhdr.status == 0 && rpayload) {
			const struct sof_net_dapm_graph_hdr *gh =
				(const struct sof_net_dapm_graph_hdr *)rpayload;
			const struct sof_net_dapm_comp_status *comps =
				(const struct sof_net_dapm_comp_status *)((const uint8_t *)rpayload + sizeof(*gh));
			const struct sof_net_dapm_node *nodes =
				(const struct sof_net_dapm_node *)((const uint8_t *)comps + gh->num_components * sizeof(*comps));
			const struct sof_net_dapm_edge *edges =
				(const struct sof_net_dapm_edge *)((const uint8_t *)nodes + gh->num_nodes * sizeof(*nodes));
			const struct sof_net_dpcm_link_state *dpcm =
				(const struct sof_net_dpcm_link_state *)((const uint8_t *)edges + gh->num_edges * sizeof(*edges));

			printf("=== DAPM Graph & DPCM Status: Card %u (%s) ===\n",
			       gh->card_id, gh->card_name);
			printf("Topology:   %s\n", gh->tplg_file[0] ? gh->tplg_file : "N/A");
			printf("Summary:    %u Components | %u/%u Powered Widgets | %u/%u Active Edges | %u DPCM Links\n\n",
			       gh->num_components, gh->num_powered_nodes, gh->num_nodes,
			       gh->num_active_edges, gh->num_edges, gh->num_dpcm_links);

			printf("--- Component Bias Status ---\n");
			for (uint32_t i = 0; i < gh->num_components; i++) {
				printf("  %-20s bias=%-10s powered=%2u/%2u widgets\n",
				       comps[i].name, comps[i].bias_str,
				       comps[i].powered_count, comps[i].widget_count);
			}

			printf("\n--- DPCM Frontend / Backend States ---\n");
			for (uint32_t i = 0; i < gh->num_dpcm_links; i++) {
				const struct sof_net_dpcm_link_state *l = &dpcm[i];
				printf("  FE [%-18s %-8s] State=%-8s Format=%-6s Ch=%u Rate=%u\n",
				       l->fe_name,
				       l->direction == SOF_STREAM_DIR_CAPTURE ? "Capture" : "Playback",
				       l->state_str,
				       l->fe_format[0] ? l->fe_format : "-",
				       l->fe_channels, l->fe_rate);
				for (uint32_t b = 0; b < l->num_backends; b++) {
					printf("    -> BE %-14s State=%-8s Format=%-6s Ch=%u Rate=%u\n",
					       l->be_names[b], l->be_states[b],
					       l->be_formats[b][0] ? l->be_formats[b] : "-",
					       l->be_channels[b], l->be_rates[b]);
				}
			}

			printf("\n--- DAPM Widget Nodes (%u) ---\n", gh->num_nodes);
			printf("  %-3s %-32s %-14s %-10s %-5s %-7s %-10s %s\n",
			       "ID", "Widget Name", "Component", "Type", "Power", "In/Out", "Grid(R,C)", "Kcontrols / Stream");
			for (uint32_t i = 0; i < gh->num_nodes; i++) {
				const struct sof_net_dapm_node *nd = &nodes[i];
				char io_s[16], grid_s[20], extra[128] = {0};
				snprintf(io_s, sizeof(io_s), "%u/%u", nd->in_ep, nd->out_ep);
				snprintf(grid_s, sizeof(grid_s), "p%d(%u,%u)",
					 nd->pipeline_id, nd->layout_row, nd->layout_col);
				if (nd->num_kcontrols > 0) {
					snprintf(extra, sizeof(extra), "kctl:[numid=%u '%s'] ",
						 nd->kcontrol_numids[0], nd->kcontrol_names[0]);
				}
				if (nd->stream_name[0]) {
					size_t l = strlen(extra);
					snprintf(extra + l, sizeof(extra) - l, "stream:'%s'(%s)",
						 nd->stream_name,
						 nd->stream_active ? "ACTIVE" : "inactive");
				}
				printf("  %3u %-32s %-14s %-10s %-5s %-7s %-10s %s\n",
				       nd->node_id, nd->name, nd->component,
				       nd->widget_type, nd->power ? "ON" : "off",
				       io_s, grid_s, extra);
			}

			printf("\n--- DAPM Directed Connections (%u) ---\n", gh->num_edges);
			for (uint32_t i = 0; i < gh->num_edges; i++) {
				const struct sof_net_dapm_edge *e = &edges[i];
				printf("  [%2u] %-30s -> %-30s via '%s' [%s]\n",
				       e->edge_id, e->src_name, e->dst_name,
				       e->control_name, e->active ? "ACTIVE" : "off");
			}

			if (dot_file) {
				FILE *df = fopen(dot_file, "w");
				if (df) {
					fprintf(df, "digraph DAPM {\n  rankdir=LR;\n");
					for (uint32_t i = 0; i < gh->num_nodes; i++) {
						const struct sof_net_dapm_node *nd = &nodes[i];
						fprintf(df, "  n%u [label=\"%s\\n(%s)%s%s\", style=filled, fillcolor=\"%s\"];\n",
							nd->node_id, nd->name, nd->widget_type,
							nd->num_kcontrols ? "\\nKCTL: " : "",
							nd->num_kcontrols ? nd->kcontrol_names[0] : "",
							nd->power ? "#a7f3d0" : "#e2e8f0");
					}
					for (uint32_t i = 0; i < gh->num_edges; i++) {
						const struct sof_net_dapm_edge *e = &edges[i];
						if (e->src_node_id >= 0 && e->dst_node_id >= 0) {
							fprintf(df, "  n%d -> n%d [label=\"%s\", color=\"%s\"];\n",
								e->src_node_id, e->dst_node_id,
								strcmp(e->control_name, "static") == 0 ? "" : e->control_name,
								e->active ? "#059669" : "#94a3b8");
						}
					}
					fprintf(df, "}\n");
					fclose(df);
					printf("\nExported Graphviz DOT to %s\n", dot_file);
				}
			}
		} else {
			fprintf(stderr, "OP_DAPM_GRAPH failed: status=%d\n", rhdr.status);
		}
	} else if (strcmp(cmd, "kctl") == 0) {
		const char *sub = (optind < argc) ? argv[optind++] : "list";
		if (strcmp(sub, "list") == 0) {
			struct sof_net_kctl_list_req kreq = {
				.card_id     = 0,
				.api_backend = SOF_AUDIO_API_ALSA,
			};
			for (int i = optind; i < argc; i++) {
				if (strcmp(argv[i], "tinyalsa") == 0)
					kreq.api_backend = SOF_AUDIO_API_TINYALSA;
				else if (strcmp(argv[i], "alsa") == 0)
					kreq.api_backend = SOF_AUDIO_API_ALSA;
				else if (argv[i][0] >= '0' && argv[i][0] <= '9')
					kreq.card_id = (uint32_t)atoi(argv[i]);
			}

			if (rpc_call(sock, SOF_NET_OP_KCTL_LIST, &kreq, sizeof(kreq), &rhdr, &rpayload) == 0 &&
			    rhdr.status == 0 && rpayload) {
				const struct sof_net_kctl_list_hdr *kh =
					(const struct sof_net_kctl_list_hdr *)rpayload;
				const struct sof_net_kctl_info *karr =
					(const struct sof_net_kctl_info *)((const uint8_t *)rpayload + sizeof(*kh));

				printf("=== Card %u Kcontrols (%u total, API=%s) ===\n",
				       kh->card_id, kh->num_kcontrols,
				       kh->api_used == SOF_AUDIO_API_TINYALSA ? "TinyALSA" : "alsa-lib");
				printf("  %-5s %-6s %-30s %-9s %-8s %-14s %-14s %s\n",
				       "NumID", "Iface", "Name", "Type", "Access", "Range/dB", "Values", "Graph Anchor (Where to Render)");
				for (uint32_t i = 0; i < kh->num_kcontrols; i++) {
					const struct sof_net_kctl_info *k = &karr[i];
					char rng_s[32] = "-", val_s[32] = "-", anc_s[96] = "CARD";
					if (k->type == SOF_KCTL_TYPE_INTEGER || k->type == SOF_KCTL_TYPE_BOOLEAN) {
						snprintf(rng_s, sizeof(rng_s), "%lld..%lld",
							 (long long)k->min_val, (long long)k->max_val);
					}
					if (k->count == 1)
						snprintf(val_s, sizeof(val_s), "%lld", (long long)k->values[0]);
					else if (k->count >= 2)
						snprintf(val_s, sizeof(val_s), "%lld,%lld",
							 (long long)k->values[0], (long long)k->values[1]);

					const char *atype =
						k->anchor_type == SOF_KCTL_ANCHOR_WIDGET ? "WIDGET" :
						k->anchor_type == SOF_KCTL_ANCHOR_EDGE   ? "EDGE" :
						k->anchor_type == SOF_KCTL_ANCHOR_PCM_FE ? "PCM_FE" :
						k->anchor_type == SOF_KCTL_ANCHOR_DAI_BE ? "DAI_BE" : "CARD";
					if (k->target_widget[0]) {
						snprintf(anc_s, sizeof(anc_s), "%s -> %s (pipe=%d, r=%u,c=%u)",
							 atype, k->target_widget, k->pipeline_id,
							 k->layout_row, k->layout_col);
					}
					printf("  %5u %-6s %-30.30s %-9s %-8s %-14s %-14s %s\n",
					       k->numid, k->iface_str, k->name, k->type_str,
					       k->access_str, rng_s, val_s, anc_s);
				}
			} else {
				fprintf(stderr, "OP_KCTL_LIST failed: status=%d\n", rhdr.status);
			}
		} else if (strcmp(sub, "get") == 0 || strcmp(sub, "set") == 0) {
			bool is_set = (strcmp(sub, "set") == 0);
			if (optind >= argc) {
				fprintf(stderr, "Usage: kctl %s <numid_or_name> [values...] [alsa|tinyalsa]\n", sub);
				return 1;
			}
			struct sof_net_kctl_io_req ioreq;
			memset(&ioreq, 0, sizeof(ioreq));
			ioreq.card_id = 0;
			ioreq.api_backend = SOF_AUDIO_API_ALSA;

			const char *id_arg = argv[optind++];
			char *endp = NULL;
			unsigned long nid = strtoul(id_arg, &endp, 10);
			if (endp && *endp == '\0' && nid > 0)
				ioreq.numid = (uint32_t)nid;
			else
				snprintf(ioreq.name, sizeof(ioreq.name), "%s", id_arg);

			while (optind < argc) {
				const char *a = argv[optind++];
				if (strcmp(a, "tinyalsa") == 0) {
					ioreq.api_backend = SOF_AUDIO_API_TINYALSA;
				} else if (strcmp(a, "alsa") == 0) {
					ioreq.api_backend = SOF_AUDIO_API_ALSA;
				} else if (is_set && ioreq.count < 16) {
					if (strcasecmp(a, "on") == 0 || strcasecmp(a, "true") == 0)
						ioreq.values[ioreq.count++] = 1;
					else if (strcasecmp(a, "off") == 0 || strcasecmp(a, "false") == 0)
						ioreq.values[ioreq.count++] = 0;
					else
						ioreq.values[ioreq.count++] = strtoll(a, NULL, 0);
				}
			}

			uint16_t op = is_set ? SOF_NET_OP_KCTL_SET : SOF_NET_OP_KCTL_GET;
			if (rpc_call(sock, op, &ioreq, sizeof(ioreq), &rhdr, &rpayload) == 0 &&
			    rpayload) {
				const struct sof_net_kctl_io_rsp *rsp =
					(const struct sof_net_kctl_io_rsp *)rpayload;
				const struct sof_net_kctl_info *k = &rsp->info;
				printf("Kcontrol %s (status=%d, API=%s):\n",
				       is_set ? "SET" : "GET", rhdr.status,
				       rsp->api_used == SOF_AUDIO_API_TINYALSA ? "TinyALSA" : "alsa-lib");
				printf("  numid=%u, iface=%s, device=%u, index=%u, name='%s'\n",
				       k->numid, k->iface_str, k->device, k->index, k->name);
				printf("  type=%s, access=%s, count=%u, range=[%lld .. %lld] step=%lld\n",
				       k->type_str, k->access_str, k->count,
				       (long long)k->min_val, (long long)k->max_val,
				       (long long)k->step_val);
				if (k->has_db_scale) {
					printf("  dB Scale: min=%.2f dB, step=%.2f dB, mute=%u\n",
					       k->db_min_cdb / 100.0, k->db_step_cdb / 100.0,
					       k->db_mute);
				}
				printf("  Values:   ");
				uint32_t nch = k->count > 8 ? 8 : k->count;
				for (uint32_t c = 0; c < nch; c++) {
					printf("%s%lld", c ? ", " : "", (long long)k->values[c]);
					if (k->has_db_scale) {
						double db = (k->db_min_cdb + k->values[c] * k->db_step_cdb) / 100.0;
						printf(" (%.2f dB)", db);
					}
				}
				printf("\n");
				printf("  Graph Anchor: type=%u widget='%s' component='%s' pipeline=%d grid=(row=%u, col=%u)\n",
				       k->anchor_type,
				       k->target_widget[0] ? k->target_widget : "card",
				       k->target_component[0] ? k->target_component : "card",
				       k->pipeline_id, k->layout_row, k->layout_col);
			} else {
				fprintf(stderr, "KCTL %s failed: status=%d\n", sub, rhdr.status);
			}
		}
	} else if (strcmp(cmd, "compr") == 0) {
		const char *sub = (optind < argc) ? argv[optind++] : "caps";
		struct sof_net_compr_req creq;
		memset(&creq, 0, sizeof(creq));
		creq.card_id = (optind < argc) ? (uint32_t)atoi(argv[optind++]) : 0;
		creq.device_id = (optind < argc) ? (uint32_t)atoi(argv[optind++]) : 0;
		creq.duration_ms = (optind < argc) ? (uint32_t)atoi(argv[optind++]) : 500;
		creq.rate = 48000;
		creq.channels = 2;
		creq.codec_id = 1; /* SND_AUDIOCODEC_PCM */
		creq.freq_hz = 1000;

		if (strcmp(sub, "play") == 0)
			creq.op = SOF_COMPR_OP_PLAY_BUFFER;
		else if (strcmp(sub, "capture") == 0) {
			creq.op = SOF_COMPR_OP_CAPTURE_BUFFER;
			creq.direction = SOF_STREAM_DIR_CAPTURE;
		} else
			creq.op = SOF_COMPR_OP_GET_CAPS;

		if (rpc_call(sock, SOF_NET_OP_COMPR_CTL, &creq, sizeof(creq), &rhdr, &rpayload) == 0 &&
		    rpayload) {
			const struct sof_net_compr_rsp *rsp =
				(const struct sof_net_compr_rsp *)rpayload;
			printf("Compress Offload (%s comprC%uD%u): status=%d frag=%u..%uB codecs=%u bytes_xfer=%u samples=%lu rate=%u\n",
			       sub, rsp->card_id, rsp->device_id, rsp->status,
			       rsp->min_fragment_size, rsp->max_fragment_size,
			       rsp->num_codecs, rsp->bytes_transferred,
			       (unsigned long)rsp->rendered_samples, rsp->sampling_rate);
		} else {
			fprintf(stderr, "OP_COMPR_CTL failed: status=%d\n", rhdr.status);
		}
	} else if (strcmp(cmd, "pcm") == 0) {
		const char *sub = (optind < argc) ? argv[optind++] : "status";
		struct sof_net_pcm_req req;
		memset(&req, 0, sizeof(req));
		strncpy(req.device, (optind < argc) ? argv[optind++] : "hw:0,0",
			sizeof(req.device) - 1);
		req.rate = 48000;
		req.channels = 2;
		req.freq_hz = (optind < argc) ? (uint32_t)atoi(argv[optind++]) : 1000;
		req.duration_ms = req.freq_hz;
		req.api_backend = SOF_AUDIO_API_ALSA;
		req.format = SOF_PCM_FMT_S16_LE;

		while (optind < argc) {
			const char *a = argv[optind++];
			if (strcmp(a, "tinyalsa") == 0 || strcmp(a, "--api=tinyalsa") == 0)
				req.api_backend = SOF_AUDIO_API_TINYALSA;
			else if (strcmp(a, "alsa") == 0 || strcmp(a, "--api=alsa") == 0)
				req.api_backend = SOF_AUDIO_API_ALSA;
			else if (strcasecmp(a, "S32_LE") == 0)
				req.format = SOF_PCM_FMT_S32_LE;
			else if (strcasecmp(a, "S24_LE") == 0)
				req.format = SOF_PCM_FMT_S24_LE;
			else if (strcasecmp(a, "S16_LE") == 0)
				req.format = SOF_PCM_FMT_S16_LE;
		}

		if (strcmp(sub, "start-tone") == 0)
			req.op = SOF_PCM_OP_START_TONE;
		else if (strcmp(sub, "stop-tone") == 0)
			req.op = SOF_PCM_OP_STOP_TONE;
		else if (strcmp(sub, "play") == 0)
			req.op = SOF_PCM_OP_PLAY_BUFFER;
		else if (strcmp(sub, "capture") == 0)
			req.op = SOF_PCM_OP_CAPTURE_BUFFER;
		else
			req.op = SOF_PCM_OP_STATUS;

		if (rpc_call(sock, SOF_NET_OP_PCM_CTL, &req, sizeof(req), &rhdr, &rpayload) == 0 &&
		    rpayload) {
			struct sof_net_pcm_rsp *rsp = (struct sof_net_pcm_rsp *)rpayload;
			printf("PCM Status (rc=%d, API=%s, fmt=%u): tone_running=%u dev=%s rate=%u ch=%u freq=%uHz frames=%lu rms=%u peak=%u xruns=%u captured_bytes=%u\n",
			       rhdr.status,
			       rsp->api_used == SOF_AUDIO_API_TINYALSA ? "TinyALSA" : "alsa-lib",
			       rsp->format, rsp->tone_running, rsp->active_device,
			       rsp->rate, rsp->channels, rsp->freq_hz,
			       (unsigned long)rsp->frames_played,
			       rsp->rms_level, rsp->peak_level, rsp->xruns, rsp->data_size);
		} else {
			fprintf(stderr, "PCM command failed: status=%d\n", rhdr.status);
		}
	} else if (strcmp(cmd, "workload-sim") == 0) {
		const char *sub = (optind < argc) ? argv[optind++] : "status";
		struct sof_net_workload_sim_req wreq;

		memset(&wreq, 0, sizeof(wreq));
		if (strcmp(sub, "set") == 0) {
			const char *m_str = (optind < argc) ? argv[optind++] : "virtual";
			const char *d_str = (optind < argc) ? argv[optind++] : "sram";
			uint32_t freq_mhz = (optind < argc) ? (uint32_t)strtoul(argv[optind++], NULL, 0) : 400;
			uint32_t dfactor = (optind < argc) ? (uint32_t)strtoul(argv[optind++], NULL, 0) : 160;

			wreq.op = SOF_WORKLOAD_SIM_OP_SET;
			if (strcmp(m_str, "0") == 0 || strcasecmp(m_str, "off") == 0)
				wreq.mode = 0;
			else if (strcmp(m_str, "2") == 0 || strcasecmp(m_str, "realtime") == 0 ||
				 strcasecmp(m_str, "pace") == 0)
				wreq.mode = 2;
			else
				wreq.mode = 1;

			wreq.default_mem_domain = (strncasecmp(d_str, "dram", 4) == 0 ||
						   strcmp(d_str, "1") == 0) ? 1 : 0;
			wreq.dram_factor_x100 = dfactor;
			wreq.dram_cycles_per_word = 2;
			for (int c = 0; c < 8; c++)
				wreq.core_freq_hz[c] = (freq_mhz < 100000U) ?
						       (freq_mhz * 1000000U) : freq_mhz;
		} else if (strcmp(sub, "mod") == 0) {
			wreq.op = SOF_WORKLOAD_SIM_OP_MOD_SET;
			wreq.target_comp_id = (optind < argc) ?
					      (uint32_t)strtoul(argv[optind++], NULL, 0) : 0;
			wreq.cpc_override = (optind < argc) ?
					    (uint32_t)strtoul(argv[optind++], NULL, 0) : 0;
			const char *d_str = (optind < argc) ? argv[optind++] : "auto";

			if (strncasecmp(d_str, "dram", 4) == 0)
				wreq.mem_domain_override = 1;
			else if (strncasecmp(d_str, "sram", 4) == 0)
				wreq.mem_domain_override = 0;
			else
				wreq.mem_domain_override = 0xFF;
		} else if (strcmp(sub, "reset") == 0) {
			wreq.op = SOF_WORKLOAD_SIM_OP_RESET_OVERRIDES;
		} else {
			wreq.op = SOF_WORKLOAD_SIM_OP_GET;
		}

		if (rpc_call(sock, SOF_NET_OP_WORKLOAD_SIM_CFG, &wreq, sizeof(wreq),
			     &rhdr, &rpayload) == 0 && rpayload) {
			const struct sof_net_workload_sim_rsp *rsp =
				(const struct sof_net_workload_sim_rsp *)rpayload;
			const char *mstr = (rsp->mode == 2) ? "REALTIME-PACED" :
					   (rsp->mode == 1) ? "VIRTUAL-SIM" : "OFF (HOST-MEASURED)";
			printf("Workload Simulation Config (status=%d):\n"
			       "  Mode:            %s (%u)\n"
			       "  Memory Domain:   %s (SRAM=%u.%02ux, DRAM=%u.%02ux + %u cyc/word)\n"
			       "  Overrides:       %u\n",
			       rsp->status, mstr, rsp->mode,
			       rsp->default_mem_domain == 1 ? "DRAM" : "SRAM",
			       rsp->sram_factor_x100 / 100, rsp->sram_factor_x100 % 100,
			       rsp->dram_factor_x100 / 100, rsp->dram_factor_x100 % 100,
			       rsp->dram_cycles_per_word, rsp->num_overrides);
			for (uint32_t c = 0; c < rsp->num_cores && c < 8; c++) {
				const struct sof_net_workload_core_sim_stat *cs = &rsp->cores[c];
				printf("  [Core %u] freq=%u MHz active=%u util=%u.%02u%% overruns=%u LL=%u/%u kcps (%lu.%03lu/%lu.%03lu us) DP=%u/%u kcps (%lu.%03lu/%lu.%03lu us)\n",
				       c, cs->freq_hz / 1000000U, cs->active,
				       cs->total_util_x100 / 100, cs->total_util_x100 % 100,
				       cs->ll_deadline_violations,
				       cs->ll_avg_kcps, cs->ll_peak_kcps,
				       (unsigned long)(cs->ll_avg_exec_ns / 1000ULL),
				       (unsigned long)(cs->ll_avg_exec_ns % 1000ULL),
				       (unsigned long)(cs->ll_peak_exec_ns / 1000ULL),
				       (unsigned long)(cs->ll_peak_exec_ns % 1000ULL),
				       cs->dp_avg_kcps, cs->dp_peak_kcps,
				       (unsigned long)(cs->dp_avg_exec_ns / 1000ULL),
				       (unsigned long)(cs->dp_avg_exec_ns % 1000ULL),
				       (unsigned long)(cs->dp_peak_exec_ns / 1000ULL),
				       (unsigned long)(cs->dp_peak_exec_ns % 1000ULL));
			}
		} else {
			fprintf(stderr, "OP_WORKLOAD_SIM_CFG failed: status=%d\n", rhdr.status);
		}
	} else {
		fprintf(stderr, "Unknown command '%s'\n", cmd);
	}

	free(rpayload);
	close(sock);
	return 0;
}
