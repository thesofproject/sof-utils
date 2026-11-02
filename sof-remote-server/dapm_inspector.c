// SPDX-License-Identifier: (GPL-2.0-only OR BSD-3-Clause)
/*
 * dapm_inspector.c — Sound Card & PCM/Compress Discovery, Binary Topology
 * Widget-to-Kcontrol Mapper, DAPM Graph & DPCM Status Parser, and Topological
 * Graph Layout / Kcontrol Anchor Resolver.
 */

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <ctype.h>
#include <unistd.h>
#include <dirent.h>
#include <fcntl.h>
#include <errno.h>
#include <pthread.h>
#include <sys/stat.h>

#include <sound/asound.h>
#include <sound/asoc.h>

#include "audio_backends.h"

/* -------------------------------------------------------------------------
 * Small File & String Helpers
 * -------------------------------------------------------------------------
 */
static int read_small_file(const char *path, char *buf, size_t cap)
{
	int fd = open(path, O_RDONLY);
	if (fd < 0) {
		buf[0] = '\0';
		return -errno;
	}
	ssize_t n = read(fd, buf, cap - 1);
	close(fd);
	if (n < 0) {
		buf[0] = '\0';
		return -errno;
	}
	buf[n] = '\0';
	return (int)n;
}

static void trim_Whitespace(char *s)
{
	size_t len = strlen(s);
	while (len > 0 && (s[len - 1] == '\n' || s[len - 1] == '\r' ||
			   s[len - 1] == ' '  || s[len - 1] == '\t')) {
		s[--len] = '\0';
	}
}

/* -------------------------------------------------------------------------
 * Binary Topology (.tplg) Parser for Widget <-> Kcontrol Bindings
 * -------------------------------------------------------------------------
 */
struct tplg_wkctl_map {
	char widget_name[64];
	char kctl_name[64];
	int32_t pipeline_id;
};

static int extract_pipeline_id_from_name(const char *wname)
{
	/* Matches "gain.52.1", "mixin.50.1", "pipeline.52", etc. */
	const char *dot = strchr(wname, '.');
	if (!dot)
		return -1;
	if (strncmp(wname, "host-copier.", 12) == 0 ||
	    strncmp(wname, "dai-copier.", 11) == 0)
		return -1;
	char *endp = NULL;
	long v = strtol(dot + 1, &endp, 10);
	if (endp && endp > dot + 1 && v >= 0 && v < 1000)
		return (int)v;
	return -1;
}

static void detect_card_tplg_file(const char *card_driver, char *tplg_out, size_t cap)
{
	tplg_out[0] = '\0';

	/* Check common SOF IPC4 topology paths on DUT */
	static const char *candidates[] = {
		"/lib/firmware/intel/sof-ipc4-tplg/sof-arl-hdmi.tplg",
		"/lib/firmware/intel/sof-ipc4-tplg/sof-hda-generic.tplg",
		"/lib/firmware/intel/sof-ipc4-tplg/sof-hda-generic-2ch.tplg",
		"/lib/firmware/intel/sof-ipc4-tplg/sof-hda-generic-4ch.tplg",
	};

	/* Also check kernel dmesg if accessible, else fall back to existing file */
	FILE *fp = popen("dmesg 2>/dev/null | grep -i 'loading topology:' | tail -n 1", "r");
	if (fp) {
		char line[256];
		if (fgets(line, sizeof(line), fp)) {
			char *p = strstr(line, "loading topology:");
			if (p) {
				p += strlen("loading topology:");
				while (*p == ' ' || *p == '\t')
					p++;
				trim_Whitespace(p);
				if (p[0]) {
					snprintf(tplg_out, cap, "%s", p);
					pclose(fp);
					return;
				}
			}
		}
		pclose(fp);
	}

	if (card_driver && strstr(card_driver, "sof")) {
		for (size_t i = 0; i < sizeof(candidates) / sizeof(candidates[0]); i++) {
			if (access(candidates[i], R_OK) == 0) {
				snprintf(tplg_out, cap, "%s",
					 candidates[i] + strlen("/lib/firmware/"));
				return;
			}
		}
	}
}

static uint32_t parse_tplg_widget_kcontrols(const char *tplg_rel_path,
					    struct tplg_wkctl_map *out_map,
					    uint32_t max_entries)
{
	if (!tplg_rel_path || !tplg_rel_path[0])
		return 0;

	char full_path[256];
	if (tplg_rel_path[0] == '/')
		snprintf(full_path, sizeof(full_path), "%s", tplg_rel_path);
	else
		snprintf(full_path, sizeof(full_path), "/lib/firmware/%s", tplg_rel_path);

	int fd = open(full_path, O_RDONLY);
	if (fd < 0)
		return 0;

	struct stat st;
	if (fstat(fd, &st) < 0 || st.st_size <= (off_t)sizeof(struct snd_soc_tplg_hdr) ||
	    st.st_size > (4 * 1024 * 1024)) {
		close(fd);
		return 0;
	}

	uint8_t *data = malloc((size_t)st.st_size);
	if (!data) {
		close(fd);
		return 0;
	}

	ssize_t nr = read(fd, data, (size_t)st.st_size);
	close(fd);
	if (nr != st.st_size) {
		free(data);
		return 0;
	}

	size_t fsize = (size_t)st.st_size;
	size_t off = 0;
	uint32_t count = 0;

	while (off + sizeof(struct snd_soc_tplg_hdr) <= fsize && count < max_entries) {
		const struct snd_soc_tplg_hdr *hdr =
			(const struct snd_soc_tplg_hdr *)(data + off);
		if (memcmp(&hdr->magic, "CoSA", 4) != 0)
			break;

		uint32_t hdr_size = hdr->size ? hdr->size : sizeof(*hdr);
		uint32_t payload_bytes = hdr->payload_size;
		size_t block_start = off + hdr_size;
		size_t block_end = block_start + payload_bytes;
		if (block_end > fsize)
			break;

		if (hdr->type == SND_SOC_TPLG_TYPE_DAPM_WIDGET) {
			size_t woff = block_start;
			for (uint32_t w = 0; w < hdr->count && woff + sizeof(struct snd_soc_tplg_dapm_widget) <= block_end; w++) {
				const struct snd_soc_tplg_dapm_widget *tw =
					(const struct snd_soc_tplg_dapm_widget *)(data + woff);
				size_t w_hdr_sz = tw->size ? tw->size : sizeof(*tw);
				size_t priv_sz = tw->priv.size;
				size_t ctl_off = woff + w_hdr_sz + priv_sz;
				if (ctl_off > block_end)
					break;

				for (uint32_t k = 0; k < tw->num_kcontrols &&
				     ctl_off + sizeof(struct snd_soc_tplg_ctl_hdr) <= block_end; k++) {
					const struct snd_soc_tplg_ctl_hdr *chdr =
						(const struct snd_soc_tplg_ctl_hdr *)(data + ctl_off);

					if (count < max_entries && tw->name[0] && chdr->name[0]) {
						snprintf(out_map[count].widget_name,
							 sizeof(out_map[count].widget_name),
							 "%.43s", tw->name);
						snprintf(out_map[count].kctl_name,
							 sizeof(out_map[count].kctl_name),
							 "%.43s", chdr->name);
						out_map[count].pipeline_id =
							extract_pipeline_id_from_name(out_map[count].widget_name);
						count++;
					}

					/* Advance ctl_off past this control + its private data */
					size_t c_step = 0;
					if (chdr->ops.info == SND_SOC_TPLG_CTL_VOLSW ||
					    chdr->ops.info == SND_SOC_TPLG_CTL_VOLSW_SX ||
					    chdr->ops.info == SND_SOC_TPLG_CTL_VOLSW_XR_SX ||
					    chdr->ops.info == SND_SOC_TPLG_CTL_RANGE ||
					    chdr->ops.info == SND_SOC_TPLG_CTL_STROBE ||
					    chdr->ops.info == SND_SOC_TPLG_DAPM_CTL_VOLSW ||
					    chdr->ops.info == SND_SOC_TPLG_DAPM_CTL_PIN) {
						if (ctl_off + sizeof(struct snd_soc_tplg_mixer_control) <= block_end) {
							const struct snd_soc_tplg_mixer_control *mc =
								(const struct snd_soc_tplg_mixer_control *)(data + ctl_off);
							c_step = (mc->size ? mc->size : sizeof(*mc)) + mc->priv.size;
						}
					} else if (chdr->ops.info == SND_SOC_TPLG_CTL_ENUM ||
						   chdr->ops.info == SND_SOC_TPLG_CTL_ENUM_VALUE ||
						   chdr->ops.info == SND_SOC_TPLG_DAPM_CTL_ENUM_DOUBLE ||
						   chdr->ops.info == SND_SOC_TPLG_DAPM_CTL_ENUM_VIRT ||
						   chdr->ops.info == SND_SOC_TPLG_DAPM_CTL_ENUM_VALUE) {
						if (ctl_off + sizeof(struct snd_soc_tplg_enum_control) <= block_end) {
							const struct snd_soc_tplg_enum_control *ec =
								(const struct snd_soc_tplg_enum_control *)(data + ctl_off);
							c_step = (ec->size ? ec->size : sizeof(*ec)) + ec->priv.size;
						}
					} else if (chdr->ops.info == SND_SOC_TPLG_CTL_BYTES) {
						if (ctl_off + sizeof(struct snd_soc_tplg_bytes_control) <= block_end) {
							const struct snd_soc_tplg_bytes_control *bc =
								(const struct snd_soc_tplg_bytes_control *)(data + ctl_off);
							c_step = (bc->size ? bc->size : sizeof(*bc)) + bc->priv.size;
						}
					}
					if (c_step == 0)
						break;
					ctl_off += c_step;
				}
				woff = ctl_off;
			}
		}

		off = block_end;
	}

	free(data);
	return count;
}

