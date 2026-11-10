// SPDX-License-Identifier: (GPL-2.0-only OR BSD-3-Clause)
/*
 * audio_backend_tinyalsa.c — Native TinyALSA (libtinyalsa) backend for
 * PCM Capability Discovery (pcm_params_get), PCM Playback/Capture (pcm_writei /
 * pcm_readi), and Mixer Kcontrol Info/Get/Set (mixer_*).
 */

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <limits.h>
#include <math.h>
#include <errno.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/ioctl.h>
#include <sound/asound.h>

#include "audio_backends.h"

#ifdef HAVE_TINYALSA
#if defined(__has_include) && __has_include(<tinyalsa/pcm.h>)
#include <tinyalsa/pcm.h>
#include <tinyalsa/mixer.h>
#define HAVE_TINYALSA_V2 1
#else
#include <tinyalsa/asoundlib.h>
#endif

#ifndef PCM_NONBLOCK
#define PCM_NONBLOCK 0
#endif

static enum pcm_format sof_to_tinyalsa_format(uint32_t fmt_id)
{
	switch (fmt_id) {
	case SOF_PCM_FMT_S8:       return PCM_FORMAT_S8;
	case SOF_PCM_FMT_S24_LE:   return PCM_FORMAT_S24_LE;
	case SOF_PCM_FMT_S24_3LE:  return PCM_FORMAT_S24_3LE;
	case SOF_PCM_FMT_S32_LE:   return PCM_FORMAT_S32_LE;
#ifdef HAVE_TINYALSA_V2
	case SOF_PCM_FMT_FLOAT_LE: return PCM_FORMAT_FLOAT_LE;
#endif
	case SOF_PCM_FMT_S16_LE:
	default:                   return PCM_FORMAT_S16_LE;
	}
}

static int tiny_pcm_write_frames(struct pcm *pcm, const void *data,
				 uint32_t frames, uint32_t bpf)
{
#ifdef HAVE_TINYALSA_V2
	(void)bpf;
	return pcm_writei(pcm, data, frames);
#else
	int rc = pcm_write(pcm, data, frames * bpf);
	return (rc == 0) ? (int)frames : rc;
#endif
}

static int tiny_pcm_read_frames(struct pcm *pcm, void *data,
				uint32_t frames, uint32_t bpf)
{
#ifdef HAVE_TINYALSA_V2
	(void)bpf;
	return pcm_readi(pcm, data, frames);
#else
	int rc = pcm_read(pcm, data, frames * bpf);
	return (rc == 0) ? (int)frames : rc;
#endif
}

static int tiny_pcm_drain(struct pcm *pcm)
{
#ifdef HAVE_TINYALSA_V2
	return pcm_drain(pcm);
#else
	return pcm_ioctl(pcm, SNDRV_PCM_IOCTL_DRAIN);
#endif
}

static unsigned int tiny_mixer_ctl_get_id(struct mixer_ctl *ctl, unsigned int fallback_idx)
{
#ifdef HAVE_TINYALSA_V2
	(void)fallback_idx;
	return mixer_ctl_get_id(ctl);
#else
	(void)ctl;
	return fallback_idx;
#endif
}

static unsigned int tiny_mixer_ctl_get_device(struct mixer_ctl *ctl)
{
#ifdef HAVE_TINYALSA_V2
	return mixer_ctl_get_device(ctl);
#else
	(void)ctl;
	return 0;
#endif
}

/* -------------------------------------------------------------------------
 * 1. PCM Capabilities via TinyALSA (pcm_params_get)
 * -------------------------------------------------------------------------
 */
int tinyalsa_query_pcm_caps(uint32_t card_id, uint32_t dev_id, uint32_t direction,
			    struct sof_net_pcm_caps *out)
{
	unsigned int flags = (direction == SOF_STREAM_DIR_CAPTURE) ? PCM_IN : PCM_OUT;

	out->card_id = card_id;
	out->device_id = dev_id;
	out->direction = direction;
	out->is_compress = 0;
	out->api_used = SOF_AUDIO_API_TINYALSA;

	struct pcm_params *params = pcm_params_get(card_id, dev_id, flags | PCM_NONBLOCK);
	if (!params) {
		out->status = -errno ? -errno : -ENODEV;
		return out->status;
	}

