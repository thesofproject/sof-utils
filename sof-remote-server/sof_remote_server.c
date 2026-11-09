// SPDX-License-Identifier: (GPL-2.0-only OR BSD-3-Clause)
/*
 * sof_remote_server.c — Multi-threaded C Network Daemon for SOF DSP Control,
 * Telemetry, LLEXT Loading, Shell, Probes, and Remote ALSA Playback/Capture.
 */

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdatomic.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <limits.h>
#include <math.h>
#include <signal.h>
#include <pthread.h>
#include <dirent.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <arpa/inet.h>

#include "sof_remote_proto.h"
#include "audio_backends.h"

/* IPC4 BaseFW Large Config Parameter IDs */
#define IPC4_MODULES_INFO_GET		9
#define IPC4_MEMORY_STATE_INFO_GET	14
#define IPC4_POWER_STATE_INFO_GET	15
#define IPC4_PERF_MEASUREMENTS_STATE	17
#define IPC4_GLOBAL_PERF_DATA		18
#define IPC4_EXTENDED_GLOBAL_PERF_DATA	30

static volatile sig_atomic_t g_running = 1;
static const char *g_cdev_path = "/dev/sof-dsp0";
static int g_port = SOF_NET_DEFAULT_PORT;

/* Global ALSA / TinyALSA Tone Generator State */
static pthread_mutex_t g_pcm_lock = PTHREAD_MUTEX_INITIALIZER;
static _Atomic bool g_tone_running = false;
static pthread_t g_tone_thread;
static char g_tone_device[32] = "hw:0,0";
static uint32_t g_tone_rate = 48000;
static uint32_t g_tone_channels = 2;
static uint32_t g_tone_freq = 1000;
static uint32_t g_tone_api = SOF_AUDIO_API_ALSA;
static uint32_t g_tone_fmt = SOF_PCM_FMT_S16_LE;
static uint32_t g_tone_period_size = 1024;
static uint32_t g_tone_period_count = 4;
static _Atomic uint64_t g_frames_played = 0;
static _Atomic uint32_t g_last_rms = 0;
static _Atomic uint32_t g_last_peak = 0;
static _Atomic uint32_t g_last_xruns = 0;
static _Atomic int g_tone_open_err = 0;

/* Shared winstream sequence tracker for DSP shell */
static pthread_mutex_t g_shell_lock = PTHREAD_MUTEX_INITIALIZER;
static uint32_t g_shell_seq = 0;
static __thread bool g_client_accepts_compressed = false;

static void sig_handler(int sig)
{
	(void)sig;
	g_running = 0;
}

static inline uint32_t min_u32_helper(uint32_t a, uint32_t b)
{
	return a < b ? a : b;
}

static int read_exact(int fd, void *buf, size_t len)
{
	uint8_t *p = (uint8_t *)buf;
	size_t rem = len;

	while (rem > 0) {
		ssize_t n = recv(fd, p, rem, 0);
		if (n <= 0) {
			if (n < 0 && errno == EINTR)
				continue;
			return -1;
		}
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
		if (n <= 0) {
			if (n < 0 && errno == EINTR)
				continue;
			return -1;
		}
		p += n;
		rem -= (size_t)n;
	}
	return 0;
}

static int send_reply(int sock, uint16_t opcode, uint32_t req_id,
		      int32_t status, const void *payload, uint32_t payload_len)
{
	uint16_t base_op = (uint16_t)(opcode & SOF_NET_OP_MASK);
	struct sof_net_hdr hdr = {
		.magic       = SOF_NET_MAGIC,
		.version     = SOF_NET_VERSION,
		.opcode      = base_op,
		.req_id      = req_id,
		.payload_len = payload ? payload_len : 0,
		.status      = status,
	};

	if (g_client_accepts_compressed && payload && payload_len >= 128U) {
		uint8_t *cbuf = malloc(payload_len);
		if (cbuf) {
			uint32_t clen = sof_net_compress((const uint8_t *)payload,
							 payload_len, cbuf, payload_len);
			if (clen > 0 && clen < payload_len) {
				hdr.opcode = (uint16_t)(base_op | SOF_NET_OP_FLAG_COMPRESSED);
				hdr.payload_len = clen;
				int rc = write_exact(sock, &hdr, sizeof(hdr));
				if (rc == 0)
					rc = write_exact(sock, cbuf, clen);
				free(cbuf);
				return rc;
			}
			free(cbuf);
		}
	}

	if (write_exact(sock, &hdr, sizeof(hdr)) < 0)
		return -1;
	if (payload && payload_len > 0) {
		if (write_exact(sock, payload, payload_len) < 0)
			return -1;
	}
	return 0;
}

/* -------------------------------------------------------------------------
 * Native ALSA-lib & TinyALSA Background Tone Generator & PCM Operations
 * -------------------------------------------------------------------------
 */

static void *tone_worker_fn(void *arg)
{
	(void)arg;
	uint32_t xruns = 0;
	int rc = 0;

	if (g_tone_api == SOF_AUDIO_API_TINYALSA) {
		uint32_t card = 0, dev = 0;
		sof_parse_card_dev(g_tone_device, &card, &dev);
		rc = tinyalsa_pcm_play_frames(card, dev, g_tone_fmt,
					      g_tone_rate, g_tone_channels,
					      g_tone_period_size,
					      g_tone_period_count,
					      NULL, 0, &g_tone_running,
					      g_tone_freq, &g_frames_played,
					      &g_last_rms, &g_last_peak,
					      &xruns);
	} else {
		rc = alsa_pcm_play_frames(g_tone_device, g_tone_fmt,
					  g_tone_rate, g_tone_channels,
					  g_tone_period_size,
					  g_tone_period_count,
					  NULL, 0, &g_tone_running,
					  g_tone_freq, &g_frames_played,
					  &g_last_rms, &g_last_peak,
					  &xruns);
	}

	atomic_store_explicit(&g_tone_open_err, rc, memory_order_relaxed);
	atomic_store_explicit(&g_last_xruns, xruns, memory_order_relaxed);
	atomic_store_explicit(&g_last_rms, 0, memory_order_relaxed);
	atomic_store_explicit(&g_last_peak, 0, memory_order_relaxed);
	atomic_store_explicit(&g_tone_running, false, memory_order_release);
	return NULL;
}

static int handle_pcm_ctl(int sock, uint32_t req_id, const uint8_t *req_buf,
			  uint32_t req_len)
{
	/* Support both v1 56-byte struct and extended 72-byte struct */
	if (req_len < 56)
		return send_reply(sock, SOF_NET_OP_PCM_CTL, req_id, -EINVAL, NULL, 0);

	struct sof_net_pcm_req req;
	memset(&req, 0, sizeof(req));
	memcpy(&req, req_buf, req_len < sizeof(req) ? req_len : sizeof(req));

	const uint8_t *trailing_data = NULL;
	if (req.data_size > 0) {
		size_t hdr_sz = (req_len >= sizeof(req) + req.data_size) ?
				sizeof(req) : 56;
		if (req_len >= hdr_sz + req.data_size)
			trailing_data = req_buf + hdr_sz;
	}

	uint32_t api = (req.api_backend == SOF_AUDIO_API_TINYALSA) ?
		       SOF_AUDIO_API_TINYALSA : SOF_AUDIO_API_ALSA;
	uint32_t fmt = req.format;
	uint32_t rate = req.rate ? req.rate : 48000;
	uint32_t ch = req.channels ? req.channels : 2;
	uint32_t freq = req.freq_hz ? req.freq_hz : 1000;
	uint32_t psize = req.period_size ? req.period_size : 1024;
	uint32_t pcnt = req.period_count ? req.period_count : 4;
	const char *dev = req.device[0] ? req.device : "hw:0,0";

	struct sof_net_pcm_rsp rsp;
	memset(&rsp, 0, sizeof(rsp));
	int status = 0;

	pthread_mutex_lock(&g_pcm_lock);

	switch (req.op) {
	case SOF_PCM_OP_STATUS:
		break;

	case SOF_PCM_OP_START_TONE:
		if (atomic_load_explicit(&g_tone_running, memory_order_acquire)) {
			atomic_store_explicit(&g_tone_running, false, memory_order_release);
			pthread_join(g_tone_thread, NULL);
		}
		snprintf(g_tone_device, sizeof(g_tone_device), "%s", dev);
		g_tone_rate = rate;
		g_tone_channels = ch;
		g_tone_freq = freq;
		g_tone_api = api;
		g_tone_fmt = fmt;
		g_tone_period_size = psize;
		g_tone_period_count = pcnt;
		atomic_store_explicit(&g_frames_played, 0, memory_order_relaxed);
		atomic_store_explicit(&g_last_xruns, 0, memory_order_relaxed);
		atomic_store_explicit(&g_tone_open_err, 0, memory_order_relaxed);
		atomic_store_explicit(&g_tone_running, true, memory_order_release);
		if (pthread_create(&g_tone_thread, NULL, tone_worker_fn, NULL) != 0) {
			atomic_store_explicit(&g_tone_running, false, memory_order_release);
			status = -EIO;
		} else {
			usleep(60000);
			if (!atomic_load_explicit(&g_tone_running, memory_order_acquire)) {
				int open_err = atomic_load_explicit(&g_tone_open_err,
								    memory_order_relaxed);
				status = open_err ? open_err : -ENODEV;
			}
		}
		break;

	case SOF_PCM_OP_STOP_TONE:
		if (atomic_load_explicit(&g_tone_running, memory_order_acquire)) {
			atomic_store_explicit(&g_tone_running, false, memory_order_release);
			pthread_join(g_tone_thread, NULL);
		}
		break;

	case SOF_PCM_OP_PLAY_BUFFER: {
		uint32_t bpf = ch * sof_pcm_fmt_bytes(fmt);
		uint32_t total_frames = 0;
		if (trailing_data && req.data_size >= bpf) {
			total_frames = req.data_size / bpf;
		} else {
			uint32_t dur_ms = (req.duration_ms && req.duration_ms <= 10000) ?
					  req.duration_ms : 250;
			total_frames = (rate * dur_ms) / 1000;
		}

		_Atomic uint64_t f_played = 0;
		_Atomic uint32_t rms = 0, peak = 0;
		uint32_t xruns = 0;
		pthread_mutex_unlock(&g_pcm_lock);

		if (api == SOF_AUDIO_API_TINYALSA) {
			uint32_t c_id = 0, d_id = 0;
			sof_parse_card_dev(dev, &c_id, &d_id);
			status = tinyalsa_pcm_play_frames(c_id, d_id, fmt, rate, ch,
							  psize, pcnt, trailing_data,
							  total_frames, NULL, freq,
							  &f_played, &rms, &peak, &xruns);
		} else {
			status = alsa_pcm_play_frames(dev, fmt, rate, ch,
						      psize, pcnt, trailing_data,
						      total_frames, NULL, freq,
						      &f_played, &rms, &peak, &xruns);
		}

		rsp.tone_running = atomic_load_explicit(&g_tone_running, memory_order_acquire) ? 1 : 0;
		snprintf(rsp.active_device, sizeof(rsp.active_device), "%s", dev);
		rsp.rate = rate;
		rsp.channels = ch;
		rsp.freq_hz = freq;
		rsp.frames_played = atomic_load_explicit(&f_played, memory_order_relaxed);
		rsp.rms_level = atomic_load_explicit(&rms, memory_order_relaxed);
		rsp.peak_level = atomic_load_explicit(&peak, memory_order_relaxed);
		rsp.api_used = api;
		rsp.format = fmt;
		rsp.xruns = xruns;
		return send_reply(sock, SOF_NET_OP_PCM_CTL, req_id, status, &rsp, sizeof(rsp));
	}

	case SOF_PCM_OP_CAPTURE_BUFFER: {
		uint32_t dur_ms = (req.duration_ms && req.duration_ms <= 5000) ?
				  req.duration_ms : 200;
		uint32_t total_frames = (rate * dur_ms) / 1000;
		uint32_t bpf = ch * sof_pcm_fmt_bytes(fmt);
		uint32_t pcm_bytes = total_frames * bpf;
		uint8_t *out_buf = calloc(1, sizeof(rsp) + pcm_bytes);
		if (!out_buf) {
			pthread_mutex_unlock(&g_pcm_lock);
			return send_reply(sock, SOF_NET_OP_PCM_CTL, req_id, -ENOMEM, NULL, 0);
		}

		uint32_t frames_read = 0, rms = 0, peak = 0, xruns = 0;
		pthread_mutex_unlock(&g_pcm_lock);

		if (api == SOF_AUDIO_API_TINYALSA) {
			uint32_t c_id = 0, d_id = 0;
			sof_parse_card_dev(dev, &c_id, &d_id);
			status = tinyalsa_pcm_capture_frames(c_id, d_id, fmt, rate, ch,
							     psize, pcnt,
							     out_buf + sizeof(rsp),
							     total_frames, &frames_read,
							     &rms, &peak, &xruns);
		} else {
			status = alsa_pcm_capture_frames(dev, fmt, rate, ch,
							 psize, pcnt,
							 out_buf + sizeof(rsp),
							 total_frames, &frames_read,
							 &rms, &peak, &xruns);
		}

		struct sof_net_pcm_rsp *crsp = (struct sof_net_pcm_rsp *)out_buf;
		crsp->tone_running = atomic_load_explicit(&g_tone_running, memory_order_acquire) ? 1 : 0;
		snprintf(crsp->active_device, sizeof(crsp->active_device), "%s", dev);
		crsp->rate = rate;
		crsp->channels = ch;
		crsp->frames_played = frames_read;
		crsp->rms_level = rms;
		crsp->peak_level = peak;
		crsp->data_size = frames_read * bpf;
		crsp->api_used = api;
		crsp->format = fmt;
		crsp->xruns = xruns;

		int ret = send_reply(sock, SOF_NET_OP_PCM_CTL, req_id, status,
				     out_buf, sizeof(rsp) + crsp->data_size);
		free(out_buf);
		return ret;
	}
	default:
		status = -EINVAL;
		break;
	}

	rsp.tone_running = atomic_load_explicit(&g_tone_running, memory_order_acquire) ? 1 : 0;
	snprintf(rsp.active_device, sizeof(rsp.active_device), "%s", g_tone_device);
	rsp.rate = g_tone_rate;
	rsp.channels = g_tone_channels;
	rsp.freq_hz = g_tone_freq;
	rsp.frames_played = atomic_load_explicit(&g_frames_played, memory_order_relaxed);
	rsp.rms_level = atomic_load_explicit(&g_last_rms, memory_order_relaxed);
	rsp.peak_level = atomic_load_explicit(&g_last_peak, memory_order_relaxed);
	rsp.data_size = 0;
	rsp.api_used = g_tone_api;
	rsp.format = g_tone_fmt;
	rsp.xruns = atomic_load_explicit(&g_last_xruns, memory_order_relaxed);

	pthread_mutex_unlock(&g_pcm_lock);
	return send_reply(sock, SOF_NET_OP_PCM_CTL, req_id, status, &rsp, sizeof(rsp));
}