/* -------------------------------------------------------------------------
 * 1. Sound Card, PCM & Compress Enumeration (SOF_NET_OP_CARD_LIST)
 * -------------------------------------------------------------------------
 */
static void parse_proc_pcm_sub0(uint32_t card_id, uint32_t dev_id, char dir_ch,
				struct sof_net_pcm_node_info *node)
{
	char path[128], buf[1024];

	snprintf(node->state_str, sizeof(node->state_str), "CLOSED");
	snprintf(path, sizeof(path), "/proc/asound/card%u/pcm%u%c/info",
		 card_id, dev_id, dir_ch);
	if (read_small_file(path, buf, sizeof(buf)) > 0) {
		char *saveptr = NULL;
		char *line = strtok_r(buf, "\n", &saveptr);
		while (line) {
			if (strncmp(line, "id: ", 4) == 0)
				snprintf(node->id, sizeof(node->id), "%s", line + 4);
			else if (strncmp(line, "name: ", 6) == 0)
				snprintf(node->name, sizeof(node->name), "%s", line + 6);
			else if (strncmp(line, "subdevices_count: ", 18) == 0)
				node->subdevices_count = (uint32_t)atoi(line + 18);
			else if (strncmp(line, "subdevices_avail: ", 18) == 0)
				node->subdevices_avail = (uint32_t)atoi(line + 18);
			line = strtok_r(NULL, "\n", &saveptr);
		}
	}

	snprintf(path, sizeof(path), "/proc/asound/card%u/pcm%u%c/sub0/status",
		 card_id, dev_id, dir_ch);
	if (read_small_file(path, buf, sizeof(buf)) > 0 &&
	    strncmp(buf, "closed", 6) != 0) {
		char *saveptr = NULL;
		char *line = strtok_r(buf, "\n", &saveptr);
		while (line) {
			if (strncmp(line, "state: ", 7) == 0) {
				snprintf(node->state_str, sizeof(node->state_str), "%s", line + 7);
				trim_Whitespace(node->state_str);
				if (strcmp(node->state_str, "RUNNING") == 0)
					node->is_running = 1;
			} else if (strncmp(line, "owner_pid   : ", 14) == 0) {
				node->owner_pid = (uint32_t)atoi(line + 14);
			} else if (strncmp(line, "delay       : ", 14) == 0) {
				node->delay_frames = (uint32_t)atoi(line + 14);
			} else if (strncmp(line, "avail       : ", 14) == 0) {
				node->avail_frames = (uint32_t)atoi(line + 14);
			} else if (strncmp(line, "hw_ptr      : ", 14) == 0) {
				node->hw_ptr = (uint64_t)strtoull(line + 14, NULL, 10);
			} else if (strncmp(line, "appl_ptr    : ", 14) == 0) {
				node->appl_ptr = (uint64_t)strtoull(line + 14, NULL, 10);
			}
			line = strtok_r(NULL, "\n", &saveptr);
		}
	}

	snprintf(path, sizeof(path), "/proc/asound/card%u/pcm%u%c/sub0/hw_params",
		 card_id, dev_id, dir_ch);
	if (read_small_file(path, buf, sizeof(buf)) > 0 &&
	    strncmp(buf, "closed", 6) != 0) {
		char *saveptr = NULL;
		char *line = strtok_r(buf, "\n", &saveptr);
		while (line) {
			if (strncmp(line, "format: ", 8) == 0) {
				snprintf(node->active_format, sizeof(node->active_format), "%s", line + 8);
				trim_Whitespace(node->active_format);
			} else if (strncmp(line, "channels: ", 10) == 0) {
				node->active_channels = (uint32_t)atoi(line + 10);
			} else if (strncmp(line, "rate: ", 6) == 0) {
				node->active_rate = (uint32_t)atoi(line + 6);
			} else if (strncmp(line, "period_size: ", 13) == 0) {
				node->period_size = (uint32_t)atoi(line + 13);
			} else if (strncmp(line, "buffer_size: ", 13) == 0) {
				node->buffer_size = (uint32_t)atoi(line + 13);
			}
			line = strtok_r(NULL, "\n", &saveptr);
		}
	}
}