	/* Formats */
	if (pcm_params_format_test(params, PCM_FORMAT_S8))
		out->formats_mask |= SOF_FMTBIT_S8;
	if (pcm_params_format_test(params, PCM_FORMAT_S16_LE))
		out->formats_mask |= SOF_FMTBIT_S16_LE;
	if (pcm_params_format_test(params, PCM_FORMAT_S24_LE))
		out->formats_mask |= SOF_FMTBIT_S24_LE;
	if (pcm_params_format_test(params, PCM_FORMAT_S24_3LE))
		out->formats_mask |= SOF_FMTBIT_S24_3LE;
	if (pcm_params_format_test(params, PCM_FORMAT_S32_LE))
		out->formats_mask |= SOF_FMTBIT_S32_LE;
#ifdef HAVE_TINYALSA_V2
	if (pcm_params_format_test(params, PCM_FORMAT_S16_BE))
		out->formats_mask |= SOF_FMTBIT_S16_BE;
	if (pcm_params_format_test(params, PCM_FORMAT_S24_BE))
		out->formats_mask |= SOF_FMTBIT_S24_BE;
	if (pcm_params_format_test(params, PCM_FORMAT_S24_3BE))
		out->formats_mask |= SOF_FMTBIT_S24_3BE;
	if (pcm_params_format_test(params, PCM_FORMAT_S32_BE))
		out->formats_mask |= SOF_FMTBIT_S32_BE;
	if (pcm_params_format_test(params, PCM_FORMAT_FLOAT_LE))
		out->formats_mask |= SOF_FMTBIT_FLOAT_LE;
#endif

	const struct pcm_mask *amask = pcm_params_get_mask(params, PCM_PARAM_ACCESS);
	if (amask)
		out->access_mask = amask->bits[0];

	out->sample_bits_min = pcm_params_get_min(params, PCM_PARAM_SAMPLE_BITS);
	out->sample_bits_max = pcm_params_get_max(params, PCM_PARAM_SAMPLE_BITS);
	out->channels_min    = pcm_params_get_min(params, PCM_PARAM_CHANNELS);
	out->channels_max    = pcm_params_get_max(params, PCM_PARAM_CHANNELS);
	out->rate_min        = pcm_params_get_min(params, PCM_PARAM_RATE);
	out->rate_max        = pcm_params_get_max(params, PCM_PARAM_RATE);
	out->period_size_min = pcm_params_get_min(params, PCM_PARAM_PERIOD_SIZE);
	out->period_size_max = pcm_params_get_max(params, PCM_PARAM_PERIOD_SIZE);
	out->period_bytes_min = pcm_params_get_min(params, PCM_PARAM_PERIOD_BYTES);
	out->period_bytes_max = pcm_params_get_max(params, PCM_PARAM_PERIOD_BYTES);
	out->periods_min     = pcm_params_get_min(params, PCM_PARAM_PERIODS);
	out->periods_max     = pcm_params_get_max(params, PCM_PARAM_PERIODS);
	out->buffer_size_min = pcm_params_get_min(params, PCM_PARAM_BUFFER_SIZE);
	out->buffer_size_max = pcm_params_get_max(params, PCM_PARAM_BUFFER_SIZE);
	out->buffer_bytes_min = pcm_params_get_min(params, PCM_PARAM_BUFFER_BYTES);
	out->buffer_bytes_max = pcm_params_get_max(params, PCM_PARAM_BUFFER_BYTES);

	/*
	 * TinyALSA exposes only [rate_min, rate_max] bounds without a discrete
	 * rate mask. Only set a discrete rate bit when rate_min == rate_max;
	 * otherwise leave rates_mask = 0 so sof_rate_mask_to_str() reports the
	 * exact known range "%u..%u".
	 */
	static const struct {
		uint32_t rate;
		uint32_t bit;
	} rates[] = {
		{ 8000,   SOF_RATEBIT_8000 },
		{ 11025,  SOF_RATEBIT_11025 },
		{ 16000,  SOF_RATEBIT_16000 },
		{ 22050,  SOF_RATEBIT_22050 },
		{ 32000,  SOF_RATEBIT_32000 },
		{ 44100,  SOF_RATEBIT_44100 },
		{ 48000,  SOF_RATEBIT_48000 },
		{ 88200,  SOF_RATEBIT_88200 },
		{ 96000,  SOF_RATEBIT_96000 },
		{ 176400, SOF_RATEBIT_176400 },
		{ 192000, SOF_RATEBIT_192000 },
	};
	if (out->rate_min > 0 && out->rate_min == out->rate_max) {
		for (size_t i = 0; i < sizeof(rates) / sizeof(rates[0]); i++) {
			if (rates[i].rate == out->rate_min)
				out->rates_mask |= rates[i].bit;
		}
	}

