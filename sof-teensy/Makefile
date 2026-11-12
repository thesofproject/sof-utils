# SPDX-License-Identifier: BSD-3-Clause
PYTHON   ?= python3
INSTALL  ?= install
PREFIX   ?= /usr
BINDIR   ?= $(PREFIX)/bin
DATADIR  ?= $(PREFIX)/share
APPDIR   ?= $(DATADIR)/sof-utils/sof-teensy
DESTDIR  ?=

SCRIPTS  := teensy_flash.py teensy_diag.py test_teensy_loopback.py

.PHONY: all check install uninstall clean

all: check

check:
	$(PYTHON) -B -m py_compile $(SCRIPTS)

install:
	$(INSTALL) -d "$(DESTDIR)$(BINDIR)"
	$(INSTALL) -d "$(DESTDIR)$(APPDIR)"
	$(INSTALL) -m 0755 $(SCRIPTS) "$(DESTDIR)$(APPDIR)/"
	$(INSTALL) -m 0644 README.md "$(DESTDIR)$(APPDIR)/"
	ln -sf "$(APPDIR)/teensy_flash.py" "$(DESTDIR)$(BINDIR)/sof-teensy-flash"
	ln -sf "$(APPDIR)/teensy_diag.py" "$(DESTDIR)$(BINDIR)/sof-teensy-diag"
	ln -sf "$(APPDIR)/test_teensy_loopback.py" "$(DESTDIR)$(BINDIR)/sof-teensy-loopback"

uninstall:
	rm -f "$(DESTDIR)$(BINDIR)/sof-teensy-flash" \
	      "$(DESTDIR)$(BINDIR)/sof-teensy-diag" \
	      "$(DESTDIR)$(BINDIR)/sof-teensy-loopback"
	rm -rf "$(DESTDIR)$(APPDIR)"

clean:
	rm -rf __pycache__