int dapm_build_card_list_payload(uint8_t **out_buf_ptr, uint32_t *out_len_ptr)
{
	*out_buf_ptr = NULL;
	*out_len_ptr = 0;

	struct sof_net_card_info cards[16];
	struct sof_net_pcm_node_info nodes[128];
	uint32_t num_cards = 0;
	uint32_t num_nodes = 0;

	memset(cards, 0, sizeof(cards));
	memset(nodes, 0, sizeof(nodes));

	/* Parse /proc/asound/cards */
	FILE *fp = fopen("/proc/asound/cards", "r");
	if (fp) {
		char line[256];
		while (fgets(line, sizeof(line), fp) && num_cards < 16) {
			unsigned int cid = 0;
			char id_str[32] = {0}, drv_str[32] = {0}, name_str[64] = {0};
			if (sscanf(line, " %u [%31[^]]]: %31s - %63[^\n]",
				   &cid, id_str, drv_str, name_str) >= 3) {
				/* Trim trailing spaces in id_str */
				trim_Whitespace(id_str);
				trim_Whitespace(name_str);
				struct sof_net_card_info *c = &cards[num_cards++];
				c->card_id = cid;
				snprintf(c->id, sizeof(c->id), "%s", id_str);
				snprintf(c->driver, sizeof(c->driver), "%s", drv_str);
				snprintf(c->name, sizeof(c->name), "%s", name_str);
				detect_card_tplg_file(c->driver, c->tplg_file, sizeof(c->tplg_file));

				/* Next line is longname */
				if (fgets(line, sizeof(line), fp)) {
					char *p = line;
					while (*p == ' ' || *p == '\t')
						p++;
					trim_Whitespace(p);
					snprintf(c->longname, sizeof(c->longname), "%s", p);
				}

				/* Count kcontrols via alsa_kctl_list */
				struct sof_net_kctl_info *tmp_k = calloc(128, sizeof(*tmp_k));
				if (tmp_k) {
					uint32_t kc = 0;
					if (alsa_kctl_list(cid, tmp_k, 128, &kc) == 0)
						c->num_kcontrols = kc;
					free(tmp_k);
				}
			}
		}
		fclose(fp);
	}

	/* Scan /proc/asound/cardX/pcmY{p,c} for each card */
	for (uint32_t i = 0; i < num_cards; i++) {
		uint32_t cid = cards[i].card_id;
		char cdir_path[64];
		snprintf(cdir_path, sizeof(cdir_path), "/proc/asound/card%u", cid);
		DIR *d = opendir(cdir_path);
		if (!d)
			continue;

		struct dirent *de;
		while ((de = readdir(d)) != NULL && num_nodes < 128) {
			unsigned int dev_id = 0;
			char dir_ch = 0;
			if (sscanf(de->d_name, "pcm%u%c", &dev_id, &dir_ch) == 2 &&
			    (dir_ch == 'p' || dir_ch == 'c')) {
				struct sof_net_pcm_node_info *n = &nodes[num_nodes++];
				n->card_id = cid;
				n->device_id = dev_id;
				n->direction = (dir_ch == 'c') ?
					       SOF_STREAM_DIR_CAPTURE : SOF_STREAM_DIR_PLAYBACK;
				n->is_compress = 0;
				parse_proc_pcm_sub0(cid, dev_id, dir_ch, n);
				cards[i].num_pcm_streams++;
				if (n->is_running)
					cards[i].active_streams++;
			}
		}
		closedir(d);

		/* Also scan /dev/snd/comprC<cid>D* for Compress Offload devices */
		DIR *sd = opendir("/dev/snd");
		if (sd) {
			while ((de = readdir(sd)) != NULL && num_nodes < 128) {
				unsigned int sc = 0, sdev = 0;
				if (sscanf(de->d_name, "comprC%uD%u", &sc, &sdev) == 2 &&
				    sc == cid) {
					struct sof_net_pcm_caps ccaps;
					uint32_t cdir = SOF_STREAM_DIR_PLAYBACK;
					bool got_dir = false;

					memset(&ccaps, 0, sizeof(ccaps));
					if (compr_query_caps(cid, sdev,
							     SOF_STREAM_DIR_PLAYBACK,
							     &ccaps) == 0) {
						cdir = ccaps.direction;
						got_dir = true;
					} else if (compr_query_caps(cid, sdev,
								    SOF_STREAM_DIR_CAPTURE,
								    &ccaps) == 0) {
						cdir = ccaps.direction;
						got_dir = true;
					}
					if (!got_dir) {
						char cinfo_path[128], cinfo_buf[512];

						snprintf(cinfo_path, sizeof(cinfo_path),
							 "/proc/asound/card%u/compr%u/info",
							 cid, sdev);
						if (read_small_file(cinfo_path, cinfo_buf,
								    sizeof(cinfo_buf)) > 0 &&
						    (strstr(cinfo_buf, "CAPTURE") ||
						     strstr(cinfo_buf, "capture"))) {
							cdir = SOF_STREAM_DIR_CAPTURE;
						}
					}

					struct sof_net_pcm_node_info *n = &nodes[num_nodes++];
					n->card_id = cid;
					n->device_id = sdev;
					n->direction = cdir;
					n->is_compress = 1;
					snprintf(n->id, sizeof(n->id), "comprC%uD%u", cid, sdev);
					snprintf(n->name, sizeof(n->name), "Compress Offload %u", sdev);
					snprintf(n->state_str, sizeof(n->state_str), "READY");
					cards[i].num_compr_streams++;
				}
			}
			closedir(sd);
		}
	}

	uint32_t total_len = sizeof(struct sof_net_card_list_hdr) +
			     num_cards * sizeof(struct sof_net_card_info) +
			     num_nodes * sizeof(struct sof_net_pcm_node_info);
	uint8_t *buf = calloc(1, total_len);
	if (!buf)
		return -ENOMEM;

	struct sof_net_card_list_hdr *hdr = (struct sof_net_card_list_hdr *)buf;
	hdr->num_cards = num_cards;
	hdr->num_pcm_nodes = num_nodes;

	uint8_t *p = buf + sizeof(*hdr);
	if (num_cards > 0) {
		memcpy(p, cards, num_cards * sizeof(struct sof_net_card_info));
		p += num_cards * sizeof(struct sof_net_card_info);
	}
	if (num_nodes > 0)
		memcpy(p, nodes, num_nodes * sizeof(struct sof_net_pcm_node_info));

	*out_buf_ptr = buf;
	*out_len_ptr = total_len;
	return 0;
}

/* -------------------------------------------------------------------------
 * 2. Per-PCM & Compress Capabilities Builder (SOF_NET_OP_PCM_CAPS)
 * -------------------------------------------------------------------------
 */