	sof_format_mask_to_str(out->formats_mask, out->formats_str, sizeof(out->formats_str));
	sof_rate_mask_to_str(out->rates_mask, out->rate_min, out->rate_max,
			     out->rates_str, sizeof(out->rates_str));

	pcm_params_free(params);
	out->status = 0;
	return 0;
}

/* -------------------------------------------------------------------------
 * 2. Native TinyALSA Playback & Capture (pcm_open / pcm_writei / pcm_readi)
 * -------------------------------------------------------------------------
 */
static void synthesize_tiny_sine(void *buf, uint32_t fmt_id, uint32_t frames,
				 uint32_t channels, double *phase_inout,
				 double phase_step)
{
	double phase = *phase_inout;

	for (uint32_t i = 0; i < frames; i++) {
		double sv = sin(phase);
		phase += phase_step;
		if (phase >= 2.0 * M_PI)
			phase -= 2.0 * M_PI;

		switch (fmt_id) {
		case SOF_PCM_FMT_S8: {
			int8_t s8 = (int8_t)(sv * 64.0);
			int8_t *p = (int8_t *)buf + (size_t)i * channels;
			for (uint32_t c = 0; c < channels; c++)
				p[c] = s8;
			break;
		}
		case SOF_PCM_FMT_S24_3LE: {
			int32_t s24 = (int32_t)(sv * 4194300.0);
			uint8_t *p = (uint8_t *)buf + (size_t)i * channels * 3U;
			for (uint32_t c = 0; c < channels; c++) {
				p[c * 3U + 0] = (uint8_t)(s24 & 0xff);
				p[c * 3U + 1] = (uint8_t)((s24 >> 8) & 0xff);
				p[c * 3U + 2] = (uint8_t)((s24 >> 16) & 0xff);
			}
			break;
		}
		case SOF_PCM_FMT_S24_LE:
		case SOF_PCM_FMT_S32_LE: {
			int32_t s32 = (fmt_id == SOF_PCM_FMT_S24_LE) ?
				      (int32_t)(sv * 4194300.0) :
				      (int32_t)(sv * 1073741820.0);
			int32_t *p = (int32_t *)buf + (size_t)i * channels;
			for (uint32_t c = 0; c < channels; c++)
				p[c] = s32;
			break;
		}
		case SOF_PCM_FMT_FLOAT_LE: {
			float f = (float)(sv * 0.5);
			float *p = (float *)buf + (size_t)i * channels;
			for (uint32_t c = 0; c < channels; c++)
				p[c] = f;
			break;
		}
		case SOF_PCM_FMT_S16_LE:
		default: {
			int16_t s16 = (int16_t)(sv * 16000.0);
			int16_t *dst16 = (int16_t *)buf + (size_t)i * channels;
			for (uint32_t c = 0; c < channels; c++)
				dst16[c] = s16;
			break;
		}
		}
	}
	*phase_inout = phase;
}

