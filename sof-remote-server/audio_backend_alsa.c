// SPDX-License-Identifier: (GPL-2.0-only OR BSD-3-Clause)
/*
 * audio_backend_alsa.c — Native alsa-lib (libasound.so.2) backend for
 * PCM Capability Discovery, PCM Playback/Capture, and Kcontrol Info/Get/Set.
 */

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <math.h>
#include <errno.h>
#include <alsa/asoundlib.h>
#include <sound/tlv.h>

#include "audio_backends.h"

int sof_parse_card_dev(const char *dev_str, uint32_t *card_out, uint32_t *dev_out)
{
	unsigned int c = 0, d = 0;

	if (!dev_str || !dev_str[0]) {
		*card_out = 0;
		*dev_out = 0;
		return 0;
	}
	if (sscanf(dev_str, "hw:%u,%u", &c, &d) == 2 ||
	    sscanf(dev_str, "plughw:%u,%u", &c, &d) == 2 ||
	    sscanf(dev_str, "%u,%u", &c, &d) == 2 ||
	    sscanf(dev_str, "pcmC%uD%u", &c, &d) == 2) {
		*card_out = c;
		*dev_out = d;
		return 0;
	}
	*card_out = 0;
	*dev_out = 0;
	return -EINVAL;
}

uint32_t sof_pcm_fmt_bytes(uint32_t fmt_id)
{
	switch (fmt_id) {
	case SOF_PCM_FMT_S8:
		return 1;
	case SOF_PCM_FMT_S24_3LE:
		return 3;
	case SOF_PCM_FMT_S24_LE:
	case SOF_PCM_FMT_S32_LE:
	case SOF_PCM_FMT_FLOAT_LE:
		return 4;
	case SOF_PCM_FMT_S16_LE:
	default:
		return 2;
	}
}

const char *sof_pcm_fmt_name(uint32_t fmt_id)
{
	switch (fmt_id) {
	case SOF_PCM_FMT_S8:       return "S8";
	case SOF_PCM_FMT_S16_LE:   return "S16_LE";
	case SOF_PCM_FMT_S24_LE:   return "S24_LE";
	case SOF_PCM_FMT_S24_3LE:  return "S24_3LE";
	case SOF_PCM_FMT_S32_LE:   return "S32_LE";
	case SOF_PCM_FMT_FLOAT_LE: return "FLOAT_LE";
	default:                   return "S16_LE";
	}
}

static snd_pcm_format_t sof_to_alsa_format(uint32_t fmt_id)
{
	switch (fmt_id) {
	case SOF_PCM_FMT_S8:       return SND_PCM_FORMAT_S8;
	case SOF_PCM_FMT_S24_LE:   return SND_PCM_FORMAT_S24_LE;
	case SOF_PCM_FMT_S24_3LE:  return SND_PCM_FORMAT_S24_3LE;
	case SOF_PCM_FMT_S32_LE:   return SND_PCM_FORMAT_S32_LE;
	case SOF_PCM_FMT_FLOAT_LE: return SND_PCM_FORMAT_FLOAT_LE;
	case SOF_PCM_FMT_S16_LE:
	default:                   return SND_PCM_FORMAT_S16_LE;
	}
}

void sof_format_mask_to_str(uint64_t mask, char *buf, size_t cap)
{
	static const struct {
		uint64_t bit;
		const char *name;
	} tbl[] = {
		{ SOF_FMTBIT_S8,       "S8" },
		{ SOF_FMTBIT_U8,       "U8" },
		{ SOF_FMTBIT_S16_LE,   "S16_LE" },
		{ SOF_FMTBIT_S16_BE,   "S16_BE" },
		{ SOF_FMTBIT_S24_LE,   "S24_LE" },
		{ SOF_FMTBIT_S24_BE,   "S24_BE" },
		{ SOF_FMTBIT_S24_3LE,  "S24_3LE" },
		{ SOF_FMTBIT_S24_3BE,  "S24_3BE" },
		{ SOF_FMTBIT_S32_LE,   "S32_LE" },
		{ SOF_FMTBIT_S32_BE,   "S32_BE" },
		{ SOF_FMTBIT_FLOAT_LE, "FLOAT_LE" },
	};
	size_t off = 0;

	buf[0] = '\0';
	for (size_t i = 0; i < sizeof(tbl) / sizeof(tbl[0]); i++) {
		if (mask & tbl[i].bit) {
			int n = snprintf(buf + off, cap - off, "%s%s",
					 off ? " " : "", tbl[i].name);
			if (n > 0 && off + (size_t)n < cap)
				off += (size_t)n;
		}
	}
	if (!off)
		snprintf(buf, cap, "NONE");
}

void sof_rate_mask_to_str(uint32_t mask, uint32_t rmin, uint32_t rmax,
			  char *buf, size_t cap)
{
	static const struct {
		uint32_t bit;
		uint32_t rate;
	} rtbl[] = {
		{ SOF_RATEBIT_8000,   8000 },
		{ SOF_RATEBIT_11025,  11025 },
		{ SOF_RATEBIT_16000,  16000 },
		{ SOF_RATEBIT_22050,  22050 },
		{ SOF_RATEBIT_32000,  32000 },
		{ SOF_RATEBIT_44100,  44100 },
		{ SOF_RATEBIT_48000,  48000 },
		{ SOF_RATEBIT_88200,  88200 },
		{ SOF_RATEBIT_96000,  96000 },
		{ SOF_RATEBIT_176400, 176400 },
		{ SOF_RATEBIT_192000, 192000 },
	};
	size_t off = 0;

	buf[0] = '\0';
	for (size_t i = 0; i < sizeof(rtbl) / sizeof(rtbl[0]); i++) {
		if (mask & rtbl[i].bit) {
			int n = snprintf(buf + off, cap - off, "%s%u",
					 off ? " " : "", rtbl[i].rate);
			if (n > 0 && off + (size_t)n < cap)
				off += (size_t)n;
		}
	}
	if (!off && (rmin || rmax))
		snprintf(buf, cap, "%u..%u", rmin, rmax);
	else if (!off)
		snprintf(buf, cap, "NONE");
}

