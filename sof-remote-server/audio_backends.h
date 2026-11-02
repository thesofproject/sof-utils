/* SPDX-License-Identifier: (GPL-2.0-only OR BSD-3-Clause) */
/*
 * Internal C backend interface for ALSA-lib, TinyALSA, TinyCompress,
 * DAPM Graph Inspection, and Kcontrol Graph Anchor Resolution.
 */

#ifndef __SOF_AUDIO_BACKENDS_H__
#define __SOF_AUDIO_BACKENDS_H__

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdatomic.h>
#include "sof_remote_proto.h"

/* Helper to parse "hw:C,D" or "C,D" or "plughw:C,D" into (card_id, dev_id) */
int sof_parse_card_dev(const char *dev_str, uint32_t *card_out, uint32_t *dev_out);

/* Format helpers */
uint32_t sof_pcm_fmt_bytes(uint32_t fmt_id);
const char *sof_pcm_fmt_name(uint32_t fmt_id);
void sof_format_mask_to_str(uint64_t mask, char *buf, size_t cap);
void sof_rate_mask_to_str(uint32_t mask, uint32_t rmin, uint32_t rmax,
			  char *buf, size_t cap);
void sof_compute_pcm_rms_peak(const void *buf, uint32_t fmt_id, size_t n_samp,
			      uint32_t *rms_out, uint32_t *peak_out);

/* -------------------------------------------------------------------------
 * 1. ALSA-lib Backend (audio_backend_alsa.c)
 * -------------------------------------------------------------------------
 */
int alsa_query_pcm_caps(uint32_t card_id, uint32_t dev_id, uint32_t direction,
			struct sof_net_pcm_caps *out);

int alsa_pcm_play_frames(const char *dev_name, uint32_t fmt_id, uint32_t rate,
			 uint32_t channels, uint32_t period_size,
			 uint32_t period_count, const void *pcm_data,
			 uint32_t total_frames, _Atomic bool *keep_running,
			 uint32_t freq_hz, _Atomic uint64_t *frames_counter,
			 _Atomic uint32_t *rms_out, _Atomic uint32_t *peak_out,
			 uint32_t *xruns_out);

int alsa_pcm_capture_frames(const char *dev_name, uint32_t fmt_id, uint32_t rate,
			    uint32_t channels, uint32_t period_size,
			    uint32_t period_count, void *out_buf,
			    uint32_t max_frames, uint32_t *frames_read_out,
			    uint32_t *rms_out, uint32_t *peak_out,
			    uint32_t *xruns_out);

int alsa_kctl_list(uint32_t card_id, struct sof_net_kctl_info *out_arr,
		   uint32_t max_items, uint32_t *count_out);

int alsa_kctl_get(const struct sof_net_kctl_io_req *req,
		  struct sof_net_kctl_io_rsp *rsp);

int alsa_kctl_set(const struct sof_net_kctl_io_req *req,
		  struct sof_net_kctl_io_rsp *rsp);

/* -------------------------------------------------------------------------
 * 2. TinyALSA Backend (audio_backend_tinyalsa.c)
 * -------------------------------------------------------------------------
 */
int tinyalsa_query_pcm_caps(uint32_t card_id, uint32_t dev_id, uint32_t direction,
			    struct sof_net_pcm_caps *out);

int tinyalsa_pcm_play_frames(uint32_t card_id, uint32_t dev_id, uint32_t fmt_id,
			     uint32_t rate, uint32_t channels,
			     uint32_t period_size, uint32_t period_count,
			     const void *pcm_data, uint32_t total_frames,
			     _Atomic bool *keep_running, uint32_t freq_hz,
			     _Atomic uint64_t *frames_counter,
			     _Atomic uint32_t *rms_out,
			     _Atomic uint32_t *peak_out,
			     uint32_t *xruns_out);

int tinyalsa_pcm_capture_frames(uint32_t card_id, uint32_t dev_id, uint32_t fmt_id,
				uint32_t rate, uint32_t channels,
				uint32_t period_size, uint32_t period_count,
				void *out_buf, uint32_t max_frames,
				uint32_t *frames_read_out,
				uint32_t *rms_out, uint32_t *peak_out,
				uint32_t *xruns_out);

int tinyalsa_kctl_list(uint32_t card_id, struct sof_net_kctl_info *out_arr,
		       uint32_t max_items, uint32_t *count_out);

int tinyalsa_kctl_get(const struct sof_net_kctl_io_req *req,
		      struct sof_net_kctl_io_rsp *rsp);

int tinyalsa_kctl_set(const struct sof_net_kctl_io_req *req,
		      struct sof_net_kctl_io_rsp *rsp);

/* -------------------------------------------------------------------------
 * 3. TinyCompress Backend (audio_backend_compress.c)
 * -------------------------------------------------------------------------
 */
int compr_query_caps(uint32_t card_id, uint32_t dev_id, uint32_t direction,
		     struct sof_net_pcm_caps *out);

int compr_handle_ctl(const struct sof_net_compr_req *req,
		     const uint8_t *payload_in,
		     uint8_t **out_buf_ptr, uint32_t *out_len_ptr);

/* -------------------------------------------------------------------------
 * 4. Card Enumeration, DAPM Graph & Kcontrol Anchor Resolver (dapm_inspector.c)
 * -------------------------------------------------------------------------
 */
int dapm_build_card_list_payload(uint8_t **out_buf_ptr, uint32_t *out_len_ptr);

int dapm_build_pcm_caps_payload(const struct sof_net_pcm_caps_req *req,
				uint8_t **out_buf_ptr, uint32_t *out_len_ptr);

int dapm_build_graph_payload(uint32_t card_id, uint8_t **out_buf_ptr,
			     uint32_t *out_len_ptr);

void dapm_resolve_kctl_anchors(uint32_t card_id,
			       struct sof_net_kctl_info *kctls,
			       uint32_t num_kctls);

#endif /* __SOF_AUDIO_BACKENDS_H__ */
