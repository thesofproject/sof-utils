# SPDX-License-Identifier: BSD-3-Clause
PYTHON   ?= python3
INSTALL  ?= install
PREFIX   ?= /usr
BINDIR   ?= $(PREFIX)/bin
DATADIR  ?= $(PREFIX)/share
APPDIR   ?= $(DATADIR)/sof-utils/sof-ucm
DESKDIR  ?= $(DATADIR)/applications
DESTDIR  ?=

PY_SRCS  := $(wildcard sof_ucm/*.py)

.PHONY: all check install uninstall clean

all: check

check:
	$(PYTHON) -B -m py_compile sof-ucm $(PY_SRCS)

install:
	$(INSTALL) -d "$(DESTDIR)$(BINDIR)"
	$(INSTALL) -d "$(DESTDIR)$(APPDIR)/sof_ucm"
	$(INSTALL) -d "$(DESTDIR)$(DESKDIR)"
	$(INSTALL) -m 0755 sof-ucm "$(DESTDIR)$(APPDIR)/sof-ucm"
	$(INSTALL) -m 0644 $(PY_SRCS) "$(DESTDIR)$(APPDIR)/sof_ucm/"
	$(INSTALL) -m 0644 pyproject.toml requirements.txt README.md "$(DESTDIR)$(APPDIR)/"
	$(INSTALL) -m 0644 desktop/org.thesofproject.SofUcm.desktop "$(DESTDIR)$(DESKDIR)/org.thesofproject.SofUcm.desktop"
	ln -sf "$(APPDIR)/sof-ucm" "$(DESTDIR)$(BINDIR)/sof-ucm"

uninstall:
	rm -f "$(DESTDIR)$(BINDIR)/sof-ucm"
	rm -f "$(DESTDIR)$(DESKDIR)/org.thesofproject.SofUcm.desktop"
	rm -rf "$(DESTDIR)$(APPDIR)"

clean:
	rm -rf sof_ucm/__pycache__ tests/__pycache__ __pycache__ build dist *.egg-info