int tinyalsa_pcm_play_frames(uint32_t card_id, uint32_t dev_id, uint32_t fmt_id,
			     uint32_t rate, uint32_t channels,
			     uint32_t period_size, uint32_t period_count,
			     const void *pcm_data, uint32_t total_frames,
			     _Atomic bool *keep_running, uint32_t freq_hz,
			     _Atomic uint64_t *frames_counter,
			     _Atomic uint32_t *rms_out,
			     _Atomic uint32_t *peak_out,
			     uint32_t *xruns_out)
{
	struct pcm_config cfg;
	memset(&cfg, 0, sizeof(cfg));
	cfg.channels = channels ? channels : 2;
	cfg.rate = rate ? rate : 48000;
	cfg.period_size = period_size ? period_size : 1024;
	cfg.period_count = period_count ? period_count : 4;
	cfg.format = sof_to_tinyalsa_format(fmt_id);

	struct pcm *pcm = pcm_open(card_id, dev_id, PCM_OUT, &cfg);
	if (!pcm || !pcm_is_ready(pcm)) {
		if (pcm)
			pcm_close(pcm);
		return -ENODEV;
	}

	uint32_t psize = cfg.period_size;
	uint32_t bpf = cfg.channels * sof_pcm_fmt_bytes(fmt_id);
	uint8_t *period_buf = calloc(psize, bpf);
	if (!period_buf) {
		pcm_close(pcm);
		return -ENOMEM;
	}

	double phase = 0.0;
	double step = 2.0 * M_PI * (double)(freq_hz ? freq_hz : 1000) / (double)cfg.rate;
	uint32_t written_frames = 0;
	uint32_t xruns = 0;
	int err = 0;

	while ((!keep_running || atomic_load_explicit(keep_running, memory_order_relaxed)) &&
	       (total_frames == 0 || written_frames < total_frames)) {
		uint32_t chunk = psize;
		if (total_frames > 0 && written_frames + chunk > total_frames)
			chunk = total_frames - written_frames;

		const void *src_ptr = period_buf;
		if (pcm_data) {
			src_ptr = (const uint8_t *)pcm_data + (size_t)written_frames * bpf;
		} else {
			synthesize_tiny_sine(period_buf, fmt_id, chunk,
					     cfg.channels, &phase, step);
		}

		int rc = tiny_pcm_write_frames(pcm, src_ptr, chunk, bpf);
		if (rc == -EPIPE) {
			xruns++;
			pcm_prepare(pcm);
			continue;
		} else if (rc < 0) {
			err = rc;
			break;
		}

		uint32_t nf = (uint32_t)rc;
		written_frames += nf;
		if (frames_counter)
			atomic_fetch_add_explicit(frames_counter, nf, memory_order_relaxed);
		if (rms_out || peak_out) {
			uint32_t c_rms = 0, c_peak = 0;

			sof_compute_pcm_rms_peak(src_ptr, fmt_id,
						 (size_t)nf * cfg.channels,
						 &c_rms, &c_peak);
			if (rms_out)
				atomic_store_explicit(rms_out, c_rms, memory_order_relaxed);
			if (peak_out)
				atomic_store_explicit(peak_out, c_peak, memory_order_relaxed);
		}
	}

	if (total_frames > 0 && err >= 0)
		tiny_pcm_drain(pcm);
	else
		pcm_stop(pcm);

	if (xruns_out)
		*xruns_out = xruns;
	free(period_buf);
	pcm_close(pcm);
	return err < 0 ? err : 0;
}

int tinyalsa_pcm_capture_frames(uint32_t card_id, uint32_t dev_id, uint32_t fmt_id,
				uint32_t rate, uint32_t channels,
				uint32_t period_size, uint32_t period_count,
				void *out_buf, uint32_t max_frames,
				uint32_t *frames_read_out,
				uint32_t *rms_out, uint32_t *peak_out,
				uint32_t *xruns_out)
{
	struct pcm_config cfg;
	memset(&cfg, 0, sizeof(cfg));
	cfg.channels = channels ? channels : 2;
	cfg.rate = rate ? rate : 48000;
	cfg.period_size = period_size ? period_size : 1024;
	cfg.period_count = period_count ? period_count : 4;
	cfg.format = sof_to_tinyalsa_format(fmt_id);

	struct pcm *pcm = pcm_open(card_id, dev_id, PCM_IN, &cfg);
	if (!pcm || !pcm_is_ready(pcm)) {
		if (pcm)
			pcm_close(pcm);
		return -ENODEV;
	}

	uint32_t psize = cfg.period_size;
	uint32_t bpf = cfg.channels * sof_pcm_fmt_bytes(fmt_id);
	uint32_t got_frames = 0;
	uint32_t xruns = 0;
	int err = 0;

	while (got_frames < max_frames) {
		uint32_t rem = max_frames - got_frames;
		uint32_t chunk = rem < psize ? rem : psize;
		uint8_t *dst = (uint8_t *)out_buf + (size_t)got_frames * bpf;

		int rc = tiny_pcm_read_frames(pcm, dst, chunk, bpf);
		if (rc == -EPIPE) {
			xruns++;
			pcm_prepare(pcm);
			continue;
		} else if (rc < 0) {
			err = rc;
			break;
		}
		got_frames += (uint32_t)rc;
	}

	pcm_close(pcm);
	*frames_read_out = got_frames;
	if (xruns_out)
		*xruns_out = xruns;

	sof_compute_pcm_rms_peak(out_buf, fmt_id,
				 (size_t)got_frames * cfg.channels,
				 rms_out, peak_out);
	return got_frames > 0 ? 0 : err;
}

