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

# -------------------------------------------------------------------------
# Android Cross-Compilation Support (NDK or AOSP Prebuilt Clang + Sysroot)
# Usage:
#   make android [ANDROID_ABI=x86_64|arm64-v8a] [ANDROID_API=34]
#   make android-push [ADB=adb]
# -------------------------------------------------------------------------
ANDROID_ABI      ?= x86_64
ANDROID_API      ?= 34
ANDROID_OUT_DIR  ?= build-android-$(ANDROID_ABI)
ADB              ?= adb
AOSP_DIR         ?= $(firstword $(wildcard $(WORK_DIR)/work-extra/AOSP/wcl-desktop $(WORK_DIR)/AOSP))

ifeq ($(ANDROID_ABI),arm64-v8a)
ANDROID_TRIPLE   := aarch64-linux-android
else ifeq ($(ANDROID_ABI),aarch64)
ANDROID_TRIPLE   := aarch64-linux-android
else
ANDROID_TRIPLE   := x86_64-linux-android
endif

ifneq ($(ANDROID_NDK_HOME),)
ANDROID_CLANG    ?= $(firstword $(wildcard $(ANDROID_NDK_HOME)/toolchains/llvm/prebuilt/*/bin/clang))
ANDROID_SYSROOT  ?= $(firstword $(wildcard $(ANDROID_NDK_HOME)/toolchains/llvm/prebuilt/*/sysroot))
else ifneq ($(AOSP_DIR),)
ANDROID_CLANG    ?= $(lastword $(sort $(wildcard $(AOSP_DIR)/prebuilts/clang/host/linux-x86/clang-r*/bin/clang)))
ANDROID_SYSROOT  ?= $(AOSP_DIR)/out/soong/ndk/sysroot
ANDROID_CRTBEGIN ?= $(dir $(firstword $(wildcard $(AOSP_DIR)/out/soong/.intermediates/bionic/libc/crtbegin_dynamic/android_$(ANDROID_ABI)*/crtbegin_dynamic.o)))
ANDROID_CRTEND   ?= $(dir $(firstword $(wildcard $(AOSP_DIR)/out/soong/.intermediates/bionic/libc/crtend_android/android_$(ANDROID_ABI)*/crtend_android.o)))
endif

ANDROID_TINYALSA_DIR ?= $(firstword $(wildcard $(AOSP_DIR)/external/tinyalsa $(WORK_DIR)/tinyalsa))
ANDROID_TINYCOMPR_DIR ?= $(firstword $(wildcard $(WORK_DIR)/tinycompress $(AOSP_DIR)/external/tinycompress))

ANDROID_CRT_FLAGS := $(if $(ANDROID_CRTBEGIN),-B$(ANDROID_CRTBEGIN)) $(if $(ANDROID_CRTEND),-B$(ANDROID_CRTEND))

ANDROID_CFLAGS   := --target=$(ANDROID_TRIPLE)$(ANDROID_API) \
                    --sysroot=$(ANDROID_SYSROOT) \
                    $(ANDROID_CRT_FLAGS) \
                    -O2 -Wall -Wextra -Wno-unused-parameter \
                    -Wno-unused-variable -Wno-unused-but-set-variable \
                    -Wno-sign-compare -Wno-macro-redefined \
                    -fPIE -pthread -DNO_ALSA
ANDROID_LDFLAGS  := --target=$(ANDROID_TRIPLE)$(ANDROID_API) \
                    --sysroot=$(ANDROID_SYSROOT) \
                    $(ANDROID_CRT_FLAGS) \
                    -pie -llog -ldl -lm -lc