int dapm_build_pcm_caps_payload(const struct sof_net_pcm_caps_req *req,
				uint8_t **out_buf_ptr, uint32_t *out_len_ptr)
{
	*out_buf_ptr = NULL;
	*out_len_ptr = 0;

	uint8_t *clist_buf = NULL;
	uint32_t clist_len = 0;
	if (dapm_build_card_list_payload(&clist_buf, &clist_len) < 0 || !clist_buf)
		return -ENOMEM;

	const struct sof_net_card_list_hdr *chdr =
		(const struct sof_net_card_list_hdr *)clist_buf;
	const struct sof_net_pcm_node_info *nodes =
		(const struct sof_net_pcm_node_info *)
		(clist_buf + sizeof(*chdr) + chdr->num_cards * sizeof(struct sof_net_card_info));

	uint32_t max_caps = chdr->num_pcm_nodes ? chdr->num_pcm_nodes : 1;
	uint32_t alloc_len = sizeof(struct sof_net_pcm_caps_hdr) +
			     max_caps * sizeof(struct sof_net_pcm_caps);
	uint8_t *out_buf = calloc(1, alloc_len);
	if (!out_buf) {
		free(clist_buf);
		return -ENOMEM;
	}

	struct sof_net_pcm_caps_hdr *ohdr = (struct sof_net_pcm_caps_hdr *)out_buf;
	struct sof_net_pcm_caps *caps_arr =
		(struct sof_net_pcm_caps *)(out_buf + sizeof(*ohdr));

	uint32_t api = req->api_backend ? req->api_backend : SOF_AUDIO_API_ALSA;
	uint32_t dmask = req->dir_mask ? req->dir_mask : SOF_CAPS_DIR_ALL;
	uint32_t count = 0;

	for (uint32_t i = 0; i < chdr->num_pcm_nodes; i++) {
		const struct sof_net_pcm_node_info *n = &nodes[i];
		if (req->card_id != 0xFFFFFFFFU && n->card_id != req->card_id)
			continue;
		if (req->device_id != 0xFFFFFFFFU && n->device_id != req->device_id)
			continue;
		if (n->is_compress && !(dmask & SOF_CAPS_DIR_COMPRESS))
			continue;
		if (!n->is_compress && n->direction == SOF_STREAM_DIR_PLAYBACK &&
		    !(dmask & SOF_CAPS_DIR_PLAYBACK))
			continue;
		if (!n->is_compress && n->direction == SOF_STREAM_DIR_CAPTURE &&
		    !(dmask & SOF_CAPS_DIR_CAPTURE))
			continue;

		struct sof_net_pcm_caps *c = &caps_arr[count++];
		snprintf(c->id, sizeof(c->id), "%s", n->id);
		snprintf(c->name, sizeof(c->name), "%s", n->name);

		if (n->is_compress) {
			compr_query_caps(n->card_id, n->device_id, n->direction, c);
		} else if (api == SOF_AUDIO_API_TINYALSA) {
			tinyalsa_query_pcm_caps(n->card_id, n->device_id, n->direction, c);
		} else {
			alsa_query_pcm_caps(n->card_id, n->device_id, n->direction, c);
		}

		/* Cache hardware capabilities when device is idle, and restore from cache or active runtime when busy */
		static struct sof_net_pcm_caps s_caps_cache[128];
		static bool s_caps_valid[128];
		static uint32_t s_caps_next_slot;
		static pthread_mutex_t s_caps_lock = PTHREAD_MUTEX_INITIALIZER;

		pthread_mutex_lock(&s_caps_lock);
		int match_slot = -1;
		for (size_t si = 0; si < sizeof(s_caps_cache) / sizeof(s_caps_cache[0]); si++) {
			if (s_caps_valid[si] &&
			    s_caps_cache[si].card_id == n->card_id &&
			    s_caps_cache[si].device_id == n->device_id &&
			    s_caps_cache[si].direction == n->direction &&
			    s_caps_cache[si].is_compress == n->is_compress &&
			    s_caps_cache[si].api_used == c->api_used) {
				match_slot = (int)si;
				break;
			}
		}

		if (c->status == 0 && (c->rate_max > 0 || (n->is_compress && c->num_codecs > 0))) {
			uint32_t dst_slot = (match_slot >= 0) ? (uint32_t)match_slot :
					    (s_caps_next_slot++ % (sizeof(s_caps_cache) / sizeof(s_caps_cache[0])));
			s_caps_cache[dst_slot] = *c;
			s_caps_valid[dst_slot] = true;
		} else if (c->status < 0 && match_slot >= 0) {
			*c = s_caps_cache[match_slot];
			c->status = 0;
		} else if (c->status < 0 && n->active_rate > 0) {
			c->rate_min = 32000;
			c->rate_max = n->active_rate > 48000 ? n->active_rate : 48000;
			c->channels_min = n->active_channels;
			c->channels_max = n->active_channels;
			c->period_size_min = n->period_size / 4 ? n->period_size / 4 : 96;
			c->period_size_max = n->buffer_size ? n->buffer_size / 2 : n->period_size;
			c->buffer_size_min = n->period_size * 2;
			c->buffer_size_max = n->buffer_size;
			snprintf(c->formats_str, sizeof(c->formats_str),
				 "%s S32_LE", n->active_format);
			snprintf(c->rates_str, sizeof(c->rates_str),
				 "32000 44100 %u", n->active_rate);
		}
		pthread_mutex_unlock(&s_caps_lock);
	}

	ohdr->num_entries = count;
	ohdr->api_used = api;
	free(clist_buf);

	*out_buf_ptr = out_buf;
	*out_len_ptr = sizeof(*ohdr) + count * sizeof(struct sof_net_pcm_caps);
	return 0;
}

/* -------------------------------------------------------------------------
 * 3. DAPM Graph & DPCM Status Parser + Topological Layout (SOF_NET_OP_DAPM_GRAPH)
 * -------------------------------------------------------------------------
 */
static uint32_t classify_widget_stage_col(const char *wtype, const char *wname)
{
	/*
	 * Assign a clean left-to-right topological column (0..6):
	 *   Col 0: Frontend PCM Stream / Mic Input ("HDMI* Playback", "SoC DMIC")
	 *   Col 1: Host Copier / Input ("host-copier.*", "DMic", "scheduler")
	 *   Col 2: Pre-mix Processing ("pga", "gain.*", "effect", "src", "asrc", "buffer")
	 *   Col 3: Pipeline Mixer In ("mixin.*", "DMIC AIF")
	 *   Col 4: Pipeline Mixer Out ("mixout.*", "mixer", "mux")
	 *   Col 5: DAI Copier / Capture DAI ("dai-copier.*", "Capture")
	 *   Col 6: Codec / Output Pin / Sink ("hifi*", "iDisp*_out", "iDisp* Tx", "output", "out_drv")
	 */
	if (strstr(wname, "Playback") && strncmp(wname, "host-copier.", 12) != 0 &&
	    strncmp(wname, "dai-copier.", 11) != 0)
		return 0;
	if (strcmp(wname, "SoC DMIC") == 0)
		return 0;
	if (strncmp(wname, "host-copier.", 12) == 0 || strcmp(wname, "DMic") == 0)
		return 1;
	if (strncmp(wname, "gain.", 5) == 0 || strcmp(wtype, "pga") == 0)
		return 2;
	if (strncmp(wname, "mixin.", 6) == 0 || strcmp(wname, "DMIC AIF") == 0)
		return 3;
	if (strncmp(wname, "mixout.", 7) == 0)
		return 4;
	if (strncmp(wname, "dai-copier.", 11) == 0 || strcmp(wname, "Capture") == 0)
		return 5;
	if (strncmp(wname, "hifi", 4) == 0 || strcmp(wtype, "output") == 0 ||
	    strcmp(wtype, "out_drv") == 0 || strcmp(wtype, "spk") == 0 ||
	    strcmp(wtype, "hp") == 0)
		return 6;
	if (strcmp(wtype, "scheduler") == 0)
		return 1;
	if (strcmp(wtype, "aif_in") == 0 || strcmp(wtype, "input") == 0 ||
	    strcmp(wtype, "mic") == 0)
		return 0;
	if (strcmp(wtype, "mixer") == 0 || strcmp(wtype, "mux") == 0)
		return 3;
	if (strcmp(wtype, "dai_in") == 0 || strcmp(wtype, "dai_out") == 0 ||
	    strcmp(wtype, "aif_out") == 0)
		return 5;
	return 2;
}