/* -------------------------------------------------------------------------
 * 3. Kcontrol Info, Get, Set via TinyALSA (mixer_*)
 * -------------------------------------------------------------------------
 */
static int fill_tinyalsa_kctl_info(uint32_t card_id, struct mixer_ctl *ctl,
				   unsigned int idx,
				   struct sof_net_kctl_info *kinfo,
				   uint8_t *raw_bytes_out, uint32_t *raw_len_out)
{
	memset(kinfo, 0, sizeof(*kinfo));
	kinfo->card_id = card_id;
	kinfo->target_node_id = -1;
	kinfo->target_edge_id = -1;
	kinfo->pipeline_id = -1;

	/* TinyALSA mixer_ctl_get_id() returns (kernel numid - 1) */
	unsigned int cid = tiny_mixer_ctl_get_id(ctl, idx);
	kinfo->numid = (cid != UINT_MAX) ? (cid + 1U) : (idx + 1U);
	kinfo->iface = 2; /* MIXER */
	snprintf(kinfo->iface_str, sizeof(kinfo->iface_str), "MIXER");
	kinfo->device = tiny_mixer_ctl_get_device(ctl);

	const char *name = mixer_ctl_get_name(ctl);
	if (name)
		snprintf(kinfo->name, sizeof(kinfo->name), "%s", name);

	enum mixer_ctl_type mtype = mixer_ctl_get_type(ctl);
	const char *tstr = mixer_ctl_get_type_string(ctl);
	if (tstr)
		snprintf(kinfo->type_str, sizeof(kinfo->type_str), "%s", tstr);

	kinfo->count = mixer_ctl_get_num_values(ctl);
	kinfo->access_mask = SOF_KCTL_ACC_READ | SOF_KCTL_ACC_WRITE;
	snprintf(kinfo->access_str, sizeof(kinfo->access_str), "rw------");
	if (mixer_ctl_is_access_tlv_rw(ctl)) {
		kinfo->access_mask |= SOF_KCTL_ACC_TLV_READ;
		kinfo->access_str[5] = 'R';
	}

	switch (mtype) {
	case MIXER_CTL_TYPE_BOOL:
		kinfo->type = SOF_KCTL_TYPE_BOOLEAN;
		kinfo->min_val = 0;
		kinfo->max_val = 1;
		kinfo->step_val = 1;
		break;
	case MIXER_CTL_TYPE_INT:
		kinfo->type = SOF_KCTL_TYPE_INTEGER;
		kinfo->min_val = mixer_ctl_get_range_min(ctl);
		kinfo->max_val = mixer_ctl_get_range_max(ctl);
		kinfo->step_val = 1;
		break;
	case MIXER_CTL_TYPE_ENUM: {
		kinfo->type = SOF_KCTL_TYPE_ENUMERATED;
		unsigned int ne = mixer_ctl_get_num_enums(ctl);
		kinfo->num_enums = ne > 8 ? 8 : ne;
		kinfo->min_val = 0;
		kinfo->max_val = ne > 0 ? ne - 1 : 0;
		for (unsigned int e = 0; e < kinfo->num_enums; e++) {
			const char *es = mixer_ctl_get_enum_string(ctl, e);
			if (es)
				snprintf(kinfo->enum_names[e],
					 sizeof(kinfo->enum_names[e]), "%s", es);
		}
		break;
	}
	case MIXER_CTL_TYPE_BYTE:
		kinfo->type = SOF_KCTL_TYPE_BYTES;
		break;
	case MIXER_CTL_TYPE_IEC958:
		kinfo->type = SOF_KCTL_TYPE_IEC958;
		break;
	case MIXER_CTL_TYPE_INT64:
		kinfo->type = SOF_KCTL_TYPE_INTEGER64;
		break;
	default:
		kinfo->type = SOF_KCTL_TYPE_NONE;
		break;
	}

	uint32_t nch = kinfo->count > 8 ? 8 : kinfo->count;
	if (mtype == MIXER_CTL_TYPE_BOOL || mtype == MIXER_CTL_TYPE_INT ||
	    mtype == MIXER_CTL_TYPE_ENUM) {
		for (uint32_t c = 0; c < nch; c++)
			kinfo->values[c] = mixer_ctl_get_value(ctl, c);
		return 0;
	} else if (mtype == MIXER_CTL_TYPE_BYTE) {
		if (kinfo->count == 0)
			return 0;
		uint32_t blen = kinfo->count > 512 ? 512 : kinfo->count;
		uint8_t tmp[512];
		memset(tmp, 0, sizeof(tmp));
		int rc = mixer_ctl_get_array(ctl, tmp, blen);
		if (rc < 0)
			return rc;
		for (uint32_t c = 0; c < nch; c++)
			kinfo->values[c] = tmp[c];
		if (raw_bytes_out && raw_len_out) {
			memcpy(raw_bytes_out, tmp, blen);
			*raw_len_out = blen;
		}
		return 0;
	} else if (mtype == MIXER_CTL_TYPE_IEC958) {
		struct snd_aes_iec958 iec;
		memset(&iec, 0, sizeof(iec));
		int rc = mixer_ctl_get_array(ctl, &iec, 1);
		if (rc < 0)
			return rc;
		for (int i = 0; i < 4; i++)
			kinfo->values[i] = iec.status[i];
		if (raw_bytes_out && raw_len_out) {
			memcpy(raw_bytes_out, iec.status, sizeof(iec.status));
			*raw_len_out = sizeof(iec.status);
		}
		return 0;
	}

	return -ENOTSUP;
}