/* -------------------------------------------------------------------------
 * Direct DebugFS (/sys/kernel/debug/sof/exception) + PCI BAR4 MMIO Backend
 * Used automatically when /dev/sof-dsp0 is unavailable AND to read full
 * 4 KB TEL2 (MW2 Slot 1) and 8 KB PMW2 (MW3) windows on any SOF DUT.
 * -------------------------------------------------------------------------
 */

struct sof_pci_hw_ctx {
	bool     initialized;
	bool     found;
	char     bdf[32];
	char     driver[64];
	char     resource4_path[128];
	char     pm_control_path[128];
	char     pm_status_path[128];
	uint32_t hw_ip_version;
	uint32_t num_cores;
	uint32_t win_bar_off[SOF_CDEV_MAX_WINDOWS];
	uint32_t win_size[SOF_CDEV_MAX_WINDOWS];
	bool     holding_d0;
};

static pthread_mutex_t g_hw_lock = PTHREAD_MUTEX_INITIALIZER;
static struct sof_pci_hw_ctx g_hw_ctx;

static void discover_sof_pci_device(void)
{
	pthread_mutex_lock(&g_hw_lock);
	if (g_hw_ctx.initialized) {
		pthread_mutex_unlock(&g_hw_lock);
		return;
	}
	g_hw_ctx.initialized = true;

	DIR *d = opendir("/sys/bus/pci/devices");
	if (!d) {
		pthread_mutex_unlock(&g_hw_lock);
		return;
	}

	struct dirent *de;
	while ((de = readdir(d)) != NULL) {
		if (de->d_name[0] == '.')
			continue;

		char drv_link[256], drv_target[256];
		snprintf(drv_link, sizeof(drv_link),
			 "/sys/bus/pci/devices/%s/driver", de->d_name);
		ssize_t n = readlink(drv_link, drv_target, sizeof(drv_target) - 1);
		if (n <= 0)
			continue;
		drv_target[n] = '\0';
		const char *base = strrchr(drv_target, '/');
		base = base ? (base + 1) : drv_target;

		if (strncmp(base, "sof-audio-pci-intel-", 20) != 0 &&
		    strstr(base, "sof") == NULL)
			continue;

		char res4[256];
		struct stat st;
		snprintf(res4, sizeof(res4),
			 "/sys/bus/pci/devices/%s/resource4", de->d_name);
		if (stat(res4, &st) < 0 || st.st_size < 0x100000)
			continue;

		g_hw_ctx.found = true;
		snprintf(g_hw_ctx.bdf, sizeof(g_hw_ctx.bdf), "%s", de->d_name);
		snprintf(g_hw_ctx.driver, sizeof(g_hw_ctx.driver), "%s", base);
		snprintf(g_hw_ctx.resource4_path, sizeof(g_hw_ctx.resource4_path),
			 "%s", res4);
		snprintf(g_hw_ctx.pm_control_path, sizeof(g_hw_ctx.pm_control_path),
			 "/sys/bus/pci/devices/%s/power/control", de->d_name);
		snprintf(g_hw_ctx.pm_status_path, sizeof(g_hw_ctx.pm_status_path),
			 "/sys/bus/pci/devices/%s/power/runtime_status", de->d_name);

		if (strstr(base, "ptl") || strstr(base, "wcl")) {
			g_hw_ctx.hw_ip_version = 6; /* ACE 3.0 */
			g_hw_ctx.num_cores = 5;
		} else if (strstr(base, "lnl")) {
			g_hw_ctx.hw_ip_version = 5; /* ACE 2.0 */
			g_hw_ctx.num_cores = 5;
		} else if (strstr(base, "mtl") || strstr(base, "arl")) {
			g_hw_ctx.hw_ip_version = 4; /* ACE 1.5 */
			g_hw_ctx.num_cores = 4;
		} else {
			g_hw_ctx.hw_ip_version = 2; /* cAVS 2.5 */
			g_hw_ctx.num_cores = 4;
		}

		if (g_hw_ctx.hw_ip_version >= 4) {
			/* ACE 1.x / 2.x / 3.x SRAM window layout in BAR4 (base 0x180000, stride 0x8000) */
			g_hw_ctx.win_bar_off[0] = 0x180000U;
			g_hw_ctx.win_size[0]    = 0x002000U;
			g_hw_ctx.win_bar_off[1] = 0x188000U;
			g_hw_ctx.win_size[1]    = 0x001000U;
			g_hw_ctx.win_bar_off[2] = 0x190000U;
			g_hw_ctx.win_size[2]    = 0x008000U;
			g_hw_ctx.win_bar_off[3] = 0x198000U;
			g_hw_ctx.win_size[3]    = 0x002000U;
		} else {
			/* cAVS 2.5 SRAM window layout in BAR4 (base 0x080000, stride 0x20000) */
			g_hw_ctx.win_bar_off[0] = 0x080000U;
			g_hw_ctx.win_size[0]    = 0x002000U;
			g_hw_ctx.win_bar_off[1] = 0x0A0000U;
			g_hw_ctx.win_size[1]    = 0x001000U;
			g_hw_ctx.win_bar_off[2] = 0x0C0000U;
			g_hw_ctx.win_size[2]    = 0x010000U;
			g_hw_ctx.win_bar_off[3] = 0x0E0000U;
			g_hw_ctx.win_size[3]    = 0x002000U;
		}
		break;
	}
	closedir(d);
	pthread_mutex_unlock(&g_hw_lock);
}

static int read_debugfs_fw_state(void)
{
	int fd = open("/sys/kernel/debug/sof/fw_state", O_RDONLY);
	if (fd < 0)
		return -1;
	uint32_t state = 0;
	ssize_t n = read(fd, &state, sizeof(state));
	close(fd);
	if (n == (ssize_t)sizeof(state) && state <= 16U)
		return (int)state;
	return -1;
}

static int get_sof_card_index(void)
{
	discover_sof_pci_device();

	/* 1. Check /sys/bus/pci/devices/<bdf>/sound/card* */
	if (g_hw_ctx.found && g_hw_ctx.bdf[0] != '\0') {
		char snd_path[256];
		snprintf(snd_path, sizeof(snd_path),
			 "/sys/bus/pci/devices/%s/sound", g_hw_ctx.bdf);
		DIR *sd = opendir(snd_path);
		if (sd) {
			struct dirent *de;
			unsigned int idx = 0;
			while ((de = readdir(sd)) != NULL) {
				if (sscanf(de->d_name, "card%u", &idx) == 1) {
					closedir(sd);
					return (int)idx;
				}
			}
			closedir(sd);
		}
	}

	/* 2. Check /sys/class/sound/card* device symlink for BDF or "sof" */
	DIR *cd = opendir("/sys/class/sound");
	if (cd) {
		struct dirent *de;
		unsigned int idx = 0;
		while ((de = readdir(cd)) != NULL) {
			if (sscanf(de->d_name, "card%u", &idx) != 1)
				continue;
			char dev_link[256], resolved[PATH_MAX];
			snprintf(dev_link, sizeof(dev_link),
				 "/sys/class/sound/%s/device", de->d_name);
			if (realpath(dev_link, resolved)) {
				if ((g_hw_ctx.found && g_hw_ctx.bdf[0] != '\0' &&
				     strstr(resolved, g_hw_ctx.bdf) != NULL) ||
				    strstr(resolved, "sof") != NULL) {
					closedir(cd);
					return (int)idx;
				}
			}
		}
		closedir(cd);
	}

	/* 3. Fallback: scan /proc/asound/cards for an SOF card */
	FILE *fp = fopen("/proc/asound/cards", "r");
	if (fp) {
		char line[256];
		while (fgets(line, sizeof(line), fp)) {
			unsigned int idx = 0;
			if (sscanf(line, " %u [", &idx) == 1 &&
			    (strstr(line, "sof-") != NULL || strstr(line, "SOF") != NULL)) {
				fclose(fp);
				return (int)idx;
			}
		}
		fclose(fp);
	}
	return -1;
}

static void trigger_on_demand_dsp_boot(void)
{
	int sof_card = get_sof_card_index();
	if (sof_card < 0)
		return;

	FILE *fp = fopen("/proc/asound/pcm", "r");
	if (!fp)
		return;

	char line[256];
	uint32_t card = 0, dev = 0;
	bool found_cap = false, found_play = false;

	while (fgets(line, sizeof(line), fp)) {
		unsigned int c = 0, d = 0;
		if (sscanf(line, "%u-%u:", &c, &d) == 2 &&
		    c == (unsigned int)sof_card) {
			if (strstr(line, "capture")) {
				card = c;
				dev = d;
				found_cap = true;
				break;
			}
			if (strstr(line, "playback") && !found_play) {
				card = c;
				dev = d;
				found_play = true;
			}
		}
	}
	fclose(fp);

	if (!found_cap && !found_play)
		return;

	char pcm_dev[32];
	snprintf(pcm_dev, sizeof(pcm_dev), "hw:%u,%u", card, dev);
	uint8_t dummy[4096];
	memset(dummy, 0, sizeof(dummy));
	uint32_t frames_io = 0, rms = 0, peak = 0, xruns = 0;

	if (found_cap) {
		if (alsa_pcm_capture_frames(pcm_dev, SOF_PCM_FMT_S16_LE, 16000, 2,
					    160, 2, dummy, 160, &frames_io,
					    &rms, &peak, &xruns) < 0) {
			alsa_pcm_capture_frames(pcm_dev, SOF_PCM_FMT_S16_LE, 48000, 2,
						240, 2, dummy, 240, &frames_io,
						&rms, &peak, &xruns);
		}
	} else {
		_Atomic uint64_t f_played = 0;
		_Atomic uint32_t vrms = 0, vpeak = 0;
		alsa_pcm_play_frames(pcm_dev, SOF_PCM_FMT_S16_LE, 48000, 2,
				     240, 2, dummy, 240, NULL, 0,
				     &f_played, &vrms, &vpeak, &xruns);
	}
}

static bool sysfs_pm_is_active(void)
{
	discover_sof_pci_device();
	if (!g_hw_ctx.found)
		return false;
	FILE *fp = fopen(g_hw_ctx.pm_status_path, "r");
	if (!fp)
		return false;
	char buf[32] = {0};
	if (fgets(buf, sizeof(buf), fp)) {
		fclose(fp);
		return strncmp(buf, "active", 6) == 0;
	}
	fclose(fp);
	return false;
}

static int sysfs_pm_get_control(char *out, size_t cap)
{
	if (!out || cap == 0)
		return -EINVAL;
	out[0] = '\0';
	discover_sof_pci_device();
	if (!g_hw_ctx.found)
		return -ENODEV;
	FILE *fp = fopen(g_hw_ctx.pm_control_path, "r");
	if (!fp)
		return -errno;
	if (!fgets(out, (int)cap, fp)) {
		fclose(fp);
		return -EIO;
	}
	fclose(fp);
	size_t len = strlen(out);
	while (len > 0 && (out[len - 1] == '\n' || out[len - 1] == '\r'))
		out[--len] = '\0';
	return 0;
}

static int sysfs_pm_set_control(const char *mode, bool hold_flag)
{
	discover_sof_pci_device();
	if (!g_hw_ctx.found)
		return -ENODEV;
	int fd = open(g_hw_ctx.pm_control_path, O_WRONLY);
	if (fd < 0)
		return -errno;
	ssize_t n = write(fd, mode, strlen(mode));
	int err = (n < 0) ? -errno : 0;
	close(fd);
	if (err == 0) {
		if (hold_flag)
			g_hw_ctx.holding_d0 = true;
		else if (strcmp(mode, "auto") == 0)
			g_hw_ctx.holding_d0 = false;
		if (strcmp(mode, "on") == 0) {
			/* Wait up to 100ms for runtime_status == active */
			for (int i = 0; i < 20 && !sysfs_pm_is_active(); i++)
				usleep(5000);
			/*
			 * On PTL (on_demand_dsp_boot), runtime_status transitions to
			 * active with fw_state == 2 (SOF_FW_BOOT_PREPARE) without booting
			 * the DSP until a PCM stream is opened. Trigger a short PCM open
			 * to complete DSP boot when waking.
			 */
			if (read_debugfs_fw_state() == 2)
				trigger_on_demand_dsp_boot();
		}
	}
	return err;
}

/*
 * 32-bit volatile MMIO read from PCI BAR4 resource4.
 * Avoids glibc AVX2/AVX-512 64-byte vector loads which return 0xFF on the
 * first cache line of PCIe MMIO BAR mappings.
 */
