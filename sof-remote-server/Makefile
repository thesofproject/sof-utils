# SPDX-License-Identifier: (GPL-2.0-only OR BSD-3-Clause)
CC      ?= gcc
CFLAGS  ?= -O2 -Wall -Wextra -g -pthread

WORK_DIR      ?= $(abspath $(CURDIR)/../..)
ALSA_INC      ?= $(WORK_DIR)/alsa-lib/include
TINYALSA_INC  ?= $(WORK_DIR)/tinyalsa/include
TINYALSALIB   ?= $(WORK_DIR)/tinyalsa/src/libtinyalsa.a
TINYCOMPR_INC ?= $(WORK_DIR)/tinycompress/include
TINYCOMPR_OBJS ?= $(WORK_DIR)/tinycompress/src/lib/.libs/libtinycompress_la-compress.o \
                  $(WORK_DIR)/tinycompress/src/lib/.libs/libtinycompress_la-compress_hw.o
ASOUND_SO     ?= $(firstword $(wildcard /usr/lib/*/libasound.so /usr/lib/*/libasound.so.2 /usr/lib64/libasound.so /usr/lib64/libasound.so.2) -lasound)

SRV_OBJS := sof_remote_server.o \
            audio_backend_alsa.o \
            audio_backend_tinyalsa.o \
            audio_backend_compress.o \
            dapm_inspector.o

all: sof_remote_server sof-remote

audio_backend_alsa.o: audio_backend_alsa.c sof_remote_proto.h audio_backends.h
	$(CC) $(CFLAGS) -I$(ALSA_INC) -c $< -o $@

audio_backend_tinyalsa.o: audio_backend_tinyalsa.c sof_remote_proto.h audio_backends.h
	$(CC) $(CFLAGS) -I$(TINYALSA_INC) -c $< -o $@

audio_backend_compress.o: audio_backend_compress.c sof_remote_proto.h audio_backends.h
	$(CC) $(CFLAGS) -I$(TINYCOMPR_INC) -c $< -o $@

dapm_inspector.o: dapm_inspector.c sof_remote_proto.h audio_backends.h
	$(CC) $(CFLAGS) -c $< -o $@

sof_remote_server.o: sof_remote_server.c sof_remote_proto.h audio_backends.h
	$(CC) $(CFLAGS) -c $< -o $@

sof_remote_server: $(SRV_OBJS)
	$(CC) $(CFLAGS) -o $@ $(SRV_OBJS) $(TINYALSALIB) $(TINYCOMPR_OBJS) $(ASOUND_SO) -ldl -lpthread -lm

sof-remote: sof_remote_cli.c sof_remote_proto.h
	$(CC) $(CFLAGS) -o $@ sof_remote_cli.c -lpthread -lm

clean:
	rm -f *.o sof_remote_server sof-remote
