# SPDX-License-Identifier: BSD-3-Clause
"""Right-hand inspector for UCM Devices/Sequences, Audio Graph Nodes, and Live Kcontrols."""

from __future__ import annotations
from typing import Callable, Optional

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from .model import (
    GraphNode,
    KcontrolInfo,
    UcmProfile,
    UcmSectionDevice,
    UcmSequenceCmd,
    UcmVerb,
)


class RightUcmInspector(Gtk.Box):
    """Context-sensitive inspector for UCM2 Sections and Graph Nodes / Kcontrols."""

    def __init__(
        self,
        on_ucm_edited: Optional[Callable[[], None]] = None,
        on_kcontrol_set: Optional[Callable[[KcontrolInfo, str], None]] = None,
    ):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.set_size_request(340, -1)
        self.set_margin_start(4)
        self.set_margin_end(6)
        self.set_margin_top(6)
        self.set_margin_bottom(6)

        self.on_ucm_edited = on_ucm_edited
        self.on_kcontrol_set = on_kcontrol_set

        self.profile: Optional[UcmProfile] = None
        self.active_verb_name: str = ""
        self.active_ucm_kind: str = ""      # "boot" | "verb" | "device" | "modifier"
        self.active_ucm_key: str = ""
        self.selected_node: Optional[GraphNode] = None

        # Header Card
        hdr_frame = Gtk.Frame()
        hdr_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        hdr_box.set_margin_start(8)
        hdr_box.set_margin_end(8)
        hdr_box.set_margin_top(6)
        hdr_box.set_margin_bottom(6)

        self.title_lbl = Gtk.Label(label="Inspector", xalign=0.0)
        self.title_lbl.add_css_class("heading")
        self.title_lbl.set_wrap(True)
        hdr_box.append(self.title_lbl)

        self.sub_lbl = Gtk.Label(label="Select a UCM Device or Graph Node", xalign=0.0)
        self.sub_lbl.set_wrap(True)
        hdr_box.append(self.sub_lbl)
        hdr_frame.set_child(hdr_box)
        self.append(hdr_frame)

        # Notebook: Tab 0 = UCM Section Editor, Tab 1 = Graph Node / Kcontrol Details
        self.notebook = Gtk.Notebook()
        self.notebook.set_vexpand(True)
        self.notebook.set_hexpand(True)

        # -----------------------------------------------------------------
        # Tab 0: UCM Section Editor (Values + Enable/Disable Sequences)
        # -----------------------------------------------------------------
        ucm_scroll = Gtk.ScrolledWindow()
        ucm_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        ucm_scroll.set_propagate_natural_width(False)
        ucm_scroll.set_vexpand(True)
        self.ucm_editor_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.ucm_editor_box.set_margin_start(6)
        self.ucm_editor_box.set_margin_end(6)
        self.ucm_editor_box.set_margin_top(6)
        self.ucm_editor_box.set_margin_bottom(6)
        ucm_scroll.set_child(self.ucm_editor_box)
        self.notebook.append_page(ucm_scroll, Gtk.Label(label="UCM Editor"))

        # -----------------------------------------------------------------
        # Tab 1: Graph Node & Live Kcontrol Inspector
        # -----------------------------------------------------------------
        self.node_store = Gtk.TreeStore.new([str, str])
        self.node_tree = Gtk.TreeView.new_with_model(self.node_store)
        c0 = Gtk.TreeViewColumn("Property", Gtk.CellRendererText(), text=0)
        c0.set_resizable(True)
        c1 = Gtk.TreeViewColumn("Value", Gtk.CellRendererText(), text=1)
        c1.set_resizable(True)
        self.node_tree.append_column(c0)
        self.node_tree.append_column(c1)

        node_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        node_scroll = Gtk.ScrolledWindow()
        node_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        node_scroll.set_propagate_natural_width(False)
        node_scroll.set_vexpand(True)
        node_scroll.set_child(self.node_tree)
        node_box.append(node_scroll)

        self.live_kctl_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.live_kctl_box.set_margin_start(6)
        self.live_kctl_box.set_margin_end(6)
        self.live_kctl_box.set_margin_bottom(6)
        node_box.append(self.live_kctl_box)

        self.notebook.append_page(node_box, Gtk.Label(label="Node / Kctl"))
        self.append(self.notebook)

    def show_ucm_target(
        self,
        profile: Optional[UcmProfile],
        kind: str,
        verb_name: str,
        key: str,
    ) -> None:
        self.profile = profile
        self.active_ucm_kind = kind
        self.active_verb_name = verb_name
        self.active_ucm_key = key
        self._rebuild_ucm_editor()
        self.notebook.set_current_page(0)

    def show_graph_node(self, node: Optional[GraphNode]) -> None:
        self.selected_node = node
        self._rebuild_node_inspector()
        if node is not None:
            self.notebook.set_current_page(1)

    def _clear_box(self, box: Gtk.Box) -> None:
        ch = box.get_first_child()
        while ch is not None:
            nxt = ch.get_next_sibling()
            box.remove(ch)
            ch = nxt

    def _rebuild_ucm_editor(self) -> None:
        self._clear_box(self.ucm_editor_box)
        if not self.profile:
            return

        if self.active_ucm_kind == "boot":
            self.title_lbl.set_text("Card BootSequence")
            self.sub_lbl.set_text(self.profile.master_rel_path or "Master config")
            self._build_seq_section("BootSequence", self.profile.boot_seq)
            return

        verb = self.profile.verbs.get(self.active_verb_name)
        if not verb:
            return

        if self.active_ucm_kind == "verb":
            self.title_lbl.set_text(f"SectionVerb: {verb.name}")
            self.sub_lbl.set_text(verb.file_path or "")
            self._build_kv_section("Verb Values", verb.values, ["TQ", "PlaybackPriority", "CapturePriority"])
            self._build_seq_section("Verb EnableSequence", verb.enable_seq)
            self._build_seq_section("Verb DisableSequence", verb.disable_seq)
            return

        if self.active_ucm_kind == "device":
            dev = verb.devices.get(self.active_ucm_key)
            if not dev:
                return
            self.title_lbl.set_text(f"SectionDevice: {dev.name}")
            self.sub_lbl.set_text(dev.comment or dev.source_file or "")
            self._build_comment_and_conflicts(verb, dev)
            self._build_kv_section(
                "Device Values",
                dev.values,
                [
                    "PlaybackPCM", "CapturePCM",
                    "PlaybackPriority", "CapturePriority",
                    "PlaybackChannels", "CaptureChannels",
                    "PlaybackVolume", "PlaybackSwitch", "PlaybackMixerElem",
                    "CaptureVolume", "CaptureSwitch", "CaptureMixerElem",
                    "JackControl", "JackHWMixer",
                ],
            )
            self._build_seq_section("EnableSequence", dev.enable_seq)
            self._build_seq_section("DisableSequence", dev.disable_seq)

    def _build_comment_and_conflicts(self, verb: UcmVerb, dev: UcmSectionDevice) -> None:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.append(Gtk.Label(label="Comment:"))
        centry = Gtk.Entry()
        centry.set_hexpand(True)
        centry.set_text(dev.comment)

        def _commit_comment(_e: Gtk.Entry) -> None:
            dev.comment = centry.get_text().strip()
            if self.on_ucm_edited:
                self.on_ucm_edited()

        centry.connect("activate", _commit_comment)
        row.append(centry)
        set_b = Gtk.Button(label="Set")
        set_b.connect("clicked", lambda _b: _commit_comment(centry))
        row.append(set_b)
        self.ucm_editor_box.append(row)

        # ConflictingDevice editor
        conf_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        conf_row.append(Gtk.Label(label="Conflicts:"))
        conf_entry = Gtk.Entry()
        conf_entry.set_hexpand(True)
        conf_entry.set_placeholder_text("Comma-separated device names")
        conf_entry.set_text(", ".join(dev.conflicting_devices))

        def _commit_conf(_w: object) -> None:
            dev.conflicting_devices = [x.strip() for x in conf_entry.get_text().split(",") if x.strip()]
            if self.on_ucm_edited:
                self.on_ucm_edited()

        conf_entry.connect("activate", _commit_conf)
        conf_row.append(conf_entry)
        cb_btn = Gtk.Button(label="Set")
        cb_btn.connect("clicked", _commit_conf)
        conf_row.append(cb_btn)
        self.ucm_editor_box.append(conf_row)

    def _build_kv_section(self, title: str, values: dict, common_keys: list[str]) -> None:
        hdr = Gtk.Label(label=title, xalign=0.0)
        hdr.add_css_class("heading")
        self.ucm_editor_box.append(hdr)

        for k in list(values.keys()):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            klbl = Gtk.Label(label=k, xalign=0.0)
            klbl.set_size_request(125, -1)
            row.append(klbl)

            vent = Gtk.Entry()
            vent.set_hexpand(True)
            vent.set_text(str(values[k]))

            def _apply(_w: object, key: str = k, entry: Gtk.Entry = vent) -> None:
                values[key] = entry.get_text().strip()
                if self.on_ucm_edited:
                    self.on_ucm_edited()

            vent.connect("activate", _apply)
            row.append(vent)

            del_b = Gtk.Button(label="✕")
            del_b.add_css_class("flat")

            def _del(_b: Gtk.Button, key: str = k) -> None:
                values.pop(key, None)
                self._rebuild_ucm_editor()
                if self.on_ucm_edited:
                    self.on_ucm_edited()

            del_b.connect("clicked", _del)
            row.append(del_b)
            self.ucm_editor_box.append(row)

        # Quick add key=value row
        add_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        k_combo = Gtk.ComboBoxText.new_with_entry()
        for ck in common_keys:
            k_combo.append_text(ck)
        k_combo.set_size_request(110, -1)
        if k_combo.get_child():
            k_combo.get_child().set_width_chars(8)
        add_row.append(k_combo)

        v_ent = Gtk.Entry()
        v_ent.set_placeholder_text("Value…")
        v_ent.set_width_chars(8)
        v_ent.set_hexpand(True)
        add_row.append(v_ent)

        add_b = Gtk.Button(label="+")

        def _add(_b: Gtk.Button) -> None:
            nk = (k_combo.get_active_text() or "").strip()
            nv = v_ent.get_text().strip()
            if nk:
                values[nk] = nv
                self._rebuild_ucm_editor()
                if self.on_ucm_edited:
                    self.on_ucm_edited()

        add_b.connect("clicked", _add)
        add_row.append(add_b)
        self.ucm_editor_box.append(add_row)

    def _build_seq_section(self, title: str, seq: list[UcmSequenceCmd]) -> None:
        hdr = Gtk.Label(label=f"{title} ({len(seq)})", xalign=0.0)
        hdr.add_css_class("heading")
        self.ucm_editor_box.append(hdr)

        for idx, cmd in enumerate(seq):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            c_ent = Gtk.Entry()
            c_ent.set_text(cmd.cmd)
            c_ent.set_width_chars(8)
            row.append(c_ent)

            a_ent = Gtk.Entry()
            a_ent.set_text(cmd.arg)
            a_ent.set_hexpand(True)
            row.append(a_ent)

            def _save(_w: object, i: int = idx, ce: Gtk.Entry = c_ent, ae: Gtk.Entry = a_ent) -> None:
                seq[i].cmd = ce.get_text().strip() or "cset"
                seq[i].arg = ae.get_text().strip()
                if self.on_ucm_edited:
                    self.on_ucm_edited()

            a_ent.connect("activate", _save)
            c_ent.connect("activate", _save)

            del_b = Gtk.Button(label="✕")
            del_b.add_css_class("flat")

            def _del(_b: Gtk.Button, i: int = idx) -> None:
                seq.pop(i)
                self._rebuild_ucm_editor()
                if self.on_ucm_edited:
                    self.on_ucm_edited()

            del_b.connect("clicked", _del)
            row.append(del_b)
            self.ucm_editor_box.append(row)

        add_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        new_cmd = Gtk.ComboBoxText.new_with_entry()
        for opt in ("cset", "cset-tlv", "disdevall", "exec", "msleep"):
            new_cmd.append_text(opt)
        new_cmd.set_active(0)
        if new_cmd.get_child():
            new_cmd.get_child().set_width_chars(6)
        add_row.append(new_cmd)

        new_arg = Gtk.Entry()
        new_arg.set_placeholder_text("name='Control Name' on")
        new_arg.set_width_chars(10)
        new_arg.set_hexpand(True)
        add_row.append(new_arg)

        add_btn = Gtk.Button(label="+ Step")

        def _add_step(_b: Gtk.Button) -> None:
            c = (new_cmd.get_active_text() or "cset").strip()
            a = new_arg.get_text().strip()
            if c:
                seq.append(UcmSequenceCmd(cmd=c, arg=a))
                self._rebuild_ucm_editor()
                if self.on_ucm_edited:
                    self.on_ucm_edited()

        add_btn.connect("clicked", _add_step)
        add_row.append(add_btn)
        self.ucm_editor_box.append(add_row)

    def _rebuild_node_inspector(self) -> None:
        self.node_store.clear()
        self._clear_box(self.live_kctl_box)
        node = self.selected_node
        if not node:
            return

        self.title_lbl.set_text(node.title)
        self.sub_lbl.set_text(node.subtitle)

        self.node_store.append(None, ["Node ID", node.id])
        self.node_store.append(None, ["Category", f"{node.class_category} ({node.category})"])
        self.node_store.append(None, ["Direction", node.direction])
        if node.component:
            self.node_store.append(None, ["Component", node.component])
        if node.pipeline_id is not None:
            self.node_store.append(None, ["Pipeline ID", str(node.pipeline_id)])
        if node.in_fmt_badge:
            self.node_store.append(None, ["Input Format", node.in_fmt_badge])
        if node.out_fmt_badge:
            self.node_store.append(None, ["Output Format", node.out_fmt_badge])
        if node.ucm_badges:
            self.node_store.append(None, ["UCM Claims", ", ".join(node.ucm_badges)])
        if node.diag_message:
            self.node_store.append(None, ["Diagnostic", node.diag_message])

        if node.pcm_info:
            p = node.pcm_info
            self.node_store.append(None, ["ALSA Device", p.alsa_dev])
            self.node_store.append(None, ["UCM Ref", p.ucm_pcm_ref])
            self.node_store.append(None, ["State", p.state_str])
            if p.formats:
                self.node_store.append(None, ["Formats", " ".join(p.formats)])
            if p.rates:
                self.node_store.append(None, ["Rates", " ".join(str(r) for r in p.rates)])

        if node.dapm_widget:
            dw = node.dapm_widget
            self.node_store.append(None, ["DAPM Power", "On" if dw.power > 0 else "Off"])
            self.node_store.append(None, ["DAPM Type", dw.widget_type])
            if dw.stream_name:
                self.node_store.append(None, ["Stream", f"{dw.stream_name} ({'active' if dw.stream_active else 'inactive'})"])

        if node.tplg_widget:
            tw = node.tplg_widget
            self.node_store.append(None, ["Domain / Core", f"{tw.proc_domain} / Core {tw.core_id}"])
            if tw.uuid_hex:
                self.node_store.append(None, ["UUID", tw.uuid_hex])

        if node.kcontrol:
            k = node.kcontrol
            self.node_store.append(None, ["Kcontrol numid", str(k.numid)])
            self.node_store.append(None, ["Interface", k.iface])
            self.node_store.append(None, ["Type", f"{k.ctl_type} (count={k.count})"])
            self.node_store.append(None, ["Range", f"[{k.min_val} .. {k.max_val}] step={k.step}"])
            if k.tlv_str:
                self.node_store.append(None, ["TLV Scale", k.tlv_str])
            self.node_store.append(None, ["Current Value", k.formatted_value()])

            if k.writable and self.on_kcontrol_set:
                lbl = Gtk.Label(label="Live Kcontrol Adjust:", xalign=0.0)
                lbl.add_css_class("heading")
                self.live_kctl_box.append(lbl)
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                ent = Gtk.Entry()
                ent.set_hexpand(True)
                ent.set_text(k.formatted_value())
                row.append(ent)
                btn = Gtk.Button(label="Apply Live")
                btn.connect("clicked", lambda _b: self.on_kcontrol_set(k, ent.get_text().strip()))
                row.append(btn)
                self.live_kctl_box.append(row)
