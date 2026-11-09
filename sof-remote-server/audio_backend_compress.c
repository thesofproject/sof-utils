// SPDX-License-Identifier: (GPL-2.0-only OR BSD-3-Clause)
/*
 * audio_backend_compress.c — Native TinyCompress (libtinycompress) & ALSA
 * Compress Offload UAPI (/dev/snd/comprC*D*) backend for compressed PCM
 * capability discovery, playback (COMPRESS_IN), and capture (COMPRESS_OUT).
 */

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <math.h>
#include <errno.h>
#include <sys/ioctl.h>

#include <sound/asound.h>
#include <sound/compress_params.h>
#include <sound/compress_offload.h>
#ifdef HAVE_TINYCOMPRESS
#include <tinycompress/tinycompress.h>
#endif

#include "audio_backends.h"

static const char *codec_id_to_name(uint32_t codec_id)
{
	switch (codec_id) {
	case SND_AUDIOCODEC_PCM:        return "PCM";
	case SND_AUDIOCODEC_MP3:        return "MP3";
	case SND_AUDIOCODEC_AMR:        return "AMR";
	case SND_AUDIOCODEC_AMRWB:      return "AMRWB";
	case SND_AUDIOCODEC_AMRWBPLUS:  return "AMRWB+";
	case SND_AUDIOCODEC_AAC:        return "AAC";
	case SND_AUDIOCODEC_WMA:        return "WMA";
	case SND_AUDIOCODEC_REAL:       return "REAL";
	case SND_AUDIOCODEC_VORBIS:     return "VORBIS";
	case SND_AUDIOCODEC_FLAC:       return "FLAC";
	case SND_AUDIOCODEC_IEC61937:   return "IEC61937";
	case SND_AUDIOCODEC_G723_1:     return "G723_1";
	case SND_AUDIOCODEC_G729:       return "G729";
	case SND_AUDIOCODEC_BESPOKE:    return "BESPOKE";
	default:                        return "OTHER";
	}
}

int compr_query_caps(uint32_t card_id, uint32_t dev_id, uint32_t direction,
		     struct sof_net_pcm_caps *out)
{
	char path[64];
	int flags = (direction == SOF_STREAM_DIR_CAPTURE) ?
		    (O_RDONLY | O_NONBLOCK) : (O_WRONLY | O_NONBLOCK);

	out->card_id = card_id;
	out->device_id = dev_id;
	out->direction = direction;
	out->is_compress = 1;
	out->api_used = SOF_AUDIO_API_TINYCOMPRESS;

	snprintf(path, sizeof(path), "/dev/snd/comprC%uD%u", card_id, dev_id);
	int fd = open(path, flags);
	if (fd < 0) {
		out->status = -errno;
		return out->status;
	}

	struct snd_compr_caps caps;
	memset(&caps, 0, sizeof(caps));
	if (ioctl(fd, SNDRV_COMPRESS_GET_CAPS, &caps) < 0) {
		int err = -errno;
		close(fd);
		out->status = err;
		return err;
	}
	close(fd);

	if (caps.direction == SND_COMPRESS_CAPTURE)
		out->direction = SOF_STREAM_DIR_CAPTURE;
	else if (caps.direction == SND_COMPRESS_PLAYBACK)
		out->direction = SOF_STREAM_DIR_PLAYBACK;

	out->min_fragment_size = caps.min_fragment_size;
	out->max_fragment_size = caps.max_fragment_size;
	out->min_fragments = caps.min_fragments;
	out->max_fragments = caps.max_fragments;
	out->num_codecs = caps.num_codecs > 16 ? 16 : caps.num_codecs;

	size_t off = 0;
	out->formats_str[0] = '\0';
	for (uint32_t i = 0; i < out->num_codecs; i++) {
		out->codecs[i] = caps.codecs[i];
		int n = snprintf(out->formats_str + off,
				 sizeof(out->formats_str) - off, "%s%s(%u)",
				 off ? " " : "",
				 codec_id_to_name(caps.codecs[i]),
				 caps.codecs[i]);
		if (n > 0 && off + (size_t)n < sizeof(out->formats_str))
			off += (size_t)n;
	}
	snprintf(out->rates_str, sizeof(out->rates_str), "frag=%u..%uB x %u..%u",
		 out->min_fragment_size, out->max_fragment_size,
		 out->min_fragments, out->max_fragments);
	out->status = 0;
	return 0;
}

