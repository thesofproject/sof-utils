#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright(c) 2026 Intel Corporation. All rights reserved.
"""
widgets_cairo.py — Hardware-accelerated GTK4 + Gtk.Snapshot + Cairo custom widget
for sof-top: UserDashboardCairoWidget
  - Moving Graph for DSP Core Utilization
  - Moving Graph for DSP Memory Utilization (SRAM + DRAM)
  - Unified DSP Task List with Resources & Status
"""

import collections
import math
import time
from typing import Deque, Dict, List, Optional, Tuple

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Graphene, Gsk, Gtk, Pango, PangoCairo

from backend import (
    ComponentPerfItem,
    CoreTelemetry,
    DspTaskItem,
    SramSegmentState,
    TelemetrySnapshot,
    SOF_CDEV_SLOT_TELEMETRY,
    SOF_CDEV_SLOT_SHELL,
    SOF_CDEV_SLOT_LLEXT_LOAD,
)


# Color Palette (Dark High-Contrast Engineering Theme)
BG_DARK = (0.07, 0.09, 0.12)
PANEL_BG = (0.10, 0.12, 0.16)
PANEL_BORDER = (0.20, 0.25, 0.32)
GRID_LINE = (1.0, 1.0, 1.0, 0.08)
TEXT_PRIMARY = (0.92, 0.94, 0.97)
TEXT_MUTED = (0.58, 0.65, 0.74)
ACCENT_GREEN = (0.18, 0.86, 0.48)
ACCENT_CYAN = (0.20, 0.76, 0.95)
ACCENT_AMBER = (0.96, 0.75, 0.20)
ACCENT_RED = (0.95, 0.30, 0.30)
ACCENT_VIOLET = (0.68, 0.46, 0.96)
ACCENT_BLUE = ACCENT_CYAN
ACCENT_PURPLE = ACCENT_VIOLET

CORE_COLORS: List[Tuple[float, float, float]] = [
    (0.18, 0.86, 0.48),  # Core 0: Emerald Green
    (0.20, 0.76, 0.95),  # Core 1: Electric Cyan
    (0.96, 0.75, 0.20),  # Core 2: Amber Gold
    (0.68, 0.46, 0.96),  # Core 3: Violet
    (0.95, 0.42, 0.62),  # Core 4: Pink
    (0.45, 0.90, 0.80),  # Core 5: Teal
]


def draw_pango_text(
    widget: Optional[Gtk.Widget],
    cr: cairo.Context,
    text: str,
    x: float,
    y: float,
    font_str: str = "Monospace 8.5",
    color: Tuple[float, ...] = TEXT_PRIMARY,
):
    if len(color) == 4:
        cr.set_source_rgba(*color)
    else:
        cr.set_source_rgb(*color)

    if widget is not None:
        layout = widget.create_pango_layout(text)
    else:
        layout = PangoCairo.create_layout(cr)
        layout.set_text(text, -1)
    layout.set_font_description(Pango.FontDescription(font_str))
    cr.move_to(x, y)
    PangoCairo.show_layout(cr, layout)


def draw_panel_box(
    cr: cairo.Context,
    x: float,
    y: float,
    w: float,
    h: float,
    bg: Tuple[float, float, float] = PANEL_BG,
    border: Tuple[float, float, float] = PANEL_BORDER,
):
    cr.set_source_rgb(*bg)
    cr.rectangle(x, y, w, h)
    cr.fill()
    cr.set_source_rgb(*border)
    cr.set_line_width(1.0)
    cr.rectangle(x + 0.5, y + 0.5, max(1.0, w - 1.0), max(1.0, h - 1.0))
    cr.stroke()