/* -------------------------------------------------------------------------
 * 1. PCM Capabilities via alsa-lib (snd_pcm_hw_params_any)
 * -------------------------------------------------------------------------
 */
int alsa_query_pcm_caps(uint32_t card_id, uint32_t dev_id, uint32_t direction,
			struct sof_net_pcm_caps *out)
{
	char dev_name[32];
	snd_pcm_t *pcm = NULL;
	snd_pcm_hw_params_t *params = NULL;
	snd_pcm_stream_t stream = (direction == SOF_STREAM_DIR_CAPTURE) ?
				  SND_PCM_STREAM_CAPTURE : SND_PCM_STREAM_PLAYBACK;

	out->card_id = card_id;
	out->device_id = dev_id;
	out->direction = direction;
	out->is_compress = 0;
	out->api_used = SOF_AUDIO_API_ALSA;

	snprintf(dev_name, sizeof(dev_name), "hw:%u,%u", card_id, dev_id);
	int err = snd_pcm_open(&pcm, dev_name, stream, SND_PCM_NONBLOCK);
	if (err < 0) {
		out->status = err;
		return err;
	}

	snd_pcm_hw_params_malloc(&params);
	err = snd_pcm_hw_params_any(pcm, params);
	if (err < 0) {
		snd_pcm_hw_params_free(params);
		snd_pcm_close(pcm);
		out->status = err;
		return err;
	}

	/* Formats */
	static const struct {
		snd_pcm_format_t afmt;
		uint64_t bit;
		uint32_t bits;
	} fmts[] = {
		{ SND_PCM_FORMAT_S8,       SOF_FMTBIT_S8,       8 },
		{ SND_PCM_FORMAT_U8,       SOF_FMTBIT_U8,       8 },
		{ SND_PCM_FORMAT_S16_LE,   SOF_FMTBIT_S16_LE,   16 },
		{ SND_PCM_FORMAT_S16_BE,   SOF_FMTBIT_S16_BE,   16 },
		{ SND_PCM_FORMAT_S24_LE,   SOF_FMTBIT_S24_LE,   32 },
		{ SND_PCM_FORMAT_S24_BE,   SOF_FMTBIT_S24_BE,   32 },
		{ SND_PCM_FORMAT_S24_3LE,  SOF_FMTBIT_S24_3LE,  24 },
		{ SND_PCM_FORMAT_S24_3BE,  SOF_FMTBIT_S24_3BE,  24 },
		{ SND_PCM_FORMAT_S32_LE,   SOF_FMTBIT_S32_LE,   32 },
		{ SND_PCM_FORMAT_S32_BE,   SOF_FMTBIT_S32_BE,   32 },
		{ SND_PCM_FORMAT_FLOAT_LE, SOF_FMTBIT_FLOAT_LE, 32 },
	};
	uint32_t sbits_min = 999, sbits_max = 0;

	for (size_t i = 0; i < sizeof(fmts) / sizeof(fmts[0]); i++) {
		if (snd_pcm_hw_params_test_format(pcm, params, fmts[i].afmt) == 0) {
			out->formats_mask |= fmts[i].bit;
			if (fmts[i].bits < sbits_min)
				sbits_min = fmts[i].bits;
			if (fmts[i].bits > sbits_max)
				sbits_max = fmts[i].bits;
		}
	}
	out->sample_bits_min = (sbits_min <= sbits_max) ? sbits_min : 0;
	out->sample_bits_max = sbits_max;

	/* Access mask */
	if (snd_pcm_hw_params_test_access(pcm, params, SND_PCM_ACCESS_MMAP_INTERLEAVED) == 0)
		out->access_mask |= (1U << 0);
	if (snd_pcm_hw_params_test_access(pcm, params, SND_PCM_ACCESS_MMAP_NONINTERLEAVED) == 0)
		out->access_mask |= (1U << 1);
	if (snd_pcm_hw_params_test_access(pcm, params, SND_PCM_ACCESS_RW_INTERLEAVED) == 0)
		out->access_mask |= (1U << 3);
	if (snd_pcm_hw_params_test_access(pcm, params, SND_PCM_ACCESS_RW_NONINTERLEAVED) == 0)
		out->access_mask |= (1U << 4);

	/* Channels & Rates */
	unsigned int val = 0;
	int dir = 0;

	if (snd_pcm_hw_params_get_channels_min(params, &val) == 0)
		out->channels_min = val;
	if (snd_pcm_hw_params_get_channels_max(params, &val) == 0)
		out->channels_max = val;

	if (snd_pcm_hw_params_get_rate_min(params, &val, &dir) == 0)
		out->rate_min = val;
	if (snd_pcm_hw_params_get_rate_max(params, &val, &dir) == 0)
		out->rate_max = val;

