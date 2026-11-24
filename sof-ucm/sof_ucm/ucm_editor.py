# SPDX-License-Identifier: BSD-3-Clause
"""Colorized ALSA UCM2 (.conf) Text Editor with live syntax highlighting and validation."""

from __future__ import annotations
import re
from typing import Callable, Optional, Tuple

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Pango


RE_BRACE = re.compile(r"[\{\}\[\]]")
RE_NUMBER = re.compile(r"\b(?:-?\d+(?:\.\d+)?%?)\b")
RE_SECTION = re.compile(
    r"\b(?:Syntax|SectionUseCase|SectionVerb|SectionDevice|SectionModifier|"
    r"BootSequence|FixedBootSequence|EnableSequence|DisableSequence|"
    r"ConflictingDevice|SupportedDevice|Value|Include|Define|DefineRegex|"
    r"DefineMacro|Macro|If|Condition|True|False|Comment|File)\b"
)
RE_CMD = re.compile(r"\b(?:cset-tlv|cset-new|cset|disdevall|enadev|disdev|exec|msleep|usleep|shell|sysw)\b")
RE_VALUE_KEY = re.compile(
    r"\b(?:PlaybackPCM|CapturePCM|PlaybackChannels|CaptureChannels|PlaybackPriority|"
    r"CapturePriority|PlaybackVolume|PlaybackSwitch|PlaybackMixerElem|CaptureVolume|"
    r"CaptureSwitch|CaptureMixerElem|JackControl|JackHWMixer|TQ|Type|Control|"
    r"Empty|Regex|String|String1|String2|Haystack|Needle)\b"
)
RE_VARIABLE = re.compile(r"\$\{[^}\n]+\}")
RE_STRING = re.compile(r'"[^"\n]*"|\'[^\'\n]*\'')
RE_COMMENT = re.compile(r"#[^\n]*")


def check_ucm_syntax_balance(text: str) -> Tuple[bool, Optional[int], str]:
    stack: list[Tuple[str, int]] = []
    pairs = {"{": "}", "[": "]"}
    closing = {"}": "{", "]": "["}
    in_dquote = False
    in_squote = False
    in_comment = False
    escaped = False
    line_no = 1
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\n":
            line_no += 1
            in_comment = False
            in_dquote = False
            in_squote = False
            escaped = False
            i += 1
            continue
        if in_comment:
            i += 1
            continue
        if escaped:
            escaped = False
            i += 1
            continue
        if (in_dquote or in_squote) and ch == "\\":
            escaped = True
            i += 1
            continue
        in_str = in_dquote or in_squote
        if ch == "#" and not in_str:
            in_comment = True
            i += 1
            continue
        if ch == '"' and not in_squote:
            in_dquote = not in_dquote
            i += 1
            continue
        if ch == "'" and not in_dquote:
            in_squote = not in_squote
            i += 1
            continue
        if not in_str:
            if ch in pairs:
                stack.append((ch, line_no))
            elif ch in closing:
                if not stack:
                    return False, line_no, f"Unexpected '{ch}' on line {line_no}"
                top_ch, top_ln = stack.pop()
                if top_ch != closing[ch]:
                    return False, line_no, f"Mismatched '{ch}' on line {line_no} (opened '{top_ch}' on line {top_ln})"
        i += 1
    if stack:
        top_ch, top_ln = stack[-1]
        return False, top_ln, f"Unclosed '{top_ch}' opened on line {top_ln}"
    return True, None, "Valid UCM2 syntax"