ANDROID_EXTRA_SRCS :=
ifneq ($(wildcard $(ANDROID_TINYALSA_DIR)/pcm.c),)
ANDROID_CFLAGS     += -DHAVE_TINYALSA -I$(ANDROID_TINYALSA_DIR)/include
ANDROID_EXTRA_SRCS += $(ANDROID_TINYALSA_DIR)/pcm.c \
                      $(ANDROID_TINYALSA_DIR)/pcm_hw.c \
                      $(ANDROID_TINYALSA_DIR)/pcm_plugin.c \
                      $(ANDROID_TINYALSA_DIR)/mixer.c \
                      $(ANDROID_TINYALSA_DIR)/mixer_hw.c \
                      $(ANDROID_TINYALSA_DIR)/mixer_plugin.c \
                      $(ANDROID_TINYALSA_DIR)/snd_utils.c
else ifneq ($(wildcard $(ANDROID_TINYALSA_DIR)/src/pcm.c),)
ANDROID_CFLAGS     += -DHAVE_TINYALSA -I$(ANDROID_TINYALSA_DIR)/include -I$(ANDROID_TINYALSA_DIR)/src
ANDROID_EXTRA_SRCS += $(ANDROID_TINYALSA_DIR)/src/pcm.c \
                      $(ANDROID_TINYALSA_DIR)/src/pcm_hw.c \
                      $(ANDROID_TINYALSA_DIR)/src/pcm_plugin.c \
                      $(ANDROID_TINYALSA_DIR)/src/mixer.c \
                      $(ANDROID_TINYALSA_DIR)/src/mixer_hw.c \
                      $(ANDROID_TINYALSA_DIR)/src/mixer_plugin.c \
                      $(ANDROID_TINYALSA_DIR)/src/snd_card_plugin.c \
                      $(ANDROID_TINYALSA_DIR)/src/limits.c
endif

ifneq ($(wildcard $(ANDROID_TINYCOMPR_DIR)/src/lib/compress.c),)
ANDROID_CFLAGS     += -DHAVE_TINYCOMPRESS -I$(ANDROID_TINYCOMPR_DIR)/include
ANDROID_EXTRA_SRCS += $(ANDROID_TINYCOMPR_DIR)/src/lib/compress.c \
                      $(ANDROID_TINYCOMPR_DIR)/src/lib/compress_hw.c
endif

.PHONY: all android android-push install uninstall clean

all: sof_remote_server sof-remote

android:
	@if [ -z "$(ANDROID_CLANG)" ] || [ ! -x "$(ANDROID_CLANG)" ]; then \
		echo "ERROR: Android Clang not found. Set ANDROID_NDK_HOME or AOSP_DIR."; \
		exit 1; \
	fi
	@if [ ! -d "$(ANDROID_SYSROOT)" ]; then \
		echo "ERROR: Android sysroot not found at $(ANDROID_SYSROOT)"; \
		exit 1; \
	fi
	mkdir -p $(ANDROID_OUT_DIR)
	$(ANDROID_CLANG) $(ANDROID_CFLAGS) \
		sof_remote_server.c \
		audio_backend_alsa.c \
		audio_backend_tinyalsa.c \
		audio_backend_compress.c \
		dapm_inspector.c \
		$(ANDROID_EXTRA_SRCS) \
		$(ANDROID_LDFLAGS) \
		-o $(ANDROID_OUT_DIR)/sof_remote_server
	$(ANDROID_CLANG) $(ANDROID_CFLAGS) \
		sof_remote_cli.c \
		$(ANDROID_LDFLAGS) \
		-o $(ANDROID_OUT_DIR)/sof-remote
	@echo "Built Android ($(ANDROID_TRIPLE)$(ANDROID_API)) binaries in $(ANDROID_OUT_DIR)/:"
	@ls -lh $(ANDROID_OUT_DIR)/sof_remote_server $(ANDROID_OUT_DIR)/sof-remote

android-push: android
	$(ADB) push $(ANDROID_OUT_DIR)/sof_remote_server $(ANDROID_OUT_DIR)/sof-remote /data/local/tmp/
	$(ADB) shell chmod 0755 /data/local/tmp/sof_remote_server /data/local/tmp/sof-remote
	$(ADB) forward tcp:9998 tcp:9998
	@echo "Pushed to /data/local/tmp/ and forwarded localhost:9998 -> Android:9998"

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
	rm -rf *.o sof_remote_server sof-remote build-android-*
