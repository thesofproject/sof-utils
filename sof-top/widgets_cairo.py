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
    compute_sram_segment_metrics,
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
    (1.00, 0.52, 0.20),  # Core 5: Orange
    (0.65, 0.89, 0.18),  # Core 6: Lime
    (0.44, 0.58, 1.00),  # Core 7: Cobalt Blue
]


def _make_pango_layout(
    widget: Optional[Gtk.Widget],
    cr: cairo.Context,
    text: str,
    font_str: str = "Monospace 8.5",
):
    if widget is not None:
        layout = widget.create_pango_layout(text)
    else:
        layout = PangoCairo.create_layout(cr)
        layout.set_text(text, -1)
    layout.set_font_description(Pango.FontDescription(font_str))
    return layout


def measure_pango_text(
    widget: Optional[Gtk.Widget],
    cr: cairo.Context,
    text: str,
    font_str: str = "Monospace 8.5",
) -> Tuple[int, int]:
    return _make_pango_layout(widget, cr, text, font_str).get_pixel_size()


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

    layout = _make_pango_layout(widget, cr, text, font_str)
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
# 0. USER EDITION RENDERER & WIDGET: UserDashboardRenderer / UserDashboardCairoWidget
# =============================================================================
class UserDashboardRenderer:
    """
    Display-independent state, 60-second timestamped history, scrollable task
    state, and Cairo renderer for `sof-top`. Can be used directly in headless
    CI environments (`--render-png`) without constructing a `Gtk.Widget`.
    """

    def __init__(self, window_seconds: float = 60.0, max_samples: int = 1200):
        self.window_seconds = float(window_seconds)
        self.max_samples = max_samples
        self.snap: TelemetrySnapshot = TelemetrySnapshot()
        self.core_pct_hist: Dict[int, Deque[Tuple[float, float]]] = collections.defaultdict(
            lambda: collections.deque(maxlen=max_samples)
        )
        self.sram_kb_hist: Deque[Tuple[float, float]] = collections.deque(maxlen=max_samples)
        self.hpsram_kb_hist: Deque[Tuple[float, float]] = collections.deque(maxlen=max_samples)
        self.lpsram_kb_hist: Deque[Tuple[float, float]] = collections.deque(maxlen=max_samples)
        self.dram_kb_hist: Deque[Tuple[float, float]] = collections.deque(maxlen=max_samples)
        self.heap_kb_hist: Deque[Tuple[float, float]] = collections.deque(maxlen=max_samples)
        self.task_scroll_offset: int = 0
        self.last_max_rows: int = 10
        self.start_ts: Optional[float] = None
        self.last_ts: Optional[float] = None

    def _prune_old(self, deq: Deque[Tuple[float, float]], cutoff: float):
        while len(deq) > 2 and deq[1][0] < cutoff:
            deq.popleft()

    def scroll_tasks(self, delta_rows: int) -> bool:
        total = len(self.snap.tasks)
        max_off = max(0, total - max(1, self.last_max_rows))
        new_off = max(0, min(max_off, self.task_scroll_offset + delta_rows))
        if new_off != self.task_scroll_offset:
            self.task_scroll_offset = new_off
            return True
        return False

    @staticmethod
    def _sram_segment_metrics(seg: SramSegmentState, bank_kb: float) -> Tuple[float, float, float]:
        """Return (used_kb, powered_kb, total_kb) distinguishing page allocation from power gates."""
        return compute_sram_segment_metrics(seg, bank_kb)

    def update_snapshot(self, snap: TelemetrySnapshot):
        self.snap = snap
        ts = snap.timestamp if snap.timestamp > 0 else time.time()
        if self.start_ts is None:
            self.start_ts = ts
        self.last_ts = ts
        cutoff = ts - self.window_seconds

        for ct in snap.cores:
            deq = self.core_pct_hist[ct.core_id]
            deq.append((ts, float(ct.cpu_load_pct)))
            self._prune_old(deq, cutoff)

        hp_kb, _, _ = self._sram_segment_metrics(snap.hpsram, 128.0)
        lp_kb, _, _ = self._sram_segment_metrics(snap.lpsram, 64.0)
        dram_kb = float(snap.dram_used_kb)
        heap_kb = float(snap.heap_allocated_bytes) / 1024.0

        for deq, val in (
            (self.sram_kb_hist, hp_kb + lp_kb),
            (self.hpsram_kb_hist, hp_kb),
            (self.lpsram_kb_hist, lp_kb),
            (self.dram_kb_hist, dram_kb),
            (self.heap_kb_hist, heap_kb),
        ):
            deq.append((ts, val))
            self._prune_old(deq, cutoff)

        max_off = max(0, len(snap.tasks) - max(1, self.last_max_rows))
        if self.task_scroll_offset > max_off:
            self.task_scroll_offset = max_off

    def _draw_moving_time_xaxis(
        self,
        widget: Optional[Gtk.Widget],
        cr: cairo.Context,
        plot_x: float,
        plot_y: float,
        plot_w: float,
        plot_h: float,
        t_end: float,
        tick_step: float = 10.0,
    ):
        """Render vertical scrolling time gridlines and moving second labels on the X-axis."""
        start_ts = self.start_ts if self.start_ts is not None else t_end
        elapsed_end = max(0.0, t_end - start_ts)
        elapsed_start = elapsed_end - self.window_seconds
        base_y = plot_y + plot_h

        now_lbl = f"{elapsed_end:.0f}s"
        now_w, _ = measure_pango_text(widget, cr, now_lbl, "Monospace Bold 6.8")
        now_x = plot_x + plot_w - now_w
        draw_pango_text(widget, cr, now_lbl, now_x, base_y + 4.0, "Monospace Bold 6.8", ACCENT_CYAN)

        k_min = math.ceil(elapsed_start / tick_step)
        k_max = math.floor(elapsed_end / tick_step)
        for k in range(k_min, k_max + 1):
            tick_sec = k * tick_step
            x_frac = (tick_sec - elapsed_start) / self.window_seconds
            if x_frac < 0.0 or x_frac > 1.0:
                continue
            gx = plot_x + x_frac * plot_w

            cr.set_source_rgba(*GRID_LINE)
            cr.set_line_width(1.0)
            cr.move_to(gx, plot_y)
            cr.line_to(gx, base_y)
            cr.stroke()

            cr.set_source_rgba(1.0, 1.0, 1.0, 0.25)
            cr.move_to(gx, base_y)
            cr.line_to(gx, base_y + 3.5)
            cr.stroke()

            lbl = f"{int(round(tick_sec))}s"
            tw, _ = measure_pango_text(widget, cr, lbl, "Monospace 6.8")
            lx_txt = gx - tw * 0.5
            # Skip label if it would clip left edge or overlap live right-edge counter
            if lx_txt >= plot_x - 4.0 and (lx_txt + tw) < (now_x - 8.0):
                draw_pango_text(widget, cr, lbl, lx_txt, base_y + 4.0, "Monospace 6.8", TEXT_MUTED)

    def _plot_time_series(
        self,
        cr: cairo.Context,
        samples: List[Tuple[float, float]],
        plot_x: float,
        plot_y: float,
        plot_w: float,
        plot_h: float,
        t_end: float,
        y_max: float,
        col: Tuple[float, float, float],
        use_gradient: bool = False,
        fill_alpha: float = 0.16,
    ):
        """Plot a rolling-window time-series clipped to the chart rectangle."""
        if len(samples) < 2:
            return
        t_start = t_end - self.window_seconds
        pts = []
        for ts_s, val in samples:
            x_frac = (ts_s - t_start) / self.window_seconds
            px = plot_x + x_frac * plot_w
            norm = min(1.0, max(0.0, val / max(1e-6, y_max)))
            py = plot_y + plot_h - norm * plot_h
            pts.append((px, py))

        cr.save()
        cr.rectangle(plot_x, plot_y, plot_w, plot_h)
        cr.clip()

        cr.move_to(pts[0][0], plot_y + plot_h)
        for px, py in pts:
            cr.line_to(px, py)
        cr.line_to(pts[-1][0], plot_y + plot_h)
        cr.close_path()
        if use_gradient:
            gfill = cairo.LinearGradient(0, plot_y, 0, plot_y + plot_h)
            gfill.add_color_stop_rgba(0.0, col[0], col[1], col[2], 0.25)
            gfill.add_color_stop_rgba(1.0, col[0], col[1], col[2], 0.02)
            cr.set_source(gfill)
        else:
            cr.set_source_rgba(col[0], col[1], col[2], fill_alpha)
        cr.fill()

        cr.set_source_rgb(*col)
        cr.set_line_width(2.0)
        cr.move_to(pts[0][0], pts[0][1])
        for px, py in pts[1:]:
            cr.line_to(px, py)
        cr.stroke()
        cr.restore()

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

        # Compact Per-Core Live Bars at top of left panel (Pango-measured so text stays inside panel)
        bar_area_y = ly + 26.0
        row_h = min(22.0, max(16.0, (top_h * 0.36) / max(1, num_cores)))
        core_rows = []
        max_clbl_w = 54.0
        max_cinfo_w = 180.0
        for idx in range(num_cores):
            ct = (
                self.snap.cores[idx]
                if idx < len(self.snap.cores)
                else CoreTelemetry(core_id=idx, enabled=(idx == 0))
            )
            st_lbl = "ON " if ct.enabled else "OFF"
            clbl = f"C{ct.core_id}[{st_lbl}]"
            eff_kcps = ct.avg_kcps if ct.avg_kcps > 0 else ct.budget_kcps
            info_s = f"{ct.cpu_load_pct:5.1f}% ({eff_kcps:6,d} KCPS | {ct.ticks_per_sec:4.0f} t/s)"
            lw, _ = measure_pango_text(widget, cr, clbl, "Monospace Bold 8")
            iw, _ = measure_pango_text(widget, cr, info_s, "Monospace 7.8")
            max_clbl_w = max(max_clbl_w, float(lw))
            max_cinfo_w = max(max_cinfo_w, float(iw))
            core_rows.append((ct, clbl, info_s))

        bx = lx + 10.0 + max_clbl_w + 8.0
        bw = max(24.0, (lx + half_w - 10.0) - bx - 8.0 - max_cinfo_w)
        cr.save()
        cr.rectangle(lx + 1.0, ly + 1.0, max(1.0, half_w - 2.0), max(1.0, top_h - 2.0))
        cr.clip()

        for idx, (ct, clbl, info_s) in enumerate(core_rows):
            ry = bar_area_y + idx * row_h
            ccol = CORE_COLORS[idx % len(CORE_COLORS)]
            draw_pango_text(
                widget,
                cr,
                clbl,
                lx + 10,
                ry + 1,
                "Monospace Bold 8",
                ccol if ct.enabled else TEXT_MUTED,
            )

            bh = max(11.0, row_h - 5.0)
            by = ry + 2.0

            cr.set_source_rgb(0.05, 0.06, 0.08)
            cr.rectangle(bx, by, bw, bh)
            cr.fill()

            pct = min(1.0, max(0.0, ct.cpu_load_pct / 100.0))
            if pct > 0:
                cr.set_source_rgb(*ccol)
                cr.rectangle(bx, by, bw * pct, bh)
                cr.fill()

            cr.set_source_rgba(1.0, 1.0, 1.0, 0.18)
            cr.set_line_width(1.0)
            cr.rectangle(bx + 0.5, by + 0.5, bw - 1.0, bh - 1.0)
            cr.stroke()

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
        cplot_h = max(50.0, (ly + top_h) - cplot_y - 24.0)

        leg_w = max(18.0, num_cores * 27.0)
        mcps_title = "MOVING GRAPH: PER-CORE MCPS / CPU UTILIZATION (60s ROLLING %)"
        tw_full, _ = measure_pango_text(widget, cr, mcps_title, "Monospace Bold 7.8")
        if (lx + 10.0 + tw_full + 10.0) > (lx + half_w - leg_w - 10.0):
            mcps_title = "PER-CORE MCPS / CPU % (60s)"
        draw_pango_text(
            widget,
            cr,
            mcps_title,
            lx + 10,
            cplot_y - 15.0,
            "Monospace Bold 7.8",
            ACCENT_CYAN,
        )
        # Compact colored legend for every core on the graph header row
        leg_x = lx + half_w - leg_w - 10.0
        for cid in range(num_cores):
            ccol = CORE_COLORS[cid % len(CORE_COLORS)]
            cr.set_source_rgb(*ccol)
            cr.rectangle(leg_x, cplot_y - 11.0, 6.0, 6.0)
            cr.fill()
            draw_pango_text(widget, cr, f"C{cid}", leg_x + 8.0, cplot_y - 14.5, "Monospace Bold 7.2", ccol)
            leg_x += 27.0

        cr.set_source_rgba(*GRID_LINE)
        cr.set_line_width(1.0)
        for i in range(5):
            gy_line = cplot_y + i * (cplot_h / 4.0)
            cr.move_to(cplot_x, gy_line)
            cr.line_to(cplot_x + cplot_w, gy_line)
            cr.stroke()

        draw_pango_text(
            widget, cr, "100%", lx + 6, cplot_y - 2, "Monospace 7", TEXT_MUTED
        )
        draw_pango_text(
            widget,
            cr,
            " 50%",
            lx + 6,
            cplot_y + cplot_h * 0.5 - 6,
            "Monospace 7",
            TEXT_MUTED,
        )
        draw_pango_text(
            widget, cr, "  0%", lx + 6, cplot_y + cplot_h - 10, "Monospace 7", TEXT_MUTED
        )

        t_now = self.last_ts if self.last_ts is not None else time.time()
        self._draw_moving_time_xaxis(widget, cr, cplot_x, cplot_y, cplot_w, cplot_h, t_now)

        # Draw lower-activity cores first so active traces stay clearly visible on top
        draw_order = sorted(
            range(num_cores),
            key=lambda c: (
                max((v for _, v in self.core_pct_hist.get(c, ())), default=0.0),
                -c,
            ),
        )
        for cid in draw_order:
            samples = list(self.core_pct_hist.get(cid, []))
            self._plot_time_series(
                cr,
                samples,
                cplot_x,
                cplot_y,
                cplot_w,
                cplot_h,
                t_now,
                100.0,
                CORE_COLORS[cid % len(CORE_COLORS)],
                use_gradient=True,
            )
        cr.restore()

        # =====================================================================
        # 2. TOP-RIGHT: Moving Graph — DSP Memory Utilization (SRAM + DRAM)
        # =====================================================================
        rx = pad * 2 + half_w
        ry_panel = pad
        draw_panel_box(cr, rx, ry_panel, half_w, top_h)

        hp = self.snap.hpsram
        lp = self.snap.lpsram
        hp_used_kb, hp_pwr_kb, hp_tot_kb = self._sram_segment_metrics(hp, 128.0)
        lp_used_kb, lp_pwr_kb, lp_tot_kb = self._sram_segment_metrics(lp, 64.0)
        dram_used_kb = self.snap.dram_used_kb
        dram_tot_kb = self.snap.dram_total_kb
        heap_used_kb = self.snap.heap_allocated_bytes / 1024.0
        heap_tot_kb = self.snap.heap_total_bytes / 1024.0

        sram_tot_kb = hp_tot_kb + lp_tot_kb
        if sram_tot_kb > 0:
            sram_hdr = f"SRAM Used: {hp_used_kb + lp_used_kb:,.0f}/{sram_tot_kb:,.0f} KB (Pwr {hp_pwr_kb + lp_pwr_kb:,.0f} KB)"
        else:
            sram_hdr = "SRAM: N/A (no TEL2/cdev)"
        dram_hdr = (
            f"DRAM: {dram_used_kb:,.0f}/{dram_tot_kb:,.0f} KB"
            if dram_tot_kb > 0
            else "DRAM: N/A"
        )
        cr.save()
        cr.rectangle(rx + 1.0, ry_panel + 1.0, max(1.0, half_w - 2.0), max(1.0, top_h - 2.0))
        cr.clip()

        draw_pango_text(
            widget,
            cr,
            f"DSP MEMORY UTILIZATION — {sram_hdr} | {dram_hdr}",
            rx + 10,
            ry_panel + 6,
            "Monospace Bold 8.6",
            ACCENT_GREEN,
        )

        # 4 Memory Segment Bars: HPSRAM, LPSRAM, DRAM (L3/IMR), DSP Heaps
        mem_rows = [
            (
                "HPSRAM (SRAM)",
                hp_used_kb,
                hp_pwr_kb,
                hp_tot_kb,
                ACCENT_GREEN,
                f"{hp.powered_banks}/{hp.total_banks} banks ON" if hp.total_banks > 0 else "no TEL2/cdev",
            ),
            (
                "LPSRAM (SRAM)",
                lp_used_kb,
                lp_pwr_kb,
                lp_tot_kb,
                ACCENT_CYAN,
                f"{lp.powered_banks}/{lp.total_banks} banks ON" if lp.total_banks > 0 else "no TEL2/cdev",
            ),
            (
                "DRAM (L3/IMR)",
                dram_used_kb,
                0.0,
                dram_tot_kb,
                ACCENT_VIOLET,
                f"HWM {self.snap.dram_hwm_kb:,.0f} KB" if dram_tot_kb > 0 else "no TEL2/cdev",
            ),
            (
                "DSP Heaps",
                heap_used_kb,
                0.0,
                heap_tot_kb,
                ACCENT_AMBER,
                f"Free {self.snap.heap_free_bytes/1024.0:,.0f} KB" if heap_tot_kb > 0 else "no TEL2/cdev",
            ),
        ]
        # Measure actual label & readout pixel widths via Pango so minfo never overflows panel/window
        mem_rendered = []
        max_mlbl_w = 90.0
        max_minfo_w = 180.0
        for mlbl, m_use, m_pwr, m_tot, mcol, mextra in mem_rows:
            mfrac = min(1.0, max(0.0, m_use / m_tot)) if m_tot > 0 else 0.0
            if m_tot > 0:
                minfo = f"{m_use:,.0f}/{m_tot:,.0f} KB ({mfrac*100:4.1f}%) | {mextra}"
            else:
                minfo = f"N/A | {mextra}"
            lw, _ = measure_pango_text(widget, cr, f"{mlbl:<13}", "Monospace Bold 7.8")
            iw, _ = measure_pango_text(widget, cr, minfo, "Monospace 7.5")
            max_mlbl_w = max(max_mlbl_w, float(lw))
            max_minfo_w = max(max_minfo_w, float(iw))
            mem_rendered.append((mlbl, m_use, m_pwr, m_tot, mcol, mfrac, minfo))

        mbx = rx + 10.0 + max_mlbl_w + 8.0
        mbw = max(24.0, (rx + half_w - 10.0) - mbx - 8.0 - max_minfo_w)
        m_row_h = min(21.0, max(16.0, (top_h * 0.36) / len(mem_rows)))
        for m_i, (mlbl, m_use, m_pwr, m_tot, mcol, mfrac, minfo) in enumerate(mem_rendered):
            my = bar_area_y + m_i * m_row_h
            draw_pango_text(widget, cr, f"{mlbl:<13}", rx + 10, my + 1, "Monospace Bold 7.8", mcol)

            mbh = max(11.0, m_row_h - 5.0)
            mby = my + 2.0

            cr.set_source_rgb(0.05, 0.06, 0.08)
            cr.rectangle(mbx, mby, mbw, mbh)
            cr.fill()

            pfrac = min(1.0, max(0.0, m_pwr / m_tot)) if m_tot > 0 else 0.0
            if pfrac > 0:
                cr.set_source_rgba(mcol[0], mcol[1], mcol[2], 0.22)
                cr.rectangle(mbx, mby, mbw * pfrac, mbh)
                cr.fill()

            if mfrac > 0:
                cr.set_source_rgb(*mcol)
                cr.rectangle(mbx, mby, mbw * mfrac, mbh)
                cr.fill()

            cr.set_source_rgba(1.0, 1.0, 1.0, 0.18)
            cr.set_line_width(1.0)
            cr.rectangle(mbx + 0.5, mby + 0.5, mbw - 1.0, mbh - 1.0)
            cr.stroke()

            draw_pango_text(widget, cr, minfo, mbx + mbw + 8.0, my + 1, "Monospace 7.5", TEXT_PRIMARY)

        # Moving Graph for DSP Memory Utilization (SRAM + DRAM)
        mplot_x = rx + 52.0
        mplot_y = bar_area_y + len(mem_rows) * m_row_h + 18.0
        mplot_w = max(40.0, half_w - 64.0)
        mplot_h = max(50.0, (ry_panel + top_h) - mplot_y - 24.0)

        draw_pango_text(
            widget,
            cr,
            "MOVING GRAPH (60s): HPSRAM (Green) | LPSRAM (Cyan) | DRAM/IMR (Violet) | Heaps (Amber)",
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

        y_max_kb = float(max(1024.0, hp_tot_kb, dram_used_kb * 1.3, heap_used_kb * 1.3))
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

        self._draw_moving_time_xaxis(widget, cr, mplot_x, mplot_y, mplot_w, mplot_h, t_now)

        for series, col in (
            (self.hpsram_kb_hist, ACCENT_GREEN),
            (self.dram_kb_hist, ACCENT_VIOLET),
            (self.heap_kb_hist, ACCENT_AMBER),
            (self.lpsram_kb_hist, ACCENT_CYAN),
        ):
            self._plot_time_series(
                cr,
                list(series),
                mplot_x,
                mplot_y,
                mplot_w,
                mplot_h,
                t_now,
                y_max_kb,
                col,
                fill_alpha=0.16,
            )
        cr.restore()

        # =====================================================================
        # 3. BOTTOM: Unified Scrollable DSP Task List (Resources & Status)
        # =====================================================================
        by_box = pad * 2 + top_h
        bw_box = w - pad * 2
        draw_panel_box(cr, pad, by_box, bw_box, bot_h)

        tasks = self.snap.tasks
        running_cnt = sum(1 for t in tasks if t.status == "RUNNING")

        th_y = by_box + 26.0
        row_y = th_y + 19.0
        row_step = 18.0
        max_rows = max(1, int((bot_h - (row_y - by_box) - 6.0) // row_step))
        self.last_max_rows = max_rows

        max_off = max(0, len(tasks) - max_rows)
        start_idx = max(0, min(self.task_scroll_offset, max_off))
        end_idx = min(len(tasks), start_idx + max_rows)
        range_str = (
            f"Showing {start_idx + 1}..{end_idx} of {len(tasks)} [Mouse Wheel / PgUp / PgDn]"
            if len(tasks) > max_rows
            else f"{len(tasks)} Tasks"
        )

        draw_pango_text(
            widget,
            cr,
            f"DSP TASK LIST (RESOURCES & STATUS) — {range_str} ({running_cnt} Running | FW: {self.snap.fw_filename} v{self.snap.fw_version} [{self.snap.fw_state_name}])",
            pad + 10,
            by_box + 6,
            "Monospace Bold 9",
            ACCENT_GREEN,
        )

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

        for idx_t, t in enumerate(tasks[start_idx:end_idx]):
            if (start_idx + idx_t) % 2 == 0:
                cr.set_source_rgba(1.0, 1.0, 1.0, 0.025)
                cr.rectangle(pad + 6, row_y - 1, bw_box - 18, row_step - 1)
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
            filled = int(round(min(1.0, max(0.0, t.cpu_pct / 25.0)) * 10))
            bar_str = "[" + ("#" * filled).ljust(10, ".") + "]"

            row_s = (
                f"{t.task_id:<10.10} {t.name:<20.20} {t.task_type:<12.12} C{t.core_id:<4d} "
                f"{t.status:<10.10} {t.cpu_pct:6.1f}%   {bar_str} {t.avg_kcps:9,d} {t.peak_kcps:9,d}   "
                f"{t.mem_desc:<24.24} {t.iterations:14,d} {t.xruns:6d}"
            )
            draw_pango_text(widget, cr, row_s, pad + 12, row_y, "Monospace 8", st_col)
            row_y += row_step

        # Draw vertical scrollbar when tasks exceed visible rows
        if len(tasks) > max_rows:
            sb_x = pad + bw_box - 10.0
            sb_y = th_y + 18.0
            sb_h = max(24.0, bot_h - (sb_y - by_box) - 8.0)
            cr.set_source_rgba(1.0, 1.0, 1.0, 0.08)
            cr.rectangle(sb_x, sb_y, 5.0, sb_h)
            cr.fill()
            thumb_h = max(16.0, sb_h * (max_rows / float(len(tasks))))
            thumb_y = sb_y + (sb_h - thumb_h) * (start_idx / float(max(1, max_off)))
            cr.set_source_rgb(*ACCENT_CYAN)
            cr.rectangle(sb_x, thumb_y, 5.0, thumb_h)
            cr.fill()


class UserDashboardCairoWidget(Gtk.Widget):
    """
    Single-screen User Edition GTK4 Widget for `sof-top` wrapping `UserDashboardRenderer`.
    Supports mouse-wheel and keyboard scrolling through the DSP Task List.
    """

    def __init__(self, window_seconds: float = 60.0):
        super().__init__()
        self.renderer = UserDashboardRenderer(window_seconds=window_seconds)
        self.set_size_request(960, 620)
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_focusable(True)

        scroll_ctrl = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        scroll_ctrl.connect("scroll", self._on_scroll)
        self.add_controller(scroll_ctrl)

        key_ctrl = Gtk.EventControllerKey.new()
        key_ctrl.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_ctrl)

    @property
    def snap(self) -> TelemetrySnapshot:
        return self.renderer.snap

    def scroll_tasks(self, delta_rows: int):
        if self.renderer.scroll_tasks(delta_rows):
            self.queue_draw()

    def _on_scroll(self, _ctrl, _dx: float, dy: float) -> bool:
        step = 3 if dy > 0 else (-3 if dy < 0 else 0)
        if step != 0:
            self.scroll_tasks(step)
            return True
        return False

    def _on_key_pressed(self, _ctrl, keyval: int, _keycode: int, _state) -> bool:
        page = max(1, self.renderer.last_max_rows - 1)
        if keyval == Gdk.KEY_Down:
            self.scroll_tasks(1)
            return True
        if keyval == Gdk.KEY_Up:
            self.scroll_tasks(-1)
            return True
        if keyval == Gdk.KEY_Page_Down:
            self.scroll_tasks(page)
            return True
        if keyval == Gdk.KEY_Page_Up:
            self.scroll_tasks(-page)
            return True
        if keyval == Gdk.KEY_Home:
            self.scroll_tasks(-10000)
            return True
        if keyval == Gdk.KEY_End:
            self.scroll_tasks(10000)
            return True
        return False

    def update_snapshot(self, snap: TelemetrySnapshot):
        self.renderer.update_snapshot(snap)
        self.queue_draw()

    def do_snapshot(self, snapshot: Gtk.Snapshot):
        w = float(self.get_width())
        h = float(self.get_height())
        if w <= 10 or h <= 10:
            return
        rect = Graphene.Rect().init(0, 0, w, h)
        cr = snapshot.append_cairo(rect)
        self.renderer.render_to_cairo(cr, w, h, self)

    def render_to_cairo(
        self,
        cr: cairo.Context,
        w: float,
        h: float,
        widget: Optional[Gtk.Widget] = None,
    ):
        self.renderer.render_to_cairo(cr, w, h, widget)

