# SPDX-License-Identifier: BSD-3-Clause
#
# Top-level Makefile for sof-utils:
#   - sof-builder       : GTK4 Topology2 Visual Graph Editor & Testbench Studio
#   - sof-top           : GTK4 Live DSP Resource, Memory (SRAM + DRAM) & Task Monitor
#   - sof-remote-server : C TCP Telemetry & Control Daemon (sof_remote_server) + CLI (sof-remote)
#   - sof-testbench     : Multi-Core LL/DP Host Simulator (sof-testbench4) + CLI (sof-tb-ctl)
#   - sof-tplg-parser   : ALSA Topology (.tplg) IPC3/IPC4 Parser Library (libsof_tplg_parser.a)

PACKAGE      := sof-utils
VERSION      ?= 0.1.0
RELEASE      ?= 1

PREFIX       ?= /usr
BINDIR       ?= $(PREFIX)/bin
LIBDIR       ?= $(PREFIX)/lib
INCLUDEDIR   ?= $(PREFIX)/include
DATADIR      ?= $(PREFIX)/share
DOCDIR       ?= $(DATADIR)/doc/$(PACKAGE)
DESTDIR      ?=

INSTALL      ?= install
PYTHON       ?= python3
CMAKE        ?= cmake
CTEST        ?= ctest
DPKG_DEB     ?= dpkg-deb
RPMBUILD     ?= rpmbuild
JOBS         ?= $(shell nproc 2>/dev/null || echo 4)

WORK_DIR     ?= $(abspath $(CURDIR)/..)
BUILD_DIR    ?= $(CURDIR)/build
TB_BUILD_DIR ?= $(CURDIR)/sof-testbench/build
DIST_DIR     ?= $(CURDIR)/dist
WITH_TESTBENCH ?= 1

# Auto-detect SOF firmware source directory (SOF_SRC_DIR) for sof-testbench & sof-tplg-parser
ifeq ($(strip $(SOF_SRC_DIR)),)
  ifneq ($(strip $(SOF_ROOT)),)
    ifneq ($(wildcard $(SOF_ROOT)/Kconfig),)
      SOF_SRC_DIR := $(abspath $(SOF_ROOT))
    endif
  endif
endif
ifeq ($(strip $(SOF_SRC_DIR)),)
  WEST_SOF_DIR := $(shell west list -f "{abspath}" sof 2>/dev/null)
  ifneq ($(and $(WEST_SOF_DIR),$(wildcard $(WEST_SOF_DIR)/Kconfig)),)
    SOF_SRC_DIR := $(WEST_SOF_DIR)
  endif
endif
ifeq ($(strip $(SOF_SRC_DIR)),)
  SOF_CANDIDATES := \
    $(WORK_DIR)/sof-dragon-fly/sof-testbench-upstream \
    $(WORK_DIR)/sof/sof \
    $(WORK_DIR)/sof \
    $(WORK_DIR)/sof-dragon-fly/sof-arl \
    $(WORK_DIR)/sof-arl/sof-testbench-upstream \
    $(WORK_DIR)/sof-arl/sof
  SOF_SRC_DIR := $(firstword $(foreach d,$(SOF_CANDIDATES),$(if $(wildcard $(d)/Kconfig),$(abspath $(d)))))
endif

DEB_ARCH     := $(shell dpkg --print-architecture 2>/dev/null || uname -m)
RPM_ARCH     := $(shell uname -m)
DEB_STAGE    := $(BUILD_DIR)/deb-stage/$(PACKAGE)_$(VERSION)-$(RELEASE)_$(DEB_ARCH)
DEB_FILE     := $(DIST_DIR)/$(PACKAGE)_$(VERSION)-$(RELEASE)_$(DEB_ARCH).deb
RPM_TOPDIR   := $(abspath $(BUILD_DIR)/rpmbuild)

.PHONY: all build remote-server builder top testbench \
        install uninstall check test ctest \
        deb rpm package packages clean distclean help

all: build

build: remote-server builder top testbench

remote-server:
	$(MAKE) -C sof-remote-server WORK_DIR="$(WORK_DIR)" all