static int bar4_mmio_read(uint32_t bar_offset, void *dst, uint32_t size)
{
	discover_sof_pci_device();
	if (!g_hw_ctx.found)
		return -ENODEV;

	int fd = open(g_hw_ctx.resource4_path, O_RDONLY | O_SYNC);
	if (fd < 0)
		return -errno;

	uint32_t page_mask = (uint32_t)(sysconf(_SC_PAGESIZE) - 1);
	uint32_t map_base = bar_offset & ~page_mask;
	uint32_t page_off = bar_offset - map_base;
	uint32_t end_aligned = (page_off + size + 3U) & ~3U;
	size_t map_len = (end_aligned + page_mask) & ~(size_t)page_mask;

	void *map = mmap(NULL, map_len, PROT_READ, MAP_SHARED, fd, (off_t)map_base);
	close(fd);
	if (map == MAP_FAILED)
		return -errno;

	volatile const uint32_t *src32 =
		(volatile const uint32_t *)((const uint8_t *)map + (page_off & ~3U));
	uint8_t *out = (uint8_t *)dst;
	uint32_t cur_off = page_off & ~3U;
	uint32_t rem = size;
	uint32_t dst_pos = 0;

	while (rem > 0) {
		uint32_t word = *src32++;
		uint32_t byte_in_word = (dst_pos == 0) ? (page_off & 3U) : 0U;
		uint32_t avail = 4U - byte_in_word;
		uint32_t copy = rem < avail ? rem : avail;
		const uint8_t *wb = (const uint8_t *)&word;

		memcpy(out + dst_pos, wb + byte_in_word, copy);
		dst_pos += copy;
		rem -= copy;
		cur_off += 4U;
		(void)cur_off;
	}

	munmap(map, map_len);
	return 0;
}

static int bar4_mmio_write(uint32_t bar_offset, const void *src, uint32_t size)
{
	discover_sof_pci_device();
	if (!g_hw_ctx.found)
		return -ENODEV;

	int fd = open(g_hw_ctx.resource4_path, O_RDWR | O_SYNC);
	if (fd < 0)
		return -errno;

	uint32_t page_mask = (uint32_t)(sysconf(_SC_PAGESIZE) - 1);
	uint32_t map_base = bar_offset & ~page_mask;
	uint32_t page_off = bar_offset - map_base;
	uint32_t end_aligned = (page_off + size + 3U) & ~3U;
	size_t map_len = (end_aligned + page_mask) & ~(size_t)page_mask;

	void *map = mmap(NULL, map_len, PROT_READ | PROT_WRITE, MAP_SHARED,
			 fd, (off_t)map_base);
	close(fd);
	if (map == MAP_FAILED)
		return -errno;

	volatile uint32_t *dst32 =
		(volatile uint32_t *)((uint8_t *)map + (page_off & ~3U));
	const uint8_t *in = (const uint8_t *)src;
	uint32_t rem = size;
	uint32_t src_pos = 0;

	while (rem > 0) {
		uint32_t byte_in_word = (src_pos == 0) ? (page_off & 3U) : 0U;
		uint32_t avail = 4U - byte_in_word;
		uint32_t copy = rem < avail ? rem : avail;
		uint32_t word = (copy < 4U) ? *dst32 : 0U;
		uint8_t *wb = (uint8_t *)&word;

		memcpy(wb + byte_in_word, in + src_pos, copy);
		*dst32++ = word;
		src_pos += copy;
		rem -= copy;
	}

	munmap(map, map_len);
	return 0;
}

/*
 * Read the 4,096-byte MW2 Slot 1 telemetry buffer via /sys/kernel/debug/sof/exception
 * (or BAR4 MW2 + 0x1000 fallback), normalizing the kernel's -4B separator_1 offset
 * so the returned 4,096-byte buffer always has canonical slot offsets (TEL2 at 0x0100).
 */
static int read_telemetry_slot_len(int cdev_fd, uint32_t win_flags,
				   uint8_t *slot_out, uint32_t max_bytes)
{
	if (max_bytes == 0 || max_bytes > 4096U)
		max_bytes = 4096U;
	memset(slot_out, 0, 4096);

	/* 1. Try /sys/kernel/debug/sof/exception first (fastest, kernel debugfs) */
	int dfd = open("/sys/kernel/debug/sof/exception", O_RDONLY);
	if (dfd >= 0) {
		uint8_t raw[4096];
		memset(raw, 0, max_bytes);
		ssize_t n = read(dfd, raw, max_bytes);
		close(dfd);
		if (n >= 256) {
			int bias = sof_tel2_slot_bias(raw, (uint32_t)n);
			if (bias == 4 ||
			    (n <= 4092 && *(const uint32_t *)raw != 0x0000C0DEU &&
			     *(const uint32_t *)raw != 0xDEC0AD0BU)) {
				uint32_t sep = 0x0000C0DEU;
				size_t limit = max_bytes - 4U;
				size_t copy = (size_t)n <= limit ? (size_t)n : limit;
				memcpy(slot_out, &sep, 4);
				memcpy(slot_out + 4, raw, copy);
			} else {
				memcpy(slot_out, raw, (size_t)n <= max_bytes ? (size_t)n : max_bytes);
			}
			return 0;
		}
	}

	/* 2. Try /dev/sof-dsp0 ioctl if open */
	if (cdev_fd >= 0) {
		struct sof_cdev_win_io wio = {
			.win_id     = SOF_CDEV_WIN_BY_SLOT_TYPE,
			.slot_type  = SOF_CDEV_SLOT_TELEMETRY,
			.slot_index = 0,
			.offset     = 0,
			.size       = max_bytes,
			.flags      = win_flags,
			.data_ptr   = (uintptr_t)slot_out,
		};
		if (ioctl(cdev_fd, SOF_IOC_WIN_READ, &wio) == 0)
			return 0;
	}

	/* 3. Fallback to direct BAR4 MMIO read of MW2 Slot 1 (MW2 + 0x1000) */
	discover_sof_pci_device();
	if (g_hw_ctx.found)
		return bar4_mmio_read(g_hw_ctx.win_bar_off[2] + 0x1000U, slot_out, max_bytes);

	return -ENODEV;
}

static int read_telemetry_slot_4k(int cdev_fd, uint32_t win_flags, uint8_t *slot_out)
{
	return read_telemetry_slot_len(cdev_fd, win_flags, slot_out, 4096U);
}

static int populate_direct_cdev_info(struct sof_cdev_info *info)
{
	discover_sof_pci_device();
	if (!g_hw_ctx.found)
		return -ENODEV;

	memset(info, 0, sizeof(*info));
	info->abi_version = SOF_CDEV_ABI_VERSION;
	info->ipc_type = 1; /* IPC4 */
	info->pm_runtime_active = sysfs_pm_is_active() ? 1 : 0;
	info->dsp_power_state = info->pm_runtime_active ? 1 /* D0 */ : 4 /* D3 */;
	info->num_cores = g_hw_ctx.num_cores;
	info->enabled_cores_mask = 1;
	info->max_payload_size = 4096;
	info->hw_ip_version = g_hw_ctx.hw_ip_version;
	const char *short_drv = g_hw_ctx.driver;
	if (strncmp(short_drv, "sof-audio-pci-intel-", 20) == 0) {
		snprintf(info->platform, sizeof(info->platform), "sof-%s (%s)",
			 short_drv + 20, g_hw_ctx.bdf);
		snprintf(info->fw_filename, sizeof(info->fw_filename),
			 "sof-%s.ri", short_drv + 20);
	} else {
		snprintf(info->platform, sizeof(info->platform), "%s (%s)",
			 short_drv, g_hw_ctx.bdf);
		snprintf(info->fw_filename, sizeof(info->fw_filename), "sof.ri");
	}

	for (int w = 0; w < SOF_CDEV_MAX_WINDOWS; w++) {
		info->windows[w].win_id = (uint32_t)w;
		info->windows[w].bar_offset = g_hw_ctx.win_bar_off[w];
		info->windows[w].size = g_hw_ctx.win_size[w];
		info->windows[w].flags = 1;
	}

	int dfs_fw_state = read_debugfs_fw_state();
	if (dfs_fw_state >= 0) {
		info->fw_state = (uint32_t)dfs_fw_state;
	} else if (info->pm_runtime_active) {
		uint32_t fw_regs[8] = {0};
		if (bar4_mmio_read(g_hw_ctx.win_bar_off[0], fw_regs, sizeof(fw_regs)) == 0 &&
		    fw_regs[0] != 0xFFFFFFFFU) {
			if (fw_regs[0] == 0U || (fw_regs[0] & 0x0FFFFFFFU) >= 5U)
				info->fw_state = 7; /* SOF_FW_BOOT_COMPLETE */
			else
				info->fw_state = fw_regs[0] & 0xFFU;
		} else {
			info->fw_state = 7;
		}
	} else {
		info->fw_state = 7;
	}

	if (info->fw_state == 7) {
		FILE *vfp = fopen("/sys/kernel/debug/sof/fw_version", "r");
		if (vfp) {
			unsigned int maj = 0, min = 0, mic = 0, bld = 0;
			char vline[128];
			while (fgets(vline, sizeof(vline), vfp)) {
				if (sscanf(vline, "%u.%u.%u.%u", &maj, &min, &mic, &bld) >= 3 ||
				    sscanf(vline, "version: %u.%u.%u.%u", &maj, &min, &mic, &bld) >= 3) {
					info->fw_ver_major = (uint16_t)maj;
					info->fw_ver_minor = (uint16_t)min;
					info->fw_ver_micro = (uint16_t)mic;
					info->fw_ver_build = (uint16_t)bld;
					break;
				}
			}
			fclose(vfp);
		}
	}

	if (info->pm_runtime_active && info->fw_state == 7) {
		/* Read MW2 Page 0 debug slot descriptors (15 x 12-byte descriptors) */
		uint32_t desc_raw[15 * 3];
		if (bar4_mmio_read(g_hw_ctx.win_bar_off[2], desc_raw, sizeof(desc_raw)) == 0) {
			for (int s = 0; s < 15; s++) {
				uint32_t res_id = desc_raw[s * 3 + 0];
				uint32_t stype  = desc_raw[s * 3 + 1];
				uint32_t vma    = desc_raw[s * 3 + 2];
				if (stype == 0 || stype == 0xFFFFFFFFU)
					continue;
				info->debug_slots[s].slot_index = (uint32_t)s;
				info->debug_slots[s].res_id = res_id;
				info->debug_slots[s].type = stype;
				info->debug_slots[s].vma = vma;
				info->debug_slots[s].win_offset = (uint32_t)(s + 1) * 4096U;
				info->debug_slots[s].bar_offset =
					g_hw_ctx.win_bar_off[2] + (uint32_t)(s + 1) * 4096U;
				info->debug_slots[s].size = 4096U;
			}
		}
	}
	return 0;
}

static int direct_win_read(const struct sof_net_win_io_req *wreq, uint8_t *rbuf)
{
	discover_sof_pci_device();
	if (!g_hw_ctx.found)
		return -ENODEV;

	bool temp_wake = false;
	if ((wreq->flags & SOF_CDEV_WIN_FL_WAKE_DSP) && !sysfs_pm_is_active()) {
		if (sysfs_pm_set_control("on", false) == 0)
			temp_wake = true;
	}

	int st = -EINVAL;
	if (wreq->win_id == SOF_CDEV_WIN_BY_SLOT_TYPE) {
		if (wreq->slot_type == SOF_CDEV_SLOT_TELEMETRY &&
		    wreq->offset + wreq->size <= 4096U) {
			uint8_t slot4k[4096];
			st = read_telemetry_slot_4k(-1, wreq->flags, slot4k);
			if (st == 0)
				memcpy(rbuf, slot4k + wreq->offset, wreq->size);
		} else {
			struct sof_cdev_info info;
			if (populate_direct_cdev_info(&info) == 0) {
				uint32_t match = 0;
				for (int s = 0; s < SOF_CDEV_MAX_DEBUG_SLOTS; s++) {
					if (info.debug_slots[s].type == wreq->slot_type) {
						if (match == wreq->slot_index) {
							if (wreq->offset + wreq->size <=
							    info.debug_slots[s].size) {
								st = bar4_mmio_read(
									info.debug_slots[s].bar_offset +
									wreq->offset,
									rbuf, wreq->size);
							}
							break;
						}
						match++;
					}
				}
			}
		}
	} else if (wreq->win_id < SOF_CDEV_MAX_WINDOWS) {
		/* If reading MW2 Slot 1 (telemetry slot), prefer normalized read_telemetry_slot_4k */
		if (wreq->win_id == SOF_CDEV_WIN_DEBUG &&
		    wreq->offset >= 4096U && wreq->offset + wreq->size <= 8192U) {
			uint8_t slot4k[4096];
			if (read_telemetry_slot_4k(-1, wreq->flags, slot4k) == 0) {
				memcpy(rbuf, slot4k + (wreq->offset - 4096U), wreq->size);
				st = 0;
			}
		}
		if (st != 0 && wreq->offset + wreq->size <= g_hw_ctx.win_size[wreq->win_id]) {
			st = bar4_mmio_read(g_hw_ctx.win_bar_off[wreq->win_id] + wreq->offset,
					    rbuf, wreq->size);
		}
	}

	if (temp_wake)
		sysfs_pm_set_control("auto", false);
	return st;
}

static int direct_win_write(const struct sof_net_win_io_req *wreq, const uint8_t *wbuf)
{
	discover_sof_pci_device();
	if (!g_hw_ctx.found)
		return -ENODEV;
	if (wreq->win_id >= SOF_CDEV_MAX_WINDOWS ||
	    wreq->offset + wreq->size > g_hw_ctx.win_size[wreq->win_id])
		return -EINVAL;
	return bar4_mmio_write(g_hw_ctx.win_bar_off[wreq->win_id] + wreq->offset,
			       wbuf, wreq->size);
}