static uint16_t classify_widget_lane_row(const char *wname, const char *comp, int32_t pipe_id)
{
	/*
	 * Group into logical pipeline lanes:
	 *   Row 0: HDMI1 Regular (pcm0p, pipeline 52 & 51, iDisp1, hifi1)
	 *   Row 1: HDMI1 Game    (pcm3p, pipeline 50)
	 *   Row 2: HDMI2         (pcm4p, pipeline 60 & 61, iDisp2, hifi2)
	 *   Row 3: HDMI3         (pcm5p, pipeline 70 & 71, iDisp3, hifi3)
	 *   Row 4: DMIC Capture  (dmic-codec, SoC DMIC)
	 *   Row 5: Aux / Other   (hifi4, iDisp* Tx/out)
	 */
	if (pipe_id == 52 || pipe_id == 51 ||
	    strcmp(wname, "HDMI1 Regular Playback") == 0 ||
	    strcmp(wname, "host-copier.0.playback") == 0 ||
	    strcmp(wname, "dai-copier.HDA.iDisp1.playback") == 0 ||
	    strcmp(wname, "hifi1") == 0 || strcmp(wname, "iDisp1_out") == 0)
		return 0;
	if (pipe_id == 50 ||
	    strcmp(wname, "HDMI1 Playback") == 0 ||
	    strcmp(wname, "host-copier.3.playback") == 0 ||
	    strcmp(wname, "iDisp1 Tx") == 0)
		return 1;
	if (pipe_id == 60 || pipe_id == 61 ||
	    strcmp(wname, "HDMI2 Playback") == 0 ||
	    strcmp(wname, "host-copier.4.playback") == 0 ||
	    strcmp(wname, "dai-copier.HDA.iDisp2.playback") == 0 ||
	    strcmp(wname, "hifi2") == 0 || strcmp(wname, "iDisp2_out") == 0)
		return 2;
	if (pipe_id == 70 || pipe_id == 71 ||
	    strcmp(wname, "HDMI3 Playback") == 0 ||
	    strcmp(wname, "host-copier.5.playback") == 0 ||
	    strcmp(wname, "dai-copier.HDA.iDisp3.playback") == 0 ||
	    strcmp(wname, "hifi3") == 0 || strcmp(wname, "iDisp3_out") == 0)
		return 3;
	if (strcmp(comp, "dmic-codec") == 0 || strcmp(wname, "SoC DMIC") == 0)
		return 4;
	return 5;
}

static uint32_t parse_bias_str(const char *s)
{
	if (strncasecmp(s, "On", 2) == 0)
		return 3;
	if (strncasecmp(s, "Prepare", 7) == 0)
		return 2;
	if (strncasecmp(s, "Standby", 7) == 0)
		return 1;
	return 0;
}

struct raw_edge_spec {
	char src_name[64];
	char src_comp[48];
	char dst_name[64];
	char dst_comp[48];
	char control_name[48];
};

static void add_unique_raw_edge(struct raw_edge_spec *edges, uint32_t *num_edges,
				uint32_t max_edges, const char *src, const char *src_c,
				const char *dst, const char *dst_c, const char *ctl)
{
	const char *sc = (src_c && src_c[0]) ? src_c : "";
	const char *dc = (dst_c && dst_c[0]) ? dst_c : "";
	const char *cn = (ctl && ctl[0]) ? ctl : "static";

	for (uint32_t i = 0; i < *num_edges; i++) {
		if (strcmp(edges[i].src_name, src) == 0 &&
		    strcmp(edges[i].src_comp, sc) == 0 &&
		    strcmp(edges[i].dst_name, dst) == 0 &&
		    strcmp(edges[i].dst_comp, dc) == 0 &&
		    strcmp(edges[i].control_name, cn) == 0)
			return;
	}
	if (*num_edges >= max_edges)
		return;
	struct raw_edge_spec *e = &edges[(*num_edges)++];
	snprintf(e->src_name, sizeof(e->src_name), "%s", src);
	snprintf(e->src_comp, sizeof(e->src_comp), "%s", sc);
	snprintf(e->dst_name, sizeof(e->dst_name), "%s", dst);
	snprintf(e->dst_comp, sizeof(e->dst_comp), "%s", dc);
	snprintf(e->control_name, sizeof(e->control_name), "%s", cn);
}

static void parse_dapm_dir(const char *dapm_dir_path, const char *comp_name,
			   struct sof_net_dapm_comp_status *comp_st,
			   struct sof_net_dapm_node *nodes, uint32_t *num_nodes,
			   uint32_t max_nodes,
			   struct raw_edge_spec *raw_edges, uint32_t *num_raw_edges,
			   uint32_t max_raw_edges)
{
	snprintf(comp_st->name, sizeof(comp_st->name), "%s", comp_name);
	snprintf(comp_st->bias_str, sizeof(comp_st->bias_str), "Off");

	char fpath[512], fbuf[4096];
	snprintf(fpath, sizeof(fpath), "%s/bias_level", dapm_dir_path);
	if (read_small_file(fpath, fbuf, sizeof(fbuf)) > 0) {
		trim_Whitespace(fbuf);
		snprintf(comp_st->bias_str, sizeof(comp_st->bias_str), "%s", fbuf);
		comp_st->bias_level = parse_bias_str(fbuf);
	}

	DIR *d = opendir(dapm_dir_path);
	if (!d)
		return;

	struct dirent *de;
	while ((de = readdir(d)) != NULL) {
		if (de->d_name[0] == '.' || strcmp(de->d_name, "bias_level") == 0)
			continue;
		if (*num_nodes >= max_nodes)
			break;

		snprintf(fpath, sizeof(fpath), "%s/%s", dapm_dir_path, de->d_name);
		if (read_small_file(fpath, fbuf, sizeof(fbuf)) <= 0)
			continue;

		struct sof_net_dapm_node *nd = &nodes[*num_nodes];
		memset(nd, 0, sizeof(*nd));
		nd->node_id = *num_nodes;
		nd->reg = -1;
		snprintf(nd->name, sizeof(nd->name), "%s", de->d_name);
		snprintf(nd->component, sizeof(nd->component), "%s", comp_name);
		snprintf(nd->widget_type, sizeof(nd->widget_type), "unknown");
		nd->pipeline_id = extract_pipeline_id_from_name(nd->name);

		char *saveptr = NULL;
		char *line = strtok_r(fbuf, "\n", &saveptr);
		int line_idx = 0;

		while (line) {
			if (line_idx == 0) {
				/* Format: "<wname>: <On|Off>[ (forced)]  in <in> out <out>" */
				size_t nlen = strlen(nd->name);
				const char *p = line;
				if (strncmp(line, nd->name, nlen) == 0 && line[nlen] == ':')
					p = line + nlen + 1;
				while (*p == ' ')
					p++;
				if (strncmp(p, "On", 2) == 0) {
					nd->power = strstr(p, "(forced)") ? 2 : 1;
				} else {
					nd->power = 0;
				}
				const char *in_p = strstr(p, " in ");
				if (in_p) {
					unsigned int iv = 0, ov = 0;
					if (sscanf(in_p, " in %u out %u", &iv, &ov) == 2) {
						nd->in_ep = iv;
						nd->out_ep = ov;
					}
				}
				const char *reg_p = strstr(p, " - R");
				if (reg_p) {
					int rv = -1;
					unsigned int rhex = 0, mhex = 0;
					if (sscanf(reg_p, " - R%d(0x%x) mask 0x%x",
						   &rv, &rhex, &mhex) >= 2) {
						nd->reg = rv;
						nd->mask = mhex;
					}
				}
			} else if (strncmp(line, " stream ", 8) == 0) {
				const char *sp = line + 8;
				const char *act = strstr(sp, " active");
				const char *inact = strstr(sp, " inactive");
				if (inact) {
					nd->stream_active = 0;
					size_t slen = (size_t)(inact - sp);
					if (slen >= sizeof(nd->stream_name))
						slen = sizeof(nd->stream_name) - 1;
					memcpy(nd->stream_name, sp, slen);
					nd->stream_name[slen] = '\0';
					trim_Whitespace(nd->stream_name);
				} else if (act) {
					nd->stream_active = 1;
					size_t slen = (size_t)(act - sp);
					if (slen >= sizeof(nd->stream_name))
						slen = sizeof(nd->stream_name) - 1;
					memcpy(nd->stream_name, sp, slen);
					nd->stream_name[slen] = '\0';
					trim_Whitespace(nd->stream_name);
				}
			} else if (strncmp(line, " widget-type ", 13) == 0) {
				snprintf(nd->widget_type, sizeof(nd->widget_type),
					 "%s", line + 13);
				trim_Whitespace(nd->widget_type);
			} else if (strncmp(line, " in  \"", 6) == 0 ||
				   strncmp(line, " out  \"", 7) == 0) {
				bool is_in = (strncmp(line, " in  \"", 6) == 0);
				char ctl_nm[48] = {0}, peer_nm[64] = {0}, peer_c[48] = {0};
				const char *q = strchr(line, '"');
				if (q && sscanf(q, "\"%47[^\"]\" \"%63[^\"]\" \"%47[^\"]\"",
						ctl_nm, peer_nm, peer_c) >= 2) {
					const char *resolved_peer_c =
						peer_c[0] ? peer_c : comp_name;
					if (is_in) {
						add_unique_raw_edge(raw_edges, num_raw_edges,
								    max_raw_edges,
								    peer_nm, resolved_peer_c,
								    nd->name, comp_name,
								    ctl_nm);
					} else {
						add_unique_raw_edge(raw_edges, num_raw_edges,
								    max_raw_edges,
								    nd->name, comp_name,
								    peer_nm, resolved_peer_c,
								    ctl_nm);
					}
				}
			}

			line_idx++;
			line = strtok_r(NULL, "\n", &saveptr);
		}

		nd->layout_col = (uint16_t)classify_widget_stage_col(nd->widget_type, nd->name);
		comp_st->widget_count++;
		if (nd->power)
			comp_st->powered_count++;
		(*num_nodes)++;
	}

	closedir(d);
}

