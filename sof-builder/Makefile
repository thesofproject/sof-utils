# SPDX-License-Identifier: BSD-3-Clause
PYTHON   ?= python3
INSTALL  ?= install
PREFIX   ?= /usr
BINDIR   ?= $(PREFIX)/bin
DATADIR  ?= $(PREFIX)/share
APPDIR   ?= $(DATADIR)/sof-utils/sof-builder
DESKDIR  ?= $(DATADIR)/applications
DESTDIR  ?=

PY_SRCS  := $(wildcard sof_builder/*.py)

.PHONY: all check install uninstall clean

all: check

check:
	$(PYTHON) -B -m py_compile sof-builder $(PY_SRCS)

install:
	$(INSTALL) -d "$(DESTDIR)$(BINDIR)"
	$(INSTALL) -d "$(DESTDIR)$(APPDIR)/sof_builder"
	$(INSTALL) -d "$(DESTDIR)$(DESKDIR)"
	$(INSTALL) -m 0755 sof-builder "$(DESTDIR)$(APPDIR)/sof-builder"
	$(INSTALL) -m 0644 $(PY_SRCS) "$(DESTDIR)$(APPDIR)/sof_builder/"
	$(INSTALL) -m 0644 pyproject.toml requirements.txt README.md "$(DESTDIR)$(APPDIR)/"
	$(INSTALL) -m 0644 desktop/org.thesofproject.SofBuilder.desktop "$(DESTDIR)$(DESKDIR)/org.thesofproject.SofBuilder.desktop"
	ln -sf "$(APPDIR)/sof-builder" "$(DESTDIR)$(BINDIR)/sof-builder"

uninstall:
	rm -f "$(DESTDIR)$(BINDIR)/sof-builder"
	rm -f "$(DESTDIR)$(DESKDIR)/org.thesofproject.SofBuilder.desktop"
	rm -rf "$(DESTDIR)$(APPDIR)"

clean:
	rm -rf sof_builder/__pycache__ __pycache__ build dist *.egg-info