/* -------------------------------------------------------------------------
 * Composite Telemetry Snapshot Handler
 * -------------------------------------------------------------------------
 */

static int read_large_cfg_helper(int cdev_fd, uint32_t param_id, void *buf,
				 uint32_t cap, uint32_t *out_len)
{
	*out_len = 0;
	if (cdev_fd < 0)
		return -ENODEV;

	struct sof_cdev_large_cfg cfg = {
		.module_id   = 0,
		.instance_id = 0,
		.param_id    = param_id,
		.set_get     = SOF_CDEV_LARGE_CFG_GET,
		.data_size   = cap,
		.data_ptr    = (uintptr_t)buf,
	};

	if (ioctl(cdev_fd, SOF_IOC_IPC_LARGE_CFG, &cfg) < 0)
		return -errno;
	*out_len = cfg.data_size;
	return 0;
}

static int read_file_first_line(const char *path, char *out, size_t cap)
{
	FILE *fp = fopen(path, "r");
	if (!fp)
		return -1;
	if (!fgets(out, (int)cap, fp)) {
		fclose(fp);
		return -1;
	}
	fclose(fp);
	size_t len = strlen(out);
	while (len > 0 && (out[len - 1] == '\n' || out[len - 1] == '\r'))
		out[--len] = '\0';
	return 0;
}

static uint32_t count_running_alsa_substreams(void)
{
	uint32_t running = atomic_load_explicit(&g_tone_running, memory_order_acquire) ? 1U : 0U;
	int sof_card = get_sof_card_index();
	if (sof_card < 0)
		return running;

	char card_path[128];
	snprintf(card_path, sizeof(card_path), "/proc/asound/card%d", sof_card);
	DIR *card_dir = opendir(card_path);
	if (!card_dir)
		return running;

	struct dirent *de;
	while ((de = readdir(card_dir)) != NULL) {
		if (strncmp(de->d_name, "pcm", 3) != 0)
			continue;
		char pcm_path[256];
		snprintf(pcm_path, sizeof(pcm_path), "%s/%s", card_path, de->d_name);
		DIR *pcm_dir = opendir(pcm_path);
		if (!pcm_dir)
			continue;
		struct dirent *sde;
		while ((sde = readdir(pcm_dir)) != NULL) {
			if (strncmp(sde->d_name, "sub", 3) != 0)
				continue;
			char st_path[384];
			snprintf(st_path, sizeof(st_path),
				 "%s/%s/status", pcm_path, sde->d_name);
			char buf[128];
			if (read_file_first_line(st_path, buf, sizeof(buf)) == 0 &&
			    strstr(buf, "RUNNING") != NULL)
				running++;
		}
		closedir(pcm_dir);
	}
	closedir(card_dir);
	return running;
}

static pthread_mutex_t g_fw_mods_lock = PTHREAD_MUTEX_INITIALIZER;
static uint8_t g_cached_fw_mods_tlv[4096];
static uint32_t g_cached_fw_mods_len;
static bool g_cached_fw_mods_loaded;

static void synthesize_modules_info_from_fw(struct sof_net_telem_snapshot *snap)
{
	if (snap->flags_present & SOF_TELEM_FL_MODULES_INFO_TLV)
		return;

	pthread_mutex_lock(&g_fw_mods_lock);
	if (!g_cached_fw_mods_loaded) {
		char fw_dir[128] = "intel/sof-ipc4/ptl/community";
		char fw_name[64] = "sof-ptl.ri";
		read_file_first_line("/sys/kernel/debug/sof/fw_profile/fw_path",
				     fw_dir, sizeof(fw_dir));
		read_file_first_line("/sys/kernel/debug/sof/fw_profile/fw_name",
				     fw_name, sizeof(fw_name));

		char candidates[4][256];
		snprintf(candidates[0], sizeof(candidates[0]),
			 "/lib/firmware/%s/%s", fw_dir, fw_name);
		snprintf(candidates[1], sizeof(candidates[1]),
			 "/lib/firmware/intel/sof-ipc4/ptl/%s", fw_name);
		snprintf(candidates[2], sizeof(candidates[2]),
			 "/lib/firmware/intel/sof-ipc4/arl/%s", fw_name);
		snprintf(candidates[3], sizeof(candidates[3]),
			 "/lib/firmware/intel/sof-ipc4/tgl/%s", fw_name);

		FILE *fp = NULL;
		for (int i = 0; i < 4 && !fp; i++)
			fp = fopen(candidates[i], "rb");

		if (fp) {
			fseek(fp, 0, SEEK_END);
			long fsz = ftell(fp);
			fseek(fp, 0, SEEK_SET);
			if (fsz > 0 && fsz <= 16 * 1024 * 1024) {
				uint8_t *fw_buf = malloc((size_t)fsz);
				if (fw_buf && fread(fw_buf, 1, (size_t)fsz, fp) == (size_t)fsz) {
					uint32_t mod_cnt = 0;
					uint32_t out_off = 4;
					for (long pos = 0; pos + 116 <= fsz && mod_cnt < 34; pos += 4) {
						if (memcmp(fw_buf + pos, "$AME", 4) == 0 &&
						    fw_buf[pos + 4] >= 'A' && fw_buf[pos + 4] <= 'Z') {
							if (out_off + 116 <= sizeof(g_cached_fw_mods_tlv)) {
								uint16_t rt_mod_id = (uint16_t)mod_cnt;
								uint16_t rt_state = 2U; /* MODULE_INITIALIZED */
								memcpy(g_cached_fw_mods_tlv + out_off,
								       fw_buf + pos, 116);
								memcpy(g_cached_fw_mods_tlv + out_off,
								       &rt_mod_id, 2);
								memcpy(g_cached_fw_mods_tlv + out_off + 2,
								       &rt_state, 2);
								out_off += 116;
								mod_cnt++;
							}
							pos += 112;
						}
					}
					if (mod_cnt > 0) {
						memcpy(g_cached_fw_mods_tlv, &mod_cnt, 4);
						g_cached_fw_mods_len = out_off;
					}
				}
				free(fw_buf);
			}
			fclose(fp);
		}
		g_cached_fw_mods_loaded = true;
	}

	if (g_cached_fw_mods_len > 0 &&
	    g_cached_fw_mods_len <= sizeof(snap->modules_info_tlv)) {
		memcpy(snap->modules_info_tlv, g_cached_fw_mods_tlv,
		       g_cached_fw_mods_len);
		snap->modules_info_len = g_cached_fw_mods_len;
		snap->flags_present |= SOF_TELEM_FL_MODULES_INFO_TLV;
	}
	pthread_mutex_unlock(&g_fw_mods_lock);
}

struct client_telem_state {
	uint32_t prev_core_ticks[SOF_TEL_MAX_CORES];
	bool     has_prev_core_ticks;
};

/*
 * Synthesize IPC4 TLVs (ext_perf_tlv, pwr_state_tlv, mem_state_tlv) directly from
 * TEL2 (MW2 Slot 1) and PMW2 (MW3) when /dev/sof-dsp0 IPC ioctls are unavailable.
 */
static void synthesize_tlvs_from_windows(struct sof_net_telem_snapshot *snap,
					 uint32_t query_mask,
					 struct client_telem_state *tstate)
{
	const uint8_t *t2 = snap->tel2_slot;
	uint8_t *w3 = snap->pmw2_win3;
	int bias = sof_tel2_slot_bias(t2, snap->tel2_slot_len);

	if (query_mask & SOF_TELEM_FL_MODULES_INFO_TLV)
		synthesize_modules_info_from_fw(snap);

	/*
	 * Sanitize per-core system_tick_info in snap->telem_slot:
	 * 1) In Zephyr SOF, telemetry_update() is only called inside schedule_ll_callback().
	 *    When no LL stream/task is running, the LL timer is stopped and systick_info[c]
	 *    remains frozen in SRAM with stale values from the end of the previous stream.
	 * 2) When a stream stops/restarts, begin_stamp - telemetry_prev_ccount[c] spans the
	 *    idle gap and inflates avg_utilization / peak_utilization beyond max_time_elapsed.
	 */
	if (snap->flags_present & SOF_TELEM_FL_TELEM_SLOT) {
		for (uint32_t c = 0; c < SOF_TEL_MAX_CORES; c++) {
			uint32_t *st32 = (uint32_t *)(snap->telem_slot + 4 + c * 32);
			uint32_t t_cnt   = st32[0];
			uint32_t last_el = st32[1];
			uint32_t max_el  = st32[2];
			uint32_t avg_u   = st32[4];
			uint32_t peak_u  = st32[5];
			uint32_t p4k_u   = st32[6];
			uint32_t p8k_u   = st32[7];

			if (t_cnt == 0xFFFFFFFFU)
				continue;

			bool ticking;
			if (tstate->has_prev_core_ticks)
				ticking = (t_cnt != tstate->prev_core_ticks[c]);
			else
				ticking = (snap->active_pcm_streams > 0 && t_cnt > 0);
			tstate->prev_core_ticks[c] = t_cnt;

			if (max_el > 400000U)
				max_el = (last_el <= 400000U) ? last_el : 0U;
			if (peak_u > 800000U || (max_el > 0 && peak_u > max_el * 2U))
				peak_u = max_el;
			if (p4k_u > 800000U || (max_el > 0 && p4k_u > max_el * 2U))
				p4k_u = peak_u;
			if (p8k_u > 800000U || (max_el > 0 && p8k_u > max_el * 2U))
				p8k_u = peak_u;

			if (!ticking) {
				/* LL scheduler is stopped (0 ticks/s) -> live load is 0 KCPS */
				last_el = 0U;
				avg_u   = 0U;
			} else if (avg_u > 800000U || (max_el > 0 && avg_u > max_el) ||
				   (last_el > 0 && avg_u > last_el * 4U)) {
				avg_u = last_el;
			}

			st32[1] = last_el;
			st32[2] = max_el;
			st32[4] = avg_u;
			st32[5] = peak_u;
			st32[6] = p4k_u;
			st32[7] = p8k_u;
		}
		tstate->has_prev_core_ticks = true;
	}

	/* 1. Synthesize pwr_state_tlv & enabled_cores_mask from TEL2 */
	if ((query_mask & SOF_TELEM_FL_PWR_STATE_TLV) &&
	    !(snap->flags_present & SOF_TELEM_FL_PWR_STATE_TLV) &&
	    (snap->flags_present & SOF_TELEM_FL_TELEM_SLOT)) {
		uint32_t core_mask = 1;
		uint32_t core_kcps[SOF_TEL_MAX_CORES] = {0};
		uint32_t ncores = snap->dev_info.num_cores ?
				  snap->dev_info.num_cores : SOF_TEL_MAX_CORES;
		if (ncores > SOF_TEL_MAX_CORES)
			ncores = SOF_TEL_MAX_CORES;

		for (uint32_t c = 0; c < ncores; c++) {
			const uint32_t *st32 =
				(const uint32_t *)(snap->telem_slot + 4 + c * 32);
			core_kcps[c] = st32[4]; /* sanitized avg_utilization */
			if (st32[0] > 0 || st32[4] > 0)
				core_mask |= (1U << c);
		}
		snap->dev_info.enabled_cores_mask = core_mask;

		uint32_t off = 0;
		uint32_t t0_type = 0, t0_len = 4;
		uint32_t t1_type = 1, t1_len = ncores * 4;
		memcpy(snap->pwr_state_tlv + off, &t0_type, 4); off += 4;
		memcpy(snap->pwr_state_tlv + off, &t0_len, 4);  off += 4;
		memcpy(snap->pwr_state_tlv + off, &core_mask, 4); off += 4;
		memcpy(snap->pwr_state_tlv + off, &t1_type, 4); off += 4;
		memcpy(snap->pwr_state_tlv + off, &t1_len, 4);  off += 4;
		memcpy(snap->pwr_state_tlv + off, core_kcps, t1_len); off += t1_len;
		snap->pwr_state_len = off;
		snap->flags_present |= SOF_TELEM_FL_PWR_STATE_TLV;
	}