static void parse_dpcm_state_file(const char *state_path, const char *fe_dir_name,
				  struct sof_net_dpcm_link_state *links,
				  uint32_t *num_links, uint32_t max_links)
{
	char fbuf[2048];
	if (*num_links >= max_links || read_small_file(state_path, fbuf, sizeof(fbuf)) <= 0)
		return;

	struct sof_net_dpcm_link_state *cur = NULL;
	bool in_backends = false;
	int cur_be = -1;

	char *saveptr = NULL;
	char *line = strtok_r(fbuf, "\n", &saveptr);
	while (line) {
		if (line[0] == '[' && *num_links < max_links) {
			cur = &links[(*num_links)++];
			memset(cur, 0, sizeof(*cur));
			snprintf(cur->fe_name, sizeof(cur->fe_name), "%s", fe_dir_name);
			cur->direction = strstr(line, "Capture") ?
					 SOF_STREAM_DIR_CAPTURE : SOF_STREAM_DIR_PLAYBACK;
			snprintf(cur->state_str, sizeof(cur->state_str), "close");
			in_backends = false;
			cur_be = -1;
		} else if (cur) {
			if (strncmp(line, "State: ", 7) == 0) {
				snprintf(cur->state_str, sizeof(cur->state_str), "%s", line + 7);
				trim_Whitespace(cur->state_str);
			} else if (strncmp(line, "Hardware Params: ", 17) == 0) {
				char fmt[16] = {0};
				unsigned int ch = 0, rt = 0;
				if (sscanf(line + 17, "Format = %15[^,], Channels = %u, Rate = %u",
					   fmt, &ch, &rt) == 3) {
					snprintf(cur->fe_format, sizeof(cur->fe_format), "%s", fmt);
					cur->fe_channels = ch;
					cur->fe_rate = rt;
				}
			} else if (strncmp(line, "Backends:", 9) == 0) {
				in_backends = true;
			} else if (in_backends && strncmp(line, "- ", 2) == 0) {
				if (cur->num_backends < 4) {
					cur_be = (int)cur->num_backends++;
					snprintf(cur->be_names[cur_be],
						 sizeof(cur->be_names[cur_be]), "%s", line + 2);
					trim_Whitespace(cur->be_names[cur_be]);
				}
			} else if (in_backends && cur_be >= 0) {
				const char *p = line;
				while (*p == ' ' || *p == '\t')
					p++;
				if (strncmp(p, "State: ", 7) == 0) {
					snprintf(cur->be_states[cur_be],
						 sizeof(cur->be_states[cur_be]), "%s", p + 7);
					trim_Whitespace(cur->be_states[cur_be]);
				} else if (strncmp(p, "Hardware Params: ", 17) == 0) {
					char fmt[16] = {0};
					unsigned int ch = 0, rt = 0;
					if (sscanf(p + 17, "Format = %15[^,], Channels = %u, Rate = %u",
						   fmt, &ch, &rt) == 3) {
						snprintf(cur->be_formats[cur_be],
							 sizeof(cur->be_formats[cur_be]), "%s", fmt);
						cur->be_channels[cur_be] = ch;
						cur->be_rates[cur_be] = rt;
					}
				}
			}
		}
		line = strtok_r(NULL, "\n", &saveptr);
	}
}

static int find_node_index_by_name(const struct sof_net_dapm_node *nodes,
				   uint32_t num_nodes, const char *name,
				   const char *comp)
{
	if (!name || !name[0])
		return -1;
	for (uint32_t i = 0; i < num_nodes; i++) {
		if (strcmp(nodes[i].name, name) == 0 &&
		    (!comp || !comp[0] || strcmp(nodes[i].component, comp) == 0))
			return (int)i;
	}
	return -1;
}

static void resolve_asoc_card_dir(uint32_t card_id, char *card_dir_name, size_t cap)
{
	char id_str[32] = {0}, drv_str[32] = {0}, name_str[64] = {0};
	FILE *fp = fopen("/proc/asound/cards", "r");

	snprintf(card_dir_name, cap, "sof-hda-dsp");
	if (fp) {
		char line[256];
		while (fgets(line, sizeof(line), fp)) {
			unsigned int cid = 0;
			char tid[32] = {0}, tdrv[32] = {0}, tname[64] = {0};
			if (sscanf(line, " %u [%31[^]]]: %31s - %63[^\n]",
				   &cid, tid, tdrv, tname) >= 3 && cid == card_id) {
				trim_Whitespace(tid);
				trim_Whitespace(tdrv);
				trim_Whitespace(tname);
				snprintf(id_str, sizeof(id_str), "%s", tid);
				snprintf(drv_str, sizeof(drv_str), "%s", tdrv);
				snprintf(name_str, sizeof(name_str), "%s", tname);
				break;
			}
		}
		fclose(fp);
	}

	const char *cands[3] = { name_str, drv_str, id_str };
	for (int i = 0; i < 3; i++) {
		if (!cands[i][0])
			continue;
		char p[192];
		snprintf(p, sizeof(p), "/sys/kernel/debug/asoc/%s", cands[i]);
		if (access(p, R_OK) == 0) {
			snprintf(card_dir_name, cap, "%s", cands[i]);
			return;
		}
	}

	DIR *asoc_d = opendir("/sys/kernel/debug/asoc");
	if (asoc_d) {
		struct dirent *de;
		uint32_t idx = 0;
		char first_dir[64] = {0};

		while ((de = readdir(asoc_d)) != NULL) {
			if (de->d_name[0] == '.' ||
			    strcmp(de->d_name, "components") == 0 ||
			    strcmp(de->d_name, "dais") == 0 ||
			    strcmp(de->d_name, "dapm_pop_time") == 0)
				continue;
			if (!first_dir[0])
				snprintf(first_dir, sizeof(first_dir), "%s", de->d_name);
			if ((name_str[0] && strstr(de->d_name, name_str)) ||
			    (drv_str[0] && strstr(de->d_name, drv_str)) ||
			    (id_str[0] && strstr(de->d_name, id_str)) ||
			    idx == card_id) {
				snprintf(card_dir_name, cap, "%s", de->d_name);
				closedir(asoc_d);
				return;
			}
			idx++;
		}
		closedir(asoc_d);
		if (first_dir[0])
			snprintf(card_dir_name, cap, "%s", first_dir);
	}
}