builder:
	$(MAKE) -C sof-builder PYTHON="$(PYTHON)" all

top:
	$(MAKE) -C sof-top PYTHON="$(PYTHON)" all

testbench:
ifeq ($(WITH_TESTBENCH),1)
  ifneq ($(strip $(SOF_SRC_DIR)),)
	@echo "==> Building sof-testbench & sof-tplg-parser against SOF_SRC_DIR=$(SOF_SRC_DIR)"
	@if [ -f "$(TB_BUILD_DIR)/CMakeCache.txt" ]; then \
		cached_src=$$(grep '^SOF_SRC_DIR:PATH=' "$(TB_BUILD_DIR)/CMakeCache.txt" 2>/dev/null | cut -d= -f2-); \
		if [ -n "$$cached_src" ] && [ ! -d "$$cached_src" ]; then \
			echo "==> Removing stale CMakeCache in $(TB_BUILD_DIR) (old SOF_SRC_DIR=$$cached_src)"; \
			rm -rf "$(TB_BUILD_DIR)/CMakeCache.txt" "$(TB_BUILD_DIR)/CMakeFiles"; \
		fi; \
	fi
	$(CMAKE) -B "$(TB_BUILD_DIR)" -S sof-testbench \
		-DSOF_SRC_DIR="$(SOF_SRC_DIR)" \
		-DCMAKE_INSTALL_PREFIX="$(PREFIX)"
	$(CMAKE) --build "$(TB_BUILD_DIR)" -j"$(JOBS)"
  else
	@echo "==> [NOTE] SOF_SRC_DIR not found; skipping sof-testbench build (pass SOF_SRC_DIR=/path/to/sof to enable)"
  endif
endif