	/* 2. Synthesize or patch mem_state_tlv when requested and TEL2 mem_sec.sram is present */
	if (query_mask & SOF_TELEM_FL_MEM_STATE_TLV) {
		uint32_t hp_tot_pages = 0, hp_free_pages = 0, hp_banks = 0;
		uint32_t pgctl[3] = { 0xFFFFFFFFU, 0xFFFFFFFFU, 0xFFFFFFFFU };
		uint32_t lp_tot_pages = 0, lp_free_pages = 0, lp_banks = 0;
		uint32_t lp_pgctl = 0xFFFFFFFFU;

		if (bias >= 0 &&
		    SOF_TEL_OFF_MEM_SEC - (uint32_t)bias + sizeof(struct sof_tel_mem_section) <=
		    snap->tel2_slot_len) {
			const struct sof_tel_mem_section *ms =
				(const struct sof_tel_mem_section *)
				(t2 + SOF_TEL_OFF_MEM_SEC - bias);
			hp_tot_pages = ms->sram.hp_sram_total_pages;
			hp_free_pages = ms->sram.hp_sram_free_pages;
			hp_banks = (hp_tot_pages + 31U) / 32U;
			pgctl[0] = ~ms->sram.ebb_power_mask_lo;
			pgctl[1] = ~ms->sram.ebb_power_mask_hi;
			lp_tot_pages = ms->sram.lp_sram_total_pages;
			lp_free_pages = ms->sram.lp_sram_free_pages;
			lp_banks = (lp_tot_pages + 15U) / 16U;
			lp_pgctl = ~ms->sram.lpsram_ebb_mask;
		}

		if (!(snap->flags_present & SOF_TELEM_FL_MEM_STATE_TLV)) {
			if (hp_banks > 0 && hp_banks <= 96) {
				uint32_t off = 0;
				uint32_t ttype = 1; /* HPSRAM */
				uint32_t ebb_dwords = (hp_banks + 31U) / 32U;
				uint32_t vlen = 12U + ebb_dwords * 4U + hp_banks * 2U;
				memcpy(snap->mem_state_tlv + off, &ttype, 4); off += 4;
				memcpy(snap->mem_state_tlv + off, &vlen, 4);  off += 4;
				memcpy(snap->mem_state_tlv + off, &hp_free_pages, 4); off += 4;
				memcpy(snap->mem_state_tlv + off, &ebb_dwords, 4); off += 4;
				for (uint32_t d = 0; d < ebb_dwords; d++) {
					memcpy(snap->mem_state_tlv + off, &pgctl[d], 4);
					off += 4;
				}
				memcpy(snap->mem_state_tlv + off, &hp_banks, 4); off += 4;
				uint32_t used_banks = (hp_tot_pages >= hp_free_pages) ?
						      ((hp_tot_pages - hp_free_pages + 31U) / 32U) : 1U;
				for (uint32_t b = 0; b < hp_banks; b++) {
					uint16_t amask = (b < used_banks) ? 0xFFFFU : 0x0000U;
					memcpy(snap->mem_state_tlv + off, &amask, 2);
					off += 2;
				}

				if (lp_banks > 0 && lp_banks <= 32 &&
				    off + 20U + lp_banks * 2U <= sizeof(snap->mem_state_tlv)) {
					uint32_t lp_type = 0; /* LPSRAM */
					uint32_t lp_dwords = 1U;
					uint32_t lp_vlen = 12U + lp_dwords * 4U + lp_banks * 2U;
					memcpy(snap->mem_state_tlv + off, &lp_type, 4); off += 4;
					memcpy(snap->mem_state_tlv + off, &lp_vlen, 4); off += 4;
					memcpy(snap->mem_state_tlv + off, &lp_free_pages, 4); off += 4;
					memcpy(snap->mem_state_tlv + off, &lp_dwords, 4); off += 4;
					memcpy(snap->mem_state_tlv + off, &lp_pgctl, 4); off += 4;
					memcpy(snap->mem_state_tlv + off, &lp_banks, 4); off += 4;
					uint32_t lp_used = (lp_tot_pages >= lp_free_pages) ?
							   ((lp_tot_pages - lp_free_pages + 15U) / 16U) : 0U;
					for (uint32_t b = 0; b < lp_banks; b++) {
						uint16_t amask = (b < lp_used) ? 0xFFFFU : 0x0000U;
						memcpy(snap->mem_state_tlv + off, &amask, 2);
						off += 2;
					}
				}

				snap->mem_state_len = off;
				snap->flags_present |= SOF_TELEM_FL_MEM_STATE_TLV;
			}
		} else if (lp_banks > 0 && lp_banks <= 32 && snap->mem_state_len >= 8) {
			/*
			 * On platforms where LPSRAM is 64 KB (1 bank) and basefw_mem_state_info
			 * divides 64 KB by SRAM_BANK_SIZE (128 KB) == 0, the IPC4 LPSRAM tuple
			 * reports 0 banks. Patch or append the LPSRAM tuple from TEL2.
			 */
			uint32_t off = 0;
			uint32_t hp_end = 0;
			bool lp_valid = false;
			while (off + 8 <= snap->mem_state_len) {
				uint32_t ttype = 0, tlen = 0;
				memcpy(&ttype, snap->mem_state_tlv + off, 4);
				memcpy(&tlen, snap->mem_state_tlv + off + 4, 4);
				if (tlen == 0 || off + 8 + tlen > snap->mem_state_len)
					break;
				if (ttype == 1) {
					hp_end = off + 8 + tlen;
				} else if (ttype == 0 && tlen >= 12) {
					uint32_t ebb_dw = 0, bcnt = 0;
					memcpy(&ebb_dw, snap->mem_state_tlv + off + 12, 4);
					if (ebb_dw <= 8 && 12 + ebb_dw * 4 <= tlen)
						memcpy(&bcnt, snap->mem_state_tlv + off + 16 + ebb_dw * 4, 4);
					if (ebb_dw > 0 && bcnt > 0)
						lp_valid = true;
				}
				off += 8 + tlen;
			}
			if (!lp_valid && hp_end > 0 &&
			    hp_end + 24U + lp_banks * 2U <= sizeof(snap->mem_state_tlv)) {
				uint32_t p_off = hp_end;
				uint32_t lp_type = 0; /* LPSRAM */
				uint32_t lp_dwords = 1U;
				uint32_t lp_vlen = 12U + lp_dwords * 4U + lp_banks * 2U;
				memcpy(snap->mem_state_tlv + p_off, &lp_type, 4); p_off += 4;
				memcpy(snap->mem_state_tlv + p_off, &lp_vlen, 4); p_off += 4;
				memcpy(snap->mem_state_tlv + p_off, &lp_free_pages, 4); p_off += 4;
				memcpy(snap->mem_state_tlv + p_off, &lp_dwords, 4); p_off += 4;
				memcpy(snap->mem_state_tlv + p_off, &lp_pgctl, 4); p_off += 4;
				memcpy(snap->mem_state_tlv + p_off, &lp_banks, 4); p_off += 4;
				uint32_t lp_used = (lp_tot_pages >= lp_free_pages) ?
						   ((lp_tot_pages - lp_free_pages + 15U) / 16U) : 0U;
				for (uint32_t b = 0; b < lp_banks; b++) {
					uint16_t amask = (b < lp_used) ? 0xFFFFU : 0x0000U;
					memcpy(snap->mem_state_tlv + p_off, &amask, 2);
					p_off += 2;
				}
				snap->mem_state_len = p_off;
			}
		}
	}

	/* 3. Synthesize ext_perf_tlv from TEL2 core ticks + PMW2 MW3 perf_data[88] */
	if ((query_mask & SOF_TELEM_FL_EXT_PERF_TLV) &&
	    !(snap->flags_present & SOF_TELEM_FL_EXT_PERF_TLV) &&
	    (snap->flags_present & SOF_TELEM_FL_PMW2_WIN3)) {
		struct {
			uint32_t resource_id;
			uint32_t power_mode : 1;
			uint32_t rsvd       : 30;
			uint32_t is_removed : 1;
			uint32_t peak_kcps;
			uint32_t avg_kcps;
			uint64_t total_iteration_count;
			uint64_t total_cycles_consumed;
		} __attribute__((packed)) *out_items =
			(void *)(snap->ext_perf_tlv + 4);
		uint32_t max_out = (sizeof(snap->ext_perf_tlv) - 4) / sizeof(*out_items);
		uint32_t count = 0;

		/* Add per-core summary items first */
		for (uint32_t c = 0; c < SOF_TEL_MAX_CORES && count < max_out; c++) {
			const uint32_t *st32 =
				(const uint32_t *)(snap->telem_slot + 4 + c * 32);
			if (st32[0] == 0 && st32[4] == 0 && c > 0)
				continue;
			out_items[count].resource_id = (c << 16);
			out_items[count].power_mode = 0;
			out_items[count].rsvd = 0;
			out_items[count].is_removed = 0;
			out_items[count].peak_kcps = st32[5];
			out_items[count].avg_kcps = st32[4];
			out_items[count].total_iteration_count = st32[0];
			out_items[count].total_cycles_consumed = st32[3];
			count++;
		}

		/* Add component items from MW3 perf_data (48-byte DSP stride) preserving FW state */
		for (uint32_t i = 0; i < SOF_TEL_MW3_MAX_COMP_ITEMS && count < max_out; i++) {
			const uint8_t *p = w3 + i * 48U;
			uint32_t res_id = *(const uint32_t *)(p + 0);
			uint32_t flags  = *(const uint32_t *)(p + 4);
			uint32_t peak_k = *(const uint32_t *)(p + 8);
			uint32_t avg_k  = *(const uint32_t *)(p + 12);
			uint32_t iters  = *(const uint32_t *)(p + 16);
			uint64_t tot_c  = *(const uint64_t *)(p + 20);

			if ((res_id == 0 && peak_k == 0 && avg_k == 0 && iters == 0) ||
			    res_id == 0xFFFFFFFFU)
				continue;
			if (res_id == 0)
				continue; /* Skip duplicate core 0 entry */

			out_items[count].resource_id = res_id;
			out_items[count].power_mode = flags & 1U;
			out_items[count].rsvd = 0;
			out_items[count].is_removed = (flags >> 31) & 1U;
			out_items[count].peak_kcps = peak_k;
			out_items[count].avg_kcps = avg_k;
			out_items[count].total_iteration_count = iters;
			out_items[count].total_cycles_consumed = tot_c;
			count++;
		}

		memcpy(snap->ext_perf_tlv, &count, 4);
		snap->ext_perf_len = 4 + count * sizeof(*out_items);
		snap->flags_present |= SOF_TELEM_FL_EXT_PERF_TLV;
	}
}

static int handle_telem_snapshot(int sock, int cdev_fd, uint32_t req_id,
				 const uint8_t *req_buf, uint32_t req_len,
				 struct client_telem_state *tstate)
{
	struct sof_net_telem_req req = {
		.query_mask = SOF_TELEM_FL_FW_REGS |
			      SOF_TELEM_FL_TELEM_SLOT |
			      SOF_TELEM_FL_PERF_WIN3 |
			      SOF_TELEM_FL_EXT_PERF_TLV |
			      SOF_TELEM_FL_TEL2_SLOT |
			      SOF_TELEM_FL_PMW2_WIN3,
		.flags = 0,
	};
	if (req_len >= sizeof(req))
		memcpy(&req, req_buf, sizeof(req));
	if (req.flags & SOF_TELEM_FL_COMPRESS)
		g_client_accepts_compressed = true;
	bool hot_only = ((req.query_mask | req.flags) & SOF_TELEM_FL_HOT_ONLY) != 0;
	if (hot_only) {
		req.query_mask &= ~(SOF_TELEM_FL_MEM_STATE_TLV |
				    SOF_TELEM_FL_PWR_STATE_TLV |
				    SOF_TELEM_FL_EXT_PERF_TLV |
				    SOF_TELEM_FL_MODULES_INFO_TLV);
	}

	struct sof_net_telem_snapshot *snap = calloc(1, sizeof(*snap));
	if (!snap)
		return send_reply(sock, SOF_NET_OP_TELEM_SNAPSHOT, req_id,
				  -ENOMEM, NULL, 0);

	bool temp_hold = false;
	bool temp_sysfs_hold = false;
	char prior_sysfs_ctl[32] = "auto";
	if (req.flags & SOF_TELEM_FL_WAKE_DSP) {
		if (cdev_fd >= 0) {
			struct sof_cdev_pm_ctl pm_q = { .op = SOF_PM_OP_QUERY };
			if (ioctl(cdev_fd, SOF_IOC_PM_CTL, &pm_q) == 0 && !pm_q.fd_holding_d0) {
				struct sof_cdev_pm_ctl pm_w = { .op = SOF_PM_OP_WAKE_HOLD };
				if (ioctl(cdev_fd, SOF_IOC_PM_CTL, &pm_w) == 0)
					temp_hold = true;
			}
		} else if (!g_hw_ctx.holding_d0) {
			sysfs_pm_get_control(prior_sysfs_ctl, sizeof(prior_sysfs_ctl));
			if (!sysfs_pm_is_active() || read_debugfs_fw_state() == 2) {
				if (sysfs_pm_set_control("on", false) == 0 &&
				    strcmp(prior_sysfs_ctl, "on") != 0)
					temp_sysfs_hold = true;
			}
		}
	}

	int info_rc = -1;
	if (cdev_fd >= 0)
		info_rc = ioctl(cdev_fd, SOF_IOC_GET_INFO, &snap->dev_info);
	if (info_rc < 0)
		info_rc = populate_direct_cdev_info(&snap->dev_info);

	if (info_rc < 0) {
		if (temp_hold) {
			struct sof_cdev_pm_ctl pm_r = { .op = SOF_PM_OP_RELEASE_HOLD };
			ioctl(cdev_fd, SOF_IOC_PM_CTL, &pm_r);
		}
		if (temp_sysfs_hold && !g_hw_ctx.holding_d0)
			sysfs_pm_set_control(prior_sysfs_ctl[0] ? prior_sysfs_ctl : "auto", false);
		free(snap);
		return send_reply(sock, SOF_NET_OP_TELEM_SNAPSHOT, req_id,
				  info_rc, NULL, 0);
	}

	snap->active_pcm_streams = count_running_alsa_substreams();

	bool can_read_wins = (snap->dev_info.pm_runtime_active &&
			      snap->dev_info.fw_state == 7 /* SOF_FW_BOOT_COMPLETE */) ||
			     (req.flags & SOF_TELEM_FL_WAKE_DSP) ||
			     sysfs_pm_is_active();
	uint32_t win_flags = (req.flags & SOF_TELEM_FL_WAKE_DSP) ?
			     SOF_CDEV_WIN_FL_WAKE_DSP : 0;