	static const struct {
		unsigned int rate;
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
	for (size_t i = 0; i < sizeof(rates) / sizeof(rates[0]); i++) {
		if (snd_pcm_hw_params_test_rate(pcm, params, rates[i].rate, 0) == 0)
			out->rates_mask |= rates[i].bit;
	}

	/* Period & Buffer intervals */
	snd_pcm_uframes_t uframes = 0;

	if (snd_pcm_hw_params_get_period_size_min(params, &uframes, &dir) == 0)
		out->period_size_min = (uint32_t)uframes;
	if (snd_pcm_hw_params_get_period_size_max(params, &uframes, &dir) == 0)
		out->period_size_max = (uint32_t)uframes;

	if (snd_pcm_hw_params_get_periods_min(params, &val, &dir) == 0)
		out->periods_min = val;
	if (snd_pcm_hw_params_get_periods_max(params, &val, &dir) == 0)
		out->periods_max = val;

	if (snd_pcm_hw_params_get_buffer_size_min(params, &uframes) == 0)
		out->buffer_size_min = (uint32_t)uframes;
	if (snd_pcm_hw_params_get_buffer_size_max(params, &uframes) == 0)
		out->buffer_size_max = (uint32_t)uframes;

	uint32_t min_bpf = out->channels_min * ((out->sample_bits_min + 7) / 8);
	uint32_t max_bpf = out->channels_max * ((out->sample_bits_max + 7) / 8);
	out->period_bytes_min = out->period_size_min * min_bpf;
	out->period_bytes_max = out->period_size_max * max_bpf;
	out->buffer_bytes_min = out->buffer_size_min * min_bpf;
	out->buffer_bytes_max = out->buffer_size_max * max_bpf;

	sof_format_mask_to_str(out->formats_mask, out->formats_str, sizeof(out->formats_str));
	sof_rate_mask_to_str(out->rates_mask, out->rate_min, out->rate_max,
			     out->rates_str, sizeof(out->rates_str));

	snd_pcm_hw_params_free(params);
	snd_pcm_close(pcm);
	out->status = 0;
	return 0;
}

/* -------------------------------------------------------------------------
 * 2. Native alsa-lib Playback & Capture
 * -------------------------------------------------------------------------
 */
static void synthesize_sine_period(void *buf, uint32_t fmt_id, uint32_t frames,
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

void sof_compute_pcm_rms_peak(const void *buf, uint32_t fmt_id, size_t n_samp,
			      uint32_t *rms_out, uint32_t *peak_out)
{
	double sum_sq = 0.0;
	uint32_t peak = 0;

	if (buf && n_samp > 0) {
		switch (fmt_id) {
		case SOF_PCM_FMT_S8: {
			const int8_t *s = (const int8_t *)buf;
			for (size_t i = 0; i < n_samp; i++) {
				int32_t v = (int32_t)s[i] * 256;
				uint32_t av = (uint32_t)(v < 0 ? -v : v);
				if (av > peak)
					peak = av;
				sum_sq += (double)v * (double)v;
			}
			break;
		}
		case SOF_PCM_FMT_S24_LE: {
			const uint32_t *s = (const uint32_t *)buf;
			for (size_t i = 0; i < n_samp; i++) {
				uint32_t u24 = s[i] & 0x00FFFFFFU;
				int32_t v24 = (u24 & 0x00800000U) ?
					      ((int32_t)u24 - 0x01000000) : (int32_t)u24;
				int32_t v = v24 >> 8;
				uint32_t av = (uint32_t)(v < 0 ? -v : v);
				if (av > peak)
					peak = av;
				sum_sq += (double)v * (double)v;
			}
			break;
		}
		case SOF_PCM_FMT_S24_3LE: {
			const uint8_t *p = (const uint8_t *)buf;
			for (size_t i = 0; i < n_samp; i++) {
				int32_t u24 = (int32_t)p[i * 3] |
					      ((int32_t)p[i * 3 + 1] << 8) |
					      ((int32_t)(int8_t)p[i * 3 + 2] * 65536);
				int32_t v = u24 >> 8;
				uint32_t av = (uint32_t)(v < 0 ? -v : v);
				if (av > peak)
					peak = av;
				sum_sq += (double)v * (double)v;
			}
			break;
		}
		case SOF_PCM_FMT_S32_LE: {
			const int32_t *s = (const int32_t *)buf;
			for (size_t i = 0; i < n_samp; i++) {
				int64_t v = (int64_t)s[i] >> 16;
				uint32_t av = (uint32_t)(v < 0 ? -v : v);
				if (av > peak)
					peak = av;
				sum_sq += (double)v * (double)v;
			}
			break;
		}
		case SOF_PCM_FMT_FLOAT_LE: {
			const float *f = (const float *)buf;
			for (size_t i = 0; i < n_samp; i++) {
				double v = (double)f[i] * 32767.0;
				uint32_t av = (uint32_t)(v < 0.0 ? -v : v);
				if (av > peak)
					peak = av;
				sum_sq += v * v;
			}
			break;
		}
		case SOF_PCM_FMT_S16_LE:
		default: {
			const int16_t *s = (const int16_t *)buf;
			for (size_t i = 0; i < n_samp; i++) {
				int32_t v = s[i];
				uint32_t av = (uint32_t)(v < 0 ? -v : v);
				if (av > peak)
					peak = av;
				sum_sq += (double)v * (double)v;
			}
			break;
		}
		}
	}

	if (rms_out)
		*rms_out = n_samp ? (uint32_t)sqrt(sum_sq / (double)n_samp) : 0;
	if (peak_out)
		*peak_out = peak;
}

static int configure_alsa_pcm(snd_pcm_t *pcm, uint32_t fmt_id, uint32_t rate,
			      uint32_t channels, uint32_t *period_size_inout,
			      uint32_t period_count)
{
	snd_pcm_hw_params_t *hw = NULL;
	snd_pcm_hw_params_malloc(&hw);
	int err = snd_pcm_hw_params_any(pcm, hw);
	if (err < 0)
		goto out;
	err = snd_pcm_hw_params_set_access(pcm, hw, SND_PCM_ACCESS_RW_INTERLEAVED);
	if (err < 0)
		goto out;
	err = snd_pcm_hw_params_set_format(pcm, hw, sof_to_alsa_format(fmt_id));
	if (err < 0)
		goto out;
	err = snd_pcm_hw_params_set_channels(pcm, hw, channels);
	if (err < 0)
		goto out;
	err = snd_pcm_hw_params_set_rate(pcm, hw, rate, 0);
	if (err < 0)
		goto out;
	snd_pcm_uframes_t ps = *period_size_inout ? *period_size_inout : 1024;
	snd_pcm_hw_params_set_period_size_near(pcm, hw, &ps, 0);
	snd_pcm_uframes_t bs = ps * (period_count ? period_count : 4);
	snd_pcm_hw_params_set_buffer_size_near(pcm, hw, &bs);
	err = snd_pcm_hw_params(pcm, hw);
	if (err == 0)
		*period_size_inout = (uint32_t)ps;
out:
	snd_pcm_hw_params_free(hw);
	return err;
}

int alsa_pcm_play_frames(const char *dev_name, uint32_t fmt_id, uint32_t rate,
			 uint32_t channels, uint32_t period_size,
			 uint32_t period_count, const void *pcm_data,
			 uint32_t total_frames, _Atomic bool *keep_running,
			 uint32_t freq_hz, _Atomic uint64_t *frames_counter,
			 _Atomic uint32_t *rms_out, _Atomic uint32_t *peak_out,
			 uint32_t *xruns_out)
{
	snd_pcm_t *pcm = NULL;
	const char *dev = (dev_name && dev_name[0]) ? dev_name : "hw:0,0";
	int err = snd_pcm_open(&pcm, dev, SND_PCM_STREAM_PLAYBACK, 0);
	if (err < 0)
		return err;

	uint32_t psize = period_size ? period_size : 1024;
	err = configure_alsa_pcm(pcm, fmt_id, rate, channels, &psize,
				 period_count ? period_count : 4);
	if (err < 0) {
		snd_pcm_close(pcm);
		return err;
	}

	uint32_t bpf = channels * sof_pcm_fmt_bytes(fmt_id);
	uint8_t *period_buf = calloc(psize, bpf);
	if (!period_buf) {
		snd_pcm_close(pcm);
		return -ENOMEM;
	}

	double phase = 0.0;
	double step = 2.0 * M_PI * (double)(freq_hz ? freq_hz : 1000) / (double)rate;
	uint32_t written_frames = 0;
	uint32_t xruns = 0;

	while ((!keep_running || atomic_load_explicit(keep_running, memory_order_relaxed)) &&
	       (total_frames == 0 || written_frames < total_frames)) {
		uint32_t chunk = psize;
		if (total_frames > 0 && written_frames + chunk > total_frames)
			chunk = total_frames - written_frames;

		const void *src_ptr = period_buf;
		if (pcm_data) {
			src_ptr = (const uint8_t *)pcm_data + (size_t)written_frames * bpf;
		} else {
			synthesize_sine_period(period_buf, fmt_id, chunk,
					       channels, &phase, step);
		}

		snd_pcm_sframes_t nw = snd_pcm_writei(pcm, src_ptr, chunk);
		if (nw == -EPIPE || nw == -ESTRPIPE) {
			xruns++;
			snd_pcm_recover(pcm, (int)nw, 1);
			continue;
		} else if (nw < 0) {
			err = (int)nw;
			break;
		}

		written_frames += (uint32_t)nw;
		if (frames_counter)
			atomic_fetch_add_explicit(frames_counter, (uint64_t)nw,
						  memory_order_relaxed);
		if (rms_out || peak_out) {
			uint32_t c_rms = 0, c_peak = 0;

			sof_compute_pcm_rms_peak(src_ptr, fmt_id,
						 (size_t)nw * channels,
						 &c_rms, &c_peak);
			if (rms_out)
				atomic_store_explicit(rms_out, c_rms, memory_order_relaxed);
			if (peak_out)
				atomic_store_explicit(peak_out, c_peak, memory_order_relaxed);
		}
	}

	if (total_frames > 0 && err >= 0)
		snd_pcm_drain(pcm);
	else
		snd_pcm_drop(pcm);

	if (xruns_out)
		*xruns_out = xruns;
	free(period_buf);
	snd_pcm_close(pcm);
	return err < 0 ? err : 0;
}

int alsa_pcm_capture_frames(const char *dev_name, uint32_t fmt_id, uint32_t rate,
			    uint32_t channels, uint32_t period_size,
			    uint32_t period_count, void *out_buf,
			    uint32_t max_frames, uint32_t *frames_read_out,
			    uint32_t *rms_out, uint32_t *peak_out,
			    uint32_t *xruns_out)
{
	snd_pcm_t *pcm = NULL;
	const char *dev = (dev_name && dev_name[0]) ? dev_name : "hw:0,0";
	int err = snd_pcm_open(&pcm, dev, SND_PCM_STREAM_CAPTURE, 0);
	if (err < 0)
		return err;

	uint32_t psize = period_size ? period_size : 1024;
	err = configure_alsa_pcm(pcm, fmt_id, rate, channels, &psize,
				 period_count ? period_count : 4);
	if (err < 0) {
		snd_pcm_close(pcm);
		return err;
	}

	uint32_t bpf = channels * sof_pcm_fmt_bytes(fmt_id);
	uint32_t got_frames = 0;
	uint32_t xruns = 0;

	while (got_frames < max_frames) {
		uint32_t rem = max_frames - got_frames;
		uint32_t chunk = rem < psize ? rem : psize;
		uint8_t *dst = (uint8_t *)out_buf + (size_t)got_frames * bpf;

		snd_pcm_sframes_t nr = snd_pcm_readi(pcm, dst, chunk);
		if (nr == -EPIPE || nr == -ESTRPIPE) {
			xruns++;
			snd_pcm_recover(pcm, (int)nr, 1);
			continue;
		} else if (nr < 0) {
			err = (int)nr;
			break;
		}
		got_frames += (uint32_t)nr;
	}

	snd_pcm_close(pcm);
	*frames_read_out = got_frames;
	if (xruns_out)
		*xruns_out = xruns;

	/* Compute RMS & Peak */
	sof_compute_pcm_rms_peak(out_buf, fmt_id,
				 (size_t)got_frames * channels,
				 rms_out, peak_out);
	return got_frames > 0 ? 0 : err;
}

/* -------------------------------------------------------------------------
 * 3. Kcontrol Info, Get, Set via alsa-lib (snd_ctl_*)
 * -------------------------------------------------------------------------
 */
static int fill_alsa_kctl_info(snd_ctl_t *ctl, uint32_t card_id,
			       snd_ctl_elem_id_t *id,
			       struct sof_net_kctl_info *kinfo,
			       uint8_t *raw_bytes_out, uint32_t *raw_len_out)
{
	snd_ctl_elem_info_t *info = NULL;
	snd_ctl_elem_value_t *val = NULL;

	snd_ctl_elem_info_alloca(&info);
	snd_ctl_elem_value_alloca(&val);

	memset(kinfo, 0, sizeof(*kinfo));
	kinfo->card_id = card_id;
	kinfo->target_node_id = -1;
	kinfo->target_edge_id = -1;
	kinfo->pipeline_id = -1;

	snd_ctl_elem_info_set_id(info, id);
	int info_err = snd_ctl_elem_info(ctl, info);
	if (info_err < 0)
		return info_err;

	kinfo->numid = snd_ctl_elem_info_get_numid(info);
	snd_ctl_elem_iface_t iface = snd_ctl_elem_info_get_interface(info);
	kinfo->iface = (uint32_t)iface;
	snprintf(kinfo->iface_str, sizeof(kinfo->iface_str), "%s",
		 snd_ctl_elem_iface_name(iface));
	kinfo->device = snd_ctl_elem_info_get_device(info);
	kinfo->subdevice = snd_ctl_elem_info_get_subdevice(info);
	kinfo->index = snd_ctl_elem_info_get_index(info);
	snprintf(kinfo->name, sizeof(kinfo->name), "%s",
		 snd_ctl_elem_info_get_name(info));

	snd_ctl_elem_type_t etype = snd_ctl_elem_info_get_type(info);
	kinfo->type = (uint32_t)etype;
	snprintf(kinfo->type_str, sizeof(kinfo->type_str), "%s",
		 snd_ctl_elem_type_name(etype));
	kinfo->count = snd_ctl_elem_info_get_count(info);

	/* Access flags */
	char acc[12] = "--------";
	if (snd_ctl_elem_info_is_readable(info)) {
		kinfo->access_mask |= SOF_KCTL_ACC_READ;
		acc[0] = 'r';
	}
	if (snd_ctl_elem_info_is_writable(info)) {
		kinfo->access_mask |= SOF_KCTL_ACC_WRITE;
		acc[1] = 'w';
	}
	if (snd_ctl_elem_info_is_volatile(info)) {
		kinfo->access_mask |= SOF_KCTL_ACC_VOLATILE;
		acc[3] = 'v';
	}
	if (snd_ctl_elem_info_is_inactive(info)) {
		kinfo->access_mask |= SOF_KCTL_ACC_INACTIVE;
		acc[4] = 'i';
	}
	if (snd_ctl_elem_info_is_locked(info)) {
		kinfo->access_mask |= SOF_KCTL_ACC_LOCK;
		acc[2] = 'l';
	}
	if (snd_ctl_elem_info_is_tlv_readable(info)) {
		kinfo->access_mask |= SOF_KCTL_ACC_TLV_READ;
		acc[5] = 'R';
	}
	if (snd_ctl_elem_info_is_tlv_writable(info)) {
		kinfo->access_mask |= SOF_KCTL_ACC_TLV_WRITE;
		acc[6] = 'W';
	}
	snprintf(kinfo->access_str, sizeof(kinfo->access_str), "%s", acc);

	/* Type-specific metadata */
	switch (etype) {
	case SND_CTL_ELEM_TYPE_BOOLEAN:
		kinfo->min_val = 0;
		kinfo->max_val = 1;
		kinfo->step_val = 1;
		break;
	case SND_CTL_ELEM_TYPE_INTEGER:
		kinfo->min_val = snd_ctl_elem_info_get_min(info);
		kinfo->max_val = snd_ctl_elem_info_get_max(info);
		kinfo->step_val = snd_ctl_elem_info_get_step(info);
		break;
	case SND_CTL_ELEM_TYPE_INTEGER64:
		kinfo->min_val = snd_ctl_elem_info_get_min64(info);
		kinfo->max_val = snd_ctl_elem_info_get_max64(info);
		kinfo->step_val = snd_ctl_elem_info_get_step64(info);
		break;
	case SND_CTL_ELEM_TYPE_ENUMERATED: {
		unsigned int items = snd_ctl_elem_info_get_items(info);
		kinfo->num_enums = items > 8 ? 8 : items;
		kinfo->min_val = 0;
		kinfo->max_val = items > 0 ? items - 1 : 0;
		for (unsigned int e = 0; e < kinfo->num_enums; e++) {
			snd_ctl_elem_info_set_item(info, e);
			if (snd_ctl_elem_info(ctl, info) == 0) {
				snprintf(kinfo->enum_names[e],
					 sizeof(kinfo->enum_names[e]), "%s",
					 snd_ctl_elem_info_get_item_name(info));
			}
		}
		break;
	}
	default:
		break;
	}

	/* Read TLV dB scale if available */
	if (kinfo->access_mask & SOF_KCTL_ACC_TLV_READ) {
		unsigned int tlv[64];
		memset(tlv, 0, sizeof(tlv));
		if (snd_ctl_elem_tlv_read(ctl, id, tlv, sizeof(tlv)) == 0) {
			if (tlv[0] == SNDRV_CTL_TLVT_DB_SCALE && tlv[1] >= 8) {
				kinfo->has_db_scale = 1;
				kinfo->db_min_cdb = (int32_t)tlv[2];
				kinfo->db_step_cdb = (int32_t)(tlv[3] & 0xffff);
				kinfo->db_mute = (tlv[3] >> 16) & 1;
			}
		}
	}

	/* Read current values */
	bool got_val = false;
	int val_err = 0;
	if (kinfo->access_mask & SOF_KCTL_ACC_READ) {
		snd_ctl_elem_value_set_id(val, id);
		val_err = snd_ctl_elem_read(ctl, val);
		if (val_err == 0) {
			got_val = true;
			uint32_t nch = kinfo->count > 8 ? 8 : kinfo->count;
			for (uint32_t c = 0; c < nch; c++) {
				switch (etype) {
				case SND_CTL_ELEM_TYPE_BOOLEAN:
					kinfo->values[c] = snd_ctl_elem_value_get_boolean(val, c);
					break;
				case SND_CTL_ELEM_TYPE_INTEGER:
					kinfo->values[c] = snd_ctl_elem_value_get_integer(val, c);
					break;
				case SND_CTL_ELEM_TYPE_INTEGER64:
					kinfo->values[c] = snd_ctl_elem_value_get_integer64(val, c);
					break;
				case SND_CTL_ELEM_TYPE_ENUMERATED:
					kinfo->values[c] = snd_ctl_elem_value_get_enumerated(val, c);
					break;
				case SND_CTL_ELEM_TYPE_BYTES:
					kinfo->values[c] = snd_ctl_elem_value_get_byte(val, c);
					break;
				default:
					break;
				}
			}
			if (raw_bytes_out && raw_len_out) {
				if (etype == SND_CTL_ELEM_TYPE_BYTES) {
					uint32_t blen = kinfo->count > 512 ? 512 : kinfo->count;
					memcpy(raw_bytes_out, snd_ctl_elem_value_get_bytes(val), blen);
					*raw_len_out = blen;
				} else if (etype == SND_CTL_ELEM_TYPE_IEC958) {
					snd_aes_iec958_t iec;
					snd_ctl_elem_value_get_iec958(val, &iec);
					memcpy(raw_bytes_out, iec.status, 24);
					*raw_len_out = 24;
					for (int i = 0; i < 4; i++)
						kinfo->values[i] = iec.status[i];
				}
			}
		}
	}

	if (!got_val && etype == SND_CTL_ELEM_TYPE_BYTES &&
	    (kinfo->access_mask & SOF_KCTL_ACC_TLV_READ)) {
		unsigned int tlv_buf[2 + 512 / sizeof(unsigned int)];
		uint32_t max_tlv_sz = (kinfo->count > 0 &&
				       kinfo->count + 8U <= sizeof(tlv_buf)) ?
				      (kinfo->count + 8U) : (uint32_t)sizeof(tlv_buf);
		if (max_tlv_sz < 2 * sizeof(unsigned int))
			max_tlv_sz = 2 * sizeof(unsigned int);
		memset(tlv_buf, 0, sizeof(tlv_buf));
		val_err = snd_ctl_elem_tlv_read(ctl, id, tlv_buf, max_tlv_sz);
		if (val_err == 0) {
			got_val = true;
			uint32_t tlv_total = 8U + tlv_buf[1];
			if (tlv_total > max_tlv_sz)
				tlv_total = max_tlv_sz;
			if (tlv_total > 512U)
				tlv_total = 512U;
			const uint8_t *bptr = (const uint8_t *)tlv_buf;
			uint32_t nch = tlv_total < 8U ? tlv_total : 8U;
			for (uint32_t c = 0; c < nch; c++)
				kinfo->values[c] = bptr[c];
			if (raw_bytes_out && raw_len_out) {
				memcpy(raw_bytes_out, bptr, tlv_total);
				*raw_len_out = tlv_total;
			}
		}
	}

	return val_err;
}

int alsa_kctl_list(uint32_t card_id, struct sof_net_kctl_info *out_arr,
		   uint32_t max_items, uint32_t *count_out)
{
	char ctl_name[32];
	snd_ctl_t *ctl = NULL;
	snd_ctl_elem_list_t *elist = NULL;

	*count_out = 0;
	snprintf(ctl_name, sizeof(ctl_name), "hw:%u", card_id);
	int err = snd_ctl_open(&ctl, ctl_name, 0);
	if (err < 0)
		return err;

	snd_ctl_elem_list_alloca(&elist);
	err = snd_ctl_elem_list(ctl, elist);
	if (err < 0) {
		snd_ctl_close(ctl);
		return err;
	}

	unsigned int total = snd_ctl_elem_list_get_count(elist);
	if (total == 0) {
		snd_ctl_close(ctl);
		return 0;
	}

	err = snd_ctl_elem_list_alloc_space(elist, total);
	if (err < 0) {
		snd_ctl_close(ctl);
		return err;
	}

	err = snd_ctl_elem_list(ctl, elist);
	if (err < 0) {
		snd_ctl_elem_list_free_space(elist);
		snd_ctl_close(ctl);
		return err;
	}

	unsigned int used = snd_ctl_elem_list_get_used(elist);
	if (used > max_items)
		used = max_items;

	snd_ctl_elem_id_t *id = NULL;
	snd_ctl_elem_id_alloca(&id);

	for (unsigned int i = 0; i < used; i++) {
		snd_ctl_elem_list_get_id(elist, i, id);
		(void)fill_alsa_kctl_info(ctl, card_id, id, &out_arr[i], NULL, NULL);
	}

	*count_out = used;
	snd_ctl_elem_list_free_space(elist);
	snd_ctl_close(ctl);

	/* Resolve DAPM graph anchors for all returned kcontrols */
	dapm_resolve_kctl_anchors(card_id, out_arr, used);
	return 0;
}

static void setup_elem_id_from_req(const struct sof_net_kctl_io_req *req,
				   snd_ctl_elem_id_t *id)
{
	snd_ctl_elem_id_clear(id);
	if (req->numid > 0) {
		snd_ctl_elem_id_set_numid(id, req->numid);
	} else {
		snd_ctl_elem_id_set_interface(id, (snd_ctl_elem_iface_t)
			(req->iface ? req->iface : SND_CTL_ELEM_IFACE_MIXER));
		snd_ctl_elem_id_set_device(id, req->device);
		snd_ctl_elem_id_set_index(id, req->index);
		snd_ctl_elem_id_set_name(id, req->name);
	}
}

int alsa_kctl_get(const struct sof_net_kctl_io_req *req,
		  struct sof_net_kctl_io_rsp *rsp)
{
	char ctl_name[32];
	snd_ctl_t *ctl = NULL;
	snd_ctl_elem_id_t *id = NULL;

	memset(rsp, 0, sizeof(*rsp));
	rsp->api_used = SOF_AUDIO_API_ALSA;

	snprintf(ctl_name, sizeof(ctl_name), "hw:%u", req->card_id);
	int err = snd_ctl_open(&ctl, ctl_name, 0);
	if (err < 0) {
		rsp->status = err;
		return err;
	}

	snd_ctl_elem_id_alloca(&id);
	setup_elem_id_from_req(req, id);

	uint32_t raw_len = 0;
	err = fill_alsa_kctl_info(ctl, req->card_id, id, &rsp->info,
				  rsp->bytes_data, &raw_len);
	rsp->bytes_len = raw_len;
	snd_ctl_close(ctl);

	if (err < 0) {
		rsp->status = err;
		return err;
	}
	if (rsp->info.numid == 0) {
		rsp->status = -ENOENT;
		return -ENOENT;
	}

	dapm_resolve_kctl_anchors(req->card_id, &rsp->info, 1);
	rsp->status = 0;
	return 0;
}

int alsa_kctl_set(const struct sof_net_kctl_io_req *req,
		  struct sof_net_kctl_io_rsp *rsp)
{
	char ctl_name[32];
	snd_ctl_t *ctl = NULL;
	snd_ctl_elem_id_t *id = NULL;
	snd_ctl_elem_info_t *info = NULL;
	snd_ctl_elem_value_t *val = NULL;

	memset(rsp, 0, sizeof(*rsp));
	rsp->api_used = SOF_AUDIO_API_ALSA;

	if (req->count > (sizeof(req->values) / sizeof(req->values[0])) ||
	    req->bytes_len > sizeof(req->bytes_data)) {
		rsp->status = -EINVAL;
		return -EINVAL;
	}

	snprintf(ctl_name, sizeof(ctl_name), "hw:%u", req->card_id);
	int err = snd_ctl_open(&ctl, ctl_name, 0);
	if (err < 0) {
		rsp->status = err;
		return err;
	}

	snd_ctl_elem_id_alloca(&id);
	snd_ctl_elem_info_alloca(&info);
	snd_ctl_elem_value_alloca(&val);

	setup_elem_id_from_req(req, id);
	snd_ctl_elem_info_set_id(info, id);
	err = snd_ctl_elem_info(ctl, info);
	if (err < 0) {
		snd_ctl_close(ctl);
		rsp->status = err;
		return err;
	}

	snd_ctl_elem_info_get_id(info, id);
	snd_ctl_elem_value_set_id(val, id);

	snd_ctl_elem_type_t etype = snd_ctl_elem_info_get_type(info);
	unsigned int total_ch = snd_ctl_elem_info_get_count(info);
	uint32_t max_vals = (uint32_t)(sizeof(req->values) / sizeof(req->values[0]));
	uint32_t req_cnt = req->count <= max_vals ? req->count : max_vals;
	uint32_t req_blen = req->bytes_len <= sizeof(req->bytes_data) ?
			    req->bytes_len : (uint32_t)sizeof(req->bytes_data);

	if (etype == SND_CTL_ELEM_TYPE_BYTES &&
	    !snd_ctl_elem_info_is_writable(info) &&
	    snd_ctl_elem_info_is_tlv_writable(info)) {
		unsigned int tlv_buf[2 + 512 / sizeof(unsigned int)];
		memset(tlv_buf, 0, sizeof(tlv_buf));

		if (req_blen >= 8U) {
			uint32_t hdr_len = 0;
			memcpy(&hdr_len, req->bytes_data + 4, 4);
			if (hdr_len <= req_blen - 8U && 8U + hdr_len == req_blen) {
				if (total_ch > 0 && req_blen > total_ch + 8U) {
					snd_ctl_close(ctl);
					rsp->status = -EINVAL;
					return -EINVAL;
				}
				memcpy(tlv_buf, req->bytes_data, req_blen);
			} else if (req_blen <= 512U - 8U &&
				   (total_ch == 0 || req_blen <= total_ch)) {
				tlv_buf[0] = SNDRV_CTL_TLVT_CONTAINER;
				tlv_buf[1] = req_blen;
				memcpy(&tlv_buf[2], req->bytes_data, req_blen);
			} else {
				snd_ctl_close(ctl);
				rsp->status = -EINVAL;
				return -EINVAL;
			}
		} else if (req_blen > 0) {
			if (total_ch > 0 && req_blen > total_ch) {
				snd_ctl_close(ctl);
				rsp->status = -EINVAL;
				return -EINVAL;
			}
			tlv_buf[0] = SNDRV_CTL_TLVT_CONTAINER;
			tlv_buf[1] = req_blen;
			memcpy(&tlv_buf[2], req->bytes_data, req_blen);
		} else if (req_cnt > 0) {
			if (total_ch > 0 && req_cnt > total_ch) {
				snd_ctl_close(ctl);
				rsp->status = -EINVAL;
				return -EINVAL;
			}
			tlv_buf[0] = SNDRV_CTL_TLVT_CONTAINER;
			tlv_buf[1] = req_cnt;
			uint8_t *dst_b = (uint8_t *)&tlv_buf[2];
			for (uint32_t i = 0; i < req_cnt; i++)
				dst_b[i] = (uint8_t)req->values[i];
		} else {
			snd_ctl_close(ctl);
			rsp->status = -EINVAL;
			return -EINVAL;
		}

		err = snd_ctl_elem_tlv_write(ctl, id, tlv_buf);
	} else {
		/* Read current values first so unmentioned channels remain intact */
		if (snd_ctl_elem_info_is_readable(info))
			snd_ctl_elem_read(ctl, val);

		if (etype == SND_CTL_ELEM_TYPE_IEC958) {
			snd_aes_iec958_t iec;

			snd_ctl_elem_value_get_iec958(val, &iec);
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
			snd_ctl_elem_value_set_iec958(val, &iec);
		} else {
			for (unsigned int c = 0; c < total_ch; c++) {
				/* If caller passed 1 value for a stereo control, broadcast it to all channels */
				int64_t v = (c < req_cnt) ? req->values[c] : req->values[0];
				switch (etype) {
				case SND_CTL_ELEM_TYPE_BOOLEAN:
					snd_ctl_elem_value_set_boolean(val, c, v ? 1 : 0);
					break;
				case SND_CTL_ELEM_TYPE_INTEGER:
					snd_ctl_elem_value_set_integer(val, c, (long)v);
					break;
				case SND_CTL_ELEM_TYPE_INTEGER64:
					snd_ctl_elem_value_set_integer64(val, c, (long long)v);
					break;
				case SND_CTL_ELEM_TYPE_ENUMERATED:
					snd_ctl_elem_value_set_enumerated(val, c, (unsigned int)v);
					break;
				case SND_CTL_ELEM_TYPE_BYTES:
					if (req_blen > 0 && c < req_blen)
						snd_ctl_elem_value_set_byte(val, c, req->bytes_data[c]);
					else if (c < req_cnt)
						snd_ctl_elem_value_set_byte(val, c, (unsigned char)v);
					break;
				default:
					snd_ctl_close(ctl);
					rsp->status = -ENOTSUP;
					return -ENOTSUP;
				}
			}
		}

		err = snd_ctl_elem_write(ctl, val);
	}

	uint32_t raw_len = 0;
	(void)fill_alsa_kctl_info(ctl, req->card_id, id, &rsp->info,
				  rsp->bytes_data, &raw_len);
	rsp->bytes_len = raw_len;
	snd_ctl_close(ctl);

	dapm_resolve_kctl_anchors(req->card_id, &rsp->info, 1);
	rsp->status = err < 0 ? err : 0;
	return rsp->status;
}
