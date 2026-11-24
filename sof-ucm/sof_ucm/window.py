# SPDX-License-Identifier: BSD-3-Clause
"""Main GTK4 Window for SOF UCM: 3-pane layout + Pipeline Graph + UCM2 Editor & Diagnostics."""

from __future__ import annotations
import copy
import os
import posixpath
import shlex
import subprocess
import tempfile
import threading
from typing import Dict, List, Optional, Set, Tuple

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango

from .discovery import (
    LocalTransport,
    SshTransport,
    Transport,
    discover_cards,
    discover_machine_audio,
    run_alsaucm_command,
    set_kcontrol_value,
)
from .graph_builder import (
    apply_ucm_overlay_to_graph,
    build_end_to_end_graph,
    trace_reachable_from_node,
)
from .graph_canvas import UcmGraphCanvas
from .layout import layout_end_to_end_graph
from .left_panes import LeftUcmPanel
from .model import (
    GraphNode,
    KcontrolInfo,
    MachineAudioSnapshot,
    SoundCardInfo,
    UcmGraphViewModel,
    UcmProfile,
    UcmSectionDevice,
    UcmSequenceCmd,
    UcmVerb,
    UndoRedoManager,
)
from .right_inspector import RightUcmInspector
from .ucm_editor import UcmTextEditor
from .ucm_parser import build_ucm_eval_context, load_ucm_profile, parse_ucm_ast, _apply_ucm_items
from .ucm_serializer import (
    generate_ucm_profile_from_graph,
    sync_profile_raw_files,
    validate_ucm_profile,
)