int dapm_build_graph_payload(uint32_t card_id, uint8_t **out_buf_ptr,
			     uint32_t *out_len_ptr)
{
	*out_buf_ptr = NULL;
	*out_len_ptr = 0;

	/* Discover card driver name in /sys/kernel/debug/asoc/ for card_id */
	char card_dir_name[64];
	resolve_asoc_card_dir(card_id, card_dir_name, sizeof(card_dir_name));

	char card_base[128];
	snprintf(card_base, sizeof(card_base), "/sys/kernel/debug/asoc/%s", card_dir_name);

	struct sof_net_dapm_comp_status comps[16];
	struct sof_net_dapm_node *nodes = calloc(256, sizeof(*nodes));
	struct raw_edge_spec *raw_edges = calloc(512, sizeof(*raw_edges));
	struct sof_net_dpcm_link_state dpcm_links[32];

	if (!nodes || !raw_edges) {
		free(nodes);
		free(raw_edges);
		return -ENOMEM;
	}

	memset(comps, 0, sizeof(comps));
	memset(dpcm_links, 0, sizeof(dpcm_links));
	uint32_t num_comps = 0, num_nodes = 0, num_raw_edges = 0, num_dpcm = 0;

	/* 1. Card-level dapm directory */
	char sub_path[512];
	snprintf(sub_path, sizeof(sub_path), "%s/dapm", card_base);
	if (access(sub_path, R_OK) == 0) {
		parse_dapm_dir(sub_path, "card", &comps[num_comps++],
			       nodes, &num_nodes, 256,
			       raw_edges, &num_raw_edges, 512);
	}

	/* 2. Subdirectories under /sys/kernel/debug/asoc/<card>/ */
	DIR *cd = opendir(card_base);
	if (cd) {
		struct dirent *de;
		while ((de = readdir(cd)) != NULL) {
			if (de->d_name[0] == '.' || strcmp(de->d_name, "dapm") == 0)
				continue;

			char dapm_sub[512], state_sub[512];
			snprintf(dapm_sub, sizeof(dapm_sub), "%s/%s/dapm", card_base, de->d_name);
			snprintf(state_sub, sizeof(state_sub), "%s/%s/state", card_base, de->d_name);

			if (access(dapm_sub, R_OK) == 0 && num_comps < 16) {
				parse_dapm_dir(dapm_sub, de->d_name, &comps[num_comps++],
					       nodes, &num_nodes, 256,
					       raw_edges, &num_raw_edges, 512);
			}
			if (access(state_sub, R_OK) == 0 && num_dpcm < 32) {
				parse_dpcm_state_file(state_sub, de->d_name,
						      dpcm_links, &num_dpcm, 32);
			}
		}
		closedir(cd);
	}

	/* 3. Resolve topology widget <-> kcontrol map & ALSA kcontrols */
	char tplg_file[96] = {0};
	detect_card_tplg_file(card_dir_name, tplg_file, sizeof(tplg_file));

	struct sof_net_kctl_info *klist = calloc(128, sizeof(*klist));
	uint32_t num_klist = 0;
	if (klist)
		alsa_kctl_list(card_id, klist, 128, &num_klist);

	for (uint32_t k = 0; k < num_klist; k++) {
		if (!klist[k].target_widget[0])
			continue;
		int nidx = find_node_index_by_name(nodes, num_nodes, klist[k].target_widget,
						   klist[k].target_component);
		if (nidx < 0)
			continue;
		struct sof_net_dapm_node *nd = &nodes[nidx];
		if (nd->num_kcontrols < 4) {
			uint32_t slot = nd->num_kcontrols++;
			nd->kcontrol_numids[slot] = klist[k].numid;
			if (slot < 2)
				snprintf(nd->kcontrol_names[slot],
					 sizeof(nd->kcontrol_names[slot]),
					 "%s", klist[k].name);
		}
	}

	/* 4. Assign logical lane rows & propagate DPCM active pipeline power states */
	bool any_dpcm_started = false;
	for (uint32_t d = 0; d < num_dpcm; d++) {
		if (strcmp(dpcm_links[d].state_str, "start") == 0 ||
		    strcmp(dpcm_links[d].state_str, "prepare") == 0) {
			any_dpcm_started = true;
			break;
		}
	}

	for (uint32_t i = 0; i < num_nodes; i++) {
		nodes[i].layout_row = classify_widget_lane_row(nodes[i].name,
							       nodes[i].component,
							       nodes[i].pipeline_id);
		if (any_dpcm_started && !nodes[i].power) {
			if (nodes[i].stream_active ||
			    (nodes[i].in_ep > 0 && strcmp(nodes[i].component, "0000:80:1f.3") == 0)) {
				nodes[i].power = 1;
			}
		}
	}

	/* Also mark scheduler widget (pipeline.N) powered if any widget in pipeline N is powered */
	for (uint32_t i = 0; i < num_nodes; i++) {
		if (strcmp(nodes[i].widget_type, "scheduler") == 0 && nodes[i].pipeline_id >= 0) {
			for (uint32_t j = 0; j < num_nodes; j++) {
				if (i != j && nodes[j].pipeline_id == nodes[i].pipeline_id &&
				    nodes[j].power > 0) {
					nodes[i].power = 1;
					break;
				}
			}
		}
	}

	/* Recompute per-component powered widget counts */
	for (uint32_t c = 0; c < num_comps; c++) {
		comps[c].powered_count = 0;
		for (uint32_t i = 0; i < num_nodes; i++) {
			if (strcmp(nodes[i].component, comps[c].name) == 0 && nodes[i].power > 0)
				comps[c].powered_count++;
		}
		if (comps[c].powered_count > 0 && comps[c].bias_level == 0) {
			snprintf(comps[c].bias_str, sizeof(comps[c].bias_str), "On (DPCM)");
			comps[c].bias_level = 3;
		}
	}

	/* 5. Build resolved edges */
	struct sof_net_dapm_edge *edges = calloc(num_raw_edges ? num_raw_edges : 1,
						 sizeof(*edges));
	if (!edges) {
		free(klist);
		free(raw_edges);
		free(nodes);
		return -ENOMEM;
	}
	uint32_t num_active_edges = 0;
	for (uint32_t i = 0; i < num_raw_edges; i++) {
		struct sof_net_dapm_edge *e = &edges[i];
		e->edge_id = i;
		e->src_node_id = find_node_index_by_name(nodes, num_nodes,
							 raw_edges[i].src_name,
							 raw_edges[i].src_comp);
		e->dst_node_id = find_node_index_by_name(nodes, num_nodes,
							 raw_edges[i].dst_name,
							 raw_edges[i].dst_comp);
		snprintf(e->src_name, sizeof(e->src_name), "%s", raw_edges[i].src_name);
		snprintf(e->dst_name, sizeof(e->dst_name), "%s", raw_edges[i].dst_name);
		snprintf(e->src_comp, sizeof(e->src_comp), "%s", raw_edges[i].src_comp);
		snprintf(e->dst_comp, sizeof(e->dst_comp), "%s", raw_edges[i].dst_comp);
		snprintf(e->control_name, sizeof(e->control_name), "%s", raw_edges[i].control_name);

		if (strcmp(e->control_name, "static") != 0 && klist) {
			for (uint32_t k = 0; k < num_klist; k++) {
				if (strcmp(klist[k].name, e->control_name) == 0) {
					e->kcontrol_numid = klist[k].numid;
					break;
				}
			}
		}

		if (e->src_node_id >= 0 && e->dst_node_id >= 0 &&
		    nodes[e->src_node_id].power > 0 &&
		    nodes[e->dst_node_id].power > 0) {
			e->active = 1;
			num_active_edges++;
		}
	}

	uint32_t num_powered = 0;
	for (uint32_t i = 0; i < num_nodes; i++) {
		if (nodes[i].power > 0)
			num_powered++;
	}

	uint32_t total_len = sizeof(struct sof_net_dapm_graph_hdr) +
			     num_comps * sizeof(struct sof_net_dapm_comp_status) +
			     num_nodes * sizeof(struct sof_net_dapm_node) +
			     num_raw_edges * sizeof(struct sof_net_dapm_edge) +
			     num_dpcm * sizeof(struct sof_net_dpcm_link_state);
	uint8_t *out_buf = calloc(1, total_len);
	if (!out_buf) {
		free(klist);
		free(edges);
		free(raw_edges);
		free(nodes);
		return -ENOMEM;
	}

	struct sof_net_dapm_graph_hdr *ghdr = (struct sof_net_dapm_graph_hdr *)out_buf;
	ghdr->card_id = card_id;
	snprintf(ghdr->card_name, sizeof(ghdr->card_name), "%s", card_dir_name);
	snprintf(ghdr->tplg_file, sizeof(ghdr->tplg_file), "%s", tplg_file);
	ghdr->num_components = num_comps;
	ghdr->num_nodes = num_nodes;
	ghdr->num_powered_nodes = num_powered;
	ghdr->num_edges = num_raw_edges;
	ghdr->num_active_edges = num_active_edges;
	ghdr->num_dpcm_links = num_dpcm;

	uint8_t *p = out_buf + sizeof(*ghdr);
	if (num_comps > 0) {
		memcpy(p, comps, num_comps * sizeof(struct sof_net_dapm_comp_status));
		p += num_comps * sizeof(struct sof_net_dapm_comp_status);
	}
	if (num_nodes > 0) {
		memcpy(p, nodes, num_nodes * sizeof(struct sof_net_dapm_node));
		p += num_nodes * sizeof(struct sof_net_dapm_node);
	}
	if (num_raw_edges > 0) {
		memcpy(p, edges, num_raw_edges * sizeof(struct sof_net_dapm_edge));
		p += num_raw_edges * sizeof(struct sof_net_dapm_edge);
	}
	if (num_dpcm > 0)
		memcpy(p, dpcm_links, num_dpcm * sizeof(struct sof_net_dpcm_link_state));

	free(klist);
	free(edges);
	free(raw_edges);
	free(nodes);

	*out_buf_ptr = out_buf;
	*out_len_ptr = total_len;
	return 0;
}

