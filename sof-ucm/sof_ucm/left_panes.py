# SPDX-License-Identifier: BSD-3-Clause
"""Left-hand navigation panel: UCM2 Profile Structure (Verbs/Devices/Modifiers) & Audio Inventory."""

from __future__ import annotations
from typing import Callable, Optional, Set

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from .model import (
    MachineAudioSnapshot,
    UcmGraphViewModel,
    UcmProfile,
)


class LeftUcmPanel(Gtk.Box):
    """Left panel with UCM Profile navigator (with device highlight toggles) and Audio Inventory."""

    def __init__(
        self,
        on_ucm_selection_changed: Optional[Callable[[str, str, str], None]] = None,
        on_device_toggles_changed: Optional[Callable[[str, Set[str]], None]] = None,
        on_add_device_requested: Optional[Callable[[str], None]] = None,
        on_delete_device_requested: Optional[Callable[[str, str], None]] = None,
        on_add_verb_requested: Optional[Callable[[], None]] = None,
        on_inventory_node_selected: Optional[Callable[[str], None]] = None,
    ):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.set_size_request(250, -1)
        self.set_margin_start(6)
        self.set_margin_end(4)
        self.set_margin_top(6)
        self.set_margin_bottom(6)

        self.on_ucm_selection_changed = on_ucm_selection_changed
        self.on_device_toggles_changed = on_device_toggles_changed
        self.on_add_device_requested = on_add_device_requested
        self.on_delete_device_requested = on_delete_device_requested
        self.on_add_verb_requested = on_add_verb_requested
        self.on_inventory_node_selected = on_inventory_node_selected

        self.profile: Optional[UcmProfile] = None
        self.snap: Optional[MachineAudioSnapshot] = None
        self.vm: Optional[UcmGraphViewModel] = None
        self.active_verb: str = ""
        self.checked_devices: Set[str] = set()
        self._suppress_signals: bool = False

        self.notebook = Gtk.Notebook()
        self.notebook.set_vexpand(True)
        self.notebook.set_hexpand(True)

        # -----------------------------------------------------------------
        # Tab 1: UCM2 Profile Tree
        # -----------------------------------------------------------------
        ucm_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        ucm_box.set_margin_top(6)
        ucm_box.set_margin_bottom(4)
        ucm_box.set_margin_start(4)
        ucm_box.set_margin_end(4)

        verb_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        verb_lbl = Gtk.Label(label="Verb:")
        verb_lbl.add_css_class("heading")
        verb_row.append(verb_lbl)

        self.verb_combo = Gtk.ComboBoxText()
        self.verb_combo.set_hexpand(True)
        self.verb_combo.connect("changed", self._on_verb_combo_changed)
        verb_row.append(self.verb_combo)

        add_verb_btn = Gtk.Button(label="+ Verb")
        add_verb_btn.connect("clicked", lambda _b: self.on_add_verb_requested() if self.on_add_verb_requested else None)
        verb_row.append(add_verb_btn)
        ucm_box.append(verb_row)

        dev_hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.ucm_info_lbl = Gtk.Label(label="Devices (check to trace on graph)", xalign=0.0)
        self.ucm_info_lbl.set_hexpand(True)
        self.ucm_info_lbl.add_css_class("heading")
        dev_hdr.append(self.ucm_info_lbl)

        add_dev_btn = Gtk.Button(label="+ Device")
        add_dev_btn.connect("clicked", self._on_add_device_clicked)
        dev_hdr.append(add_dev_btn)

        del_dev_btn = Gtk.Button(label="Del")
        del_dev_btn.connect("clicked", self._on_del_device_clicked)
        dev_hdr.append(del_dev_btn)
        ucm_box.append(dev_hdr)

        # Columns: 0=Trace(bool), 1=ShowCheck(bool), 2=Name(str), 3=Summary(str), 4=Kind(str), 5=Key(str)
        self.ucm_store = Gtk.TreeStore.new([bool, bool, str, str, str, str])
        self.ucm_tree = Gtk.TreeView.new_with_model(self.ucm_store)
        self.ucm_tree.set_headers_visible(True)

        r_tog = Gtk.CellRendererToggle()
        r_tog.connect("toggled", self._on_device_toggled)
        col_tog = Gtk.TreeViewColumn("Trace", r_tog, active=0, visible=1)
        self.ucm_tree.append_column(col_tog)

        r_nm = Gtk.CellRendererText()
        col_nm = Gtk.TreeViewColumn("UCM Section", r_nm, text=2)
        col_nm.set_resizable(True)
        self.ucm_tree.append_column(col_nm)

        r_sum = Gtk.CellRendererText()
        r_sum.set_property("foreground", "#94a3b8")
        col_sum = Gtk.TreeViewColumn("Target / Summary", r_sum, text=3)
        col_sum.set_resizable(True)
        self.ucm_tree.append_column(col_sum)

        sel = self.ucm_tree.get_selection()
        sel.connect("changed", self._on_ucm_row_selected)

        ucm_scroll = Gtk.ScrolledWindow()
        ucm_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        ucm_scroll.set_propagate_natural_width(False)
        ucm_scroll.set_vexpand(True)
        ucm_scroll.set_child(self.ucm_tree)
        ucm_frame = Gtk.Frame()
        ucm_frame.set_vexpand(True)
        ucm_frame.set_child(ucm_scroll)
        ucm_box.append(ucm_frame)

        self.notebook.append_page(ucm_box, Gtk.Label(label="UCM Profile"))

        # -----------------------------------------------------------------
        # Tab 2: Discovered Audio Inventory (PCMs, Endpoints, Kcontrols)
        # -----------------------------------------------------------------
        inv_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        inv_box.set_margin_top(6)
        inv_box.set_margin_bottom(4)
        inv_box.set_margin_start(4)
        inv_box.set_margin_end(4)

        self.inv_search = Gtk.SearchEntry()
        self.inv_search.set_placeholder_text("Filter PCMs, endpoints, kcontrols…")
        self.inv_search.connect("search-changed", lambda _e: self._rebuild_inventory())
        inv_box.append(self.inv_search)

        # Columns: 0=Name(str), 1=Detail(str), 2=NodeId(str)
        self.inv_store = Gtk.TreeStore.new([str, str, str])
        self.inv_tree = Gtk.TreeView.new_with_model(self.inv_store)
        self.inv_tree.set_headers_visible(True)

        c0 = Gtk.TreeViewColumn("Object", Gtk.CellRendererText(), text=0)
        c0.set_resizable(True)
        self.inv_tree.append_column(c0)

        r_det = Gtk.CellRendererText()
        r_det.set_property("foreground", "#94a3b8")
        c1 = Gtk.TreeViewColumn("Info", r_det, text=1)
        c1.set_resizable(True)
        self.inv_tree.append_column(c1)

        self.inv_tree.connect("row-activated", self._on_inv_row_activated)
        inv_sel = self.inv_tree.get_selection()
        inv_sel.connect("changed", self._on_inv_selection_changed)

        inv_scroll = Gtk.ScrolledWindow()
        inv_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        inv_scroll.set_propagate_natural_width(False)
        inv_scroll.set_vexpand(True)
        inv_scroll.set_child(self.inv_tree)
        inv_frame = Gtk.Frame()
        inv_frame.set_vexpand(True)
        inv_frame.set_child(inv_scroll)
        inv_box.append(inv_frame)

        self.notebook.append_page(inv_box, Gtk.Label(label="Audio Stack"))
        self.append(self.notebook)

    def set_data(
        self,
        snap: Optional[MachineAudioSnapshot],
        vm: Optional[UcmGraphViewModel],
        profile: Optional[UcmProfile],
    ) -> None:
        self.snap = snap
        self.vm = vm
        self.profile = profile
        self._rebuild_ucm_panel()
        self._rebuild_inventory()

    def _rebuild_ucm_panel(self) -> None:
        self._suppress_signals = True
        self.verb_combo.remove_all()
        self.ucm_store.clear()

        if not self.profile:
            self.active_verb = ""
            self.checked_devices.clear()
            self._suppress_signals = False
            return

        verbs = list(self.profile.verbs.keys())
        for v in verbs:
            self.verb_combo.append_text(v)
        if verbs:
            if self.active_verb not in verbs:
                self.active_verb = "HiFi" if "HiFi" in verbs else verbs[0]
            self.verb_combo.set_active(verbs.index(self.active_verb))
        else:
            self.active_verb = ""

        # BootSequence root row
        boot_cnt = len(self.profile.boot_seq)
        self.ucm_store.append(None, [False, False, "BootSequence", f"{boot_cnt} cmds", "boot", "BootSequence"])

        if self.active_verb and self.active_verb in self.profile.verbs:
            verb = self.profile.verbs[self.active_verb]
            self.ucm_store.append(None, [False, False, "SectionVerb", verb.values.get("TQ", self.active_verb), "verb", self.active_verb])

            dev_parent = self.ucm_store.append(None, [False, False, f"Devices ({len(verb.devices)})", "", "header", ""])
            self.checked_devices = {d for d in self.checked_devices if d in verb.devices}
            for dname, dev in verb.devices.items():
                pcm = dev.values.get("PlaybackPCM") or dev.values.get("CapturePCM") or ""
                checked = dname in self.checked_devices
                self.ucm_store.append(dev_parent, [checked, True, dname, pcm or dev.comment, "device", dname])

            if verb.modifiers:
                mod_parent = self.ucm_store.append(None, [False, False, f"Modifiers ({len(verb.modifiers)})", "", "header", ""])
                for mname, mod in verb.modifiers.items():
                    self.ucm_store.append(mod_parent, [False, False, mname, mod.comment, "modifier", mname])

        self.ucm_tree.expand_all()
        self._suppress_signals = False

    def _rebuild_inventory(self) -> None:
        self.inv_store.clear()
        if not self.snap or not self.vm:
            return
        flt = self.inv_search.get_text().strip().lower()

        pcm_root = self.inv_store.append(None, [f"ALSA PCMs ({len(self.snap.pcms)})", "", ""])
        for p in self.snap.pcms:
            lbl = f"PCM{p.device_id} {p.dir_short}: {p.id or p.name}"
            det = f"{p.alsa_dev} • {p.state_str}"
            if flt and flt not in lbl.lower() and flt not in det.lower():
                continue
            self.inv_store.append(pcm_root, [lbl, det, f"pcm:{p.direction}:{p.device_id}"])

        eps = [n for n in self.vm.nodes.values() if n.class_category == "Endpoint"]
        ep_root = self.inv_store.append(None, [f"HW Endpoints ({len(eps)})", "", ""])
        for ep in eps:
            if flt and flt not in ep.title.lower() and flt not in ep.subtitle.lower():
                continue
            self.inv_store.append(ep_root, [ep.title, ep.direction, ep.id])

        mods = [n for n in self.vm.nodes.values() if n.class_category in ("Widget", "Codec")]
        mod_root = self.inv_store.append(None, [f"DSP / Codec Modules ({len(mods)})", "", ""])
        for m in mods:
            if flt and flt not in m.title.lower() and flt not in m.subtitle.lower():
                continue
            self.inv_store.append(mod_root, [m.title, m.subtitle, m.id])

        k_nodes = [n for n in self.vm.nodes.values() if n.class_category == "Control"]
        k_root = self.inv_store.append(None, [f"Kcontrols ({len(k_nodes)})", "", ""])
        for kn in k_nodes:
            val_s = kn.kcontrol.formatted_value() if kn.kcontrol else ""
            if flt and flt not in kn.title.lower() and flt not in val_s.lower():
                continue
            self.inv_store.append(k_root, [kn.title, val_s, kn.id])

        self.inv_tree.expand_all()

    def _on_verb_combo_changed(self, combo: Gtk.ComboBoxText) -> None:
        if self._suppress_signals:
            return
        txt = combo.get_active_text()
        if txt:
            self.active_verb = txt
            self.checked_devices.clear()
            self._rebuild_ucm_panel()
            if self.on_device_toggles_changed:
                self.on_device_toggles_changed(self.active_verb, set(self.checked_devices))
            if self.on_ucm_selection_changed:
                self.on_ucm_selection_changed("verb", self.active_verb, self.active_verb)

    def _on_device_toggled(self, _rend: Gtk.CellRendererToggle, path_str: str) -> None:
        if self._suppress_signals:
            return
        it = self.ucm_store.get_iter_from_string(path_str)
        if not it:
            return
        cur = bool(self.ucm_store.get_value(it, 0))
        dname = self.ucm_store.get_value(it, 5)
        nxt = not cur
        self.ucm_store.set_value(it, 0, nxt)
        if nxt:
            self.checked_devices.add(dname)
        else:
            self.checked_devices.discard(dname)
        if self.on_device_toggles_changed:
            self.on_device_toggles_changed(self.active_verb, set(self.checked_devices))

    def _on_ucm_row_selected(self, sel: Gtk.TreeSelection) -> None:
        if self._suppress_signals:
            return
        model, it = sel.get_selected()
        if not it:
            return
        kind = model.get_value(it, 4)
        key = model.get_value(it, 5)
        if kind in ("boot", "verb", "device", "modifier") and self.on_ucm_selection_changed:
            self.on_ucm_selection_changed(kind, self.active_verb, key)

    def _on_add_device_clicked(self, _btn: Gtk.Button) -> None:
        if self.on_add_device_requested and self.active_verb:
            self.on_add_device_requested(self.active_verb)

    def _on_del_device_clicked(self, _btn: Gtk.Button) -> None:
        sel = self.ucm_tree.get_selection()
        model, it = sel.get_selected()
        if not it:
            return
        if model.get_value(it, 4) == "device":
            dname = model.get_value(it, 5)
            if self.on_delete_device_requested and self.active_verb:
                self.on_delete_device_requested(self.active_verb, dname)

    def _on_inv_selection_changed(self, sel: Gtk.TreeSelection) -> None:
        model, it = sel.get_selected()
        if not it:
            return
        nid = model.get_value(it, 2)
        if nid and self.on_inventory_node_selected:
            self.on_inventory_node_selected(nid)

    def _on_inv_row_activated(self, _tv: Gtk.TreeView, path: Gtk.TreePath, _col: Gtk.TreeViewColumn) -> None:
        it = self.inv_store.get_iter(path)
        if not it:
            return
        nid = self.inv_store.get_value(it, 2)
        if nid and self.on_inventory_node_selected:
            self.on_inventory_node_selected(nid)