int tinyalsa_kctl_list(uint32_t card_id, struct sof_net_kctl_info *out_arr,
		       uint32_t max_items, uint32_t *count_out)
{
	*count_out = 0;
	struct mixer *mixer = mixer_open(card_id);
	if (!mixer)
		return -ENODEV;

	unsigned int total = mixer_get_num_ctls(mixer);
	if (total > max_items)
		total = max_items;

	for (unsigned int i = 0; i < total; i++) {
		struct mixer_ctl *ctl = mixer_get_ctl(mixer, i);
		if (ctl)
			(void)fill_tinyalsa_kctl_info(card_id, ctl, i, &out_arr[i], NULL, NULL);
	}

	*count_out = total;
	mixer_close(mixer);

	dapm_resolve_kctl_anchors(card_id, out_arr, total);
	return 0;
}

static struct mixer_ctl *find_tiny_ctl(struct mixer *mixer,
				       const struct sof_net_kctl_io_req *req,
				       unsigned int *idx_out, int *err_out)
{
	unsigned int total = mixer_get_num_ctls(mixer);
	unsigned int target_numid = req->numid;

	if (err_out)
		*err_out = -ENOENT;

	if (target_numid == 0 && req->name[0]) {
		char cpath[64];
		snprintf(cpath, sizeof(cpath), "/dev/snd/controlC%u", req->card_id);
		int cfd = open(cpath, O_RDONLY | O_CLOEXEC);
		if (cfd >= 0) {
			struct snd_ctl_elem_info ei;
			memset(&ei, 0, sizeof(ei));
			ei.id.iface = req->iface ? req->iface : SNDRV_CTL_ELEM_IFACE_MIXER;
			ei.id.device = req->device;
			ei.id.index = req->index;
			snprintf((char *)ei.id.name, sizeof(ei.id.name), "%s", req->name);
			if (ioctl(cfd, SNDRV_CTL_IOCTL_ELEM_INFO, &ei) == 0 &&
			    ei.id.numid > 0) {
				target_numid = ei.id.numid;
			} else {
				if (err_out)
					*err_out = errno ? -errno : -ENOENT;
				close(cfd);
				return NULL;
			}
			close(cfd);
		} else {
			if ((req->iface != 0 && req->iface != SNDRV_CTL_ELEM_IFACE_MIXER) ||
			    req->index != 0) {
				if (err_out)
					*err_out = -EINVAL;
				return NULL;
			}
			struct mixer_ctl *matched = NULL;
			unsigned int matched_idx = 0;
			unsigned int match_count = 0;
			for (unsigned int i = 0; i < total; i++) {
				struct mixer_ctl *c = mixer_get_ctl(mixer, i);
				if (!c)
					continue;
				const char *nm = mixer_ctl_get_name(c);
				if (nm && strcmp(nm, req->name) == 0 &&
				    tiny_mixer_ctl_get_device(c) == req->device) {
					matched = c;
					matched_idx = i;
					match_count++;
				}
			}
			if (match_count > 1) {
				if (err_out)
					*err_out = -EINVAL;
				return NULL;
			}
			if (match_count == 1) {
				*idx_out = matched_idx;
				if (err_out)
					*err_out = 0;
				return matched;
			}
			return NULL;
		}
	}

	if (target_numid > 0) {
		for (unsigned int i = 0; i < total; i++) {
			struct mixer_ctl *c = mixer_get_ctl(mixer, i);
			if (!c)
				continue;
			unsigned int cid = tiny_mixer_ctl_get_id(c, i);
			unsigned int numid = (cid != UINT_MAX) ? (cid + 1U) : (i + 1U);
			if (numid == target_numid) {
				*idx_out = i;
				if (err_out)
					*err_out = 0;
				return c;
			}
		}
	}

	return NULL;
}