/* -------------------------------------------------------------------------
 * 4. Kcontrol Graph Anchor Resolver (Where each Kcontrol renders in Graph)
 * -------------------------------------------------------------------------
 */
void dapm_resolve_kctl_anchors(uint32_t card_id,
			       struct sof_net_kctl_info *kctls,
			       uint32_t num_kctls)
{
	if (!kctls || num_kctls == 0)
		return;

	char card_dir_name[64];
	char tplg_file[96] = {0};
	resolve_asoc_card_dir(card_id, card_dir_name, sizeof(card_dir_name));
	detect_card_tplg_file(card_dir_name, tplg_file, sizeof(tplg_file));

	struct tplg_wkctl_map wkmap[128];
	memset(wkmap, 0, sizeof(wkmap));
	uint32_t num_wkmap = parse_tplg_widget_kcontrols(tplg_file, wkmap, 128);

	for (uint32_t i = 0; i < num_kctls; i++) {
		struct sof_net_kctl_info *k = &kctls[i];
		k->anchor_type = SOF_KCTL_ANCHOR_CARD;
		k->target_node_id = -1;
		k->target_edge_id = -1;
		k->pipeline_id = -1;

		/* 1. Direct Topology Widget Match (e.g. "Regular Playback Volume" -> "gain.52.1") */
		bool matched = false;
		for (uint32_t m = 0; m < num_wkmap; m++) {
			if (strcmp(k->name, wkmap[m].kctl_name) == 0) {
				k->anchor_type = SOF_KCTL_ANCHOR_WIDGET;
				snprintf(k->target_widget, sizeof(k->target_widget),
					 "%s", wkmap[m].widget_name);
				snprintf(k->target_component, sizeof(k->target_component),
					 "0000:80:1f.3");
				k->pipeline_id = wkmap[m].pipeline_id;
				k->layout_col = (uint16_t)classify_widget_stage_col("pga", k->target_widget);
				k->layout_row = (k->pipeline_id == 52) ? 0 :
						(k->pipeline_id == 50) ? 1 : 2;
				matched = true;
				break;
			}
		}
		if (matched)
			continue;

		/* 2. PCM Interface Controls (e.g. Playback Channel Map, ELD -> host-copier.<dev>.playback) */
		if (k->iface == 3 /* SNDRV_CTL_ELEM_IFACE_PCM */ ||
		    strcmp(k->iface_str, "PCM") == 0) {
			k->anchor_type = SOF_KCTL_ANCHOR_PCM_FE;
			snprintf(k->target_widget, sizeof(k->target_widget),
				 "host-copier.%u.playback", k->device);
			snprintf(k->target_component, sizeof(k->target_component),
				 "0000:80:1f.3");
			k->pipeline_id = (k->device == 0) ? 52 :
					 (k->device == 3) ? 50 :
					 (k->device == 4) ? 60 :
					 (k->device == 5) ? 70 : -1;
			k->layout_col = 1;
			k->layout_row = (k->device == 0) ? 0 :
					(k->device == 3) ? 1 :
					(k->device == 4) ? 2 : 3;
			continue;
		}

		/* 3. HDMI/DP Jack or IEC958 Codec Controls -> Backend DAI / hifiN */
		unsigned int pcm_num = 0;
		if (sscanf(k->name, "HDMI/DP,pcm=%u Jack", &pcm_num) == 1) {
			k->anchor_type = SOF_KCTL_ANCHOR_DAI_BE;
			uint32_t disp_idx = (pcm_num >= 3 && pcm_num <= 5) ? (pcm_num - 2) : 1;
			snprintf(k->target_widget, sizeof(k->target_widget),
				 "hifi%u", disp_idx);
			snprintf(k->target_component, sizeof(k->target_component),
				 "ehdaudio0D2");
			k->layout_col = 6;
			k->layout_row = disp_idx - 1;
			continue;
		}
		if (strncmp(k->name, "IEC958 Playback ", 16) == 0 ||
		    strcmp(k->name, "HDMI/DP Jack") == 0) {
			k->anchor_type = SOF_KCTL_ANCHOR_DAI_BE;
			uint32_t disp_idx = (k->index < 4) ? (k->index + 1) : 1;
			snprintf(k->target_widget, sizeof(k->target_widget),
				 "hifi%u", disp_idx);
			snprintf(k->target_component, sizeof(k->target_component),
				 "ehdaudio0D2");
			k->layout_col = 6;
			k->layout_row = disp_idx - 1;
			continue;
		}
	}
}
