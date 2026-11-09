# SPDX-License-Identifier: (GPL-2.0-only OR BSD-3-Clause)
CC       ?= gcc
CFLAGS   ?= -O2 -Wall -Wextra -g -pthread
LDFLAGS  ?=
INSTALL  ?= install
PREFIX   ?= /usr
BINDIR   ?= $(PREFIX)/bin
DESTDIR  ?=

WORK_DIR      ?= $(abspath $(CURDIR)/../..)
ALSA_INC      ?= $(WORK_DIR)/alsa-lib/include
TINYALSA_INC  ?= $(WORK_DIR)/tinyalsa/include
TINYALSALIB   ?= $(WORK_DIR)/tinyalsa/src/libtinyalsa.a
TINYCOMPR_INC ?= $(WORK_DIR)/tinycompress/include
TINYCOMPR_OBJS ?= $(WORK_DIR)/tinycompress/src/lib/.libs/libtinycompress_la-compress.o \
                  $(WORK_DIR)/tinycompress/src/lib/.libs/libtinycompress_la-compress_hw.o
ASOUND_SO     ?= $(firstword $(wildcard /usr/lib/*/libasound.so /usr/lib/*/libasound.so.2 /usr/lib64/libasound.so /usr/lib64/libasound.so.2) -lasound)

ALSA_CFLAGS :=
ifneq ($(wildcard $(ALSA_INC)/alsa/asoundlib.h),)
ALSA_CFLAGS := -I$(ALSA_INC)
endif

TINYALSA_CFLAGS :=
TINYALSA_LIBS   :=
ifneq ($(and $(wildcard $(TINYALSA_INC)/tinyalsa/pcm.h),$(wildcard $(TINYALSALIB))),)
TINYALSA_CFLAGS := -DHAVE_TINYALSA -I$(TINYALSA_INC)
TINYALSA_LIBS   := $(TINYALSALIB)
else ifneq ($(wildcard /usr/include/tinyalsa/pcm.h),)
TINYALSA_CFLAGS := -DHAVE_TINYALSA
TINYALSA_LIBS   := -ltinyalsa
endif

TINYCOMPR_CFLAGS :=
TINYCOMPR_LIBS   :=
ifneq ($(and $(wildcard $(TINYCOMPR_INC)/tinycompress/tinycompress.h),$(wildcard $(firstword $(TINYCOMPR_OBJS)))),)
TINYCOMPR_CFLAGS := -DHAVE_TINYCOMPRESS -I$(TINYCOMPR_INC)
TINYCOMPR_LIBS   := $(TINYCOMPR_OBJS)
else ifneq ($(wildcard /usr/include/tinycompress/tinycompress.h),)
TINYCOMPR_CFLAGS := -DHAVE_TINYCOMPRESS
TINYCOMPR_LIBS   := -ltinycompress
endif

SRV_OBJS := sof_remote_server.o \
            audio_backend_alsa.o \
            audio_backend_tinyalsa.o \
            audio_backend_compress.o \
            dapm_inspector.o

.PHONY: all install uninstall clean

all: sof_remote_server sof-remote

audio_backend_alsa.o: audio_backend_alsa.c sof_remote_proto.h audio_backends.h
	$(CC) $(CFLAGS) $(ALSA_CFLAGS) -c $< -o $@

audio_backend_tinyalsa.o: audio_backend_tinyalsa.c sof_remote_proto.h audio_backends.h
	$(CC) $(CFLAGS) $(TINYALSA_CFLAGS) -c $< -o $@

audio_backend_compress.o: audio_backend_compress.c sof_remote_proto.h audio_backends.h
	$(CC) $(CFLAGS) $(TINYCOMPR_CFLAGS) -c $< -o $@

dapm_inspector.o: dapm_inspector.c sof_remote_proto.h audio_backends.h
	$(CC) $(CFLAGS) -c $< -o $@

sof_remote_server.o: sof_remote_server.c sof_remote_proto.h audio_backends.h
	$(CC) $(CFLAGS) -c $< -o $@

sof_remote_server: $(SRV_OBJS)
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ $(SRV_OBJS) $(TINYALSA_LIBS) $(TINYCOMPR_LIBS) $(ASOUND_SO) -ldl -lpthread -lm

sof-remote: sof_remote_cli.c sof_remote_proto.h
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ sof_remote_cli.c -lpthread -lm

install: all
	$(INSTALL) -d "$(DESTDIR)$(BINDIR)"
	$(INSTALL) -m 0755 sof_remote_server "$(DESTDIR)$(BINDIR)/sof_remote_server"
	ln -sf sof_remote_server "$(DESTDIR)$(BINDIR)/sof-remote-server"
	$(INSTALL) -m 0755 sof-remote "$(DESTDIR)$(BINDIR)/sof-remote"

uninstall:
	rm -f "$(DESTDIR)$(BINDIR)/sof_remote_server" \
	      "$(DESTDIR)$(BINDIR)/sof-remote-server" \
	      "$(DESTDIR)$(BINDIR)/sof-remote"

clean:
	rm -f *.o sof_remote_server sof-remote