int compr_handle_ctl(const struct sof_net_compr_req *req,
		     const uint8_t *payload_in,
		     uint8_t **out_buf_ptr, uint32_t *out_len_ptr)
{
	*out_buf_ptr = NULL;
	*out_len_ptr = 0;

	struct sof_net_compr_rsp rsp;
	memset(&rsp, 0, sizeof(rsp));
	rsp.card_id = req->card_id;
	rsp.device_id = req->device_id;
	rsp.direction = req->direction;
	rsp.codec_id = req->codec_id ? req->codec_id : SND_AUDIOCODEC_PCM;

	if (req->op == SOF_COMPR_OP_GET_CAPS) {
		struct sof_net_pcm_caps caps;
		memset(&caps, 0, sizeof(caps));
		int rc = compr_query_caps(req->card_id, req->device_id,
					  req->direction, &caps);
		rsp.status = rc;
		rsp.min_fragment_size = caps.min_fragment_size;
		rsp.max_fragment_size = caps.max_fragment_size;
		rsp.fragment_size = caps.min_fragment_size;
		rsp.fragments = caps.min_fragments;
		rsp.num_codecs = caps.num_codecs;
		memcpy(rsp.codecs, caps.codecs, sizeof(rsp.codecs));

		uint8_t *buf = malloc(sizeof(rsp));
		if (!buf)
			return -ENOMEM;
		memcpy(buf, &rsp, sizeof(rsp));
		*out_buf_ptr = buf;
		*out_len_ptr = sizeof(rsp);
		return 0;
	}

#ifdef HAVE_TINYCOMPRESS
	struct snd_codec codec;
	memset(&codec, 0, sizeof(codec));
	codec.id = rsp.codec_id;
	codec.ch_in = req->channels ? req->channels : 2;
	codec.ch_out = codec.ch_in;
	codec.sample_rate = req->rate ? req->rate : 48000;
	switch (req->format) {
	case SOF_PCM_FMT_S16_LE:
		codec.format = SNDRV_PCM_FORMAT_S16_LE;
		break;
	case SOF_PCM_FMT_S24_LE:
		codec.format = SNDRV_PCM_FORMAT_S24_LE;
		break;
	case SOF_PCM_FMT_S32_LE:
		codec.format = SNDRV_PCM_FORMAT_S32_LE;
		break;
	default:
		return -EINVAL;
	}

	struct compr_config config;
	memset(&config, 0, sizeof(config));
	config.fragment_size = req->fragment_size;
	config.fragments = req->fragments;
	config.codec = &codec;

	uint32_t sample_bytes = (codec.format == SNDRV_PCM_FORMAT_S32_LE ||
				 codec.format == SNDRV_PCM_FORMAT_S24_LE) ?
				sizeof(int32_t) : sizeof(int16_t);

	if (req->op == SOF_COMPR_OP_PLAY_BUFFER) {
		if (req->data_size > 0 && !payload_in)
			return -EINVAL;
		if (req->data_size == 0 && codec.id != SND_AUDIOCODEC_PCM)
			return -EINVAL;

		struct compress *compr = compress_open(req->card_id, req->device_id,
						       COMPRESS_IN, &config);
		if (!compr || !is_compress_ready(compr)) {
			if (compr)
				compress_close(compr);
			rsp.status = -ENODEV;
			uint8_t *buf = malloc(sizeof(rsp));
			if (!buf)
				return -ENOMEM;
			memcpy(buf, &rsp, sizeof(rsp));
			*out_buf_ptr = buf;
			*out_len_ptr = sizeof(rsp);
			return 0;
		}

		rsp.fragment_size = config.fragment_size;
		rsp.fragments = config.fragments;

		uint8_t *synth_buf = NULL;
		const uint8_t *src_data = payload_in;
		uint32_t total_bytes = req->data_size;

		if (total_bytes == 0) {
			uint32_t dur_ms = (req->duration_ms && req->duration_ms <= 10000) ?
					  req->duration_ms : 500;
			uint32_t total_frames =
				(uint32_t)(((uint64_t)codec.sample_rate * dur_ms) / 1000ULL);
			uint64_t raw_bytes =
				(uint64_t)total_frames * codec.ch_in * sample_bytes;
			if (raw_bytes > (16ULL * 1024ULL * 1024ULL)) {
				raw_bytes = 16ULL * 1024ULL * 1024ULL;
				total_frames = (uint32_t)(raw_bytes /
							  ((uint64_t)codec.ch_in * sample_bytes));
			}
			total_bytes = (uint32_t)raw_bytes;
			synth_buf = calloc(1, total_bytes ? total_bytes : 1);
			if (!synth_buf) {
				compress_close(compr);
				return -ENOMEM;
			}
			double phase = 0.0;
			double step = 2.0 * M_PI *
				      (double)(req->freq_hz ? req->freq_hz : 1000) /
				      (double)codec.sample_rate;
			if (sample_bytes == sizeof(int32_t)) {
				int32_t *s32 = (int32_t *)synth_buf;
				double amp = (codec.format == SNDRV_PCM_FORMAT_S24_LE) ?
					     4194300.0 : 1073741820.0;
				for (uint32_t i = 0; i < total_frames; i++) {
					int32_t v = (int32_t)(amp * sin(phase));
					phase += step;
					for (uint32_t c = 0; c < codec.ch_in; c++)
						s32[i * codec.ch_in + c] = v;
				}
			} else {
				int16_t *s16 = (int16_t *)synth_buf;
				for (uint32_t i = 0; i < total_frames; i++) {
					int16_t v = (int16_t)(16000.0 * sin(phase));
					phase += step;
					for (uint32_t c = 0; c < codec.ch_in; c++)
						s16[i * codec.ch_in + c] = v;
				}
			}
			src_data = synth_buf;
		}

		uint32_t written = 0;
		bool started = false;
		uint32_t chunk_sz = config.fragment_size ? config.fragment_size : 4096;

		while (src_data && written < total_bytes) {
			uint32_t rem = total_bytes - written;
			uint32_t to_write = rem < chunk_sz ? rem : chunk_sz;
			int nw = compress_write(compr, src_data + written, to_write);
			if (nw < 0) {
				rsp.status = nw;
				break;
			}
			written += (uint32_t)nw;
			if (!started) {
				compress_start(compr);
				started = true;
			}
		}

		unsigned int samples = 0, srate = 0;
		if (compress_get_tstamp(compr, &samples, &srate) == 0) {
			rsp.rendered_samples = samples;
			rsp.sampling_rate = srate;
		}
		if (started)
			compress_drain(compr);

		rsp.bytes_transferred = written;
		free(synth_buf);
		compress_close(compr);

		uint8_t *buf = malloc(sizeof(rsp));
		if (!buf)
			return -ENOMEM;
		memcpy(buf, &rsp, sizeof(rsp));
		*out_buf_ptr = buf;
		*out_len_ptr = sizeof(rsp);
		return 0;
	}

	if (req->op == SOF_COMPR_OP_CAPTURE_BUFFER) {
		struct compress *compr = compress_open(req->card_id, req->device_id,
						       COMPRESS_OUT, &config);
		if (!compr || !is_compress_ready(compr)) {
			if (compr)
				compress_close(compr);
			rsp.status = -ENODEV;
			uint8_t *buf = malloc(sizeof(rsp));
			if (!buf)
				return -ENOMEM;
			memcpy(buf, &rsp, sizeof(rsp));
			*out_buf_ptr = buf;
			*out_len_ptr = sizeof(rsp);
			return 0;
		}

		rsp.fragment_size = config.fragment_size;
		rsp.fragments = config.fragments;

		uint32_t dur_ms = (req->duration_ms && req->duration_ms <= 5000) ?
				  req->duration_ms : 250;
		uint64_t raw_max_bytes = ((uint64_t)codec.sample_rate * codec.ch_in *
					  sample_bytes * dur_ms) / 1000ULL;
		if (config.fragment_size && raw_max_bytes < config.fragment_size)
			raw_max_bytes = config.fragment_size;
		if (raw_max_bytes > (2ULL * 1024ULL * 1024ULL))
			raw_max_bytes = 2ULL * 1024ULL * 1024ULL;
		uint32_t max_bytes = (uint32_t)raw_max_bytes;

		uint8_t *out_buf = calloc(1, sizeof(rsp) + max_bytes);
		if (!out_buf) {
			compress_close(compr);
			return -ENOMEM;
		}

		compress_start(compr);
		uint32_t got_bytes = 0;
		uint32_t chunk_sz = config.fragment_size ? config.fragment_size : 4096;

		while (got_bytes < max_bytes) {
			uint32_t rem = max_bytes - got_bytes;
			uint32_t to_read = rem < chunk_sz ? rem : chunk_sz;
			int nr = compress_read(compr, out_buf + sizeof(rsp) + got_bytes, to_read);
			if (nr <= 0) {
				rsp.status = nr < 0 ? nr : 0;
				break;
			}
			got_bytes += (uint32_t)nr;
		}

		unsigned int samples = 0, srate = 0;
		if (compress_get_tstamp(compr, &samples, &srate) == 0) {
			rsp.rendered_samples = samples;
			rsp.sampling_rate = srate;
		}
		compress_stop(compr);
		compress_close(compr);

		rsp.bytes_transferred = got_bytes;
		rsp.data_size = got_bytes;
		memcpy(out_buf, &rsp, sizeof(rsp));
		*out_buf_ptr = out_buf;
		*out_len_ptr = sizeof(rsp) + got_bytes;
		return 0;
	}
#else
	(void)payload_in;
	return -ENOTSUP;
#endif

	return -EINVAL;
}
