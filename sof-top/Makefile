# SPDX-License-Identifier: BSD-3-Clause
PYTHON   ?= python3
INSTALL  ?= install
PREFIX   ?= /usr
BINDIR   ?= $(PREFIX)/bin
DATADIR  ?= $(PREFIX)/share
APPDIR   ?= $(DATADIR)/sof-utils/sof-top
DESKDIR  ?= $(DATADIR)/applications
DESTDIR  ?=

PY_MODS  := backend.py widgets_cairo.py

.PHONY: all check install uninstall clean

all: check

check:
	$(PYTHON) -B -m py_compile sof-top $(PY_MODS)

install:
	$(INSTALL) -d "$(DESTDIR)$(BINDIR)"
	$(INSTALL) -d "$(DESTDIR)$(APPDIR)"
	$(INSTALL) -d "$(DESTDIR)$(DESKDIR)"
	$(INSTALL) -m 0755 sof-top "$(DESTDIR)$(APPDIR)/sof-top"
	$(INSTALL) -m 0644 $(PY_MODS) "$(DESTDIR)$(APPDIR)/"
	$(INSTALL) -m 0644 pyproject.toml requirements.txt README.md "$(DESTDIR)$(APPDIR)/"
	$(INSTALL) -m 0644 desktop/org.thesofproject.SofTop.desktop "$(DESTDIR)$(DESKDIR)/org.thesofproject.SofTop.desktop"
	ln -sf "$(APPDIR)/sof-top" "$(DESTDIR)$(BINDIR)/sof-top"

uninstall:
	rm -f "$(DESTDIR)$(BINDIR)/sof-top"
	rm -f "$(DESTDIR)$(DESKDIR)/org.thesofproject.SofTop.desktop"
	rm -rf "$(DESTDIR)$(APPDIR)"

clean:
	rm -rf __pycache__ build dist *.egg-info