	if (can_read_wins) {
		if (req.query_mask & SOF_TELEM_FL_FW_REGS) {
			bool got_fw = false;
			if (cdev_fd >= 0) {
				struct sof_cdev_win_io wio = {
					.win_id   = SOF_CDEV_WIN_FW_REGS,
					.offset   = 0,
					.size     = sizeof(snap->fw_regs),
					.flags    = win_flags,
					.data_ptr = (uintptr_t)snap->fw_regs,
				};
				if (ioctl(cdev_fd, SOF_IOC_WIN_READ, &wio) == 0)
					got_fw = true;
			}
			if (!got_fw && g_hw_ctx.found) {
				if (bar4_mmio_read(g_hw_ctx.win_bar_off[0],
						   snap->fw_regs,
						   sizeof(snap->fw_regs)) == 0)
					got_fw = true;
			}
			if (got_fw)
				snap->flags_present |= SOF_TELEM_FL_FW_REGS;
		}

		if (req.query_mask & (SOF_TELEM_FL_TELEM_SLOT | SOF_TELEM_FL_TEL2_SLOT)) {
			uint32_t read_sz = hot_only ? SOF_TEL_HOT_ZONE_BYTES :
					   (uint32_t)sizeof(snap->tel2_slot);
			if (read_telemetry_slot_len(cdev_fd, win_flags,
						    snap->tel2_slot, read_sz) == 0) {
				snap->tel2_slot_len = read_sz;
				snap->flags_present |= SOF_TELEM_FL_TELEM_SLOT |
						       SOF_TELEM_FL_TEL2_SLOT |
						       (hot_only ? SOF_TELEM_FL_HOT_ONLY : 0);

				/*
				 * Populate legacy 512B snap->telem_slot with 32-byte per-core
				 * system_tick_info stride so legacy readers (softop, testbench.py)
				 * read all cores 0..4 accurately even when DSP struct system_tick_info
				 * is 40 bytes (8 uint32_t + rsvd[2]).
				 */
				if (sof_tel2_slot_bias(snap->tel2_slot,
						       snap->tel2_slot_len) == 0) {
					memcpy(snap->telem_slot, snap->tel2_slot, 4);
					memset(snap->telem_slot + 4, 0,
					       sizeof(snap->telem_slot) - 4);
					for (int c = 0; c < 5; c++) {
						memcpy(snap->telem_slot + 4 + c * 32,
						       snap->tel2_slot + 4 + c * 40,
						       32);
					}
				} else {
					memcpy(snap->telem_slot, snap->tel2_slot,
					       sizeof(snap->telem_slot));
				}
			}
		}

		if (req.query_mask & (SOF_TELEM_FL_PERF_WIN3 | SOF_TELEM_FL_PMW2_WIN3 |
				      SOF_TELEM_FL_EXT_PERF_TLV)) {
			bool got_w3 = false;
			discover_sof_pci_device();
			if (g_hw_ctx.found) {
				if (hot_only) {
					/* Read only perf_data + mod_hot (0x0000..0x1200) + PMW2 hdr (0x1FC0..0x2000) */
					if (bar4_mmio_read(g_hw_ctx.win_bar_off[3],
							   snap->pmw2_win3,
							   SOF_TEL_MW3_MOD_COLD_OFFSET) == 0 &&
					    bar4_mmio_read(g_hw_ctx.win_bar_off[3] +
							   SOF_TEL_MW3_HDR_OFFSET,
							   snap->pmw2_win3 + SOF_TEL_MW3_HDR_OFFSET,
							   sizeof(struct sof_tel_mw3_hdr)) == 0)
						got_w3 = true;
				} else {
					if (bar4_mmio_read(g_hw_ctx.win_bar_off[3],
							   snap->pmw2_win3,
							   sizeof(snap->pmw2_win3)) == 0)
						got_w3 = true;
				}
			}
			if (!got_w3 && cdev_fd >= 0) {
				struct sof_cdev_win_io wio = {
					.win_id   = SOF_CDEV_WIN_PERF,
					.offset   = 0,
					.size     = sizeof(snap->pmw2_win3),
					.flags    = win_flags,
					.data_ptr = (uintptr_t)snap->pmw2_win3,
				};
				if (ioctl(cdev_fd, SOF_IOC_WIN_READ, &wio) == 0)
					got_w3 = true;
			}
			if (got_w3) {
				/*
				 * When client accepts SOFZ compression and PMW2 is present,
				 * leave perf_win3[4096] zeroed on the wire (compresses to 3B)
				 * because pmw2_win3[0..4095] already carries those exact bytes.
				 */
				if (!g_client_accepts_compressed ||
				    !(req.query_mask & SOF_TELEM_FL_PMW2_WIN3)) {
					sof_tel_pmw2_to_legacy_win3(snap->perf_win3,
								    snap->pmw2_win3);
				}
				snap->pmw2_win3_len = sizeof(snap->pmw2_win3);
				snap->flags_present |= SOF_TELEM_FL_PERF_WIN3 |
						       SOF_TELEM_FL_PMW2_WIN3;
			}
		}

		if (req.query_mask & SOF_TELEM_FL_MEM_STATE_TLV) {
			if (read_large_cfg_helper(cdev_fd, IPC4_MEMORY_STATE_INFO_GET,
						  snap->mem_state_tlv,
						  sizeof(snap->mem_state_tlv),
						  &snap->mem_state_len) == 0)
				snap->flags_present |= SOF_TELEM_FL_MEM_STATE_TLV;
		}

		if (req.query_mask & SOF_TELEM_FL_PWR_STATE_TLV) {
			if (read_large_cfg_helper(cdev_fd, IPC4_POWER_STATE_INFO_GET,
						  snap->pwr_state_tlv,
						  sizeof(snap->pwr_state_tlv),
						  &snap->pwr_state_len) == 0)
				snap->flags_present |= SOF_TELEM_FL_PWR_STATE_TLV;
		}

		if (req.query_mask & SOF_TELEM_FL_EXT_PERF_TLV) {
			if (read_large_cfg_helper(cdev_fd, IPC4_EXTENDED_GLOBAL_PERF_DATA,
						  snap->ext_perf_tlv,
						  sizeof(snap->ext_perf_tlv),
						  &snap->ext_perf_len) == 0 ||
			    read_large_cfg_helper(cdev_fd, IPC4_GLOBAL_PERF_DATA,
						  snap->ext_perf_tlv,
						  sizeof(snap->ext_perf_tlv),
						  &snap->ext_perf_len) == 0)
				snap->flags_present |= SOF_TELEM_FL_EXT_PERF_TLV;
		}

		if (req.query_mask & SOF_TELEM_FL_MODULES_INFO_TLV) {
			if (read_large_cfg_helper(cdev_fd, IPC4_MODULES_INFO_GET,
						  snap->modules_info_tlv,
						  sizeof(snap->modules_info_tlv),
						  &snap->modules_info_len) == 0)
				snap->flags_present |= SOF_TELEM_FL_MODULES_INFO_TLV;
		}

		/* Synthesize any missing TLVs from TEL2 and PMW2 windows */
		synthesize_tlvs_from_windows(snap, req.query_mask, tstate);
		if (hot_only) {
			snap->mem_state_len = 0;
			memset(snap->mem_state_tlv, 0, sizeof(snap->mem_state_tlv));
			snap->pwr_state_len = 0;
			memset(snap->pwr_state_tlv, 0, sizeof(snap->pwr_state_tlv));
			snap->flags_present &= ~(SOF_TELEM_FL_MEM_STATE_TLV |
						 SOF_TELEM_FL_PWR_STATE_TLV |
						 SOF_TELEM_FL_MODULES_INFO_TLV);
		}

		/* Refresh dev_info if we woke the DSP */
		if ((req.flags & SOF_TELEM_FL_WAKE_DSP) && cdev_fd >= 0)
			ioctl(cdev_fd, SOF_IOC_GET_INFO, &snap->dev_info);
	}

	if (temp_hold) {
		struct sof_cdev_pm_ctl pm_r = { .op = SOF_PM_OP_RELEASE_HOLD };
		ioctl(cdev_fd, SOF_IOC_PM_CTL, &pm_r);
	}
	if (temp_sysfs_hold && !g_hw_ctx.holding_d0)
		sysfs_pm_set_control(prior_sysfs_ctl[0] ? prior_sysfs_ctl : "auto", false);

	uint32_t send_sz = (req.query_mask & (SOF_TELEM_FL_TEL2_SLOT | SOF_TELEM_FL_PMW2_WIN3)) ?
			   (uint32_t)sizeof(*snap) : SOF_NET_TELEM_SNAPSHOT_V1_SIZE;
	if (send_sz == SOF_NET_TELEM_SNAPSHOT_V1_SIZE) {
		snap->flags_present &= ~(SOF_TELEM_FL_TEL2_SLOT |
					 SOF_TELEM_FL_PMW2_WIN3 |
					 SOF_TELEM_FL_HOT_ONLY);
	}
	int ret = send_reply(sock, SOF_NET_OP_TELEM_SNAPSHOT, req_id, 0,
			     snap, send_sz);
	free(snap);
	return ret;
}

/* -------------------------------------------------------------------------
 * DSP Shell Execution Helper (via SOF_IOC_WINSTREAM_IO or host command)
 * -------------------------------------------------------------------------
 */

static int exec_dsp_shell(int cdev_fd, const char *cmd, uint32_t timeout_ms,
			  char *out_buf, size_t out_cap)
{
	if (strncmp(cmd, "host:", 5) == 0) {
		FILE *fp = popen(cmd + 5, "r");
		if (!fp)
			return -errno;
		size_t total = 0;
		while (total + 1 < out_cap) {
			size_t n = fread(out_buf + total, 1, out_cap - 1 - total, fp);
			if (n == 0)
				break;
			total += n;
		}
		out_buf[total] = '\0';
		pclose(fp);
		return (int)total;
	}

	pthread_mutex_lock(&g_shell_lock);

	char tx_cmd[256];
	int tx_len = snprintf(tx_cmd, sizeof(tx_cmd), "%s\r\n", cmd);
	if (tx_len < 0 || (size_t)tx_len >= sizeof(tx_cmd)) {
		pthread_mutex_unlock(&g_shell_lock);
		return -EINVAL;
	}

	/* Drain any stale winstream RX bytes first */
	uint8_t drain[1024];
	struct sof_cdev_winstream_io ws = {
		.slot_type   = SOF_CDEV_SLOT_SHELL,
		.rx_win_size = 256,
		.tx_len      = 0,
		.rx_buf_len  = sizeof(drain),
		.seq_in_out  = g_shell_seq,
		.rx_ptr      = (uintptr_t)drain,
	};
	if (ioctl(cdev_fd, SOF_IOC_WINSTREAM_IO, &ws) == 0)
		g_shell_seq = ws.seq_in_out;

	/* Send command and poll for output */
	ws.tx_len = (uint32_t)tx_len;
	ws.tx_ptr = (uintptr_t)tx_cmd;
	ws.rx_buf_len = 0;
	ws.rx_ptr = 0;
	if (ioctl(cdev_fd, SOF_IOC_WINSTREAM_IO, &ws) < 0) {
		int err = -errno;
		pthread_mutex_unlock(&g_shell_lock);
		return err;
	}
	g_shell_seq = ws.seq_in_out;

	size_t total = 0;
	uint32_t elapsed = 0;
	uint32_t idle_After_data = 0;
	if (!timeout_ms || timeout_ms > 5000)
		timeout_ms = 600;

	while (elapsed < timeout_ms && total + 1 < out_cap) {
		usleep(20000);
		elapsed += 20;

		uint8_t chunk[1024];
		memset(&ws, 0, sizeof(ws));
		ws.slot_type = SOF_CDEV_SLOT_SHELL;
		ws.rx_win_size = 256;
		ws.rx_buf_len = sizeof(chunk);
		ws.seq_in_out = g_shell_seq;
		ws.rx_ptr = (uintptr_t)chunk;

		if (ioctl(cdev_fd, SOF_IOC_WINSTREAM_IO, &ws) < 0)
			break;
		g_shell_seq = ws.seq_in_out;

		if (ws.rx_bytes_read > 0) {
			size_t copy = ws.rx_bytes_read;
			if (total + copy >= out_cap)
				copy = out_cap - 1 - total;
			memcpy(out_buf + total, chunk, copy);
			total += copy;
			out_buf[total] = '\0';
			idle_After_data = 0;
			if (strstr(out_buf, "uart:~$"))
				break;
		} else if (total > 0) {
			idle_After_data += 20;
			if (idle_After_data >= 100)
				break;
		}
	}

	out_buf[total] = '\0';
	pthread_mutex_unlock(&g_shell_lock);
	return (int)total;
}

/* -------------------------------------------------------------------------
 * Per-Client Connection Worker Thread
 * -------------------------------------------------------------------------
 */

struct client_ctx {
	int sock;
	struct sockaddr_in addr;
};