int tinyalsa_kctl_get(const struct sof_net_kctl_io_req *req,
		      struct sof_net_kctl_io_rsp *rsp)
{
	memset(rsp, 0, sizeof(*rsp));
	rsp->api_used = SOF_AUDIO_API_TINYALSA;

	struct mixer *mixer = mixer_open(req->card_id);
	if (!mixer) {
		rsp->status = -ENODEV;
		return -ENODEV;
	}

	unsigned int idx = 0;
	int find_err = -ENOENT;
	struct mixer_ctl *ctl = find_tiny_ctl(mixer, req, &idx, &find_err);
	if (!ctl) {
		mixer_close(mixer);
		rsp->status = find_err;
		return find_err;
	}

	uint32_t raw_len = 0;
	int err = fill_tinyalsa_kctl_info(req->card_id, ctl, idx, &rsp->info,
					  rsp->bytes_data, &raw_len);
	rsp->bytes_len = raw_len;
	mixer_close(mixer);

	if (err < 0) {
		rsp->status = err;
		return err;
	}

	dapm_resolve_kctl_anchors(req->card_id, &rsp->info, 1);
	rsp->status = 0;
	return 0;
}

int tinyalsa_kctl_set(const struct sof_net_kctl_io_req *req,
		      struct sof_net_kctl_io_rsp *rsp)
{
	memset(rsp, 0, sizeof(*rsp));
	rsp->api_used = SOF_AUDIO_API_TINYALSA;

	if (req->count > (sizeof(req->values) / sizeof(req->values[0])) ||
	    req->bytes_len > sizeof(req->bytes_data)) {
		rsp->status = -EINVAL;
		return -EINVAL;
	}

	struct mixer *mixer = mixer_open(req->card_id);
	if (!mixer) {
		rsp->status = -ENODEV;
		return -ENODEV;
	}

	unsigned int idx = 0;
	int find_err = -ENOENT;
	struct mixer_ctl *ctl = find_tiny_ctl(mixer, req, &idx, &find_err);
	if (!ctl) {
		mixer_close(mixer);
		rsp->status = find_err;
		return find_err;
	}

	int err = 0;
	enum mixer_ctl_type mtype = mixer_ctl_get_type(ctl);
	unsigned int nch = mixer_ctl_get_num_values(ctl);
	uint32_t max_vals = (uint32_t)(sizeof(req->values) / sizeof(req->values[0]));
	uint32_t req_cnt = req->count <= max_vals ? req->count : max_vals;
	uint32_t req_blen = req->bytes_len <= sizeof(req->bytes_data) ?
			    req->bytes_len : (uint32_t)sizeof(req->bytes_data);