class UcmTextEditor(Gtk.Box):
    """Syntax-highlighted UCM2 (.conf) editor widget synchronized with the visual graph."""

    def __init__(
        self,
        rel_path: str,
        on_apply_text: Optional[Callable[[str, str], Tuple[bool, str]]] = None,
    ):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.rel_path = rel_path
        self.on_apply_text = on_apply_text
        self._suppress_change = False

        # Toolbar
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bar.set_margin_start(6)
        bar.set_margin_end(6)
        bar.set_margin_top(4)

        self.path_lbl = Gtk.Label(label=rel_path, xalign=0.0)
        self.path_lbl.add_css_class("heading")
        self.path_lbl.set_hexpand(True)
        bar.append(self.path_lbl)

        self.status_lbl = Gtk.Label(label="Valid UCM2 syntax")
        bar.append(self.status_lbl)

        apply_btn = Gtk.Button(label="Apply to UCM Model")
        apply_btn.add_css_class("suggested-action")
        apply_btn.connect("clicked", self._on_apply_clicked)
        bar.append(apply_btn)
        self.append(bar)

        self.buffer = Gtk.TextBuffer()
        self._init_tags()
        self.buffer.connect("changed", self._on_buffer_changed)

        self.textview = Gtk.TextView.new_with_buffer(self.buffer)
        self.textview.set_monospace(True)
        self.textview.set_left_margin(10)
        self.textview.set_right_margin(10)
        self.textview.set_top_margin(8)
        self.textview.set_bottom_margin(8)

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_hexpand(True)
        scroll.set_child(self.textview)
        self.append(scroll)

    def _init_tags(self) -> None:
        tt = self.buffer.get_tag_table()
        specs = {
            "section": {"foreground": "#60a5fa", "weight": Pango.Weight.BOLD},
            "cmd": {"foreground": "#34d399", "weight": Pango.Weight.BOLD},
            "vkey": {"foreground": "#c084fc"},
            "var": {"foreground": "#fbbf24"},
            "str": {"foreground": "#fca5a5"},
            "num": {"foreground": "#38bdf8"},
            "brace": {"foreground": "#e2e8f0", "weight": Pango.Weight.BOLD},
            "comment": {"foreground": "#64748b", "style": Pango.Style.ITALIC},
        }
        for nm, props in specs.items():
            tag = Gtk.TextTag.new(nm)
            for k, v in props.items():
                tag.set_property(k, v)
            tt.add(tag)

    def set_text(self, text: str) -> None:
        self._suppress_change = True
        self.buffer.set_text(text)
        self._suppress_change = False
        self._rehighlight()

    def get_text(self) -> str:
        return self.buffer.get_text(self.buffer.get_start_iter(), self.buffer.get_end_iter(), True)

    def _on_buffer_changed(self, _buf: Gtk.TextBuffer) -> None:
        if self._suppress_change:
            return
        self._rehighlight()

    def _rehighlight(self) -> None:
        text = self.get_text()
        ok, _ln, msg = check_ucm_syntax_balance(text)
        esc_msg = GLib.markup_escape_text(msg)
        if ok:
            self.status_lbl.set_markup(f"<span foreground='#34d399'>{esc_msg}</span>")
        else:
            self.status_lbl.set_markup(f"<span foreground='#f87171'>{esc_msg}</span>")

        start = self.buffer.get_start_iter()
        end = self.buffer.get_end_iter()
        self.buffer.remove_all_tags(start, end)

        for tag_name, pattern in (
            ("brace", RE_BRACE),
            ("num", RE_NUMBER),
            ("vkey", RE_VALUE_KEY),
            ("section", RE_SECTION),
            ("cmd", RE_CMD),
            ("var", RE_VARIABLE),
            ("str", RE_STRING),
            ("comment", RE_COMMENT),
        ):
            for m in pattern.finditer(text):
                it0 = self.buffer.get_iter_at_offset(m.start())
                it1 = self.buffer.get_iter_at_offset(m.end())
                self.buffer.apply_tag_by_name(tag_name, it0, it1)

    def _on_apply_clicked(self, _btn: Gtk.Button) -> None:
        if self.on_apply_text:
            ok, msg = self.on_apply_text(self.rel_path, self.get_text())
            color = "#34d399" if ok else "#f87171"
            esc_msg = GLib.markup_escape_text(msg)
            self.status_lbl.set_markup(f"<span foreground='{color}'>{esc_msg}</span>")