static void *client_worker_fn(void *arg)
{
	struct client_ctx *ctx = (struct client_ctx *)arg;
	int sock = ctx->sock;
	free(ctx);

	int one = 1;
	setsockopt(sock, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));

	int cdev_fd = open(g_cdev_path, O_RDWR);
	if (cdev_fd < 0)
		discover_sof_pci_device();
	struct client_telem_state tstate = {0};

	while (g_running) {
		struct sof_net_hdr hdr;
		if (read_exact(sock, &hdr, sizeof(hdr)) < 0)
			break;

		if (hdr.magic != SOF_NET_MAGIC || hdr.version != SOF_NET_VERSION) {
			send_reply(sock, hdr.opcode, hdr.req_id, -EPROTO, NULL, 0);
			break;
		}

		if (hdr.payload_len > (16 * 1024 * 1024)) {
			send_reply(sock, hdr.opcode, hdr.req_id, -E2BIG, NULL, 0);
			break;
		}

		g_client_accepts_compressed = (hdr.opcode & SOF_NET_OP_FLAG_COMPRESSED) != 0;
		hdr.opcode &= SOF_NET_OP_MASK;

		uint8_t *req_buf = NULL;
		if (hdr.payload_len > 0) {
			req_buf = malloc(hdr.payload_len + 1);
			if (!req_buf)
				break;
			if (read_exact(sock, req_buf, hdr.payload_len) < 0) {
				free(req_buf);
				break;
			}
			req_buf[hdr.payload_len] = '\0';
			if (g_client_accepts_compressed &&
			    hdr.payload_len >= sizeof(struct sof_net_comp_hdr) &&
			    *(const uint32_t *)req_buf == SOF_NET_COMP_MAGIC) {
				uint8_t *dbuf = NULL;
				uint32_t dlen = 0;
				if (sof_net_decompress(req_buf, hdr.payload_len,
						       &dbuf, &dlen) == 0) {
					free(req_buf);
					req_buf = dbuf;
					hdr.payload_len = dlen;
				}
			}
		}

		int rc = 0;
		switch (hdr.opcode) {
		case SOF_NET_OP_PING:
			rc = send_reply(sock, hdr.opcode, hdr.req_id, 0, "PONG", 4);
			break;

		case SOF_NET_OP_GET_INFO: {
			struct sof_cdev_info info;
			memset(&info, 0, sizeof(info));
			int st = -ENODEV;
			if (cdev_fd >= 0)
				st = (ioctl(cdev_fd, SOF_IOC_GET_INFO, &info) < 0) ? -errno : 0;
			if (st < 0)
				st = populate_direct_cdev_info(&info);
			rc = send_reply(sock, hdr.opcode, hdr.req_id, st,
					st == 0 ? &info : NULL,
					st == 0 ? sizeof(info) : 0);
			break;
		}

		case SOF_NET_OP_PM_CTL: {
			if (hdr.payload_len < sizeof(struct sof_cdev_pm_ctl)) {
				rc = send_reply(sock, hdr.opcode, hdr.req_id, -EINVAL, NULL, 0);
				break;
			}
			struct sof_cdev_pm_ctl ctl;
			memcpy(&ctl, req_buf, sizeof(ctl));
			int st = -ENODEV;
			if (cdev_fd >= 0)
				st = (ioctl(cdev_fd, SOF_IOC_PM_CTL, &ctl) < 0) ? -errno : 0;
			if (st < 0) {
				discover_sof_pci_device();
				if (g_hw_ctx.found) {
					st = 0;
					if (ctl.op == SOF_PM_OP_WAKE_HOLD ||
					    ctl.op == SOF_PM_OP_BOOT_DSP)
						st = sysfs_pm_set_control("on", true);
					else if (ctl.op == SOF_PM_OP_RELEASE_HOLD)
						st = sysfs_pm_set_control("auto", false);
					ctl.pm_runtime_active = sysfs_pm_is_active() ? 1 : 0;
					ctl.dsp_power_state = ctl.pm_runtime_active ? 1 : 4;
					int dfs_st = read_debugfs_fw_state();
					ctl.fw_state = (dfs_st >= 0) ? (uint32_t)dfs_st : 7;
					ctl.enabled_cores_mask = 1;
					ctl.fd_holding_d0 = g_hw_ctx.holding_d0 ? 1 : 0;
				}
			}
			rc = send_reply(sock, hdr.opcode, hdr.req_id, st,
					&ctl, sizeof(ctl));
			break;
		}

		case SOF_NET_OP_IPC_TX: {
			if (hdr.payload_len < sizeof(struct sof_net_ipc_tx_req)) {
				rc = send_reply(sock, hdr.opcode, hdr.req_id, -EINVAL, NULL, 0);
				break;
			}
			struct sof_net_ipc_tx_req *ireq = (struct sof_net_ipc_tx_req *)req_buf;
			if (hdr.payload_len < sizeof(*ireq) + ireq->tx_size ||
			    ireq->rx_size > 65536) {
				rc = send_reply(sock, hdr.opcode, hdr.req_id, -EINVAL, NULL, 0);
				break;
			}
			uint8_t *out_buf = calloc(1, sizeof(struct sof_net_ipc_tx_rsp) + ireq->rx_size);
			if (!out_buf) {
				rc = send_reply(sock, hdr.opcode, hdr.req_id, -ENOMEM, NULL, 0);
				break;
			}
			struct sof_cdev_ipc_msg kmsg = {
				.primary     = ireq->primary,
				.extension   = ireq->extension,
				.tx_size     = ireq->tx_size,
				.rx_size     = ireq->rx_size,
				.flags       = ireq->flags,
				.tx_data_ptr = ireq->tx_size ? (uintptr_t)(req_buf + sizeof(*ireq)) : 0,
				.rx_data_ptr = ireq->rx_size ? (uintptr_t)(out_buf + sizeof(struct sof_net_ipc_tx_rsp)) : 0,
			};
			int st = (cdev_fd >= 0 && ioctl(cdev_fd, SOF_IOC_IPC_TX, &kmsg) == 0) ?
				 0 : (cdev_fd >= 0 ? -errno : -ENODEV);
			struct sof_net_ipc_tx_rsp *irsp = (struct sof_net_ipc_tx_rsp *)out_buf;
			irsp->reply_primary = kmsg.reply_primary;
			irsp->reply_extension = kmsg.reply_extension;
			irsp->ipc_status = kmsg.ipc_status;
			irsp->rx_size = (st == 0) ? kmsg.rx_size : 0;
			rc = send_reply(sock, hdr.opcode, hdr.req_id, st,
					out_buf, sizeof(*irsp) + irsp->rx_size);
			free(out_buf);
			break;
		}

		case SOF_NET_OP_IPC_LARGE_CFG: {
			if (hdr.payload_len < sizeof(struct sof_net_large_cfg_req)) {
				rc = send_reply(sock, hdr.opcode, hdr.req_id, -EINVAL, NULL, 0);
				break;
			}
			struct sof_net_large_cfg_req *lreq = (struct sof_net_large_cfg_req *)req_buf;
			if (!lreq->data_size || lreq->data_size > (256 * 1024)) {
				rc = send_reply(sock, hdr.opcode, hdr.req_id, -EINVAL, NULL, 0);
				break;
			}
			uint8_t *dbuf = calloc(1, lreq->data_size);
			if (!dbuf) {
				rc = send_reply(sock, hdr.opcode, hdr.req_id, -ENOMEM, NULL, 0);
				break;
			}
			if (lreq->set_get == SOF_CDEV_LARGE_CFG_SET) {
				if (hdr.payload_len < sizeof(*lreq) + lreq->data_size) {
					free(dbuf);
					rc = send_reply(sock, hdr.opcode, hdr.req_id, -EINVAL, NULL, 0);
					break;
				}
				memcpy(dbuf, req_buf + sizeof(*lreq), lreq->data_size);
			}
			struct sof_cdev_large_cfg kcfg = {
				.module_id   = lreq->module_id,
				.instance_id = lreq->instance_id,
				.param_id    = lreq->param_id,
				.set_get     = lreq->set_get,
				.data_size   = lreq->data_size,
				.flags       = lreq->flags,
				.data_ptr    = (uintptr_t)dbuf,
			};
			int st = (cdev_fd >= 0 && ioctl(cdev_fd, SOF_IOC_IPC_LARGE_CFG, &kcfg) == 0) ?
				 0 : (cdev_fd >= 0 ? -errno : -ENODEV);
			rc = send_reply(sock, hdr.opcode, hdr.req_id, st,
					(st == 0 && !lreq->set_get) ? dbuf : NULL,
					(st == 0 && !lreq->set_get) ? kcfg.data_size : 0);
			free(dbuf);
			break;
		}

		case SOF_NET_OP_WIN_INFO: {
			struct sof_cdev_win_info winfo;
			memset(&winfo, 0, sizeof(winfo));
			int st = -ENODEV;
			if (cdev_fd >= 0)
				st = (ioctl(cdev_fd, SOF_IOC_WIN_INFO, &winfo) < 0) ? -errno : 0;
			if (st < 0) {
				struct sof_cdev_info info;
				st = populate_direct_cdev_info(&info);
				if (st == 0) {
					memcpy(winfo.windows, info.windows, sizeof(winfo.windows));
					memcpy(winfo.debug_slots, info.debug_slots,
					       sizeof(winfo.debug_slots));
				}
			}
			rc = send_reply(sock, hdr.opcode, hdr.req_id, st,
					st == 0 ? &winfo : NULL,
					st == 0 ? sizeof(winfo) : 0);
			break;
		}

		case SOF_NET_OP_WIN_READ: {
			if (hdr.payload_len < sizeof(struct sof_net_win_io_req)) {
				rc = send_reply(sock, hdr.opcode, hdr.req_id, -EINVAL, NULL, 0);
				break;
			}
			struct sof_net_win_io_req *wreq = (struct sof_net_win_io_req *)req_buf;
			if (!wreq->size || wreq->size > (128 * 1024)) {
				rc = send_reply(sock, hdr.opcode, hdr.req_id, -EINVAL, NULL, 0);
				break;
			}
			uint8_t *rbuf = calloc(1, wreq->size);
			if (!rbuf) {
				rc = send_reply(sock, hdr.opcode, hdr.req_id, -ENOMEM, NULL, 0);
				break;
			}
			int st = -ENODEV;
			if (cdev_fd >= 0) {
				struct sof_cdev_win_io kwio = {
					.win_id     = wreq->win_id,
					.slot_type  = wreq->slot_type,
					.slot_index = wreq->slot_index,
					.offset     = wreq->offset,
					.size       = wreq->size,
					.flags      = wreq->flags,
					.data_ptr   = (uintptr_t)rbuf,
				};
				st = (ioctl(cdev_fd, SOF_IOC_WIN_READ, &kwio) < 0) ? -errno : 0;
			}
			if (st < 0)
				st = direct_win_read(wreq, rbuf);
			rc = send_reply(sock, hdr.opcode, hdr.req_id, st,
					st == 0 ? rbuf : NULL,
					st == 0 ? wreq->size : 0);
			free(rbuf);
			break;
		}

		case SOF_NET_OP_WIN_WRITE: {
			if (hdr.payload_len < sizeof(struct sof_net_win_io_req)) {
				rc = send_reply(sock, hdr.opcode, hdr.req_id, -EINVAL, NULL, 0);
				break;
			}
			struct sof_net_win_io_req *wreq = (struct sof_net_win_io_req *)req_buf;
			if (!wreq->size || hdr.payload_len < sizeof(*wreq) + wreq->size) {
				rc = send_reply(sock, hdr.opcode, hdr.req_id, -EINVAL, NULL, 0);
				break;
			}
			int st = -ENODEV;
			if (cdev_fd >= 0) {
				struct sof_cdev_win_io kwio = {
					.win_id     = wreq->win_id,
					.slot_type  = wreq->slot_type,
					.slot_index = wreq->slot_index,
					.offset     = wreq->offset,
					.size       = wreq->size,
					.flags      = wreq->flags,
					.data_ptr   = (uintptr_t)(req_buf + sizeof(*wreq)),
				};
				st = (ioctl(cdev_fd, SOF_IOC_WIN_WRITE, &kwio) < 0) ? -errno : 0;
			}
			if (st < 0)
				st = direct_win_write(wreq, req_buf + sizeof(*wreq));
			rc = send_reply(sock, hdr.opcode, hdr.req_id, st, NULL, 0);
			break;
		}

		case SOF_NET_OP_WINSTREAM_IO: {
			if (hdr.payload_len < sizeof(struct sof_net_winstream_req)) {
				rc = send_reply(sock, hdr.opcode, hdr.req_id, -EINVAL, NULL, 0);
				break;
			}
			struct sof_net_winstream_req *wsreq = (struct sof_net_winstream_req *)req_buf;
			if (hdr.payload_len < sizeof(*wsreq) + wsreq->tx_len ||
			    wsreq->rx_buf_len > 16384) {
				rc = send_reply(sock, hdr.opcode, hdr.req_id, -EINVAL, NULL, 0);
				break;
			}
			uint8_t *out_buf = calloc(1, sizeof(struct sof_net_winstream_rsp) + wsreq->rx_buf_len);
			if (!out_buf) {
				rc = send_reply(sock, hdr.opcode, hdr.req_id, -ENOMEM, NULL, 0);
				break;
			}
			struct sof_cdev_winstream_io kws = {
				.slot_type   = wsreq->slot_type,
				.rx_win_size = wsreq->rx_win_size,
				.tx_len      = wsreq->tx_len,
				.rx_buf_len  = wsreq->rx_buf_len,
				.seq_in_out  = wsreq->seq_in,
				.tx_ptr      = wsreq->tx_len ? (uintptr_t)(req_buf + sizeof(*wsreq)) : 0,
				.rx_ptr      = wsreq->rx_buf_len ? (uintptr_t)(out_buf + sizeof(struct sof_net_winstream_rsp)) : 0,
			};
			int st = (ioctl(cdev_fd, SOF_IOC_WINSTREAM_IO, &kws) < 0) ? -errno : 0;
			struct sof_net_winstream_rsp *wsrsp = (struct sof_net_winstream_rsp *)out_buf;
			wsrsp->rx_bytes_read = (st == 0) ? kws.rx_bytes_read : 0;
			wsrsp->seq_out = kws.seq_in_out;
			rc = send_reply(sock, hdr.opcode, hdr.req_id, st,
					out_buf, sizeof(*wsrsp) + wsrsp->rx_bytes_read);
			free(out_buf);
			break;
		}

		case SOF_NET_OP_TELEM_SNAPSHOT:
			rc = handle_telem_snapshot(sock, cdev_fd, hdr.req_id,
						   req_buf, hdr.payload_len,
						   &tstate);
			break;

		case SOF_NET_OP_LLEXT_LOAD: {
			if (hdr.payload_len < sizeof(struct sof_net_llext_req)) {
				rc = send_reply(sock, hdr.opcode, hdr.req_id, -EINVAL, NULL, 0);
				break;
			}
			struct sof_net_llext_req *lreq = (struct sof_net_llext_req *)req_buf;
			if (!lreq->data_size ||
			    hdr.payload_len < sizeof(*lreq) + lreq->data_size) {
				rc = send_reply(sock, hdr.opcode, hdr.req_id, -EINVAL, NULL, 0);
				break;
			}
			struct sof_net_llext_rsp rsp;
			memset(&rsp, 0, sizeof(rsp));

			if (lreq->mode == SOF_LLEXT_MODE_SHELL_SHM &&
			    lreq->issue_shell_cmd) {
				char scmd[128];
				snprintf(scmd, sizeof(scmd), "sof llext_load %s %u",
					 lreq->mod_name[0] ? lreq->mod_name : "mod",
					 lreq->lib_id ? lreq->lib_id : 1);
				exec_dsp_shell(cdev_fd, scmd, 200,
					       rsp.shell_out, sizeof(rsp.shell_out));
			}

			struct sof_cdev_llext_load kllext = {
				.mode      = lreq->mode,
				.lib_id    = lreq->lib_id,
				.data_size = lreq->data_size,
				.data_ptr  = (uintptr_t)(req_buf + sizeof(*lreq)),
			};
			int st = (ioctl(cdev_fd, SOF_IOC_LLEXT_LOAD, &kllext) < 0) ? -errno : 0;
			rsp.lib_id = kllext.lib_id;
			rsp.fw_state = kllext.fw_state;
			rc = send_reply(sock, hdr.opcode, hdr.req_id, st, &rsp, sizeof(rsp));
			break;
		}

		case SOF_NET_OP_SHELL_EXEC: {
			if (hdr.payload_len < sizeof(struct sof_net_shell_req)) {
				rc = send_reply(sock, hdr.opcode, hdr.req_id, -EINVAL, NULL, 0);
				break;
			}
			struct sof_net_shell_req *sreq = (struct sof_net_shell_req *)req_buf;
			const char *cmd_str = (const char *)(req_buf + sizeof(*sreq));
			char out_text[8192];
			memset(out_text, 0, sizeof(out_text));
			int n = exec_dsp_shell(cdev_fd, cmd_str, sreq->timeout_ms,
					       out_text, sizeof(out_text));
			if (n < 0)
				rc = send_reply(sock, hdr.opcode, hdr.req_id, n, NULL, 0);
			else
				rc = send_reply(sock, hdr.opcode, hdr.req_id, 0,
						out_text, (uint32_t)n);
			break;
		}

		case SOF_NET_OP_PROBE_CTL: {
			if (hdr.payload_len < sizeof(struct sof_net_probe_req)) {
				rc = send_reply(sock, hdr.opcode, hdr.req_id, -EINVAL, NULL, 0);
				break;
			}
			struct sof_net_probe_req *preq = (struct sof_net_probe_req *)req_buf;
			uint32_t cap = (preq->num_points && preq->num_points <= 64) ?
				       preq->num_points : 64;
			uint8_t *out_buf = calloc(1, sizeof(*preq) + cap * sizeof(struct sof_cdev_probe_point));
			if (!out_buf) {
				rc = send_reply(sock, hdr.opcode, hdr.req_id, -ENOMEM, NULL, 0);
				break;
			}
			struct sof_cdev_probe_point *pts = (struct sof_cdev_probe_point *)(out_buf + sizeof(*preq));
			if ((preq->op == SOF_PROBE_OP_POINTS_ADD ||
			     preq->op == SOF_PROBE_OP_POINTS_REMOVE) &&
			    hdr.payload_len >= sizeof(*preq) + preq->num_points * sizeof(*pts)) {
				memcpy(pts, req_buf + sizeof(*preq),
				       preq->num_points * sizeof(*pts));
			}
			struct sof_cdev_probe_ctl kpctl = {
				.op              = preq->op,
				.stream_tag      = preq->stream_tag,
				.dma_buffer_size = preq->dma_buffer_size,
				.num_points      = cap,
				.points_ptr      = (uintptr_t)pts,
			};
			int st = (ioctl(cdev_fd, SOF_IOC_PROBE_CTL, &kpctl) < 0) ? -errno : 0;
			struct sof_net_probe_req *prsp = (struct sof_net_probe_req *)out_buf;
			*prsp = *preq;
			prsp->num_points = (st == 0) ? min_u32_helper(kpctl.num_points, cap) : 0;
			uint32_t rsp_len = sizeof(*prsp) +
					   ((preq->op == SOF_PROBE_OP_INFO_ACTIVE ||
					     preq->op == SOF_PROBE_OP_INFO_AVAILABLE) ?
					    prsp->num_points * sizeof(*pts) : 0);
			rc = send_reply(sock, hdr.opcode, hdr.req_id, st, out_buf, rsp_len);
			free(out_buf);
			break;
		}

		case SOF_NET_OP_PCM_CTL:
			rc = handle_pcm_ctl(sock, hdr.req_id, req_buf, hdr.payload_len);
			break;

		case SOF_NET_OP_CARD_LIST: {
			uint8_t *out_buf = NULL;
			uint32_t out_len = 0;
			int st = dapm_build_card_list_payload(&out_buf, &out_len);
			rc = send_reply(sock, hdr.opcode, hdr.req_id, st,
					st == 0 ? out_buf : NULL,
					st == 0 ? out_len : 0);
			free(out_buf);
			break;
		}

		case SOF_NET_OP_PCM_CAPS: {
			struct sof_net_pcm_caps_req creq = {
				.card_id     = 0xFFFFFFFFU,
				.device_id   = 0xFFFFFFFFU,
				.dir_mask    = SOF_CAPS_DIR_ALL,
				.api_backend = SOF_AUDIO_API_ALSA,
			};
			if (hdr.payload_len >= sizeof(creq))
				memcpy(&creq, req_buf, sizeof(creq));

			uint8_t *out_buf = NULL;
			uint32_t out_len = 0;
			int st = dapm_build_pcm_caps_payload(&creq, &out_buf, &out_len);
			rc = send_reply(sock, hdr.opcode, hdr.req_id, st,
					st == 0 ? out_buf : NULL,
					st == 0 ? out_len : 0);
			free(out_buf);
			break;
		}

		case SOF_NET_OP_DAPM_GRAPH: {
			struct sof_net_dapm_req dreq = { .card_id = 0, .flags = 0 };
			if (hdr.payload_len >= sizeof(dreq))
				memcpy(&dreq, req_buf, sizeof(dreq));

			uint8_t *out_buf = NULL;
			uint32_t out_len = 0;
			int st = dapm_build_graph_payload(dreq.card_id, &out_buf, &out_len);
			rc = send_reply(sock, hdr.opcode, hdr.req_id, st,
					st == 0 ? out_buf : NULL,
					st == 0 ? out_len : 0);
			free(out_buf);
			break;
		}

		case SOF_NET_OP_COMPR_CTL: {
			if (hdr.payload_len < sizeof(struct sof_net_compr_req)) {
				rc = send_reply(sock, hdr.opcode, hdr.req_id, -EINVAL, NULL, 0);
				break;
			}
			const struct sof_net_compr_req *creq =
				(const struct sof_net_compr_req *)req_buf;
			const uint8_t *in_data = NULL;
			if (creq->data_size > 0) {
				if (hdr.payload_len < sizeof(*creq) + creq->data_size) {
					rc = send_reply(sock, hdr.opcode, hdr.req_id,
							-EINVAL, NULL, 0);
					break;
				}
				in_data = req_buf + sizeof(*creq);
			}
			uint8_t *out_buf = NULL;
			uint32_t out_len = 0;
			int st = compr_handle_ctl(creq, in_data, &out_buf, &out_len);
			rc = send_reply(sock, hdr.opcode, hdr.req_id, st,
					out_buf, out_len);
			free(out_buf);
			break;
		}

		case SOF_NET_OP_KCTL_LIST: {
			struct sof_net_kctl_list_req kreq = {
				.card_id     = 0,
				.api_backend = SOF_AUDIO_API_ALSA,
			};
			if (hdr.payload_len >= sizeof(kreq))
				memcpy(&kreq, req_buf, sizeof(kreq));

			uint32_t max_k = 128;
			uint32_t alloc_len = sizeof(struct sof_net_kctl_list_hdr) +
					     max_k * sizeof(struct sof_net_kctl_info);
			uint8_t *out_buf = calloc(1, alloc_len);
			if (!out_buf) {
				rc = send_reply(sock, hdr.opcode, hdr.req_id, -ENOMEM, NULL, 0);
				break;
			}
			struct sof_net_kctl_list_hdr *khdr =
				(struct sof_net_kctl_list_hdr *)out_buf;
			struct sof_net_kctl_info *karr =
				(struct sof_net_kctl_info *)(out_buf + sizeof(*khdr));
			uint32_t count = 0;
			uint32_t api = (kreq.api_backend == SOF_AUDIO_API_TINYALSA) ?
				       SOF_AUDIO_API_TINYALSA : SOF_AUDIO_API_ALSA;
			int st = (api == SOF_AUDIO_API_TINYALSA) ?
				 tinyalsa_kctl_list(kreq.card_id, karr, max_k, &count) :
				 alsa_kctl_list(kreq.card_id, karr, max_k, &count);
			khdr->card_id = kreq.card_id;
			khdr->api_used = api;
			khdr->num_kcontrols = count;
			uint32_t out_len = sizeof(*khdr) + count * sizeof(*karr);
			rc = send_reply(sock, hdr.opcode, hdr.req_id, st,
					st == 0 ? out_buf : NULL,
					st == 0 ? out_len : 0);
			free(out_buf);
			break;
		}

		case SOF_NET_OP_KCTL_GET:
		case SOF_NET_OP_KCTL_SET: {
			if (hdr.payload_len < sizeof(struct sof_net_kctl_io_req)) {
				rc = send_reply(sock, hdr.opcode, hdr.req_id, -EINVAL, NULL, 0);
				break;
			}
			const struct sof_net_kctl_io_req *ioreq =
				(const struct sof_net_kctl_io_req *)req_buf;
			if (hdr.opcode == SOF_NET_OP_KCTL_SET &&
			    (ioreq->count > (sizeof(ioreq->values) / sizeof(ioreq->values[0])) ||
			     ioreq->bytes_len > sizeof(ioreq->bytes_data))) {
				rc = send_reply(sock, hdr.opcode, hdr.req_id, -EINVAL, NULL, 0);
				break;
			}
			struct sof_net_kctl_io_rsp *iorsp = calloc(1, sizeof(*iorsp));
			if (!iorsp) {
				rc = send_reply(sock, hdr.opcode, hdr.req_id, -ENOMEM, NULL, 0);
				break;
			}
			bool is_set = (hdr.opcode == SOF_NET_OP_KCTL_SET);
			bool is_tiny = (ioreq->api_backend == SOF_AUDIO_API_TINYALSA);
			int st = 0;
			if (is_set)
				st = is_tiny ? tinyalsa_kctl_set(ioreq, iorsp) :
					       alsa_kctl_set(ioreq, iorsp);
			else
				st = is_tiny ? tinyalsa_kctl_get(ioreq, iorsp) :
					       alsa_kctl_get(ioreq, iorsp);

			rc = send_reply(sock, hdr.opcode, hdr.req_id, st,
					iorsp, sizeof(*iorsp));
			free(iorsp);
			break;
		}

		default:
			rc = send_reply(sock, hdr.opcode, hdr.req_id, -ENOSYS, NULL, 0);
			break;
		}

		free(req_buf);
		if (rc < 0)
			break;
	}

	if (cdev_fd >= 0)
		close(cdev_fd);
	close(sock);
	return NULL;
}