class SofUcmMainWindow(Gtk.ApplicationWindow):
    """Main GTK4 Application Window for SOF UCM Visualizer & Editor."""

    def __init__(
        self,
        app: Gtk.Application,
        ssh_target: Optional[str] = None,
        ssh_port: int = 22,
        ssh_key: Optional[str] = None,
        initial_card: Optional[int] = None,
        tplg_override: Optional[str] = None,
        ucm_dir: str = "/usr/share/alsa/ucm2",
        sudo_debugfs: bool = False,
    ):
        super().__init__(application=app, title="SOF UCM — End-to-End Pipeline Visualizer & UCM2 Editor")
        self.set_default_size(1560, 920)

        self.transport: Transport = (
            SshTransport(ssh_target, port=ssh_port, identity_file=ssh_key)
            if ssh_target
            else LocalTransport()
        )
        self.selected_card_id: Optional[int] = initial_card
        self.tplg_override: Optional[str] = tplg_override
        self.ucm_root_dir: str = ucm_dir
        self.use_sudo_debugfs: bool = sudo_debugfs

        self.cards: List[SoundCardInfo] = []
        self.snap: Optional[MachineAudioSnapshot] = None
        self.vm: Optional[UcmGraphViewModel] = None
        self.profile: Optional[UcmProfile] = None
        self.mirror_capture: bool = False
        self.record_to_ucm: bool = False
        self._suppress_card_combo: bool = False
        self._text_editors: Dict[str, UcmTextEditor] = {}
        self.undo_mgr = UndoRedoManager()
        self._last_profile_snapshot: Optional[UcmProfile] = None
        self._last_layout_size: Tuple[int, int] = (0, 0)
        self._applying_paned_layout: bool = False
        self._user_paned_adjusted: bool = False

        self._build_ui()
        key_ctrl = Gtk.EventControllerKey.new()
        key_ctrl.connect("key-pressed", self._on_window_key_pressed)
        self.add_controller(key_ctrl)
        self.maximize()
        GLib.idle_add(self._initial_load)

    def _on_window_key_pressed(
        self, _ctrl: Gtk.EventControllerKey, keyval: int, _kc: int, state: Gdk.ModifierType
    ) -> bool:
        focus = self.get_focus()
        if isinstance(focus, (Gtk.TextView, Gtk.Entry)):
            return False
        if not (state & Gdk.ModifierType.CONTROL_MASK):
            return False
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        if keyval in (Gdk.KEY_z, Gdk.KEY_Z):
            return self._redo_ucm() if shift else self._undo_ucm()
        if keyval in (Gdk.KEY_y, Gdk.KEY_Y):
            return self._redo_ucm()
        return False

    def _apply_restored_profile(self, label: str, restored: UcmProfile) -> None:
        self.profile = restored
        self._last_profile_snapshot = copy.deepcopy(restored)
        if self.snap and self.vm:
            validate_ucm_profile(self.profile, self.snap, self.vm)
        self.left_panel.set_data(self.snap, self.vm, self.profile)
        self._rebuild_text_editor_tabs()
        self._apply_ucm_overlay_and_redraw(auto_fit=False)
        self.append_log(f"[UCM] {label}")

    def _undo_ucm(self) -> bool:
        if not self.profile:
            return False
        res = self.undo_mgr.undo(copy.deepcopy(self.profile))
        if not res:
            return False
        label, prev = res
        self._apply_restored_profile(f"Undo: {label}", prev)
        return True

    def _redo_ucm(self) -> bool:
        if not self.profile:
            return False
        res = self.undo_mgr.redo(copy.deepcopy(self.profile))
        if not res:
            return False
        label, nxt = res
        self._apply_restored_profile(f"Redo: {label}", nxt)
        return True

    def _build_ui(self) -> None:
        root_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_child(root_vbox)

        # -----------------------------------------------------------------
        # HeaderBar: Target (Local / SSH) + Card + Topology + UCM Actions
        # -----------------------------------------------------------------
        hbar = Gtk.HeaderBar()
        self.set_titlebar(hbar)

        # Left side: Local / SSH target selector
        self.target_mode_combo = Gtk.ComboBoxText()
        self.target_mode_combo.append("local", "Local Machine")
        self.target_mode_combo.append("ssh", "Remote (SSH)")
        self.target_mode_combo.set_active_id("ssh" if self.transport.is_remote else "local")
        self.target_mode_combo.connect("changed", self._on_target_mode_changed)
        hbar.pack_start(self.target_mode_combo)

        self.ssh_entry = Gtk.Entry()
        self.ssh_entry.set_placeholder_text("user@host[:port]")
        self.ssh_entry.set_width_chars(18)
        if isinstance(self.transport, SshTransport):
            self.ssh_entry.set_text(
                f"{self.transport.target}:{self.transport.port}" if self.transport.port != 22 else self.transport.target
            )
        self.ssh_entry.set_visible(self.transport.is_remote)
        self.ssh_entry.connect("activate", lambda _e: self._connect_and_refresh())
        hbar.pack_start(self.ssh_entry)

        self.connect_btn = Gtk.Button(label="Connect / Scan")
        self.connect_btn.connect("clicked", lambda _b: self._connect_and_refresh())
        hbar.pack_start(self.connect_btn)

        self.card_combo = Gtk.ComboBoxText()
        self.card_combo.connect("changed", self._on_card_combo_changed)
        hbar.pack_start(self.card_combo)

        self.sudo_chk = Gtk.CheckButton(label="sudo DAPM")
        self.sudo_chk.set_active(self.use_sudo_debugfs)
        self.sudo_chk.set_tooltip_text("Use passwordless sudo (-n) to read /sys/kernel/debug/asoc")
        self.sudo_chk.connect("toggled", lambda c: setattr(self, "use_sudo_debugfs", c.get_active()))
        hbar.pack_start(self.sudo_chk)

        # Right side: UCM & View actions
        save_btn = Gtk.Button(label="Save / Export…")
        save_btn.add_css_class("suggested-action")
        save_btn.connect("clicked", self._on_save_export_clicked)
        hbar.pack_end(save_btn)

        test_btn = Gtk.Button(label="Test (alsaucm)")
        test_btn.set_tooltip_text("Stage UCM2 to temp dir and test active Verb/Devices with alsaucm")
        test_btn.connect("clicked", self._on_test_alsaucm_clicked)
        hbar.pack_end(test_btn)

        val_btn = Gtk.Button(label="Validate")
        val_btn.connect("clicked", self._on_validate_clicked)
        hbar.pack_end(val_btn)

        new_ucm_btn = Gtk.Button(label="New UCM Wizard")
        new_ucm_btn.set_tooltip_text("Generate a complete UCM2 profile by tracing the machine's PCM->HW paths")
        new_ucm_btn.connect("clicked", self._on_new_ucm_wizard_clicked)
        hbar.pack_end(new_ucm_btn)

        self.record_btn = Gtk.ToggleButton(label="● Record -> UCM")
        self.record_btn.set_tooltip_text("Record kcontrol slider/switch changes into selected Device EnableSequence")
        self.record_btn.connect("toggled", lambda b: setattr(self, "record_to_ucm", b.get_active()))
        hbar.pack_end(self.record_btn)

        # -----------------------------------------------------------------
        # Secondary Toolbar: Topology, UCM Dir, View Controls
        # -----------------------------------------------------------------
        tbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        tbar.set_margin_start(8)
        tbar.set_margin_end(8)
        tbar.set_margin_top(4)
        tbar.set_margin_bottom(4)

        self.tplg_lbl = Gtk.Label(label="Topology: (auto)", xalign=0.0)
        self.tplg_lbl.set_hexpand(True)
        self.tplg_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        tbar.append(self.tplg_lbl)

        tplg_btn = Gtk.Button(label="Select .tplg…")
        tplg_btn.connect("clicked", self._on_choose_tplg_clicked)
        tbar.append(tplg_btn)

        ucm_dir_btn = Gtk.Button(label="UCM Dir…")
        ucm_dir_btn.connect("clicked", self._on_choose_ucm_dir_clicked)
        tbar.append(ucm_dir_btn)

        mirror_chk = Gtk.CheckButton(label="Mirror Capture")
        mirror_chk.set_tooltip_text("Render capture pipelines right-to-left like sof-builder")
        mirror_chk.connect("toggled", self._on_mirror_toggled)
        tbar.append(mirror_chk)

        clear_trace_btn = Gtk.Button(label="Clear Trace")
        clear_trace_btn.connect("clicked", self._on_clear_trace_clicked)
        tbar.append(clear_trace_btn)

        fit_btn = Gtk.Button(label="Fit View")
        fit_btn.connect("clicked", lambda _b: self.canvas.fit_to_view())
        tbar.append(fit_btn)

        refresh_btn = Gtk.Button(label="Refresh Live")
        refresh_btn.connect("clicked", lambda _b: self._refresh_current_card())
        tbar.append(refresh_btn)

        root_vbox.append(tbar)

        # -----------------------------------------------------------------
        # 3-Pane Body: Left (UCM/Audio Stack) | Center (Pipeline Graph + Bottom Log) | Right (UCM Editor/Node/Kctl)
        # -----------------------------------------------------------------
        self.hpaned_outer = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.hpaned_outer.set_wide_handle(True)
        self.hpaned_outer.set_resize_start_child(False)
        self.hpaned_outer.set_resize_end_child(True)
        self.hpaned_outer.set_shrink_start_child(False)
        self.hpaned_outer.set_shrink_end_child(False)
        self.hpaned_outer.set_vexpand(True)
        self.hpaned_outer.set_hexpand(True)

        self.left_panel = LeftUcmPanel(
            on_ucm_selection_changed=self._on_ucm_selection_changed,
            on_device_toggles_changed=self._on_ucm_device_toggles_changed,
            on_add_device_requested=self._on_add_device_requested,
            on_delete_device_requested=self._on_delete_device_requested,
            on_add_verb_requested=self._on_add_verb_requested,
            on_inventory_node_selected=self._on_inventory_node_selected,
        )
        left_frame = Gtk.Frame()
        left_frame.add_css_class("high-level-panel")
        left_frame.set_margin_start(4)
        left_frame.set_margin_end(2)
        left_frame.set_margin_top(4)
        left_frame.set_margin_bottom(4)
        left_frame.set_child(self.left_panel)
        self.hpaned_outer.set_start_child(left_frame)

        self.hpaned_inner = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.hpaned_inner.set_wide_handle(True)
        self.hpaned_inner.set_resize_start_child(True)
        self.hpaned_inner.set_resize_end_child(False)
        self.hpaned_inner.set_shrink_start_child(False)
        self.hpaned_inner.set_shrink_end_child(False)
        self.hpaned_inner.set_vexpand(True)
        self.hpaned_inner.set_hexpand(True)
        self.hpaned_outer.set_end_child(self.hpaned_inner)

        # Center vertical paned: Top = Pipeline Graph Notebook (dominant), Bottom = Diagnostics & Log
        self.center_vpaned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        self.center_vpaned.set_wide_handle(True)
        self.center_vpaned.set_resize_start_child(True)
        self.center_vpaned.set_resize_end_child(False)
        self.center_vpaned.set_shrink_start_child(False)
        self.center_vpaned.set_shrink_end_child(False)
        self.center_vpaned.set_vexpand(True)
        self.center_vpaned.set_hexpand(True)

        center_frame = Gtk.Frame()
        center_frame.add_css_class("high-level-panel")
        center_frame.set_margin_start(2)
        center_frame.set_margin_end(2)
        center_frame.set_margin_top(4)
        center_frame.set_margin_bottom(4)
        center_frame.set_vexpand(True)
        center_frame.set_hexpand(True)
        center_frame.set_child(self.center_vpaned)
        self.hpaned_inner.set_start_child(center_frame)

        self.center_notebook = Gtk.Notebook()
        self.center_notebook.set_vexpand(True)
        self.center_notebook.set_hexpand(True)
        self.center_notebook.set_show_border(False)

        self.canvas = UcmGraphCanvas(
            on_node_selected=self._on_canvas_node_selected,
            on_kcontrol_changed=self._on_canvas_kcontrol_changed,
            on_bind_kcontrol_to_ucm=self._on_bind_kcontrol_to_ucm,
            on_bind_pcm_to_ucm=self._on_bind_pcm_to_ucm,
            on_trace_node=self._on_trace_node,
        )
        self.center_notebook.append_page(self.canvas, Gtk.Label(label="Pipeline Graph"))
        graph_frame = Gtk.Frame()
        graph_frame.add_css_class("sub-panel-frame")
        graph_frame.set_vexpand(True)
        graph_frame.set_hexpand(True)
        graph_frame.set_child(self.center_notebook)
        self.center_vpaned.set_start_child(graph_frame)

        # Bottom Log / Diagnostics drawer
        log_frame = Gtk.Frame()
        log_frame.add_css_class("sub-panel-frame")
        log_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        log_hdr = Gtk.Label(label="Diagnostics & Activity Log", xalign=0.0)
        log_hdr.add_css_class("heading")
        log_hdr.set_margin_start(8)
        log_hdr.set_margin_top(4)
        log_box.append(log_hdr)

        self.log_buf = Gtk.TextBuffer()
        self.log_view = Gtk.TextView.new_with_buffer(self.log_buf)
        self.log_view.set_editable(False)
        self.log_view.set_monospace(True)
        log_scroll = Gtk.ScrolledWindow()
        log_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        log_scroll.set_vexpand(True)
        log_scroll.set_child(self.log_view)
        log_box.append(log_scroll)
        log_frame.set_child(log_box)
        log_frame.set_size_request(-1, 140)
        self.center_vpaned.set_end_child(log_frame)

        self.right_inspector = RightUcmInspector(
            on_ucm_edited=self._on_ucm_edited_in_inspector,
            on_kcontrol_set=self._on_inspector_kcontrol_set,
        )
        right_frame = Gtk.Frame()
        right_frame.add_css_class("high-level-panel")
        right_frame.set_margin_start(2)
        right_frame.set_margin_end(4)
        right_frame.set_margin_top(4)
        right_frame.set_margin_bottom(4)
        right_frame.set_child(self.right_inspector)
        self.hpaned_inner.set_end_child(right_frame)

        self.hpaned_outer.set_position(280)
        self.hpaned_inner.set_position(920)
        self.center_vpaned.set_position(700)
        self.hpaned_outer.connect("notify::position", self._on_user_paned_changed)
        self.hpaned_inner.connect("notify::position", self._on_user_paned_changed)
        self.center_vpaned.connect("notify::position", self._on_user_paned_changed)
        self.connect("notify::default-width", lambda *_: self._schedule_paned_sync())
        self.connect("notify::default-height", lambda *_: self._schedule_paned_sync())
        self.connect("notify::maximized", lambda *_: self._schedule_paned_sync())
        GLib.timeout_add(60, self._poll_paned_initial_sync)
        root_vbox.append(self.hpaned_outer)

    def _on_user_paned_changed(self, _paned: Gtk.Paned, _pspec: object) -> None:
        if not self._applying_paned_layout and self._last_layout_size[0] > 0:
            self._user_paned_adjusted = True

    def _schedule_paned_sync(self) -> None:
        GLib.idle_add(self._apply_proportional_paned_layout)

    def _poll_paned_initial_sync(self) -> bool:
        self._apply_proportional_paned_layout()
        return self._last_layout_size == (0, 0)

    def _apply_proportional_paned_layout(self) -> bool:
        total_w = self.hpaned_outer.get_width()
        total_h = self.hpaned_outer.get_height()
        if total_w <= 200 or total_h <= 200:
            return False
        if (total_w, total_h) == self._last_layout_size:
            return False
        self._last_layout_size = (total_w, total_h)
        if self._user_paned_adjusted:
            return False

        left_w = max(250, min(390, int(round(total_w * 0.17))))
        right_w = max(310, min(460, int(round(total_w * 0.21))))
        log_h = max(150, min(250, int(round(total_h * 0.18))))
        inner_w = max(400, total_w - left_w)
        center_w = max(360, inner_w - right_w)
        graph_h = max(280, total_h - log_h)

        self._applying_paned_layout = True
        try:
            self.hpaned_outer.set_position(left_w)
            self.hpaned_inner.set_position(center_w)
            self.center_vpaned.set_position(graph_h)
        finally:
            GLib.idle_add(self._finish_applying_paned_layout)
        GLib.idle_add(self._fit_if_untouched)
        return False

    def _fit_if_untouched(self) -> bool:
        if self.canvas._is_fitted and self.canvas._drag_mode == "none":
            self.canvas.fit_to_view()
        return False

    def _finish_applying_paned_layout(self) -> bool:
        self._applying_paned_layout = False
        return False

    def append_log(self, msg: str) -> None:
        end_it = self.log_buf.get_end_iter()
        self.log_buf.insert(end_it, msg.rstrip() + "\n")
        mark = self.log_buf.create_mark(None, self.log_buf.get_end_iter(), False)
        self.log_view.scroll_to_mark(mark, 0.0, False, 0.0, 0.0)

    # -----------------------------------------------------------------
    # Machine Connection & Discovery
    # -----------------------------------------------------------------

    def _on_target_mode_changed(self, combo: Gtk.ComboBoxText) -> None:
        is_ssh = combo.get_active_id() == "ssh"
        self.ssh_entry.set_visible(is_ssh)

    def _connect_and_refresh(self) -> None:
        if self.target_mode_combo.get_active_id() == "ssh":
            raw = self.ssh_entry.get_text().strip()
            if not raw:
                self.append_log("[ERROR] Enter an SSH target (e.g. root@192.168.1.50)")
                return
            host = raw
            port = 22
            if ":" in raw:
                host, p_str = raw.rsplit(":", 1)
                if p_str.isdigit():
                    port = int(p_str)
            self.transport = SshTransport(host, port=port)
        else:
            self.transport = LocalTransport()
        self.selected_card_id = None
        self._initial_load()

    def _initial_load(self) -> None:
        self.append_log(f"[INFO] Scanning sound cards on {self.transport.label}…")
        transport = self.transport

        def worker() -> None:
            cards = discover_cards(transport)
            GLib.idle_add(self._on_cards_discovered, cards)

        threading.Thread(target=worker, daemon=True).start()

    def _on_cards_discovered(self, cards: List[SoundCardInfo]) -> None:
        self.cards = cards
        self._suppress_card_combo = True
        self.card_combo.remove_all()
        if not cards:
            self.card_combo.append("-1", "(No ALSA cards found)")
            self.card_combo.set_active(0)
            self._suppress_card_combo = False
            self.append_log(f"[WARN] No ALSA sound cards found on {self.transport.label}")
            return

        active_idx = 0
        # Prefer SOF card by default if initial_card was not specified
        for idx, c in enumerate(cards):
            lbl = f"card{c.card_id}: {c.driver} - {c.name}"
            self.card_combo.append(str(c.card_id), lbl)
            if self.selected_card_id is not None and c.card_id == self.selected_card_id:
                active_idx = idx
            elif self.selected_card_id is None and "sof" in c.driver.lower():
                active_idx = idx

        self.card_combo.set_active(active_idx)
        self.selected_card_id = cards[active_idx].card_id
        self._suppress_card_combo = False
        self._refresh_current_card()

    def _on_card_combo_changed(self, combo: Gtk.ComboBoxText) -> None:
        if self._suppress_card_combo:
            return
        aid = combo.get_active_id()
        if aid is not None and aid != "-1":
            self.selected_card_id = int(aid)
            self.tplg_override = None
            self._refresh_current_card()

    def _refresh_current_card(self) -> None:
        if self.selected_card_id is None:
            return
        cid = self.selected_card_id
        transport = self.transport
        tplg_ovr = self.tplg_override
        ucm_dir = self.ucm_root_dir
        sudo_dbg = self.use_sudo_debugfs
        self.append_log(f"[INFO] Reading topology, PCMs, kcontrols, DAPM & UCM2 for card {cid} ({transport.label})…")

        def worker() -> None:
            snap = discover_machine_audio(transport, cid, tplg_override=tplg_ovr, use_sudo_debugfs=sudo_dbg)
            vm = build_end_to_end_graph(snap)
            layout_end_to_end_graph(vm, mirror_capture=self.mirror_capture)
            profile = load_ucm_profile(transport, snap, ucm_root=ucm_dir)
            if profile is not None:
                validate_ucm_profile(profile, snap, vm)
            GLib.idle_add(self._on_machine_audio_loaded, snap, vm, profile)

        threading.Thread(target=worker, daemon=True).start()

    def _on_machine_audio_loaded(
        self,
        snap: MachineAudioSnapshot,
        vm: UcmGraphViewModel,
        profile: Optional[UcmProfile],
    ) -> None:
        self.snap = snap
        self.vm = vm
        self.profile = profile
        self.undo_mgr.clear()
        self._last_profile_snapshot = copy.deepcopy(profile) if profile else None

        tplg_txt = snap.card.tplg_path if (snap.card and snap.card.tplg_path) else "(none detected)"
        dapm_txt = f"DAPM: {len(snap.dapm_widgets)} widgets" if snap.dapm_available else "DAPM: synthesized"
        self.tplg_lbl.set_text(
            f"Topology: {tplg_txt}  •  PCMs: {len(snap.pcms)}  •  Kctls: {len(snap.kcontrols)}  •  {dapm_txt}"
        )
        for w in snap.warnings:
            self.append_log(f"[WARN] {w}")

        if profile is None:
            self.append_log(
                f"[INFO] No existing UCM2 profile found under {self.ucm_root_dir} for this card. "
                "Click 'New UCM Wizard' to create one from the discovered graph."
            )
        else:
            self.append_log(
                f"[INFO] Loaded UCM2 '{profile.master_rel_path}' ({len(profile.verbs)} verbs, "
                f"{len(profile.diagnostics)} diagnostics)."
            )
            for d in profile.diagnostics:
                self.append_log(f"[UCM:{d.severity.upper()}] {d.message}")

        self.left_panel.set_data(snap, vm, profile)
        self._apply_ucm_overlay_and_redraw(auto_fit=True)
        self._rebuild_text_editor_tabs()

    def _apply_ucm_overlay_and_redraw(self, auto_fit: bool = False) -> None:
        if not self.vm:
            return
        apply_ucm_overlay_to_graph(
            self.vm,
            self.profile,
            self.left_panel.active_verb,
            self.left_panel.checked_devices,
        )
        self.canvas.set_view_model(self.vm, auto_fit=auto_fit)

    # -----------------------------------------------------------------
    # UCM Text Editor Tabs Synchronization
    # -----------------------------------------------------------------

    def _rebuild_text_editor_tabs(self) -> None:
        # Remove all tabs after page 0 (Pipeline Graph)
        while self.center_notebook.get_n_pages() > 1:
            self.center_notebook.remove_page(1)
        self._text_editors.clear()

        if not self.profile:
            return
        for rel_path, text in self.profile.raw_files.items():
            ed = UcmTextEditor(rel_path, on_apply_text=self._on_editor_apply_text)
            ed.set_text(text)
            self._text_editors[rel_path] = ed
            lbl = Gtk.Label(label=os.path.basename(rel_path) or rel_path)
            self.center_notebook.append_page(ed, lbl)

    def _refresh_text_editors_from_profile(self) -> None:
        if not self.profile:
            return
        sync_profile_raw_files(self.profile)
        for rel_path, text in self.profile.raw_files.items():
            if rel_path in self._text_editors:
                self._text_editors[rel_path].set_text(text)
            else:
                ed = UcmTextEditor(rel_path, on_apply_text=self._on_editor_apply_text)
                ed.set_text(text)
                self._text_editors[rel_path] = ed
                self.center_notebook.append_page(ed, Gtk.Label(label=os.path.basename(rel_path) or rel_path))

    def _on_editor_apply_text(self, rel_path: str, new_text: str) -> Tuple[bool, str]:
        if not self.profile or not self.snap:
            return False, "No active UCM profile"
        self.profile.raw_files[rel_path] = new_text
        self.profile.is_modified = True
        try:
            # Re-parse raw_files into a fresh profile view
            master_key = self.profile.master_rel_path
            if master_key in self.profile.raw_files:
                ctx = build_ucm_eval_context(self.snap)
                rebuilt = UcmProfile(
                    card_name=self.profile.card_name,
                    card_longname=self.profile.card_longname,
                    card_driver=self.profile.card_driver,
                    ucm_root_dir=self.profile.ucm_root_dir,
                    master_rel_path=master_key,
                    raw_files=dict(self.profile.raw_files),
                    is_modified=True,
                )
                # Walk master + verb files from raw_files in memory
                ast = parse_ucm_ast(rebuilt.raw_files[master_key])
                _apply_ucm_items(ast, rebuilt, ctx, self.snap, None, master_key)
                for vname, verb in rebuilt.verbs.items():
                    vrel = verb.file_path.lstrip("/")
                    if vrel in rebuilt.raw_files:
                        vast = parse_ucm_ast(rebuilt.raw_files[vrel])
                        _apply_ucm_items(vast, rebuilt, ctx, self.snap, None, vrel, cur_verb=verb)
                if self._last_profile_snapshot is not None:
                    self.undo_mgr.push("Edit UCM2 text", self._last_profile_snapshot)
                self.profile = rebuilt
                self._last_profile_snapshot = copy.deepcopy(rebuilt)
                if self.vm:
                    validate_ucm_profile(self.profile, self.snap, self.vm)
                self.left_panel.set_data(self.snap, self.vm, self.profile)
                self._apply_ucm_overlay_and_redraw(auto_fit=False)
                return True, "Applied UCM2 text to model"
        except Exception as exc:
            return False, f"Parse error: {exc}"
        return True, "Updated file buffer"

    # -----------------------------------------------------------------
    # Interactive Callbacks (Left, Canvas, Right)
    # -----------------------------------------------------------------

    def _on_ucm_selection_changed(self, kind: str, verb_name: str, key: str) -> None:
        self.right_inspector.show_ucm_target(self.profile, kind, verb_name, key)

    def _on_ucm_device_toggles_changed(self, verb_name: str, checked: Set[str]) -> None:
        self._apply_ucm_overlay_and_redraw(auto_fit=False)

    def _on_inventory_node_selected(self, node_id: str) -> None:
        self.center_notebook.set_current_page(0)
        self.canvas.focus_node(node_id)

    def _on_canvas_node_selected(self, node: Optional[GraphNode]) -> None:
        self.right_inspector.show_graph_node(node)

    def _on_trace_node(self, node: GraphNode) -> None:
        if not self.vm:
            return
        vis_nodes, vis_edges = trace_reachable_from_node(self.vm, node.id)
        self.vm.highlighted_node_ids = vis_nodes
        self.vm.highlighted_edge_ids = vis_edges
        self.vm.has_active_trace = True
        for e in self.vm.edges:
            e.is_ucm_highlighted = e.id in vis_edges
        self.canvas._edge_version += 1
        self.canvas.queue_draw()
        self.append_log(f"[TRACE] {node.title}: {len(vis_nodes)} nodes, {len(vis_edges)} edges")

    def _on_clear_trace_clicked(self, _btn: Gtk.Button) -> None:
        self.left_panel.checked_devices.clear()
        self.left_panel._rebuild_ucm_panel()
        if self.vm:
            self.vm.highlighted_node_ids.clear()
            self.vm.highlighted_edge_ids.clear()
            self.vm.active_ucm_devices.clear()
            self.vm.has_active_trace = False
            for n in self.vm.nodes.values():
                n.ucm_badges.clear()
            for e in self.vm.edges:
                e.is_ucm_highlighted = False
            self.canvas._edge_version += 1
            self.canvas.queue_draw()

    def _on_mirror_toggled(self, chk: Gtk.CheckButton) -> None:
        self.mirror_capture = chk.get_active()
        if self.vm:
            layout_end_to_end_graph(self.vm, mirror_capture=self.mirror_capture)
            self.canvas.set_view_model(self.vm, auto_fit=True)

    def _on_canvas_kcontrol_changed(self, kctl: KcontrolInfo, new_val_str: str) -> None:
        if not self.snap or not self.snap.card:
            return
        transport = self.transport
        cid = self.snap.card.card_id

        def worker() -> None:
            ok, msg = set_kcontrol_value(transport, cid, kctl, new_val_str)

            def finish() -> bool:
                self.append_log(f"[{'KCTL' if ok else 'ERR'}] {kctl.name} -> {new_val_str} ({msg})")
                if ok and self.record_to_ucm:
                    self._record_kctl_to_selected_device(kctl, new_val_str)
                return False

            GLib.idle_add(finish)

        threading.Thread(target=worker, daemon=True).start()

    def _on_inspector_kcontrol_set(self, kctl: KcontrolInfo, new_val_str: str) -> None:
        self._on_canvas_kcontrol_changed(kctl, new_val_str)
        self.canvas.queue_draw()

    def _record_kctl_to_selected_device(self, kctl: KcontrolInfo, val_str: str) -> None:
        if not self.profile:
            return
        vname = self.left_panel.active_verb
        verb = self.profile.verbs.get(vname)
        if not verb:
            return
        target_dev_name = self.right_inspector.active_ucm_key
        if self.right_inspector.active_ucm_kind != "device" or target_dev_name not in verb.devices:
            if self.left_panel.checked_devices:
                target_dev_name = next(iter(self.left_panel.checked_devices))
            else:
                self.append_log("[WARN] Select or check a UCM Device first to record kcontrol steps.")
                return
        dev = verb.devices[target_dev_name]
        prefix = f"name='{kctl.name}'"
        new_arg = f"{prefix} {val_str}"
        replaced = False
        for cmd in dev.enable_seq:
            if cmd.cmd == "cset" and cmd.arg.startswith(prefix):
                cmd.arg = new_arg
                replaced = True
                break
        if not replaced:
            dev.enable_seq.append(UcmSequenceCmd("cset", new_arg))
        self.profile.is_modified = True
        self._on_ucm_edited_in_inspector()
        self.append_log(f"[UCM REC] {target_dev_name}.EnableSequence <- cset \"{new_arg}\"")

    def _on_bind_kcontrol_to_ucm(self, kctl: KcontrolInfo, target_role: str) -> None:
        if not self.profile:
            self.append_log("[WARN] No UCM profile loaded. Click 'New UCM Wizard' first.")
            return
        if target_role == "boot_seq":
            self.profile.boot_seq.append(UcmSequenceCmd("cset", f"name='{kctl.name}' {kctl.formatted_value()}"))
            self._on_ucm_edited_in_inspector()
            return

        vname = self.left_panel.active_verb
        verb = self.profile.verbs.get(vname)
        if not verb:
            return
        dname = self.right_inspector.active_ucm_key
        if dname not in verb.devices and self.left_panel.checked_devices:
            dname = next(iter(self.left_panel.checked_devices))
        dev = verb.devices.get(dname)
        if not dev:
            self.append_log("[WARN] Select a UCM Device in the left panel first.")
            return

        if target_role == "enable_seq":
            dev.enable_seq.append(UcmSequenceCmd("cset", f"name='{kctl.name}' {kctl.formatted_value()}"))
        elif target_role == "disable_seq":
            dev.disable_seq.append(UcmSequenceCmd("cset", f"name='{kctl.name}' {kctl.formatted_value()}"))
        else:
            dev.values[target_role] = kctl.name
        self.profile.is_modified = True
        self._on_ucm_edited_in_inspector()
        self.append_log(f"[UCM] Bound '{kctl.name}' -> {dname}.{target_role}")

    def _on_bind_pcm_to_ucm(self, node: GraphNode, _role: str) -> None:
        if not self.profile or not node.pcm_info:
            return
        vname = self.left_panel.active_verb
        verb = self.profile.verbs.get(vname)
        if not verb:
            return
        dname = self.right_inspector.active_ucm_key
        if dname not in verb.devices and self.left_panel.checked_devices:
            dname = next(iter(self.left_panel.checked_devices))
        dev = verb.devices.get(dname)
        if not dev:
            self.append_log("[WARN] Select a UCM Device in the left panel first.")
            return
        key = "PlaybackPCM" if node.pcm_info.direction == "playback" else "CapturePCM"
        dev.values[key] = node.pcm_info.ucm_pcm_ref
        self.profile.is_modified = True
        self._on_ucm_edited_in_inspector()
        self.append_log(f"[UCM] Set {dname}.{key} = {node.pcm_info.ucm_pcm_ref}")

    def _on_ucm_edited_in_inspector(self) -> None:
        if not self.profile or not self.snap:
            return
        if self._last_profile_snapshot is not None:
            self.undo_mgr.push("Edit UCM profile", self._last_profile_snapshot)
        self.profile.is_modified = True
        validate_ucm_profile(self.profile, self.snap, self.vm)
        self._refresh_text_editors_from_profile()
        self.left_panel._rebuild_ucm_panel()
        self.right_inspector._rebuild_ucm_editor()
        self._apply_ucm_overlay_and_redraw(auto_fit=False)
        self._last_profile_snapshot = copy.deepcopy(self.profile)

    # -----------------------------------------------------------------
    # UCM Creation, Device CRUD, Validation, Testing & Export
    # -----------------------------------------------------------------

    def _on_new_ucm_wizard_clicked(self, _btn: Gtk.Button) -> None:
        if not self.snap or not self.vm:
            return
        self.profile = generate_ucm_profile_from_graph(self.snap, self.vm, ucm_root=self.ucm_root_dir)
        self.undo_mgr.clear()
        self._last_profile_snapshot = copy.deepcopy(self.profile)
        self.left_panel.set_data(self.snap, self.vm, self.profile)
        self._rebuild_text_editor_tabs()
        self._apply_ucm_overlay_and_redraw(auto_fit=False)
        self.append_log(
            f"[WIZARD] Generated UCM2 profile '{self.profile.master_rel_path}' with "
            f"{len(self.profile.verbs.get('HiFi', UcmVerb('HiFi')).devices)} devices."
        )

    def _on_add_device_requested(self, verb_name: str) -> None:
        if not self.profile:
            self._on_new_ucm_wizard_clicked(Gtk.Button())
            return
        verb = self.profile.verbs.get(verb_name)
        if not verb:
            return
        idx = len(verb.devices) + 1
        dname = f"Device{idx}"
        while dname in verb.devices:
            idx += 1
            dname = f"Device{idx}"
        verb.devices[dname] = UcmSectionDevice(name=dname, comment="New UCM Device", source_file=verb.file_path)
        self._on_ucm_edited_in_inspector()
        self.right_inspector.show_ucm_target(self.profile, "device", verb_name, dname)

    def _on_delete_device_requested(self, verb_name: str, dev_name: str) -> None:
        if not self.profile or verb_name not in self.profile.verbs:
            return
        self.profile.verbs[verb_name].devices.pop(dev_name, None)
        self.left_panel.checked_devices.discard(dev_name)
        self._on_ucm_edited_in_inspector()

    def _on_add_verb_requested(self) -> None:
        if not self.profile:
            self._on_new_ucm_wizard_clicked(Gtk.Button())
            return
        idx = len(self.profile.verbs) + 1
        vname = "VoiceCall" if "VoiceCall" not in self.profile.verbs else f"Verb{idx}"
        drv = self.profile.card_driver or "sof-card"
        self.profile.verbs[vname] = UcmVerb(
            name=vname,
            file_path=f"/{drv}/{vname}.conf",
            comment=f"{vname} use case",
            values={"TQ": vname},
        )
        self._on_ucm_edited_in_inspector()

    def _on_validate_clicked(self, _btn: Gtk.Button) -> None:
        if not self.profile or not self.snap:
            return
        diags = validate_ucm_profile(self.profile, self.snap, self.vm)
        self._apply_ucm_overlay_and_redraw(auto_fit=False)
        if not diags:
            self.append_log("[VALIDATE] OK — 0 UCM2 errors or warnings.")
        else:
            for d in diags:
                self.append_log(f"[{d.severity.upper()}] {d.message}")

    @staticmethod
    def _confine_local_path(dest_root: str, rel_path: str) -> Optional[str]:
        root_abs = os.path.abspath(dest_root)
        full_abs = os.path.abspath(os.path.join(root_abs, rel_path.lstrip("/")))
        try:
            if os.path.commonpath([root_abs, full_abs]) != root_abs or full_abs == root_abs:
                return None
        except ValueError:
            return None
        return full_abs

    @staticmethod
    def _confine_posix_path(stage_root: str, rel_path: str) -> Optional[str]:
        norm_rel = posixpath.normpath(rel_path.lstrip("/"))
        if not norm_rel or norm_rel in (".", "..") or norm_rel.startswith("../") or posixpath.isabs(norm_rel):
            return None
        return posixpath.join(stage_root, norm_rel)

    def _on_test_alsaucm_clicked(self, _btn: Gtk.Button) -> None:
        if not self.profile or not self.snap or not self.snap.card:
            return
        if self.profile.is_generated or self.profile.is_modified:
            sync_profile_raw_files(self.profile)
        cid = self.snap.card.card_id
        verb = self.left_panel.active_verb or next(iter(self.profile.verbs.keys()), "HiFi")
        devices = sorted(self.left_panel.checked_devices)

        # Stage raw_files into a temp UCM2 directory (copying /usr/share/alsa/ucm2/ucm.conf + lib/common if local or remote)
        cmds = ["set", "_verb", verb]
        for d in devices:
            cmds.extend(["set", "_enadev", d])
        cmds.extend(["list", "_devices"])

        if not self.transport.is_remote:
            with tempfile.TemporaryDirectory(prefix="sof-ucm-stage-") as tmpd:
                if os.path.exists("/usr/share/alsa/ucm2/ucm.conf"):
                    os.symlink("/usr/share/alsa/ucm2/ucm.conf", os.path.join(tmpd, "ucm.conf"))
                for sub in ("lib", "common", "codecs", "HDA"):
                    src_sub = os.path.join("/usr/share/alsa/ucm2", sub)
                    if os.path.exists(src_sub):
                        os.symlink(src_sub, os.path.join(tmpd, sub))
                for rel, content in self.profile.raw_files.items():
                    dest = self._confine_local_path(tmpd, rel)
                    if not dest:
                        self.append_log(f"[WARN] Skipped out-of-tree UCM2 staging path: {rel}")
                        continue
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with open(dest, "w", encoding="utf-8") as f:
                        f.write(content)
                rc, out, err = run_alsaucm_command(self.transport, cid, tmpd, cmds)
                self.append_log(f"[ALSAUCM rc={rc}] verb={verb} devices={devices}\n{out}\n{err}".strip())
        else:
            rc_mk, out_mk, err_mk = self.transport.run("mktemp -d /tmp/sof-ucm-stage.XXXXXX")
            stage = out_mk.strip().splitlines()[-1].strip() if rc_mk == 0 and out_mk.strip() else ""
            if not stage or not stage.startswith("/tmp/sof-ucm-stage."):
                self.append_log(f"[ERR] Failed to create remote staging dir: {err_mk.strip()}")
                return
            try:
                q_stage = shlex.quote(stage)
                self.transport.run(
                    f"for f in ucm.conf lib common codecs HDA; do "
                    f"[ -e /usr/share/alsa/ucm2/$f ] && ln -s /usr/share/alsa/ucm2/$f {q_stage}/$f; done"
                )
                for rel, content in self.profile.raw_files.items():
                    remote_dest = self._confine_posix_path(stage, rel)
                    if not remote_dest:
                        self.append_log(f"[WARN] Skipped out-of-tree remote staging path: {rel}")
                        continue
                    self.transport.write_text(remote_dest, content, use_sudo=False)
                rc, out, err = run_alsaucm_command(self.transport, cid, stage, cmds)
                self.append_log(f"[SSH ALSAUCM rc={rc}] verb={verb} devices={devices}\n{out}\n{err}".strip())
            finally:
                self.transport.run(f"rm -rf {shlex.quote(stage)}")

    def _on_save_export_clicked(self, _btn: Gtk.Button) -> None:
        if not self.profile:
            return
        if self.profile.is_generated or self.profile.is_modified:
            sync_profile_raw_files(self.profile)
        dlg = Gtk.FileDialog()
        dlg.set_title("Select UCM2 Root Directory to Save/Export")
        dlg.select_folder(self, None, self._on_save_folder_chosen)

    def _on_save_folder_chosen(self, dlg: Gtk.FileDialog, res: object) -> None:
        try:
            folder = dlg.select_folder_finish(res)
        except Exception:
            return
        if not folder or not self.profile:
            return
        dest_root = folder.get_path()
        if not dest_root:
            return
        dest_abs = os.path.abspath(dest_root)
        confined: List[Tuple[str, str, str]] = []
        for rel, content in self.profile.raw_files.items():
            full_path = self._confine_local_path(dest_abs, rel)
            if not full_path:
                self.append_log(f"[WARN] Skipped out-of-tree UCM2 export path: {rel}")
                continue
            confined.append((rel, full_path, content))
        if not confined:
            self.append_log("[WARN] No valid UCM2 files within target root to save.")
            return

        need_sudo = dest_abs.startswith("/usr") and os.geteuid() != 0
        if need_sudo:
            with tempfile.TemporaryDirectory(prefix="sof-ucm-export-") as tmpd:
                for rel, _full_path, content in confined:
                    staged = self._confine_local_path(tmpd, rel)
                    if not staged:
                        continue
                    os.makedirs(os.path.dirname(staged), exist_ok=True)
                    with open(staged, "w", encoding="utf-8") as f:
                        f.write(content)
                proc = subprocess.run(
                    [
                        "pkexec",
                        "sh",
                        "-c",
                        'mkdir -p "$2" && cp -r -- "$1"/. "$2"/',
                        "sof-ucm-save",
                        tmpd,
                        dest_abs,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                if proc.returncode == 0:
                    self.append_log(f"[SAVED] Exported {len(confined)} UCM2 file(s) to {dest_abs}")
                else:
                    self.append_log(f"[ERR] Failed (rc={proc.returncode}): {proc.stderr.strip()}")
            return

        for _rel, full_path, content in confined:
            ok, msg = LocalTransport().write_text(full_path, content, use_sudo=False)
            self.append_log(f"[{'SAVED' if ok else 'ERR'}] {msg}")

    def _on_choose_tplg_clicked(self, _btn: Gtk.Button) -> None:
        dlg = Gtk.FileDialog()
        dlg.set_title("Select ALSA Topology (.tplg)")
        dlg.open(self, None, self._on_tplg_chosen)

    def _on_tplg_chosen(self, dlg: Gtk.FileDialog, res: object) -> None:
        try:
            f = dlg.open_finish(res)
        except Exception:
            return
        if f and f.get_path():
            self.tplg_override = f.get_path()
            self._refresh_current_card()

    def _on_choose_ucm_dir_clicked(self, _btn: Gtk.Button) -> None:
        dlg = Gtk.FileDialog()
        dlg.set_title("Select UCM2 Root Directory")
        dlg.select_folder(self, None, self._on_ucm_dir_chosen)

    def _on_ucm_dir_chosen(self, dlg: Gtk.FileDialog, res: object) -> None:
        try:
            folder = dlg.select_folder_finish(res)
        except Exception:
            return
        if folder and folder.get_path():
            self.ucm_root_dir = folder.get_path()
            self._refresh_current_card()