install: build
	$(MAKE) -C sof-remote-server DESTDIR="$(DESTDIR)" PREFIX="$(PREFIX)" BINDIR="$(BINDIR)" WORK_DIR="$(WORK_DIR)" install
	$(MAKE) -C sof-builder DESTDIR="$(DESTDIR)" PREFIX="$(PREFIX)" BINDIR="$(BINDIR)" DATADIR="$(DATADIR)" install
	$(MAKE) -C sof-top DESTDIR="$(DESTDIR)" PREFIX="$(PREFIX)" BINDIR="$(BINDIR)" DATADIR="$(DATADIR)" install
	$(MAKE) -C sof-teensy DESTDIR="$(DESTDIR)" PREFIX="$(PREFIX)" BINDIR="$(BINDIR)" DATADIR="$(DATADIR)" install
	$(INSTALL) -d "$(DESTDIR)$(DOCDIR)"
	$(INSTALL) -m 0644 README.md LICENSE "$(DESTDIR)$(DOCDIR)/"
	@if [ -f "$(TB_BUILD_DIR)/sof-testbench4" ]; then \
		$(INSTALL) -d "$(DESTDIR)$(BINDIR)"; \
		$(INSTALL) -m 0755 "$(TB_BUILD_DIR)/sof-testbench4" "$(DESTDIR)$(BINDIR)/sof-testbench4"; \
	fi
	@if [ -f "$(TB_BUILD_DIR)/sof-testbench3" ]; then \
		$(INSTALL) -d "$(DESTDIR)$(BINDIR)"; \
		$(INSTALL) -m 0755 "$(TB_BUILD_DIR)/sof-testbench3" "$(DESTDIR)$(BINDIR)/sof-testbench3"; \
	fi
	@if [ -f "$(TB_BUILD_DIR)/sof-tb-ctl" ]; then \
		$(INSTALL) -d "$(DESTDIR)$(BINDIR)"; \
		$(INSTALL) -m 0755 "$(TB_BUILD_DIR)/sof-tb-ctl" "$(DESTDIR)$(BINDIR)/sof-tb-ctl"; \
	fi
	@if [ -f "$(TB_BUILD_DIR)/sof-tplg-parser/libsof_tplg_parser.a" ]; then \
		$(INSTALL) -d "$(DESTDIR)$(LIBDIR)"; \
		$(INSTALL) -m 0644 "$(TB_BUILD_DIR)/sof-tplg-parser/libsof_tplg_parser.a" "$(DESTDIR)$(LIBDIR)/libsof_tplg_parser.a"; \
		$(INSTALL) -d "$(DESTDIR)$(INCLUDEDIR)/tplg_parser"; \
		$(INSTALL) -m 0644 sof-tplg-parser/include/tplg_parser/*.h "$(DESTDIR)$(INCLUDEDIR)/tplg_parser/"; \
	fi

uninstall:
	$(MAKE) -C sof-remote-server DESTDIR="$(DESTDIR)" PREFIX="$(PREFIX)" BINDIR="$(BINDIR)" uninstall
	$(MAKE) -C sof-builder DESTDIR="$(DESTDIR)" PREFIX="$(PREFIX)" BINDIR="$(BINDIR)" DATADIR="$(DATADIR)" uninstall
	$(MAKE) -C sof-top DESTDIR="$(DESTDIR)" PREFIX="$(PREFIX)" BINDIR="$(BINDIR)" DATADIR="$(DATADIR)" uninstall
	$(MAKE) -C sof-teensy DESTDIR="$(DESTDIR)" PREFIX="$(PREFIX)" BINDIR="$(BINDIR)" DATADIR="$(DATADIR)" uninstall
	rm -f "$(DESTDIR)$(BINDIR)/sof-testbench4" \
	      "$(DESTDIR)$(BINDIR)/sof-testbench3" \
	      "$(DESTDIR)$(BINDIR)/sof-tb-ctl" \
	      "$(DESTDIR)$(LIBDIR)/libsof_tplg_parser.a"
	rm -rf "$(DESTDIR)$(INCLUDEDIR)/tplg_parser" \
	       "$(DESTDIR)$(DOCDIR)"

check:
	$(MAKE) -C sof-builder PYTHON="$(PYTHON)" check
	$(MAKE) -C sof-top PYTHON="$(PYTHON)" check
	$(MAKE) -C sof-teensy PYTHON="$(PYTHON)" check

test: check

ctest: testbench
	$(CTEST) --test-dir "$(TB_BUILD_DIR)" --output-on-failure

# ------------------------------------------------------------------------------
# Debian (.deb) Package Target
# ------------------------------------------------------------------------------
deb: build
	@echo "==> Staging Debian package into $(DEB_STAGE)"
	rm -rf "$(DEB_STAGE)"
	$(MAKE) DESTDIR="$(DEB_STAGE)" PREFIX=/usr install
	$(INSTALL) -d "$(DEB_STAGE)/DEBIAN"
	@installed_kb=$$(du -sk "$(DEB_STAGE)/usr" | awk '{print $$1}'); \
	{ \
		echo "Package: $(PACKAGE)"; \
		echo "Version: $(VERSION)-$(RELEASE)"; \
		echo "Section: sound"; \
		echo "Priority: optional"; \
		echo "Architecture: $(DEB_ARCH)"; \
		echo "Installed-Size: $$installed_kb"; \
		echo "Maintainer: Liam Girdwood <liam.r.girdwood@intel.com>"; \
		echo "Homepage: https://github.com/thesofproject/sof-utils"; \
		echo "Depends: libc6, libasound2t64 | libasound2, python3, python3-gi, python3-gi-cairo, gir1.2-gtk-4.0, gir1.2-pango-1.0, alsa-utils"; \
		echo "Description: Sound Open Firmware (SOF) Utilities, GTK4 Visual Tools, Remote Server & Testbench"; \
		echo " Developer utilities, visual GTK4 applications, and host/DUT tools for the"; \
		echo " Sound Open Firmware (SOF) ecosystem:"; \
		echo "  * sof-builder: GTK4 Topology2 Visual Graph Editor, IPC4 Compiler & Testbench Studio"; \
		echo "  * sof-top: GTK4 Live DSP Resource, Memory (SRAM + DRAM) & Task Monitor"; \
		echo "  * sof_remote_server & sof-remote: TCP Telemetry & Control Daemon and CLI for DUTs"; \
		echo "  * sof-testbench4 & sof-tb-ctl: Multi-core LL/DP Host DSP Simulator & Control CLI"; \
		echo "  * sof-tplg-parser: ALSA Topology (.tplg) IPC3/IPC4 Parser Library"; \
	} > "$(DEB_STAGE)/DEBIAN/control"
	@cd "$(DEB_STAGE)" && find usr -type f -exec md5sum {} + > DEBIAN/md5sums
	$(INSTALL) -d "$(DIST_DIR)"
	$(DPKG_DEB) --root-owner-group -Zxz --build "$(DEB_STAGE)" "$(DEB_FILE)"
	@echo "==> Built Debian package: $(DEB_FILE)"

# ------------------------------------------------------------------------------
# RPM (.rpm) Package Target
# ------------------------------------------------------------------------------
rpm:
	@echo "==> Preparing RPM build tree in $(RPM_TOPDIR)"
	rm -rf "$(RPM_TOPDIR)"
	$(INSTALL) -d "$(RPM_TOPDIR)/BUILD" \
	              "$(RPM_TOPDIR)/BUILDROOT" \
	              "$(RPM_TOPDIR)/RPMS" \
	              "$(RPM_TOPDIR)/SOURCES" \
	              "$(RPM_TOPDIR)/SPECS" \
	              "$(RPM_TOPDIR)/SRPMS" \
	              "$(RPM_TOPDIR)/TMP/$(PACKAGE)-$(VERSION)"
	rsync -a \
		--exclude='/build' \
		--exclude='/dist' \
		--exclude='/.git' \
		--exclude='/sof-testbench/build' \
		--exclude='__pycache__' \
		--exclude='*.pyc' \
		--exclude='*.o' \
		"$(CURDIR)/" "$(RPM_TOPDIR)/TMP/$(PACKAGE)-$(VERSION)/"
	tar -C "$(RPM_TOPDIR)/TMP" -czf "$(RPM_TOPDIR)/SOURCES/$(PACKAGE)-$(VERSION).tar.gz" "$(PACKAGE)-$(VERSION)"
	rm -rf "$(RPM_TOPDIR)/TMP"
	cp packaging/rpm/sof-utils.spec "$(RPM_TOPDIR)/SPECS/sof-utils.spec"
	$(RPMBUILD) -bb "$(RPM_TOPDIR)/SPECS/sof-utils.spec" \
		--define "_topdir $(RPM_TOPDIR)" \
		--define "pkg_version $(VERSION)" \
		--define "pkg_release $(RELEASE)" \
		--define "_work_dir $(WORK_DIR)" \
		$(if $(strip $(SOF_SRC_DIR)),--define "_sof_src_dir $(SOF_SRC_DIR)" --define "with_testbench 1",)
	$(INSTALL) -d "$(DIST_DIR)"
	find "$(RPM_TOPDIR)/RPMS" -name "*.rpm" -exec cp -f {} "$(DIST_DIR)/" \;
	@echo "==> Built RPM package(s) in $(DIST_DIR):"
	@ls -lh "$(DIST_DIR)"/*.rpm

package packages: deb rpm
	@echo "==> All packages available in $(DIST_DIR):"
	@ls -lh "$(DIST_DIR)"

clean:
	$(MAKE) -C sof-remote-server clean
	$(MAKE) -C sof-builder clean
	$(MAKE) -C sof-top clean
	rm -rf "$(BUILD_DIR)"

distclean: clean
	rm -rf "$(TB_BUILD_DIR)" "$(DIST_DIR)"

help:
	@echo "SOF Utilities (sof-utils) Build & Packaging Targets:"
	@echo "  make [all|build]          - Build sof-remote-server, sof-builder, sof-top, and sof-testbench"
	@echo "  make install [DESTDIR=..] - Install all binaries, libraries, headers, desktop files, and docs"
	@echo "  make uninstall            - Remove installed files"
	@echo "  make check                - Verify Python syntax/bytecode compilation"
	@echo "  make ctest                - Run the 5-tier sof-testbench CTest suite"
	@echo "  make deb                  - Build Debian package in dist/*.deb"
	@echo "  make rpm                  - Build RPM package in dist/*.rpm"
	@echo "  make packages             - Build both .deb and .rpm packages in dist/"
	@echo "  make clean / distclean    - Clean build artifacts"