	if (mtype == MIXER_CTL_TYPE_BYTE && req_blen > 0) {
		err = mixer_ctl_set_array(ctl, req->bytes_data, req_blen);
	} else if (mtype == MIXER_CTL_TYPE_IEC958) {
		struct snd_aes_iec958 iec;
		memset(&iec, 0, sizeof(iec));
		(void)mixer_ctl_get_array(ctl, &iec, 1);
		if (req_blen > 0) {
			uint32_t copy_len = req_blen < sizeof(iec.status) ?
					    req_blen : (uint32_t)sizeof(iec.status);
			memcpy(iec.status, req->bytes_data, copy_len);
		} else if (req_cnt > 0) {
			uint32_t n = req_cnt < sizeof(iec.status) ?
				     req_cnt : (uint32_t)sizeof(iec.status);
			for (uint32_t i = 0; i < n; i++)
				iec.status[i] = (unsigned char)req->values[i];
		}
		err = mixer_ctl_set_array(ctl, &iec, 1);
	} else if (mtype == MIXER_CTL_TYPE_BOOL || mtype == MIXER_CTL_TYPE_INT ||
		   mtype == MIXER_CTL_TYPE_ENUM || mtype == MIXER_CTL_TYPE_BYTE) {
		for (unsigned int c = 0; c < nch; c++) {
			int val = (int)((c < req_cnt) ? req->values[c] : req->values[0]);
			int rc = mixer_ctl_set_value(ctl, c, val);
			if (rc < 0)
				err = rc;
		}
	} else {
		err = -ENOTSUP;
	}

	uint32_t raw_len = 0;
	(void)fill_tinyalsa_kctl_info(req->card_id, ctl, idx, &rsp->info,
				      rsp->bytes_data, &raw_len);
	rsp->bytes_len = raw_len;
	mixer_close(mixer);

	dapm_resolve_kctl_anchors(req->card_id, &rsp->info, 1);
	rsp->status = err < 0 ? err : 0;
	return rsp->status;
}

#else /* !HAVE_TINYALSA */

int tinyalsa_query_pcm_caps(uint32_t card_id, uint32_t dev_id, uint32_t direction,
			    struct sof_net_pcm_caps *out)
{
	(void)card_id;
	(void)dev_id;
	(void)direction;
	if (out)
		out->status = -ENOTSUP;
	return -ENOTSUP;
}

int tinyalsa_pcm_play_frames(uint32_t card_id, uint32_t dev_id, uint32_t fmt_id,
			     uint32_t rate, uint32_t channels,
			     uint32_t period_size, uint32_t period_count,
			     const void *pcm_data, uint32_t total_frames,
			     _Atomic bool *keep_running, uint32_t freq_hz,
			     _Atomic uint64_t *frames_counter,
			     _Atomic uint32_t *rms_out,
			     _Atomic uint32_t *peak_out,
			     uint32_t *xruns_out)
{
	(void)card_id;
	(void)dev_id;
	(void)fmt_id;
	(void)rate;
	(void)channels;
	(void)period_size;
	(void)period_count;
	(void)pcm_data;
	(void)total_frames;
	(void)keep_running;
	(void)freq_hz;
	(void)frames_counter;
	(void)rms_out;
	(void)peak_out;
	(void)xruns_out;
	return -ENOTSUP;
}

int tinyalsa_pcm_capture_frames(uint32_t card_id, uint32_t dev_id, uint32_t fmt_id,
				uint32_t rate, uint32_t channels,
				uint32_t period_size, uint32_t period_count,
				void *out_buf, uint32_t max_frames,
				uint32_t *frames_read_out,
				uint32_t *rms_out, uint32_t *peak_out,
				uint32_t *xruns_out)
{
	(void)card_id;
	(void)dev_id;
	(void)fmt_id;
	(void)rate;
	(void)channels;
	(void)period_size;
	(void)period_count;
	(void)out_buf;
	(void)max_frames;
	(void)frames_read_out;
	(void)rms_out;
	(void)peak_out;
	(void)xruns_out;
	return -ENOTSUP;
}

int tinyalsa_kctl_list(uint32_t card_id, struct sof_net_kctl_info *out_arr,
		       uint32_t max_items, uint32_t *count_out)
{
	(void)card_id;
	(void)out_arr;
	(void)max_items;
	if (count_out)
		*count_out = 0;
	return -ENOTSUP;
}

int tinyalsa_kctl_get(const struct sof_net_kctl_io_req *req,
		      struct sof_net_kctl_io_rsp *rsp)
{
	(void)req;
	if (rsp)
		rsp->status = -ENOTSUP;
	return -ENOTSUP;
}

int tinyalsa_kctl_set(const struct sof_net_kctl_io_req *req,
		      struct sof_net_kctl_io_rsp *rsp)
{
	(void)req;
	if (rsp)
		rsp->status = -ENOTSUP;
	return -ENOTSUP;
}

#endif /* HAVE_TINYALSA */