# =============================================================================
# 0. USER EDITION WIDGET: UserDashboardCairoWidget (sof-top)
# =============================================================================
class UserDashboardCairoWidget(Gtk.Widget):
    """
    Single-screen User Edition Dashboard for `sof-top`:
      1) Top-Left: Moving Graph for DSP Core Utilization + Live Per-Core Bars
      2) Top-Right: Moving Graph for DSP Memory Utilization (SRAM: HPSRAM+LPSRAM,
         DRAM: L3/IMR, and DSP Heaps) + Live Memory Bars
      3) Bottom: Unified DSP Task List with Resources & Status
    """

    def __init__(self, history_len: int = 300):
        super().__init__()
        self.history_len = history_len
        self.snap: TelemetrySnapshot = TelemetrySnapshot()
        self.core_pct_hist: Dict[int, Deque[float]] = collections.defaultdict(
            lambda: collections.deque(maxlen=history_len)
        )
        self.sram_kb_hist: Deque[float] = collections.deque(maxlen=history_len)
        self.hpsram_kb_hist: Deque[float] = collections.deque(maxlen=history_len)
        self.lpsram_kb_hist: Deque[float] = collections.deque(maxlen=history_len)
        self.dram_kb_hist: Deque[float] = collections.deque(maxlen=history_len)
        self.heap_kb_hist: Deque[float] = collections.deque(maxlen=history_len)
        self.set_size_request(960, 620)
        self.set_hexpand(True)
        self.set_vexpand(True)

    def update_snapshot(self, snap: TelemetrySnapshot):
        self.snap = snap
        for ct in snap.cores:
            self.core_pct_hist[ct.core_id].append(float(ct.cpu_load_pct))

        hp_kb = float(snap.hpsram.powered_banks * 128)
        lp_kb = float(snap.lpsram.powered_banks * 64)
        dram_kb = float(snap.dram_used_kb)
        heap_kb = float(snap.heap_allocated_bytes) / 1024.0

        self.sram_kb_hist.append(hp_kb + lp_kb)
        self.hpsram_kb_hist.append(hp_kb)
        self.lpsram_kb_hist.append(lp_kb)
        self.dram_kb_hist.append(dram_kb)
        self.heap_kb_hist.append(heap_kb)
        self.queue_draw()

    def do_snapshot(self, snapshot: Gtk.Snapshot):
        w = float(self.get_width())
        h = float(self.get_height())
        if w <= 10 or h <= 10:
            return
        rect = Graphene.Rect().init(0, 0, w, h)
        cr = snapshot.append_cairo(rect)
        self.render_to_cairo(cr, w, h, self)

    def render_to_cairo(
        self,
        cr: cairo.Context,
        w: float,
        h: float,
        widget: Optional[Gtk.Widget] = None,
    ):
        cr.set_source_rgb(*BG_DARK)
        cr.rectangle(0, 0, w, h)
        cr.fill()

        pad = 8.0
        top_h = max(270.0, (h - pad * 3) * 0.52)
        bot_h = max(180.0, h - top_h - pad * 3)
        half_w = (w - pad * 3) * 0.5

        num_cores = max(1, len(self.snap.cores)) if self.snap.cores else 2
        clock_mhz = self.snap.dsp_clock_hz / 1e6
        clock_khz = max(1.0, self.snap.dsp_clock_hz / 1e3)

        # =====================================================================
        # 1. TOP-LEFT: Moving Graph — DSP Core Utilization
        # =====================================================================
        lx = pad
        ly = pad
        draw_panel_box(cr, lx, ly, half_w, top_h)

        pm_txt = "D0i0 ACTIVE" if self.snap.pm_runtime_active else "D3 SLEEP"
        pm_col = ACCENT_GREEN if self.snap.pm_runtime_active else ACCENT_AMBER
        draw_pango_text(
            widget,
            cr,
            f"DSP CORE UTILIZATION — {self.snap.platform.upper()} ({clock_mhz:.0f} MHz | {pm_txt})",
            lx + 10,
            ly + 6,
            "Monospace Bold 9",
            pm_col,
        )

        # Compact Per-Core Live Bars at top of left panel
        bar_area_y = ly + 26.0
        row_h = min(22.0, max(16.0, (top_h * 0.36) / max(1, num_cores)))
        for idx in range(num_cores):
            ct = (
                self.snap.cores[idx]
                if idx < len(self.snap.cores)
                else CoreTelemetry(core_id=idx, enabled=(idx == 0))
            )
            ry = bar_area_y + idx * row_h
            ccol = CORE_COLORS[idx % len(CORE_COLORS)]
            st_lbl = "ON " if ct.enabled else "OFF"
            draw_pango_text(
                widget,
                cr,
                f"C{ct.core_id}[{st_lbl}]",
                lx + 10,
                ry + 1,
                "Monospace Bold 8",
                ccol if ct.enabled else TEXT_MUTED,
            )

            bx = lx + 72.0
            bw = max(60.0, half_w - 72.0 - 215.0)
            bh = max(11.0, row_h - 5.0)
            by = ry + 2.0

            cr.set_source_rgb(0.05, 0.06, 0.08)
            cr.rectangle(bx, by, bw, bh)
            cr.fill()

            eff_kcps = ct.avg_kcps if ct.avg_kcps > 0 else ct.budget_kcps
            pct = min(1.0, max(0.0, ct.cpu_load_pct / 100.0))
            if pct > 0:
                cr.set_source_rgb(*ccol)
                cr.rectangle(bx, by, bw * pct, bh)
                cr.fill()

            cr.set_source_rgba(1.0, 1.0, 1.0, 0.18)
            cr.set_line_width(1.0)
            cr.rectangle(bx + 0.5, by + 0.5, bw - 1.0, bh - 1.0)
            cr.stroke()

            info_s = f"{ct.cpu_load_pct:5.1f}% ({eff_kcps:6,d} KCPS | {ct.ticks_per_sec:4.0f} t/s)"
            draw_pango_text(
                widget,
                cr,
                info_s,
                bx + bw + 8.0,
                ry + 1,
                "Monospace 7.8",
                TEXT_PRIMARY if ct.enabled else TEXT_MUTED,
            )

        # Moving Graph for DSP Core Utilization
        cplot_x = lx + 48.0
        cplot_y = bar_area_y + num_cores * row_h + 18.0
        cplot_w = max(40.0, half_w - 60.0)
        cplot_h = max(50.0, (ly + top_h) - cplot_y - 12.0)

        draw_pango_text(
            widget,
            cr,
            "MOVING GRAPH: PER-CORE CPU UTILIZATION (60s ROLLING %)",
            lx + 10,
            cplot_y - 15.0,
            "Monospace Bold 7.8",
            ACCENT_CYAN,
        )

        cr.set_source_rgba(*GRID_LINE)
        cr.set_line_width(1.0)
        for i in range(5):
            gy_line = cplot_y + i * (cplot_h / 4.0)
            cr.move_to(cplot_x, gy_line)
            cr.line_to(cplot_x + cplot_w, gy_line)
            cr.stroke()

        max_pct = 20.0
        for cid in range(num_cores):
            deq = self.core_pct_hist.get(cid)
            if deq:
                max_pct = max(max_pct, max(deq) * 1.25)
        max_pct = min(100.0, max(10.0, max_pct))

        draw_pango_text(
            widget, cr, f"{max_pct:4.0f}%", lx + 6, cplot_y - 2, "Monospace 7", TEXT_MUTED
        )
        draw_pango_text(
            widget,
            cr,
            f"{max_pct*0.5:4.0f}%",
            lx + 6,
            cplot_y + cplot_h * 0.5 - 6,
            "Monospace 7",
            TEXT_MUTED,
        )
        draw_pango_text(
            widget, cr, "   0%", lx + 6, cplot_y + cplot_h - 10, "Monospace 7", TEXT_MUTED
        )

        for cid in range(num_cores):
            samples = list(self.core_pct_hist.get(cid, []))
            if len(samples) < 2:
                continue
            ccol = CORE_COLORS[cid % len(CORE_COLORS)]
            dx = cplot_w / max(1, len(samples) - 1)
            pts = []
            for idx_s, val in enumerate(samples):
                px = cplot_x + idx_s * dx
                norm = min(1.0, max(0.0, val / max_pct))
                py = cplot_y + cplot_h - norm * cplot_h
                pts.append((px, py))

            cr.move_to(pts[0][0], cplot_y + cplot_h)
            for px, py in pts:
                cr.line_to(px, py)
            cr.line_to(pts[-1][0], cplot_y + cplot_h)
            cr.close_path()
            gfill = cairo.LinearGradient(0, cplot_y, 0, cplot_y + cplot_h)
            gfill.add_color_stop_rgba(0.0, ccol[0], ccol[1], ccol[2], 0.30)
            gfill.add_color_stop_rgba(1.0, ccol[0], ccol[1], ccol[2], 0.02)
            cr.set_source(gfill)
            cr.fill()

            cr.set_source_rgb(*ccol)
            cr.set_line_width(2.0)
            cr.move_to(pts[0][0], pts[0][1])
            for px, py in pts[1:]:
                cr.line_to(px, py)
            cr.stroke()

        # =====================================================================
        # 2. TOP-RIGHT: Moving Graph — DSP Memory Utilization (SRAM + DRAM)
        # =====================================================================
        rx = pad * 2 + half_w
        ry_panel = pad
        draw_panel_box(cr, rx, ry_panel, half_w, top_h)

        hp = self.snap.hpsram
        lp = self.snap.lpsram
        hp_pwr_kb = hp.powered_banks * 128.0
        hp_tot_kb = max(128.0, hp.total_banks * 128.0)
        lp_pwr_kb = lp.powered_banks * 64.0
        lp_tot_kb = max(64.0, lp.total_banks * 64.0)
        dram_used_kb = self.snap.dram_used_kb
        dram_tot_kb = max(512.0, self.snap.dram_total_kb)
        heap_used_kb = self.snap.heap_allocated_bytes / 1024.0
        heap_tot_kb = max(256.0, self.snap.heap_total_bytes / 1024.0)

        draw_pango_text(
            widget,
            cr,
            f"DSP MEMORY UTILIZATION (SRAM + DRAM) — Total SRAM: {hp_pwr_kb + lp_pwr_kb:,.0f}/{hp_tot_kb + lp_tot_kb:,.0f} KB | DRAM: {dram_used_kb:,.0f}/{dram_tot_kb:,.0f} KB",
            rx + 10,
            ry_panel + 6,
            "Monospace Bold 9",
            ACCENT_GREEN,
        )

        # 4 Memory Segment Bars: HPSRAM, LPSRAM, DRAM (L3/IMR), DSP Heaps
        mem_rows = [
            ("HPSRAM (SRAM)", hp_pwr_kb, hp_tot_kb, ACCENT_GREEN, f"{hp.powered_banks}/{hp.total_banks} banks ON"),
            ("LPSRAM (SRAM)", lp_pwr_kb, lp_tot_kb, ACCENT_CYAN, f"{lp.powered_banks}/{lp.total_banks} banks ON"),
            ("DRAM (L3/IMR)", dram_used_kb, dram_tot_kb, ACCENT_VIOLET, f"HWM {self.snap.dram_hwm_kb:,.0f} KB"),
            ("DSP Heaps", heap_used_kb, heap_tot_kb, ACCENT_AMBER, f"Free {self.snap.heap_free_bytes/1024.0:,.0f} KB"),
        ]
        m_row_h = min(21.0, max(16.0, (top_h * 0.36) / len(mem_rows)))
        for m_i, (mlbl, m_use, m_tot, mcol, mextra) in enumerate(mem_rows):
            my = bar_area_y + m_i * m_row_h
            draw_pango_text(widget, cr, f"{mlbl:<13}", rx + 10, my + 1, "Monospace Bold 7.8", mcol)

            mbx = rx + 112.0
            mbw = max(60.0, half_w - 112.0 - 215.0)
            mbh = max(11.0, m_row_h - 5.0)
            mby = my + 2.0

            cr.set_source_rgb(0.05, 0.06, 0.08)
            cr.rectangle(mbx, mby, mbw, mbh)
            cr.fill()

            mfrac = min(1.0, max(0.0, m_use / max(1.0, m_tot)))
            if mfrac > 0:
                cr.set_source_rgb(*mcol)
                cr.rectangle(mbx, mby, mbw * mfrac, mbh)
                cr.fill()

            cr.set_source_rgba(1.0, 1.0, 1.0, 0.18)
            cr.set_line_width(1.0)
            cr.rectangle(mbx + 0.5, mby + 0.5, mbw - 1.0, mbh - 1.0)
            cr.stroke()

            minfo = f"{m_use:5.0f}/{m_tot:5.0f} KB ({mfrac*100:4.1f}%) | {mextra}"
            draw_pango_text(widget, cr, minfo, mbx + mbw + 8.0, my + 1, "Monospace 7.5", TEXT_PRIMARY)

        # Moving Graph for DSP Memory Utilization (SRAM + DRAM)
        mplot_x = rx + 52.0
        mplot_y = bar_area_y + len(mem_rows) * m_row_h + 18.0
        mplot_w = max(40.0, half_w - 64.0)
        mplot_h = max(50.0, (ry_panel + top_h) - mplot_y - 12.0)

        draw_pango_text(
            widget,
            cr,
            "MOVING GRAPH: HPSRAM (Green) | LPSRAM (Cyan) | DRAM/IMR (Violet) | Heaps (Amber)",
            rx + 10,
            mplot_y - 15.0,
            "Monospace Bold 7.6",
            ACCENT_CYAN,
        )

        cr.set_source_rgba(*GRID_LINE)
        cr.set_line_width(1.0)
        for i in range(5):
            gy_line = mplot_y + i * (mplot_h / 4.0)
            cr.move_to(mplot_x, gy_line)
            cr.line_to(mplot_x + mplot_w, gy_line)
            cr.stroke()

        y_max_kb = float(max(1024.0, hp_tot_kb, dram_used_kb * 1.3))
        draw_pango_text(
            widget, cr, f"{y_max_kb:4.0f}K", rx + 6, mplot_y - 2, "Monospace 7", TEXT_MUTED
        )
        draw_pango_text(
            widget,
            cr,
            f"{y_max_kb*0.5:4.0f}K",
            rx + 6,
            mplot_y + mplot_h * 0.5 - 6,
            "Monospace 7",
            TEXT_MUTED,
        )
        draw_pango_text(
            widget, cr, "   0K", rx + 6, mplot_y + mplot_h - 10, "Monospace 7", TEXT_MUTED
        )

        for series, col in (
            (self.hpsram_kb_hist, ACCENT_GREEN),
            (self.dram_kb_hist, ACCENT_VIOLET),
            (self.heap_kb_hist, ACCENT_AMBER),
            (self.lpsram_kb_hist, ACCENT_CYAN),
        ):
            s_list = list(series)
            if len(s_list) < 2:
                continue
            dx = mplot_w / max(1, len(s_list) - 1)
            pts = []
            for idx_s, val in enumerate(s_list):
                px = mplot_x + idx_s * dx
                py = mplot_y + mplot_h - min(1.0, max(0.0, val / y_max_kb)) * mplot_h
                pts.append((px, py))

            cr.move_to(pts[0][0], mplot_y + mplot_h)
            for px, py in pts:
                cr.line_to(px, py)
            cr.line_to(pts[-1][0], mplot_y + mplot_h)
            cr.close_path()
            cr.set_source_rgba(col[0], col[1], col[2], 0.16)
            cr.fill()

            cr.set_source_rgb(*col)
            cr.set_line_width(2.0)
            cr.move_to(pts[0][0], pts[0][1])
            for px, py in pts[1:]:
                cr.line_to(px, py)
            cr.stroke()

        # =====================================================================
        # 3. BOTTOM: Unified DSP Task List (Resources & Status)
        # =====================================================================
        by_box = pad * 2 + top_h
        bw_box = w - pad * 2
        draw_panel_box(cr, pad, by_box, bw_box, bot_h)

        tasks = self.snap.tasks
        running_cnt = sum(1 for t in tasks if t.status == "RUNNING")
        draw_pango_text(
            widget,
            cr,
            f"DSP TASK LIST (RESOURCES & STATUS) — {len(tasks)} Tasks ({running_cnt} Running | FW: {self.snap.fw_filename} v{self.snap.fw_version} [{self.snap.fw_state_name}])",
            pad + 10,
            by_box + 6,
            "Monospace Bold 9",
            ACCENT_GREEN,
        )

        th_y = by_box + 26.0
        col_hdr = (
            f"{'TASK ID':<10} {'TASK / MODULE NAME':<20} {'TYPE':<12} {'CORE':<5} "
            f"{'STATUS':<10} {'CPU %':>7}   {'CPU BAR':<12} {'AVG KCPS':>9} {'PEAK KCPS':>9}   "
            f"{'MEMORY / STACK RESOURCE':<24} {'ITERS / CYCLES':>14} {'XRUNS':>6}"
        )
        draw_pango_text(widget, cr, col_hdr, pad + 12, th_y, "Monospace Bold 8", ACCENT_CYAN)

        cr.set_source_rgba(*GRID_LINE)
        cr.move_to(pad + 8, th_y + 15)
        cr.line_to(w - pad - 8, th_y + 15)
        cr.stroke()

        row_y = th_y + 19.0
        row_step = 18.0
        max_rows = max(1, int((bot_h - (row_y - by_box) - 6.0) // row_step))

        for idx_t, t in enumerate(tasks[:max_rows]):
            if idx_t % 2 == 0:
                cr.set_source_rgba(1.0, 1.0, 1.0, 0.025)
                cr.rectangle(pad + 6, row_y - 1, bw_box - 12, row_step - 1)
                cr.fill()

            if t.status in ("XRUN!", "CPC_VIOL"):
                st_col = ACCENT_RED
            elif t.status == "RUNNING":
                st_col = ACCENT_GREEN
            elif t.status in ("READY", "ACTIVE"):
                st_col = ACCENT_CYAN
            else:
                st_col = TEXT_MUTED

            # Build inline ASCII bar for CPU load
            filled = int( round(min(1.0, max(0.0, t.cpu_pct / 25.0)) * 10) )
            bar_str = "[" + ("#" * filled).ljust(10, ".") + "]"

            row_s = (
                f"{t.task_id:<10.10} {t.name:<20.20} {t.task_type:<12.12} C{t.core_id:<4d} "
                f"{t.status:<10.10} {t.cpu_pct:6.1f}%   {bar_str} {t.avg_kcps:9,d} {t.peak_kcps:9,d}   "
                f"{t.mem_desc:<24.24} {t.iterations:14,d} {t.xruns:6d}"
            )
            draw_pango_text(widget, cr, row_s, pad + 12, row_y, "Monospace 8", st_col)
            row_y += row_step