int main(int argc, char **argv)
{
	int opt;
	bool daemonize = false;

	while ((opt = getopt(argc, argv, "d:p:Dh")) != -1) {
		switch (opt) {
		case 'd':
			g_cdev_path = optarg;
			break;
		case 'p':
			g_port = atoi(optarg);
			break;
		case 'D':
			daemonize = true;
			break;
		default:
			fprintf(stderr,
				"Usage: %s [-d /dev/sof-dsp0] [-p 9998] [-D]\n",
				argv[0]);
			return 1;
		}
	}

	signal(SIGINT, sig_handler);
	signal(SIGTERM, sig_handler);
	signal(SIGPIPE, SIG_IGN);

	int srv_fd = socket(AF_INET, SOCK_STREAM, 0);
	if (srv_fd < 0) {
		perror("socket");
		return 1;
	}

	int one = 1;
	setsockopt(srv_fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));

	struct sockaddr_in bind_addr;
	memset(&bind_addr, 0, sizeof(bind_addr));
	bind_addr.sin_family = AF_INET;
	bind_addr.sin_addr.s_addr = htonl(INADDR_ANY);
	bind_addr.sin_port = htons((uint16_t)g_port);

	if (bind(srv_fd, (struct sockaddr *)&bind_addr, sizeof(bind_addr)) < 0) {
		perror("bind");
		close(srv_fd);
		return 1;
	}

	if (listen(srv_fd, 16) < 0) {
		perror("listen");
		close(srv_fd);
		return 1;
	}

	if (daemonize) {
		if (daemon(0, 0) < 0)
			perror("daemon");
	}

	printf("sof_remote_server listening on 0.0.0.0:%d (device %s)\n",
	       g_port, g_cdev_path);
	fflush(stdout);

	while (g_running) {
		struct sockaddr_in cli_addr;
		socklen_t cli_len = sizeof(cli_addr);
		int cli_fd = accept(srv_fd, (struct sockaddr *)&cli_addr, &cli_len);
		if (cli_fd < 0) {
			if (errno == EINTR)
				continue;
			perror("accept");
			break;
		}

		struct client_ctx *ctx = malloc(sizeof(*ctx));
		if (!ctx) {
			close(cli_fd);
			continue;
		}
		ctx->sock = cli_fd;
		ctx->addr = cli_addr;

		pthread_t tid;
		pthread_attr_t attr;
		pthread_attr_init(&attr);
		pthread_attr_setdetachstate(&attr, PTHREAD_CREATE_DETACHED);
		if (pthread_create(&tid, &attr, client_worker_fn, ctx) != 0) {
			close(cli_fd);
			free(ctx);
		}
		pthread_attr_destroy(&attr);
	}

	if (atomic_load_explicit(&g_tone_running, memory_order_acquire)) {
		atomic_store_explicit(&g_tone_running, false, memory_order_release);
		pthread_join(g_tone_thread, NULL);
	}

	close(srv_fd);
	return 0;
}
