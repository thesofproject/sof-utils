# SPDX-License-Identifier: BSD-3-Clause
"""Hardware-accelerated GTK4 Graph Canvas (Gtk.Snapshot + Gsk + cached Pango) + Cairo PNG Renderer."""

from __future__ import annotations
import math
from typing import Callable, Dict, List, Optional, Tuple

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, GLib, Graphene, Gsk, Gtk, Pango, PangoCairo

from .layout import (
    CTRL_GROUP_HDR,
    CTRL_GROUP_PAD,
    update_container_bounds,
)
from .model import (
    GraphEdge,
    GraphNode,
    KcontrolInfo,
    PipelineContainer,
    UcmGraphViewModel,
)


# Color tuples (r, g, b, a) matching sof-builder/sof_builder/graph_canvas.py
CATEGORY_COLORS: Dict[str, Tuple[Tuple[float, float, float, float], Tuple[float, float, float, float]]] = {
    "host": ((0.14, 0.42, 0.78, 1.0), (0.11, 0.16, 0.24, 0.96)),
    "widget": ((0.12, 0.56, 0.52, 1.0), (0.11, 0.19, 0.20, 0.96)),
    "mix": ((0.78, 0.43, 0.12, 1.0), (0.22, 0.16, 0.11, 0.96)),
    "dai": ((0.20, 0.62, 0.28, 1.0), (0.11, 0.20, 0.14, 0.96)),
    "pcm": ((0.45, 0.32, 0.78, 1.0), (0.16, 0.13, 0.24, 0.96)),
    "codec": ((0.25, 0.48, 0.72, 1.0), (0.12, 0.17, 0.23, 0.96)),
    "hw_endpoint": ((0.15, 0.62, 0.42, 1.0), (0.11, 0.18, 0.16, 0.97)),
    "control_mixer": ((0.62, 0.26, 0.88, 1.0), (0.18, 0.12, 0.25, 0.96)),
    "control_switch": ((0.86, 0.56, 0.12, 1.0), (0.24, 0.18, 0.10, 0.96)),
    "control_bytes": ((0.12, 0.66, 0.84, 1.0), (0.10, 0.19, 0.24, 0.96)),
    "control_enum": ((0.86, 0.24, 0.42, 1.0), (0.24, 0.11, 0.16, 0.96)),
}

BG_COLOR = (0.08, 0.09, 0.12, 1.0)
GRID_COLOR = (0.16, 0.18, 0.23, 1.0)
CONTAINER_FILL_PB = (0.14, 0.24, 0.38, 0.22)
CONTAINER_FILL_CAP = (0.14, 0.36, 0.26, 0.22)
CONTAINER_BORDER_PB = (0.28, 0.52, 0.82, 0.48)
CONTAINER_BORDER_CAP = (0.28, 0.72, 0.48, 0.48)
NODE_BORDER_NORMAL = (0.30, 0.35, 0.44, 0.90)
NODE_BORDER_SELECTED = (0.35, 0.78, 1.00, 1.00)
NODE_BORDER_POWERED = (0.22, 0.74, 0.97, 0.88)
NODE_BORDER_UCM = (0.18, 0.92, 0.62, 0.96)
NODE_SHADOW_SELECTED = (0.20, 0.68, 1.00, 0.42)
TEXT_WHITE = (0.95, 0.97, 1.00, 1.0)
TEXT_MUTED = (0.68, 0.74, 0.82, 1.0)
BADGE_BG = (0.18, 0.22, 0.30, 0.95)
BADGE_FG = (0.78, 0.88, 1.00, 1.0)
UCM_PILL_BG = (0.09, 0.48, 0.34, 0.96)
UCM_PILL_FG = (0.76, 1.00, 0.88, 1.0)
PORT_IN_COLOR = (0.32, 0.82, 0.96, 1.0)
PORT_OUT_COLOR = (0.98, 0.68, 0.26, 1.0)
EDGE_INTRA_COLOR = (0.52, 0.78, 0.96, 0.88)
EDGE_INTER_COLOR = (0.98, 0.68, 0.28, 0.94)
EDGE_ACTIVE_COLOR = (0.22, 0.94, 0.64, 0.98)
EDGE_MUTED_COLOR = (0.35, 0.40, 0.50, 0.36)


_RGBA_CACHE: Dict[Tuple[float, float, float, float], Gdk.RGBA] = {}


def _gdk_rgba(c: Tuple[float, float, float, float]) -> Gdk.RGBA:
    g = _RGBA_CACHE.get(c)
    if g is None:
        g = Gdk.RGBA()
        g.red, g.green, g.blue, g.alpha = c
        _RGBA_CACHE[c] = g
    return g


def _with_alpha(c: Tuple[float, float, float, float], alpha_mul: float) -> Tuple[float, float, float, float]:
    if alpha_mul >= 0.999:
        return c
    return (c[0], c[1], c[2], round(c[3] * alpha_mul, 3))


def _cairo_rounded_rect(cr: cairo.Context, x: float, y: float, w: float, h: float, r: float) -> None:
    r = min(r, w * 0.5, h * 0.5)
    cr.new_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2.0, 0.0)
    cr.arc(x + w - r, y + h - r, r, 0.0, math.pi / 2.0)
    cr.arc(x + r, y + h - r, r, math.pi / 2.0, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3.0 * math.pi / 2.0)
    cr.close_path()


def _get_port_world(node: GraphNode, port_id: str, direction: str) -> Tuple[float, float]:
    ports = node.out_ports if direction == "out" else node.in_ports
    for p in ports:
        if p.id == port_id:
            return node.x + p.rel_x, node.y + p.rel_y
    if ports:
        return node.x + ports[0].rel_x, node.y + ports[0].rel_y
    return (
        node.x + (node.width if direction == "out" else 0.0),
        node.y + node.height * 0.5,
    )


class UcmCanvasRenderer:
    """Display-independent Cairo + PangoCairo renderer for headless PNG export (--render-png)."""

    def __init__(self) -> None:
        self.vm: Optional[UcmGraphViewModel] = None
        self.pan_x: float = 24.0
        self.pan_y: float = 24.0
        self.zoom: float = 1.0
        self.selected_node_id: Optional[str] = None
        self.selected_edge_id: Optional[str] = None
        self.hovered_node_id: Optional[str] = None
        self.font_family: str = "Sans"
        self._font_cache: Dict[Tuple[float, bool], Pango.FontDescription] = {}

    def _get_font_desc(self, size_pt: float, bold: bool) -> Pango.FontDescription:
        key = (size_pt, bold)
        fd = self._font_cache.get(key)
        if fd is None:
            weight = "Bold " if bold else ""
            fd = Pango.FontDescription(f"{self.font_family} {weight}{size_pt}")
            self._font_cache[key] = fd
        return fd

    def _draw_text(
        self,
        cr: cairo.Context,
        text: str,
        x: float,
        y: float,
        size_pt: float = 8.5,
        bold: bool = False,
        color: Tuple[float, float, float, float] = TEXT_WHITE,
    ) -> Tuple[float, float]:
        lyt = PangoCairo.create_layout(cr)
        lyt.set_text(text, -1)
        lyt.set_font_description(self._get_font_desc(size_pt, bold))
        w, h = lyt.get_pixel_size()
        cr.save()
        cr.set_source_rgba(*color)
        cr.new_path()
        cr.move_to(x, y)
        PangoCairo.show_layout(cr, lyt)
        cr.new_path()
        cr.restore()
        return float(w), float(h)

    def _measure_text(self, cr: cairo.Context, text: str, size_pt: float = 8.5, bold: bool = False) -> Tuple[float, float]:
        lyt = PangoCairo.create_layout(cr)
        lyt.set_text(text, -1)
        lyt.set_font_description(self._get_font_desc(size_pt, bold))
        w, h = lyt.get_pixel_size()
        return float(w), float(h)

    def render_world_to_cairo(self, cr: cairo.Context, width: float, height: float) -> None:
        cr.save()
        cr.set_source_rgba(*BG_COLOR)
        cr.rectangle(0, 0, width, height)
        cr.fill()

        step = max(18.0, 32.0 * self.zoom)
        cr.set_source_rgba(*GRID_COLOR)
        cr.set_line_width(1.0)
        gx = math.fmod(self.pan_x, step)
        if gx < 0:
            gx += step
        while gx < width:
            cr.move_to(gx + 0.5, 0.0)
            cr.line_to(gx + 0.5, height)
            gx += step
        gy = math.fmod(self.pan_y, step)
        if gy < 0:
            gy += step
        while gy < height:
            cr.move_to(0.0, gy + 0.5)
            cr.line_to(width, gy + 0.5)
            gy += step
        cr.stroke()

        if not self.vm:
            self._draw_text(
                cr,
                "No audio topology loaded. Select a Sound Card or connect via SSH.",
                28.0,
                32.0,
                size_pt=10.5,
                bold=True,
                color=TEXT_MUTED,
            )
            cr.restore()
            return

        cr.save()
        cr.translate(self.pan_x, self.pan_y)
        cr.scale(self.zoom, self.zoom)

        wx0 = (0.0 - self.pan_x) / self.zoom
        wy0 = (0.0 - self.pan_y) / self.zoom
        wx1 = (width - self.pan_x) / self.zoom
        wy1 = (height - self.pan_y) / self.zoom

        for _pid, cont in self.vm.containers.items():
            if cont.x + cont.width < wx0 or cont.x > wx1 or cont.y + cont.height < wy0 or cont.y > wy1:
                continue
            self._draw_container(cr, cont)

        for edge in self.vm.edges:
            self._draw_edge(cr, edge, wx0, wy0, wx1, wy1)

        self._draw_kcontrol_groups(cr, wx0, wy0, wx1, wy1)

        for nid, node in self.vm.nodes.items():
            if node.x + node.width < wx0 or node.x > wx1 or node.y + node.height < wy0 or node.y > wy1:
                continue
            self._draw_node(cr, node, is_selected=(nid == self.selected_node_id))

        cr.restore()
        cr.restore()

    def _draw_container(self, cr: cairo.Context, cont: PipelineContainer) -> None:
        is_cap = "capture" in cont.direction.lower()
        fill_col = CONTAINER_FILL_CAP if is_cap else CONTAINER_FILL_PB
        border_col = (
            NODE_BORDER_POWERED if cont.powered
            else (CONTAINER_BORDER_CAP if is_cap else CONTAINER_BORDER_PB)
        )
        _cairo_rounded_rect(cr, cont.x, cont.y, cont.width, cont.height, 12.0)
        cr.set_source_rgba(*fill_col)
        cr.fill_preserve()
        cr.set_source_rgba(*border_col)
        cr.set_line_width(2.0 if cont.powered else 1.4)
        cr.stroke()

        pwr_tag = "  •  [POWERED]" if cont.powered else ""
        self._draw_text(
            cr,
            f"{cont.title}{pwr_tag}",
            cont.x + 14.0,
            cont.y + 10.0,
            size_pt=9.0,
            bold=True,
            color=TEXT_WHITE,
        )

    def _draw_edge(self, cr: cairo.Context, edge: GraphEdge, wx0: float, wy0: float, wx1: float, wy1: float) -> None:
        if not self.vm:
            return
        u = self.vm.nodes.get(edge.source_node)
        v = self.vm.nodes.get(edge.sink_node)
        if not u or not v:
            return

        sx, sy = _get_port_world(u, edge.source_port, "out")
        ex, ey = _get_port_world(v, edge.sink_port, "in")
        if max(sx, ex) + 60.0 < wx0 or min(sx, ex) - 60.0 > wx1 or max(sy, ey) + 40.0 < wy0 or min(sy, ey) - 40.0 > wy1:
            return

        dimmed = self.vm.has_active_trace and edge.id not in self.vm.highlighted_edge_ids
        is_sel = edge.id == self.selected_edge_id

        if is_sel:
            col, lw = NODE_BORDER_SELECTED, 3.0
        elif edge.is_ucm_highlighted or edge.is_active:
            col, lw = EDGE_ACTIVE_COLOR, 2.6
        elif dimmed:
            col, lw = EDGE_MUTED_COLOR, 1.4
        elif edge.is_inter_pipeline:
            col, lw = EDGE_INTER_COLOR, 2.0
        else:
            col, lw = EDGE_INTRA_COLOR, 1.8

        dx = max(42.0, abs(ex - sx) * 0.45)
        sign_out = -1.0 if (sx <= u.x + 1.0) else 1.0
        sign_in = 1.0 if (ex >= v.x + v.width - 1.0) else -1.0
        c1x, c1y = sx + dx * sign_out, sy
        c2x, c2y = ex + dx * sign_in, ey

        cr.save()
        if dimmed or (edge.control_name and not edge.is_active and not edge.is_ucm_highlighted):
            cr.set_dash([5.0, 4.0])
        cr.set_source_rgba(*col)
        cr.set_line_width(lw)
        cr.new_path()
        cr.move_to(sx, sy)
        cr.curve_to(c1x, c1y, c2x, c2y, ex, ey)
        cr.stroke()
        cr.restore()

        ang = math.atan2(ey - c2y, ex - c2x)
        ah = 7.0
        cr.save()
        cr.set_source_rgba(*col)
        cr.new_path()
        cr.move_to(ex, ey)
        cr.line_to(ex - ah * math.cos(ang - 0.42), ey - ah * math.sin(ang - 0.42))
        cr.line_to(ex - ah * math.cos(ang + 0.42), ey - ah * math.sin(ang + 0.42))
        cr.close_path()
        cr.fill()
        cr.restore()

        if edge.control_name:
            mx = 0.125 * sx + 0.375 * c1x + 0.375 * c2x + 0.125 * ex
            my = 0.125 * sy + 0.375 * c1y + 0.375 * c2y + 0.125 * ey
            lbl = edge.control_name[:22] + ("…" if len(edge.control_name) > 22 else "")
            if edge.kcontrol:
                lbl = f"{lbl}: {edge.kcontrol.formatted_value()}"
            tw, th = self._measure_text(cr, lbl, size_pt=6.5, bold=True)
            pw, ph = tw + 10.0, th + 5.0
            _cairo_rounded_rect(cr, mx - pw * 0.5, my - ph * 0.5, pw, ph, 4.0)
            cr.set_source_rgba(0.10, 0.13, 0.19, 0.94)
            cr.fill_preserve()
            cr.set_source_rgba(*col)
            cr.set_line_width(1.0)
            cr.stroke()
            self._draw_text(cr, lbl, mx - tw * 0.5, my - th * 0.5, size_pt=6.5, bold=True, color=TEXT_WHITE)

    def _draw_kcontrol_groups(self, cr: cairo.Context, wx0: float, wy0: float, wx1: float, wy1: float) -> None:
        if not self.vm:
            return
        by_parent: Dict[str, List[GraphNode]] = {}
        for n in self.vm.nodes.values():
            if n.class_category == "Control" and n.parent_node_id and n.parent_node_id in self.vm.nodes:
                by_parent.setdefault(n.parent_node_id, []).append(n)

        for parent_id, ctrls in by_parent.items():
            parent = self.vm.nodes[parent_id]
            gx = min(c.x for c in ctrls) - CTRL_GROUP_PAD
            gy = min(c.y for c in ctrls) - CTRL_GROUP_HDR
            gw = (max(c.x + c.width for c in ctrls) - min(c.x for c in ctrls)) + 2.0 * CTRL_GROUP_PAD
            gh = (max(c.y + c.height for c in ctrls) - min(c.y for c in ctrls)) + CTRL_GROUP_HDR + CTRL_GROUP_PAD
            if gx + gw < wx0 or gx > wx1 or gy + gh < wy0 or gy > wy1:
                continue

            any_sel = parent_id == self.selected_node_id or any(c.id == self.selected_node_id for c in ctrls)
            dimmed = (
                self.vm.has_active_trace
                and parent_id not in self.vm.highlighted_node_ids
                and not any(c.id in self.vm.highlighted_node_ids for c in ctrls)
            )
            alpha_mul = 0.38 if dimmed else 1.0
            p_hdr, p_body = CATEGORY_COLORS.get(parent.category, CATEGORY_COLORS["widget"])

            _cairo_rounded_rect(cr, gx, gy, gw, gh, 6.0)
            cr.set_source_rgba(p_body[0], p_body[1], p_body[2], 0.58 * alpha_mul)
            cr.fill_preserve()
            b_col = NODE_BORDER_SELECTED if any_sel else (p_hdr[0], p_hdr[1], p_hdr[2], 0.85 * alpha_mul)
            cr.set_source_rgba(*b_col)
            cr.set_line_width(1.6 if any_sel else 1.2)
            cr.stroke()

            self._draw_text(
                cr,
                f"{parent.title[:22]} ({len(ctrls)})",
                gx + 5.0,
                gy + 2.0,
                size_pt=6.0,
                bold=True,
                color=(0.90, 0.95, 1.0, 0.92 * alpha_mul),
            )

            x1 = gx + gw * 0.5
            y1 = gy
            x2 = max(parent.x + 12.0, min(parent.x + parent.width - 12.0, x1))
            y2 = parent.y + parent.height
            dy = max(5.0, abs(y1 - y2) * 0.45)
            cr.save()
            cr.set_dash([3.5, 2.5])
            cr.set_source_rgba(*b_col)
            cr.set_line_width(1.5)
            cr.new_path()
            cr.move_to(x1, y1)
            cr.curve_to(x1, y1 - dy, x2, y2 + dy, x2, y2)
            cr.stroke()
            cr.restore()

            cr.save()
            cr.set_source_rgba(*b_col)
            cr.new_path()
            cr.move_to(x2, y2)
            cr.line_to(x2 - 3.0, y2 + 5.0)
            cr.line_to(x2 + 3.0, y2 + 5.0)
            cr.close_path()
            cr.fill()
            cr.restore()

    def _draw_node(self, cr: cairo.Context, node: GraphNode, is_selected: bool) -> None:
        assert self.vm is not None
        dimmed = self.vm.has_active_trace and node.id not in self.vm.highlighted_node_ids
        is_ucm = bool(node.ucm_badges) or (self.vm.has_active_trace and node.id in self.vm.highlighted_node_ids)
        alpha = 0.35 if dimmed else 1.0

        hdr_col, body_col = CATEGORY_COLORS.get(node.category, CATEGORY_COLORS["widget"])
        hdr_col = _with_alpha(hdr_col, alpha)
        body_col = _with_alpha(body_col, alpha)

        if is_selected:
            _cairo_rounded_rect(cr, node.x - 4.0, node.y - 4.0, node.width + 8.0, node.height + 8.0, 12.0)
            cr.set_source_rgba(*NODE_SHADOW_SELECTED)
            cr.fill()

        if node.class_category == "Control":
            self._draw_kcontrol_node(cr, node, is_selected, hdr_col, body_col, alpha, is_ucm)
            return

        radius = 9.0
        _cairo_rounded_rect(cr, node.x, node.y, node.width, node.height, radius)
        cr.save()
        cr.clip()
        cr.set_source_rgba(*body_col)
        cr.rectangle(node.x, node.y, node.width, node.height)
        cr.fill()
        cr.set_source_rgba(*hdr_col)
        cr.rectangle(node.x, node.y, node.width, 26.0)
        cr.fill()
        cr.restore()

        if node.has_error:
            bcol, bw = (1.0, 0.28, 0.28, 1.0), 2.4
        elif node.has_warning:
            bcol, bw = (0.98, 0.78, 0.18, 1.0), 2.2
        elif is_selected:
            bcol, bw = NODE_BORDER_SELECTED, 2.2
        elif is_ucm:
            bcol, bw = NODE_BORDER_UCM, 2.2
        elif node.powered:
            bcol, bw = NODE_BORDER_POWERED, 1.8
        else:
            bcol, bw = _with_alpha(NODE_BORDER_NORMAL, alpha), 1.2

        _cairo_rounded_rect(cr, node.x, node.y, node.width, node.height, radius)
        cr.set_source_rgba(*bcol)
        cr.set_line_width(bw)
        cr.stroke()

        title_txt = node.title
        max_chars = max(14, int((node.width - 28.0) / 7.2))
        if len(title_txt) > max_chars:
            title_txt = title_txt[:max_chars - 1] + "…"
        self._draw_text(
            cr,
            title_txt,
            node.x + (24.0 if (node.has_error or node.has_warning) else 10.0),
            node.y + 5.5,
            size_pt=9.0,
            bold=True,
            color=_with_alpha(TEXT_WHITE, alpha),
        )

        if node.has_error or node.has_warning:
            bc = (0.92, 0.14, 0.18, 1.0) if node.has_error else (0.95, 0.72, 0.10, 1.0)
            cr.new_path()
            cr.arc(node.x + 13.0, node.y + 13.0, 7.0, 0, 2 * math.pi)
            cr.set_source_rgba(*bc)
            cr.fill()
            self._draw_text(cr, "!", node.x + 10.5, node.y + 5.5, size_pt=8.5, bold=True, color=TEXT_WHITE)

        sub_txt = node.subtitle
        max_sub = max(18, int((node.width - 20.0) / 6.0))
        if len(sub_txt) > max_sub:
            sub_txt = sub_txt[:max_sub - 1] + "…"
        self._draw_text(
            cr,
            sub_txt,
            node.x + 10.0,
            node.y + 31.0,
            size_pt=8.0,
            bold=node.powered,
            color=((0.49, 0.88, 0.99, alpha) if node.powered else _with_alpha(TEXT_MUTED, alpha)),
        )

        by = node.y + node.height - 23.0
        bx = node.x + 10.0
        all_pills: List[Tuple[str, Tuple[float, float, float, float], Tuple[float, float, float, float]]] = []
        for ub in node.ucm_badges[:2]:
            all_pills.append((ub, UCM_PILL_BG, UCM_PILL_FG))
        if node.in_fmt_badge:
            all_pills.append((f"IN {node.in_fmt_badge}", BADGE_BG, BADGE_FG))
        if node.out_fmt_badge:
            all_pills.append((f"OUT {node.out_fmt_badge}", BADGE_BG, BADGE_FG))
        if not node.in_fmt_badge and not node.out_fmt_badge:
            for b in node.badges[:2]:
                all_pills.append((b, BADGE_BG, BADGE_FG))

        for p_txt, p_bg, p_fg in all_pills:
            pw_t, _ = self._measure_text(cr, p_txt, size_pt=7.0, bold=True)
            bw_px = pw_t + 10.0
            if bx + bw_px > node.x + node.width - 8.0:
                break
            _cairo_rounded_rect(cr, bx, by, bw_px, 16.0, 4.0)
            cr.set_source_rgba(*_with_alpha(p_bg, alpha))
            cr.fill()
            self._draw_text(cr, p_txt, bx + 5.0, by + 2.5, size_pt=7.0, bold=True, color=_with_alpha(p_fg, alpha))
            bx += bw_px + 6.0

        for port in node.in_ports:
            self._draw_port(cr, node.x + port.rel_x, node.y + port.rel_y, PORT_IN_COLOR, alpha)
        for port in node.out_ports:
            self._draw_port(cr, node.x + port.rel_x, node.y + port.rel_y, PORT_OUT_COLOR, alpha)

    def _draw_port(self, cr: cairo.Context, cx: float, cy: float, color: Tuple[float, float, float, float], alpha: float) -> None:
        cr.new_path()
        cr.arc(cx, cy, 4.8, 0.0, 2.0 * math.pi)
        cr.set_source_rgba(*_with_alpha(color, alpha))
        cr.fill_preserve()
        cr.set_source_rgba(0.06, 0.08, 0.12, alpha)
        cr.set_line_width(1.2)
        cr.stroke()

    def _draw_kcontrol_node(
        self,
        cr: cairo.Context,
        node: GraphNode,
        is_selected: bool,
        hdr_col: Tuple[float, float, float, float],
        body_col: Tuple[float, float, float, float],
        alpha: float,
        is_ucm: bool,
    ) -> None:
        _cairo_rounded_rect(cr, node.x, node.y, node.width, node.height, 6.0)
        cr.save()
        cr.clip()
        cr.set_source_rgba(*body_col)
        cr.rectangle(node.x, node.y, node.width, node.height)
        cr.fill()
        cr.set_source_rgba(*hdr_col)
        cr.rectangle(node.x, node.y, node.width, 15.0)
        cr.fill()

        k = node.kcontrol
        short_nm = node.title.replace(" Playback ", " ").replace(" Capture ", " ").replace(" Volume", "").replace(" Switch", "")

        if node.category == "control_mixer" and k is not None:
            disp_t = short_nm[:8] if len(short_nm) > 8 else short_nm
            self._draw_text(cr, disp_t, node.x + 4.0, node.y + 2.0, size_pt=6.0, bold=True, color=(1, 1, 1, alpha))
            cur_v = int(k.values[0]) if k.values and isinstance(k.values[0], int) else k.min_val
            span = max(1, k.max_val - k.min_val)
            frac = max(0.0, min(1.0, (cur_v - k.min_val) / float(span)))

            track_x, track_w = node.x + 11.0, 8.0
            track_y, track_h = node.y + 21.0, max(32.0, node.height - 37.0)
            _cairo_rounded_rect(cr, track_x, track_y, track_w, track_h, 4.0)
            cr.set_source_rgba(0.07, 0.08, 0.12, 0.96 * alpha)
            cr.fill_preserve()
            cr.set_source_rgba(0.42, 0.36, 0.62, 0.92 * alpha)
            cr.set_line_width(1.0)
            cr.stroke()

            if frac > 0.0:
                fill_h = track_h * frac
                _cairo_rounded_rect(cr, track_x, track_y + track_h - fill_h, track_w, fill_h, 3.0)
                cr.set_source_rgba(0.68, 0.42, 0.98, 0.96 * alpha)
                cr.fill()

            cr.set_source_rgba(0.55, 0.62, 0.76, 0.65 * alpha)
            cr.set_line_width(1.0)
            for tf in (0.0, 0.25, 0.5, 0.75, 1.0):
                ty = track_y + track_h * tf
                cr.move_to(track_x + track_w + 2.0, ty)
                cr.line_to(track_x + track_w + 5.0, ty)
            cr.stroke()

            knob_x = track_x + track_w * 0.5
            knob_y = track_y + track_h * (1.0 - frac)
            cr.new_path()
            cr.arc(knob_x, knob_y, 5.2, 0, 2 * math.pi)
            cr.set_source_rgba(0.95, 0.97, 1.0, alpha)
            cr.fill()
            cr.new_path()
            cr.arc(knob_x, knob_y, 3.4, 0, 2 * math.pi)
            cr.set_source_rgba(*hdr_col)
            cr.fill()

            self._draw_text(cr, f"{cur_v}", node.x + 25.0, track_y + track_h * 0.25, size_pt=7.0, bold=True, color=(1, 1, 1, alpha))
            self._draw_text(cr, f"/{k.max_val}", node.x + 25.0, track_y + track_h * 0.52, size_pt=6.0, color=(0.7, 0.76, 0.84, alpha))
            foot = "UCM" if node.ucm_badges else "VOL"
            self._draw_text(cr, foot, node.x + 17.0, node.y + node.height - 13.0, size_pt=6.0, bold=True, color=(0.86, 0.72, 1.0, alpha))
        else:
            disp_t = short_nm[:12] + "…" if len(short_nm) > 13 else short_nm
            self._draw_text(cr, disp_t, node.x + 5.0, node.y + 2.0, size_pt=6.0, bold=True, color=(1, 1, 1, alpha))
            tag = {"control_switch": "SW", "control_enum": "ENUM", "control_bytes": "BYTES"}.get(node.category, "CTL")
            self._draw_text(cr, tag, node.x + node.width - 24.0, node.y + 2.5, size_pt=5.5, bold=True, color=(0.92, 0.96, 1.0, alpha))
            ctrl_y, ctrl_h = node.y + 18.0, 20.0
            if node.category == "control_switch" and k is not None:
                is_on = any(bool(v) for v in k.values)
                _cairo_rounded_rect(cr, node.x + 6.0, ctrl_y, node.width - 12.0, ctrl_h, 9.5)
                cr.set_source_rgba(*(0.14, 0.56, 0.36, 0.95 * alpha) if is_on else (0.22, 0.16, 0.20, 0.95 * alpha))
                cr.fill_preserve()
                cr.set_source_rgba(*(0.32, 0.92, 0.62, 0.95 * alpha) if is_on else (0.65, 0.32, 0.38, 0.90 * alpha))
                cr.set_line_width(1.1)
                cr.stroke()
                cx = (node.x + node.width - 16.0) if is_on else (node.x + 16.0)
                cr.new_path()
                cr.arc(cx, ctrl_y + ctrl_h * 0.5, 5.2, 0, 2 * math.pi)
                cr.set_source_rgba(*(0.65, 1.0, 0.80, alpha) if is_on else (0.75, 0.78, 0.84, alpha))
                cr.fill()
                sw_txt = "ON (Active)" if is_on else "OFF (Muted)"
                self._draw_text(cr, sw_txt, node.x + (14.0 if is_on else 26.0), ctrl_y + 4.0, size_pt=6.0, bold=True, color=(1, 1, 1, alpha) if is_on else (0.7, 0.76, 0.84, alpha))
            elif node.category == "control_enum" and k is not None:
                _cairo_rounded_rect(cr, node.x + 6.0, ctrl_y, node.width - 12.0, ctrl_h, 4.5)
                cr.set_source_rgba(0.08, 0.11, 0.15, 0.96 * alpha)
                cr.fill_preserve()
                cr.set_source_rgba(0.86, 0.28, 0.48, 0.95 * alpha)
                cr.set_line_width(1.0)
                cr.stroke()
                cur_opt = k.formatted_value()
                disp_opt = cur_opt[:13] + "…" if len(cur_opt) > 14 else cur_opt
                self._draw_text(cr, disp_opt, node.x + 10.0, ctrl_y + 4.0, size_pt=6.0, bold=True, color=(1.0, 0.76, 0.86, alpha))
                self._draw_text(cr, "▾", node.x + node.width - 16.0, ctrl_y + 3.0, size_pt=7.0, bold=True, color=(1, 1, 1, alpha))
            else:
                _cairo_rounded_rect(cr, node.x + 6.0, ctrl_y, node.width - 12.0, ctrl_h, 4.5)
                cr.set_source_rgba(0.08, 0.12, 0.16, 0.96 * alpha)
                cr.fill_preserve()
                cr.set_source_rgba(0.18, 0.68, 0.88, 0.95 * alpha)
                cr.set_line_width(1.0)
                cr.stroke()
                btxt = k.formatted_value() if k else "bytes"
                self._draw_text(cr, btxt[:15], node.x + 10.0, ctrl_y + 4.0, size_pt=6.0, bold=True, color=(0.68, 0.92, 1.0, alpha))

        cr.restore()

        if node.has_error:
            bcol, bw = (1.0, 0.28, 0.28, 1.0), 2.2
        elif is_selected:
            bcol, bw = NODE_BORDER_SELECTED, 2.0
        elif is_ucm:
            bcol, bw = NODE_BORDER_UCM, 1.8
        else:
            bcol, bw = hdr_col, 1.1
        _cairo_rounded_rect(cr, node.x, node.y, node.width, node.height, 6.0)
        cr.set_source_rgba(*bcol)
        cr.set_line_width(bw)
        cr.stroke()

    def fit_to_bounds(self, width: float, height: float) -> None:
        if not self.vm or not self.vm.nodes:
            return
        x0 = min(n.x for n in self.vm.nodes.values())
        y0 = min(n.y for n in self.vm.nodes.values())
        x1 = max(n.x + n.width for n in self.vm.nodes.values())
        y1 = max(n.y + n.height for n in self.vm.nodes.values())
        if self.vm.containers:
            x0 = min(x0, *(c.x for c in self.vm.containers.values()))
            y0 = min(y0, *(c.y for c in self.vm.containers.values()))
            x1 = max(x1, *(c.x + c.width for c in self.vm.containers.values()))
            y1 = max(y1, *(c.y + c.height for c in self.vm.containers.values()))
        gw = max(100.0, x1 - x0 + 80.0)
        gh = max(100.0, y1 - y0 + 80.0)
        self.zoom = max(0.18, min(1.35, min(width / gw, height / gh)))
        self.pan_x = (width - (x1 - x0) * self.zoom) * 0.5 - x0 * self.zoom
        self.pan_y = (height - (y1 - y0) * self.zoom) * 0.5 - y0 * self.zoom

    def render_to_png(self, out_path: str, width: int = 1600, height: int = 900) -> None:
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        cr = cairo.Context(surface)
        self.render_world_to_cairo(cr, float(width), float(height))
        surface.write_to_png(out_path)


class UcmGraphCanvas(Gtk.Widget):
    """Hardware-accelerated GTK4 Graph Canvas using Gtk.Snapshot, Gsk, and cached Pango layouts."""

    __gtype_name__ = "UcmGraphCanvas"

    def __init__(
        self,
        on_node_selected: Optional[Callable[[Optional[GraphNode]], None]] = None,
        on_kcontrol_changed: Optional[Callable[[KcontrolInfo, str], None]] = None,
        on_bind_kcontrol_to_ucm: Optional[Callable[[KcontrolInfo, str], None]] = None,
        on_bind_pcm_to_ucm: Optional[Callable[[GraphNode, str], None]] = None,
        on_trace_node: Optional[Callable[[GraphNode], None]] = None,
    ):
        super().__init__()
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_focusable(True)
        self.set_overflow(Gtk.Overflow.HIDDEN)

        self.renderer = UcmCanvasRenderer()
        self.on_node_selected = on_node_selected
        self.on_kcontrol_changed = on_kcontrol_changed
        self.on_bind_kcontrol_to_ucm = on_bind_kcontrol_to_ucm
        self.on_bind_pcm_to_ucm = on_bind_pcm_to_ucm
        self.on_trace_node = on_trace_node

        self._drag_mode: str = "none"
        self._drag_start_screen: Tuple[float, float] = (0.0, 0.0)
        self._drag_start_pan: Tuple[float, float] = (0.0, 0.0)
        self._drag_node_id: Optional[str] = None
        self._drag_node_initial: Tuple[float, float] = (0.0, 0.0)
        self._is_fitted: bool = True
        self._last_size: Tuple[int, int] = (0, 0)
        self._pointer_pos: Optional[Tuple[float, float]] = None
        self._active_popover: Optional[Gtk.Popover] = None

        # Cached GPU rendering primitives (eliminates per-frame Pango shaping & FontDescription churn)
        self._layout_cache: Dict[Tuple[str, float, bool], Pango.Layout] = {}
        self._font_cache: Dict[Tuple[float, bool], Pango.FontDescription] = {}
        self._stroke_solid: Dict[float, Gsk.Stroke] = {}
        self._stroke_dash: Dict[Tuple[float, Tuple[float, float]], Gsk.Stroke] = {}

        # Precomputed kcontrol grouping cache per GraphViewModel identity
        self._kctl_groups_vm_id: int = 0
        self._kctl_groups_cache: List[Tuple[str, List[GraphNode]]] = []

        # Cached Cairo-baked edge + dashed connector texture (avoids slow Gsk.Path stroke tessellation on pan/zoom)
        self._edge_tex: Optional[Gdk.Texture] = None
        self._edge_tex_bounds: Optional[Tuple[float, float, float, float]] = None
        self._edge_tex_key: Optional[tuple] = None
        self._edge_version: int = 0

        # Cursor cache
        self._cursor_cache: Dict[str, Optional[Gdk.Cursor]] = {}
        self._active_cursor_name: Optional[str] = None

        self._setup_controllers()

    def _set_canvas_cursor(self, name: Optional[str]) -> None:
        if name == self._active_cursor_name:
            return
        self._active_cursor_name = name
        if name is None:
            self.set_cursor(None)
            return
        cur = self._cursor_cache.get(name)
        if cur is None and name not in self._cursor_cache:
            cur = Gdk.Cursor.new_from_name(name, None)
            self._cursor_cache[name] = cur
        self.set_cursor(cur)

    def _update_hover_cursor(self, sx: float, sy: float) -> None:
        wx, wy = self.screen_to_world(sx, sy)
        node = self._hit_test_node(wx, wy)
        if node is None:
            self._set_canvas_cursor(None)
        elif node.category == "control_mixer" and node.kcontrol and node.kcontrol.writable:
            if node.x <= wx <= node.x + 28.0 and node.y + 16.0 <= wy <= node.y + node.height:
                self._set_canvas_cursor("ns-resize")
            else:
                self._set_canvas_cursor(None)
        elif node.category in ("control_switch", "control_enum") and node.kcontrol and node.kcontrol.writable:
            self._set_canvas_cursor("pointer")
        elif node.class_category != "Control":
            self._set_canvas_cursor("grab")
        else:
            self._set_canvas_cursor(None)

    @property
    def vm(self) -> Optional[UcmGraphViewModel]:
        return self.renderer.vm

    def set_view_model(self, vm: Optional[UcmGraphViewModel], auto_fit: bool = True) -> None:
        self.renderer.vm = vm
        self.renderer.selected_node_id = None
        self._kctl_groups_vm_id = 0
        self._kctl_groups_cache = []
        self._edge_tex = None
        self._edge_tex_key = None
        self._edge_version += 1
        if len(self._layout_cache) > 4096:
            self._layout_cache.clear()
        if vm and auto_fit:
            self._is_fitted = True
            self.fit_to_view()
        self.queue_draw()

    def screen_to_world(self, sx: float, sy: float) -> Tuple[float, float]:
        return (
            (sx - self.renderer.pan_x) / self.renderer.zoom,
            (sy - self.renderer.pan_y) / self.renderer.zoom,
        )

    def fit_to_view(self) -> None:
        vm = self.renderer.vm
        if not vm or not vm.nodes:
            return
        w = max(400.0, float(self.get_width() or 1000))
        h = max(300.0, float(self.get_height() or 700))
        self.renderer.fit_to_bounds(w, h)
        self._is_fitted = True
        self.queue_draw()

    def focus_node(self, node_id: str) -> None:
        vm = self.renderer.vm
        if not vm or node_id not in vm.nodes:
            return
        node = vm.nodes[node_id]
        self.renderer.selected_node_id = node_id
        w = float(self.get_width() or 900)
        h = float(self.get_height() or 600)
        self.renderer.zoom = max(0.85, self.renderer.zoom)
        self.renderer.pan_x = w * 0.5 - (node.x + node.width * 0.5) * self.renderer.zoom
        self.renderer.pan_y = h * 0.5 - (node.y + node.height * 0.5) * self.renderer.zoom
        self._is_fitted = False
        if self.on_node_selected:
            self.on_node_selected(node)
        self.queue_draw()

    def _get_pango_layout(self, text: str, size_pt: float = 8.5, bold: bool = False) -> Pango.Layout:
        key = (text, size_pt, bold)
        lyt = self._layout_cache.get(key)
        if lyt is None:
            lyt = self.create_pango_layout(text)
            fkey = (size_pt, bold)
            fd = self._font_cache.get(fkey)
            if fd is None:
                fd = Pango.FontDescription.from_string(f"Sans {'Bold ' if bold else ''}{size_pt}")
                self._font_cache[fkey] = fd
            lyt.set_font_description(fd)
            if len(self._layout_cache) < 4096:
                self._layout_cache[key] = lyt
        return lyt

    def _get_stroke(self, width: float, dash: Optional[Tuple[float, float]] = None) -> Gsk.Stroke:
        if dash is None:
            st = self._stroke_solid.get(width)
            if st is None:
                st = Gsk.Stroke.new(width)
                self._stroke_solid[width] = st
            return st
        dkey = (width, dash)
        st = self._stroke_dash.get(dkey)
        if st is None:
            st = Gsk.Stroke.new(width)
            st.set_dash([dash[0], dash[1]])
            self._stroke_dash[dkey] = st
        return st

    def _hit_test_node(self, wx: float, wy: float) -> Optional[GraphNode]:
        vm = self.renderer.vm
        if not vm:
            return None
        for n in reversed(list(vm.nodes.values())):
            if n.class_category == "Control" and n.x <= wx <= n.x + n.width and n.y <= wy <= n.y + n.height:
                return n
        for n in reversed(list(vm.nodes.values())):
            if n.class_category != "Control" and n.x <= wx <= n.x + n.width and n.y <= wy <= n.y + n.height:
                return n
        return None

    def _setup_controllers(self) -> None:
        motion = Gtk.EventControllerMotion.new()
        motion.connect("motion", self._on_pointer_motion)
        motion.connect("leave", lambda _c: setattr(self, "_pointer_pos", None))
        self.add_controller(motion)

        lclick = Gtk.GestureClick.new()
        lclick.set_button(1)
        lclick.connect("pressed", self._on_left_pressed)
        lclick.connect("released", self._on_left_released)
        lclick.connect("stopped", lambda _g: self._on_left_released(None, 0, 0.0, 0.0))
        self.add_controller(lclick)

        rclick = Gtk.GestureClick.new()
        rclick.set_button(3)
        rclick.connect("pressed", self._on_right_pressed)
        self.add_controller(rclick)

        drag = Gtk.GestureDrag.new()
        drag.set_button(1)
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        drag.connect("cancel", lambda _g, _seq: self._on_drag_end(_g, 0.0, 0.0))
        self.add_controller(drag)
        drag.group(lclick)

        mid_drag = Gtk.GestureDrag.new()
        mid_drag.set_button(2)
        mid_drag.connect("drag-begin", self._on_mid_drag_begin)
        mid_drag.connect("drag-update", self._on_mid_drag_update)
        mid_drag.connect("drag-end", self._on_mid_drag_end)
        mid_drag.connect("cancel", lambda _g, _seq: self._on_mid_drag_end(_g, 0.0, 0.0))
        self.add_controller(mid_drag)

        scroll = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll.connect("scroll", self._on_scroll)
        self.add_controller(scroll)

        key_ctrl = Gtk.EventControllerKey.new()
        key_ctrl.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_ctrl)

    def _on_pointer_motion(self, _ctrl: Gtk.EventControllerMotion, sx: float, sy: float) -> None:
        self._pointer_pos = (sx, sy)
        if self._drag_mode == "none":
            self._update_hover_cursor(sx, sy)

    def _on_key_pressed(self, _ctrl: Gtk.EventControllerKey, keyval: int, _kc: int, _state: Gdk.ModifierType) -> bool:
        if keyval in (Gdk.KEY_f, Gdk.KEY_F):
            self.fit_to_view()
            return True
        if keyval in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add):
            self._zoom_at_point(1.18, float(self.get_width()) * 0.5, float(self.get_height()) * 0.5)
            return True
        if keyval in (Gdk.KEY_minus, Gdk.KEY_KP_Subtract):
            self._zoom_at_point(1.0 / 1.18, float(self.get_width()) * 0.5, float(self.get_height()) * 0.5)
            return True
        if keyval == Gdk.KEY_0:
            self._is_fitted = False
            self.renderer.zoom = 1.0
            self.queue_draw()
            return True
        return False

    def _on_left_pressed(self, gest: Gtk.GestureClick, n_press: int, sx: float, sy: float) -> None:
        gest.set_state(Gtk.EventSequenceState.CLAIMED)
        self.grab_focus()
        wx, wy = self.screen_to_world(sx, sy)
        node = self._hit_test_node(wx, wy)
        self.renderer.selected_node_id = node.id if node else None
        if self.on_node_selected:
            self.on_node_selected(node)

        if node and node.class_category == "Control" and node.kcontrol and node.kcontrol.writable:
            k = node.kcontrol
            if node.category == "control_mixer" and node.x <= wx <= node.x + 28.0 and node.y + 16.0 <= wy <= node.y + node.height:
                self._set_canvas_cursor("ns-resize")
            elif node.category == "control_switch":
                cur_on = any(bool(v) for v in k.values)
                new_val = "off" if cur_on else "on"
                k.values = [0 if cur_on else 1] * max(1, k.count)
                if self.on_kcontrol_changed:
                    self.on_kcontrol_changed(k, new_val)
            elif node.category == "control_enum" and k.enum_items:
                self._show_enum_popover(node, sx, sy)
        elif node and n_press == 2 and self.on_trace_node:
            self.on_trace_node(node)
        else:
            # Show grabbing cursor as soon as left mouse button is held down for dragging/panning
            self._set_canvas_cursor("grabbing")

        self.queue_draw()

    def _on_left_released(self, _gest: Optional[Gtk.GestureClick], _n_press: int, sx: float, sy: float) -> None:
        if self._drag_mode == "none":
            if self._pointer_pos is not None:
                self._update_hover_cursor(*self._pointer_pos)
            else:
                self._set_canvas_cursor(None)

    def _on_right_pressed(self, gest: Gtk.GestureClick, _n_press: int, sx: float, sy: float) -> None:
        gest.set_state(Gtk.EventSequenceState.CLAIMED)
        wx, wy = self.screen_to_world(sx, sy)
        node = self._hit_test_node(wx, wy)
        if not node:
            return
        self.renderer.selected_node_id = node.id
        if self.on_node_selected:
            self.on_node_selected(node)
        self._show_context_menu(node, sx, sy)
        self.queue_draw()

    def _show_enum_popover(self, node: GraphNode, sx: float, sy: float) -> None:
        k = node.kcontrol
        if not k or not k.enum_items:
            return
        if self._active_popover:
            self._active_popover.popdown()
        pop = Gtk.Popover()
        pop.set_parent(self)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(sx), int(sy), 1, 1
        pop.set_pointing_to(rect)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_start(6)
        box.set_margin_end(6)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        for idx, item in enumerate(k.enum_items):
            btn = Gtk.Button(label=item)
            btn.add_css_class("flat")

            def _pick(_b: Gtk.Button, i: int = idx, name: str = item) -> None:
                k.values = [i]
                if self.on_kcontrol_changed:
                    self.on_kcontrol_changed(k, name)
                pop.popdown()
                self.queue_draw()

            btn.connect("clicked", _pick)
            box.append(btn)
        pop.set_child(box)
        self._active_popover = pop
        pop.popup()

    def _show_context_menu(self, node: GraphNode, sx: float, sy: float) -> None:
        if self._active_popover:
            self._active_popover.popdown()
        pop = Gtk.Popover()
        pop.set_parent(self)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(sx), int(sy), 1, 1
        pop.set_pointing_to(rect)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.set_margin_top(8)
        box.set_margin_bottom(8)

        def add_action(label: str, cb: Callable[[], None]) -> None:
            btn = Gtk.Button(label=label)
            btn.add_css_class("flat")
            btn.connect("clicked", lambda _b: (pop.popdown(), cb()))
            box.append(btn)

        if self.on_trace_node:
            add_action("Trace End-to-End Path Through Node", lambda: self.on_trace_node(node))

        if node.class_category == "PCM" and self.on_bind_pcm_to_ucm:
            add_action(f"Assign {node.direction.title()}PCM to Selected UCM Device", lambda: self.on_bind_pcm_to_ucm(node, "pcm"))

        if node.kcontrol and self.on_bind_kcontrol_to_ucm:
            k = node.kcontrol
            add_action("Add current value (cset) to Device EnableSequence", lambda: self.on_bind_kcontrol_to_ucm(k, "enable_seq"))
            add_action("Add current value (cset) to Device DisableSequence", lambda: self.on_bind_kcontrol_to_ucm(k, "disable_seq"))
            add_action("Add current value (cset) to Card BootSequence", lambda: self.on_bind_kcontrol_to_ucm(k, "boot_seq"))
            if node.category == "control_mixer":
                add_action("Bind as Device PlaybackVolume", lambda: self.on_bind_kcontrol_to_ucm(k, "PlaybackVolume"))
                add_action("Bind as Device CaptureVolume", lambda: self.on_bind_kcontrol_to_ucm(k, "CaptureVolume"))
            elif node.category == "control_switch":
                add_action("Bind as Device PlaybackSwitch", lambda: self.on_bind_kcontrol_to_ucm(k, "PlaybackSwitch"))
                add_action("Bind as Device CaptureSwitch", lambda: self.on_bind_kcontrol_to_ucm(k, "CaptureSwitch"))
                add_action("Bind as Device JackControl", lambda: self.on_bind_kcontrol_to_ucm(k, "JackControl"))

        pop.set_child(box)
        self._active_popover = pop
        pop.popup()

    def _on_drag_begin(self, gest: Gtk.GestureDrag, sx: float, sy: float) -> None:
        gest.set_state(Gtk.EventSequenceState.CLAIMED)
        self._drag_start_screen = (sx, sy)
        self._drag_start_pan = (self.renderer.pan_x, self.renderer.pan_y)
        wx, wy = self.screen_to_world(sx, sy)
        node = self._hit_test_node(wx, wy)

        if node and node.class_category == "Control":
            if node.category == "control_mixer" and node.kcontrol and node.kcontrol.writable:
                if node.x <= wx <= node.x + 28.0 and node.y + 16.0 <= wy <= node.y + node.height:
                    self._drag_mode = "slider"
                    self._drag_node_id = node.id
                    self._set_canvas_cursor("ns-resize")
                    self._update_slider_from_world_y(node, wy, commit=False)
                    return
            self._drag_mode = "none"
            self._drag_node_id = None
            return

        if node and node.class_category != "Control":
            self._drag_mode = "node"
            self._drag_node_id = node.id
            self._drag_node_initial = (node.x, node.y)
            self._set_canvas_cursor("grabbing")
        else:
            self._drag_mode = "pan"
            self._is_fitted = False
            self._set_canvas_cursor("grabbing")

    def _update_slider_from_world_y(self, node: GraphNode, wy: float, commit: bool) -> None:
        k = node.kcontrol
        if not k:
            return
        track_y = node.y + 21.0
        track_h = max(32.0, node.height - 37.0)
        frac = max(0.0, min(1.0, 1.0 - (wy - track_y) / track_h))
        new_val = int(round(k.min_val + frac * (k.max_val - k.min_val)))
        k.values = [new_val] * max(1, k.count)
        if commit and self.on_kcontrol_changed:
            val_str = ",".join([str(new_val)] * max(1, k.count))
            self.on_kcontrol_changed(k, val_str)
        self.queue_draw()

    def _on_drag_update(self, _gest: Gtk.GestureDrag, dx: float, dy: float) -> None:
        sx, sy = self._drag_start_screen[0] + dx, self._drag_start_screen[1] + dy
        if self._drag_mode == "pan":
            self.renderer.pan_x = self._drag_start_pan[0] + dx
            self.renderer.pan_y = self._drag_start_pan[1] + dy
            self.queue_draw()
        elif self._drag_mode == "slider" and self._drag_node_id and self.renderer.vm:
            node = self.renderer.vm.nodes.get(self._drag_node_id)
            if node:
                _, wy = self.screen_to_world(sx, sy)
                self._update_slider_from_world_y(node, wy, commit=False)
        elif self._drag_mode == "node" and self._drag_node_id and self.renderer.vm:
            node = self.renderer.vm.nodes.get(self._drag_node_id)
            if node:
                w_dx = dx / self.renderer.zoom
                w_dy = dy / self.renderer.zoom
                old_x, old_y = node.x, node.y
                node.x = self._drag_node_initial[0] + w_dx
                node.y = self._drag_node_initial[1] + w_dy
                shift_x = node.x - old_x
                shift_y = node.y - old_y
                for child in self.renderer.vm.nodes.values():
                    if child.parent_node_id == node.id:
                        child.x += shift_x
                        child.y += shift_y
                update_container_bounds(self.renderer.vm)
                self._edge_version += 1
                self.queue_draw()

    def _on_drag_end(self, _gest: Gtk.GestureDrag, dx: float, dy: float) -> None:
        if self._drag_mode == "slider" and self._drag_node_id and self.renderer.vm:
            node = self.renderer.vm.nodes.get(self._drag_node_id)
            if node:
                _, wy = self.screen_to_world(self._drag_start_screen[0] + dx, self._drag_start_screen[1] + dy)
                self._update_slider_from_world_y(node, wy, commit=True)
        self._drag_mode = "none"
        self._drag_node_id = None
        if self._pointer_pos is not None:
            self._update_hover_cursor(*self._pointer_pos)
        else:
            self._set_canvas_cursor(None)

    def _on_mid_drag_begin(self, gest: Gtk.GestureDrag, _sx: float, _sy: float) -> None:
        gest.set_state(Gtk.EventSequenceState.CLAIMED)
        self._drag_start_pan = (self.renderer.pan_x, self.renderer.pan_y)
        self._is_fitted = False
        self._set_canvas_cursor("grabbing")

    def _on_mid_drag_update(self, _gest: Gtk.GestureDrag, dx: float, dy: float) -> None:
        self.renderer.pan_x = self._drag_start_pan[0] + dx
        self.renderer.pan_y = self._drag_start_pan[1] + dy
        self.queue_draw()

    def _on_mid_drag_end(self, _gest: Gtk.GestureDrag, _dx: float, _dy: float) -> None:
        if self._pointer_pos is not None:
            self._update_hover_cursor(*self._pointer_pos)
        else:
            self._set_canvas_cursor(None)

    def _zoom_at_point(self, factor: float, px: float, py: float) -> None:
        self._is_fitted = False
        new_zoom = max(0.08, min(4.0, self.renderer.zoom * factor))
        wx, wy = self.screen_to_world(px, py)
        self.renderer.zoom = new_zoom
        self.renderer.pan_x = px - wx * new_zoom
        self.renderer.pan_y = py - wy * new_zoom
        self.queue_draw()

    def _on_scroll(self, ctrl: Gtk.EventControllerScroll, dx: float, dy: float) -> bool:
        state = ctrl.get_current_event_state()
        shift_held = bool(state & Gdk.ModifierType.SHIFT_MASK)

        # Horizontal scroll or Shift+scroll pans cleanly
        if shift_held or (abs(dx) > 0.0 and abs(dy) < 1e-6):
            self._is_fitted = False
            pan_dx = dy if shift_held and dx == 0.0 else dx
            self.renderer.pan_x -= pan_dx * 42.0
            if not shift_held:
                self.renderer.pan_y -= dy * 42.0
            self.queue_draw()
            return True

        if abs(dy) < 1e-6:
            return False

        # Smooth proportional zoom anchored on pointer (handles both discrete ±1 wheel and fractional touchpad dy)
        clamped_dy = max(-3.0, min(3.0, dy))
        factor = math.pow(1.16, -clamped_dy)
        if self._pointer_pos is not None:
            px, py = self._pointer_pos
        else:
            px = float(self.get_width()) * 0.5
            py = float(self.get_height()) * 0.5
        self._zoom_at_point(factor, px, py)
        return True

    # -------------------------------------------------------------------------
    # Hardware-Accelerated GPU Gtk.Snapshot Rendering (sof-builder style)
    # -------------------------------------------------------------------------

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        iw = self.get_width()
        ih = self.get_height()
        if iw <= 0 or ih <= 0:
            return
        if (iw, ih) != self._last_size:
            self._last_size = (iw, ih)
            if self._is_fitted and self._drag_mode == "none" and self.renderer.vm:
                self.renderer.fit_to_bounds(max(400.0, float(iw)), max(300.0, float(ih)))

        w, h = float(iw), float(ih)
        bounds = Graphene.Rect.alloc()
        bounds.init(0.0, 0.0, w, h)
        snapshot.append_color(_gdk_rgba(BG_COLOR), bounds)

        self._snapshot_grid(snapshot, w, h)

        vm = self.renderer.vm
        if not vm:
            lyt = self._get_pango_layout("No audio topology loaded. Select a Sound Card or connect via SSH.", 10.5, True)
            snapshot.save()
            pt = Graphene.Point.alloc()
            pt.init(28.0, 32.0)
            snapshot.translate(pt)
            snapshot.append_layout(lyt, _gdk_rgba(TEXT_MUTED))
            snapshot.restore()
            return

        # GPU Camera Transform (zero CPU surface re-allocation on pan/zoom)
        snapshot.save()
        pt = Graphene.Point.alloc()
        pt.init(self.renderer.pan_x, self.renderer.pan_y)
        snapshot.translate(pt)
        snapshot.scale(self.renderer.zoom, self.renderer.zoom)

        wx0, wy0 = self.screen_to_world(0.0, 0.0)
        wx1, wy1 = self.screen_to_world(w, h)

        # 1. Pipeline Swimlane Containers
        for cont in vm.containers.values():
            if cont.x + cont.width < wx0 or cont.x > wx1 or cont.y + cont.height < wy0 or cont.y > wy1:
                continue
            self._snapshot_container(snapshot, cont)

        # 2. Edges & kcontrol connectors (cached Cairo texture -> single GPU quad during pan/zoom)
        self._snapshot_edge_layer(snapshot, vm)
        if self.renderer.zoom >= 0.35:
            for edge in vm.edges:
                if edge.control_name:
                    self._snapshot_edge_badge(snapshot, vm, edge, wx0, wy0, wx1, wy1)

        # 3. Kcontrol Group Enclosures
        self._snapshot_kcontrol_groups(snapshot, vm, wx0, wy0, wx1, wy1)

        # 4. Graph Nodes (culled to visible viewport)
        sel_id = self.renderer.selected_node_id
        for nid, node in vm.nodes.items():
            if node.x + node.width < wx0 or node.x > wx1 or node.y + node.height < wy0 or node.y > wy1:
                continue
            self._snapshot_node(snapshot, vm, node, is_selected=(nid == sel_id))

        snapshot.restore()

        # 5. HUD bar in screen space
        self._snapshot_hud(snapshot, w)

    def _snapshot_grid(self, snapshot: Gtk.Snapshot, w: float, h: float) -> None:
        step = max(18.0, 32.0 * self.renderer.zoom)
        gcol = _gdk_rgba(GRID_COLOR)
        r = Graphene.Rect.alloc()
        x = math.fmod(self.renderer.pan_x, step)
        if x < 0:
            x += step
        while x < w:
            r.init(x, 0.0, 1.0, h)
            snapshot.append_color(gcol, r)
            x += step
        y = math.fmod(self.renderer.pan_y, step)
        if y < 0:
            y += step
        while y < h:
            r.init(0.0, y, w, 1.0)
            snapshot.append_color(gcol, r)
            y += step

    def _snapshot_container(self, snapshot: Gtk.Snapshot, cont: PipelineContainer) -> None:
        is_cap = "capture" in cont.direction.lower()
        fill_col = CONTAINER_FILL_CAP if is_cap else CONTAINER_FILL_PB
        border_col = (
            NODE_BORDER_POWERED if cont.powered
            else (CONTAINER_BORDER_CAP if is_cap else CONTAINER_BORDER_PB)
        )
        rect = Graphene.Rect.alloc()
        rect.init(cont.x, cont.y, cont.width, cont.height)
        rr = Gsk.RoundedRect()
        rr.init_from_rect(rect, 12.0)

        snapshot.push_rounded_clip(rr)
        snapshot.append_color(_gdk_rgba(fill_col), rect)
        snapshot.pop()

        bw = 2.0 if cont.powered else 1.4
        bc = _gdk_rgba(border_col)
        snapshot.append_border(rr, [bw, bw, bw, bw], [bc, bc, bc, bc])

        pwr_tag = "  •  [POWERED]" if cont.powered else ""
        lyt = self._get_pango_layout(f"{cont.title}{pwr_tag}", 9.0, True)
        snapshot.save()
        pt = Graphene.Point.alloc()
        pt.init(cont.x + 14.0, cont.y + 10.0)
        snapshot.translate(pt)
        snapshot.append_layout(lyt, _gdk_rgba(TEXT_WHITE))
        snapshot.restore()

    def _snapshot_circle(self, snapshot: Gtk.Snapshot, cx: float, cy: float, radius: float, color: Gdk.RGBA) -> None:
        rect = Graphene.Rect.alloc()
        rect.init(cx - radius, cy - radius, radius * 2.0, radius * 2.0)
        rr = Gsk.RoundedRect()
        rr.init_from_rect(rect, radius)
        snapshot.push_rounded_clip(rr)
        snapshot.append_color(color, rect)
        snapshot.pop()

    def _ensure_edge_texture(self, vm: UcmGraphViewModel) -> None:
        sel_node = self.renderer.selected_node_id
        sel_edge = self.renderer.selected_edge_id
        key = (
            id(vm),
            self._edge_version,
            vm.has_active_trace,
            len(vm.highlighted_edge_ids),
            len(vm.highlighted_node_ids),
            sel_node,
            sel_edge,
        )
        if self._edge_tex is not None and self._edge_tex_key == key:
            return
        if not vm.nodes:
            self._edge_tex = None
            self._edge_tex_bounds = None
            self._edge_tex_key = key
            return

        x0 = min(n.x for n in vm.nodes.values()) - 60.0
        y0 = min(n.y for n in vm.nodes.values()) - 60.0
        x1 = max(n.x + n.width for n in vm.nodes.values()) + 60.0
        y1 = max(n.y + n.height for n in vm.nodes.values()) + 60.0
        world_w = max(64.0, x1 - x0)
        world_h = max(64.0, y1 - y0)

        # Cap texture dimensions safely below GPU 4096px limit while staying crisp
        bake_scale = min(1.5, 4096.0 / max(world_w, world_h))
        tex_w = max(16, int(math.ceil(world_w * bake_scale)))
        tex_h = max(16, int(math.ceil(world_h * bake_scale)))

        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, tex_w, tex_h)
        cr = cairo.Context(surf)
        cr.scale(bake_scale, bake_scale)
        cr.translate(-x0, -y0)

        # Draw all graph edges + arrowheads
        for edge in vm.edges:
            u = vm.nodes.get(edge.source_node)
            v = vm.nodes.get(edge.sink_node)
            if not u or not v:
                continue
            sx, sy = _get_port_world(u, edge.source_port, "out")
            ex, ey = _get_port_world(v, edge.sink_port, "in")

            dimmed = vm.has_active_trace and edge.id not in vm.highlighted_edge_ids
            is_sel = edge.id == sel_edge
            if is_sel:
                col, lw = NODE_BORDER_SELECTED, 3.0
            elif edge.is_ucm_highlighted or edge.is_active:
                col, lw = EDGE_ACTIVE_COLOR, 2.6
            elif dimmed:
                col, lw = EDGE_MUTED_COLOR, 1.4
            elif edge.is_inter_pipeline:
                col, lw = EDGE_INTER_COLOR, 2.0
            else:
                col, lw = EDGE_INTRA_COLOR, 1.8

            dx = max(42.0, abs(ex - sx) * 0.45)
            sign_out = -1.0 if (sx <= u.x + 1.0) else 1.0
            sign_in = 1.0 if (ex >= v.x + v.width - 1.0) else -1.0
            c1x, c1y = sx + dx * sign_out, sy
            c2x, c2y = ex + dx * sign_in, ey

            cr.save()
            if dimmed or (edge.control_name and not edge.is_active and not edge.is_ucm_highlighted):
                cr.set_dash([5.0, 4.0])
            cr.set_source_rgba(*col)
            cr.set_line_width(lw)
            cr.new_path()
            cr.move_to(sx, sy)
            cr.curve_to(c1x, c1y, c2x, c2y, ex, ey)
            cr.stroke()
            cr.restore()

            ang = math.atan2(ey - c2y, ex - c2x)
            ah = 7.0
            cr.new_path()
            cr.move_to(ex, ey)
            cr.line_to(ex - ah * math.cos(ang - 0.42), ey - ah * math.sin(ang - 0.42))
            cr.line_to(ex - ah * math.cos(ang + 0.42), ey - ah * math.sin(ang + 0.42))
            cr.close_path()
            cr.set_source_rgba(*col)
            cr.fill()

        # Draw kcontrol-group dashed connectors & arrowheads
        for parent_id, ctrls in self._get_kcontrol_groups(vm):
            parent = vm.nodes[parent_id]
            gx = min(c.x for c in ctrls) - CTRL_GROUP_PAD
            gy = min(c.y for c in ctrls) - CTRL_GROUP_HDR
            gw = (max(c.x + c.width for c in ctrls) - min(c.x for c in ctrls)) + 2.0 * CTRL_GROUP_PAD
            any_sel = parent_id == sel_node or any(c.id == sel_node for c in ctrls)
            dimmed = (
                vm.has_active_trace
                and parent_id not in vm.highlighted_node_ids
                and not any(c.id in vm.highlighted_node_ids for c in ctrls)
            )
            alpha_mul = 0.38 if dimmed else 1.0
            p_hdr, _ = CATEGORY_COLORS.get(parent.category, CATEGORY_COLORS["widget"])
            b_col = NODE_BORDER_SELECTED if any_sel else (p_hdr[0], p_hdr[1], p_hdr[2], round(0.85 * alpha_mul, 3))

            x1, y1 = gx + gw * 0.5, gy
            x2 = max(parent.x + 12.0, min(parent.x + parent.width - 12.0, x1))
            y2 = parent.y + parent.height
            dy = max(5.0, abs(y1 - y2) * 0.45)

            cr.save()
            cr.set_dash([3.5, 2.5])
            cr.set_source_rgba(*b_col)
            cr.set_line_width(1.5)
            cr.new_path()
            cr.move_to(x1, y1)
            cr.curve_to(x1, y1 - dy, x2, y2 + dy, x2, y2)
            cr.stroke()
            cr.restore()

            cr.new_path()
            cr.move_to(x2, y2)
            cr.line_to(x2 - 3.0, y2 + 5.0)
            cr.line_to(x2 + 3.0, y2 + 5.0)
            cr.close_path()
            cr.set_source_rgba(*b_col)
            cr.fill()

        surf.flush()
        raw_bytes = bytes(surf.get_data())
        self._edge_tex = Gdk.MemoryTexture.new(
            tex_w,
            tex_h,
            Gdk.MemoryFormat.B8G8R8A8_PREMULTIPLIED,
            GLib.Bytes.new(raw_bytes),
            surf.get_stride(),
        )
        self._edge_tex_bounds = (x0, y0, world_w, world_h)
        self._edge_tex_key = key

    def _snapshot_edge_layer(self, snapshot: Gtk.Snapshot, vm: UcmGraphViewModel) -> None:
        self._ensure_edge_texture(vm)
        if self._edge_tex is None or self._edge_tex_bounds is None:
            return
        x0, y0, ww, wh = self._edge_tex_bounds
        rect = Graphene.Rect.alloc()
        rect.init(x0, y0, ww, wh)
        snapshot.append_scaled_texture(self._edge_tex, Gsk.ScalingFilter.LINEAR, rect)

    def _snapshot_edge_badge(
        self,
        snapshot: Gtk.Snapshot,
        vm: UcmGraphViewModel,
        edge: GraphEdge,
        wx0: float,
        wy0: float,
        wx1: float,
        wy1: float,
    ) -> None:
        u = vm.nodes.get(edge.source_node)
        v = vm.nodes.get(edge.sink_node)
        if not u or not v:
            return
        sx, sy = _get_port_world(u, edge.source_port, "out")
        ex, ey = _get_port_world(v, edge.sink_port, "in")
        if max(sx, ex) + 60.0 < wx0 or min(sx, ex) - 60.0 > wx1 or max(sy, ey) + 40.0 < wy0 or min(sy, ey) - 40.0 > wy1:
            return

        dimmed = vm.has_active_trace and edge.id not in vm.highlighted_edge_ids
        is_sel = edge.id == self.renderer.selected_edge_id
        if is_sel:
            col = NODE_BORDER_SELECTED
        elif edge.is_ucm_highlighted or edge.is_active:
            col = EDGE_ACTIVE_COLOR
        elif dimmed:
            col = EDGE_MUTED_COLOR
        elif edge.is_inter_pipeline:
            col = EDGE_INTER_COLOR
        else:
            col = EDGE_INTRA_COLOR
        gcol = _gdk_rgba(col)

        dx = max(42.0, abs(ex - sx) * 0.45)
        sign_out = -1.0 if (sx <= u.x + 1.0) else 1.0
        sign_in = 1.0 if (ex >= v.x + v.width - 1.0) else -1.0
        c1x, c1y = sx + dx * sign_out, sy
        c2x, c2y = ex + dx * sign_in, ey
        mx = 0.125 * sx + 0.375 * c1x + 0.375 * c2x + 0.125 * ex
        my = 0.125 * sy + 0.375 * c1y + 0.375 * c2y + 0.125 * ey

        lbl = edge.control_name[:22] + ("…" if len(edge.control_name) > 22 else "")
        if edge.kcontrol:
            lbl = f"{lbl}: {edge.kcontrol.formatted_value()}"
        lyt = self._get_pango_layout(lbl, 6.5, True)
        _, ext = lyt.get_pixel_extents()
        pw, ph = float(ext.width) + 10.0, float(ext.height) + 5.0
        brect = Graphene.Rect.alloc()
        brect.init(mx - pw * 0.5, my - ph * 0.5, pw, ph)
        brr = Gsk.RoundedRect()
        brr.init_from_rect(brect, 4.0)
        snapshot.push_rounded_clip(brr)
        snapshot.append_color(_gdk_rgba((0.10, 0.13, 0.19, 0.94)), brect)
        snapshot.pop()
        snapshot.append_border(brr, [1.0, 1.0, 1.0, 1.0], [gcol, gcol, gcol, gcol])
        snapshot.save()
        pt = Graphene.Point.alloc()
        pt.init(mx - float(ext.width) * 0.5, my - float(ext.height) * 0.5)
        snapshot.translate(pt)
        snapshot.append_layout(lyt, _gdk_rgba(TEXT_WHITE))
        snapshot.restore()

    def _get_kcontrol_groups(self, vm: UcmGraphViewModel) -> List[Tuple[str, List[GraphNode]]]:
        vid = id(vm)
        if self._kctl_groups_vm_id == vid and self._kctl_groups_cache:
            return self._kctl_groups_cache
        by_parent: Dict[str, List[GraphNode]] = {}
        for n in vm.nodes.values():
            if n.class_category == "Control" and n.parent_node_id and n.parent_node_id in vm.nodes:
                by_parent.setdefault(n.parent_node_id, []).append(n)
        self._kctl_groups_vm_id = vid
        self._kctl_groups_cache = list(by_parent.items())
        return self._kctl_groups_cache

    def _snapshot_kcontrol_groups(
        self,
        snapshot: Gtk.Snapshot,
        vm: UcmGraphViewModel,
        wx0: float,
        wy0: float,
        wx1: float,
        wy1: float,
    ) -> None:
        sel_id = self.renderer.selected_node_id
        for parent_id, ctrls in self._get_kcontrol_groups(vm):
            parent = vm.nodes[parent_id]
            gx = min(c.x for c in ctrls) - CTRL_GROUP_PAD
            gy = min(c.y for c in ctrls) - CTRL_GROUP_HDR
            gw = (max(c.x + c.width for c in ctrls) - min(c.x for c in ctrls)) + 2.0 * CTRL_GROUP_PAD
            gh = (max(c.y + c.height for c in ctrls) - min(c.y for c in ctrls)) + CTRL_GROUP_HDR + CTRL_GROUP_PAD
            if gx + gw < wx0 or gx > wx1 or gy + gh < wy0 or gy > wy1:
                continue

            any_sel = parent_id == sel_id or any(c.id == sel_id for c in ctrls)
            dimmed = (
                vm.has_active_trace
                and parent_id not in vm.highlighted_node_ids
                and not any(c.id in vm.highlighted_node_ids for c in ctrls)
            )
            alpha_mul = 0.38 if dimmed else 1.0
            p_hdr, p_body = CATEGORY_COLORS.get(parent.category, CATEGORY_COLORS["widget"])

            grect = Graphene.Rect.alloc()
            grect.init(gx, gy, gw, gh)
            grr = Gsk.RoundedRect()
            grr.init_from_rect(grect, 6.0)
            snapshot.push_rounded_clip(grr)
            snapshot.append_color(_gdk_rgba((p_body[0], p_body[1], p_body[2], round(0.58 * alpha_mul, 3))), grect)
            snapshot.pop()

            b_col = NODE_BORDER_SELECTED if any_sel else (p_hdr[0], p_hdr[1], p_hdr[2], round(0.85 * alpha_mul, 3))
            gb = _gdk_rgba(b_col)
            bw = 1.6 if any_sel else 1.2
            snapshot.append_border(grr, [bw, bw, bw, bw], [gb, gb, gb, gb])

            lyt = self._get_pango_layout(f"{parent.title[:22]} ({len(ctrls)})", 6.0, True)
            snapshot.save()
            pt = Graphene.Point.alloc()
            pt.init(gx + 5.0, gy + 2.0)
            snapshot.translate(pt)
            snapshot.append_layout(lyt, _gdk_rgba((0.90, 0.95, 1.0, round(0.92 * alpha_mul, 3))))
            snapshot.restore()

    def _snapshot_node(
        self,
        snapshot: Gtk.Snapshot,
        vm: UcmGraphViewModel,
        node: GraphNode,
        is_selected: bool,
    ) -> None:
        dimmed = vm.has_active_trace and node.id not in vm.highlighted_node_ids
        is_ucm = bool(node.ucm_badges) or (vm.has_active_trace and node.id in vm.highlighted_node_ids)
        alpha = 0.35 if dimmed else 1.0

        hdr_col, body_col = CATEGORY_COLORS.get(node.category, CATEGORY_COLORS["widget"])
        hdr_col = _with_alpha(hdr_col, alpha)
        body_col = _with_alpha(body_col, alpha)

        if is_selected:
            grect = Graphene.Rect.alloc()
            grect.init(node.x - 4.0, node.y - 4.0, node.width + 8.0, node.height + 8.0)
            grr = Gsk.RoundedRect()
            grr.init_from_rect(grect, 12.0)
            snapshot.push_rounded_clip(grr)
            snapshot.append_color(_gdk_rgba(NODE_SHADOW_SELECTED), grect)
            snapshot.pop()

        if node.class_category == "Control":
            self._snapshot_kcontrol_node(snapshot, node, is_selected, hdr_col, body_col, alpha, is_ucm)
            return

        rect = Graphene.Rect.alloc()
        rect.init(node.x, node.y, node.width, node.height)
        rr = Gsk.RoundedRect()
        rr.init_from_rect(rect, 9.0)

        snapshot.push_rounded_clip(rr)
        snapshot.append_color(_gdk_rgba(body_col), rect)
        hrect = Graphene.Rect.alloc()
        hrect.init(node.x, node.y, node.width, 26.0)
        snapshot.append_color(_gdk_rgba(hdr_col), hrect)
        snapshot.pop()

        if node.has_error:
            bcol, bw = (1.0, 0.28, 0.28, 1.0), 2.4
        elif node.has_warning:
            bcol, bw = (0.98, 0.78, 0.18, 1.0), 2.2
        elif is_selected:
            bcol, bw = NODE_BORDER_SELECTED, 2.2
        elif is_ucm:
            bcol, bw = NODE_BORDER_UCM, 2.2
        elif node.powered:
            bcol, bw = NODE_BORDER_POWERED, 1.8
        else:
            bcol, bw = _with_alpha(NODE_BORDER_NORMAL, alpha), 1.2
        gb = _gdk_rgba(bcol)
        snapshot.append_border(rr, [bw, bw, bw, bw], [gb, gb, gb, gb])

        # Skip tiny text layouts when zoomed way out for ultra-fast overview panning
        if self.renderer.zoom >= 0.22:
            title_txt = node.title
            max_chars = max(14, int((node.width - 28.0) / 7.2))
            if len(title_txt) > max_chars:
                title_txt = title_txt[:max_chars - 1] + "…"
            t_lyt = self._get_pango_layout(title_txt, 9.0, True)
            pt = Graphene.Point.alloc()
            snapshot.save()
            pt.init(node.x + (24.0 if (node.has_error or node.has_warning) else 10.0), node.y + 5.5)
            snapshot.translate(pt)
            snapshot.append_layout(t_lyt, _gdk_rgba(_with_alpha(TEXT_WHITE, alpha)))
            snapshot.restore()

            if node.has_error or node.has_warning:
                bc = (0.92, 0.14, 0.18, 1.0) if node.has_error else (0.95, 0.72, 0.10, 1.0)
                self._snapshot_circle(snapshot, node.x + 13.0, node.y + 13.0, 7.0, _gdk_rgba(bc))
                b_lyt = self._get_pango_layout("!", 8.5, True)
                snapshot.save()
                pt.init(node.x + 10.5, node.y + 5.5)
                snapshot.translate(pt)
                snapshot.append_layout(b_lyt, _gdk_rgba(TEXT_WHITE))
                snapshot.restore()

            sub_txt = node.subtitle
            max_sub = max(18, int((node.width - 20.0) / 6.0))
            if len(sub_txt) > max_sub:
                sub_txt = sub_txt[:max_sub - 1] + "…"
            s_lyt = self._get_pango_layout(sub_txt, 8.0, node.powered)
            s_col = (0.49, 0.88, 0.99, alpha) if node.powered else _with_alpha(TEXT_MUTED, alpha)
            snapshot.save()
            pt.init(node.x + 10.0, node.y + 31.0)
            snapshot.translate(pt)
            snapshot.append_layout(s_lyt, _gdk_rgba(s_col))
            snapshot.restore()

            by = node.y + node.height - 23.0
            bx = node.x + 10.0
            all_pills: List[Tuple[str, Tuple[float, float, float, float], Tuple[float, float, float, float]]] = []
            for ub in node.ucm_badges[:2]:
                all_pills.append((ub, UCM_PILL_BG, UCM_PILL_FG))
            if node.in_fmt_badge:
                all_pills.append((f"IN {node.in_fmt_badge}", BADGE_BG, BADGE_FG))
            if node.out_fmt_badge:
                all_pills.append((f"OUT {node.out_fmt_badge}", BADGE_BG, BADGE_FG))
            if not node.in_fmt_badge and not node.out_fmt_badge:
                for b in node.badges[:2]:
                    all_pills.append((b, BADGE_BG, BADGE_FG))

            for p_txt, p_bg, p_fg in all_pills:
                p_lyt = self._get_pango_layout(p_txt, 7.0, True)
                _, p_ext = p_lyt.get_pixel_extents()
                bw_px = float(p_ext.width) + 10.0
                if bx + bw_px > node.x + node.width - 8.0:
                    break
                prect = Graphene.Rect.alloc()
                prect.init(bx, by, bw_px, 16.0)
                prr = Gsk.RoundedRect()
                prr.init_from_rect(prect, 4.0)
                snapshot.push_rounded_clip(prr)
                snapshot.append_color(_gdk_rgba(_with_alpha(p_bg, alpha)), prect)
                snapshot.pop()
                snapshot.save()
                pt.init(bx + 5.0, by + 2.5)
                snapshot.translate(pt)
                snapshot.append_layout(p_lyt, _gdk_rgba(_with_alpha(p_fg, alpha)))
                snapshot.restore()
                bx += bw_px + 6.0

        in_c = _gdk_rgba(_with_alpha(PORT_IN_COLOR, alpha))
        out_c = _gdk_rgba(_with_alpha(PORT_OUT_COLOR, alpha))
        ring_c = _gdk_rgba((0.06, 0.08, 0.12, alpha))
        for port in node.in_ports:
            px, py = node.x + port.rel_x, node.y + port.rel_y
            self._snapshot_circle(snapshot, px, py, 5.4, ring_c)
            self._snapshot_circle(snapshot, px, py, 4.2, in_c)
        for port in node.out_ports:
            px, py = node.x + port.rel_x, node.y + port.rel_y
            self._snapshot_circle(snapshot, px, py, 5.4, ring_c)
            self._snapshot_circle(snapshot, px, py, 4.2, out_c)

    def _snapshot_kcontrol_node(
        self,
        snapshot: Gtk.Snapshot,
        node: GraphNode,
        is_selected: bool,
        hdr_col: Tuple[float, float, float, float],
        body_col: Tuple[float, float, float, float],
        alpha: float,
        is_ucm: bool,
    ) -> None:
        rect = Graphene.Rect.alloc()
        rect.init(node.x, node.y, node.width, node.height)
        rr = Gsk.RoundedRect()
        rr.init_from_rect(rect, 6.0)

        snapshot.push_rounded_clip(rr)
        snapshot.append_color(_gdk_rgba(body_col), rect)
        hrect = Graphene.Rect.alloc()
        hrect.init(node.x, node.y, node.width, 15.0)
        snapshot.append_color(_gdk_rgba(hdr_col), hrect)
        snapshot.pop()

        k = node.kcontrol
        short_nm = node.title.replace(" Playback ", " ").replace(" Capture ", " ").replace(" Volume", "").replace(" Switch", "")
        pt = Graphene.Point.alloc()
        white_a = _gdk_rgba((1.0, 1.0, 1.0, alpha))

        if node.category == "control_mixer" and k is not None:
            disp_t = short_nm[:8] if len(short_nm) > 8 else short_nm
            if self.renderer.zoom >= 0.25:
                tl = self._get_pango_layout(disp_t, 6.0, True)
                snapshot.save()
                pt.init(node.x + 4.0, node.y + 2.0)
                snapshot.translate(pt)
                snapshot.append_layout(tl, white_a)
                snapshot.restore()

            cur_v = int(k.values[0]) if k.values and isinstance(k.values[0], int) else k.min_val
            span = max(1, k.max_val - k.min_val)
            frac = max(0.0, min(1.0, (cur_v - k.min_val) / float(span)))

            track_x, track_w = node.x + 11.0, 8.0
            track_y, track_h = node.y + 21.0, max(32.0, node.height - 37.0)
            trect = Graphene.Rect.alloc()
            trect.init(track_x, track_y, track_w, track_h)
            trr = Gsk.RoundedRect()
            trr.init_from_rect(trect, 4.0)
            snapshot.push_rounded_clip(trr)
            snapshot.append_color(_gdk_rgba((0.07, 0.08, 0.12, round(0.96 * alpha, 3))), trect)
            if frac > 0.0:
                fill_h = track_h * frac
                frect = Graphene.Rect.alloc()
                frect.init(track_x, track_y + track_h - fill_h, track_w, fill_h)
                snapshot.append_color(_gdk_rgba((0.68, 0.42, 0.98, round(0.96 * alpha, 3))), frect)
            snapshot.pop()
            tbc = _gdk_rgba((0.42, 0.36, 0.62, round(0.92 * alpha, 3)))
            snapshot.append_border(trr, [1.0, 1.0, 1.0, 1.0], [tbc, tbc, tbc, tbc])

            knob_x = track_x + track_w * 0.5
            knob_y = track_y + track_h * (1.0 - frac)
            self._snapshot_circle(snapshot, knob_x, knob_y, 5.2, _gdk_rgba((0.95, 0.97, 1.0, alpha)))
            self._snapshot_circle(snapshot, knob_x, knob_y, 3.4, _gdk_rgba(hdr_col))

            if self.renderer.zoom >= 0.25:
                for txt, tx, ty, sz, bld, col in (
                    (f"{cur_v}", node.x + 25.0, track_y + track_h * 0.25, 7.0, True, white_a),
                    (f"/{k.max_val}", node.x + 25.0, track_y + track_h * 0.52, 6.0, False, _gdk_rgba((0.7, 0.76, 0.84, alpha))),
                    ("UCM" if node.ucm_badges else "VOL", node.x + 17.0, node.y + node.height - 13.0, 6.0, True, _gdk_rgba((0.86, 0.72, 1.0, alpha))),
                ):
                    lyt = self._get_pango_layout(txt, sz, bld)
                    snapshot.save()
                    pt.init(tx, ty)
                    snapshot.translate(pt)
                    snapshot.append_layout(lyt, col)
                    snapshot.restore()
        else:
            if self.renderer.zoom >= 0.25:
                disp_t = short_nm[:12] + "…" if len(short_nm) > 13 else short_nm
                tl = self._get_pango_layout(disp_t, 6.0, True)
                snapshot.save()
                pt.init(node.x + 5.0, node.y + 2.0)
                snapshot.translate(pt)
                snapshot.append_layout(tl, white_a)
                snapshot.restore()

                tag = {"control_switch": "SW", "control_enum": "ENUM", "control_bytes": "BYTES"}.get(node.category, "CTL")
                tgl = self._get_pango_layout(tag, 5.5, True)
                snapshot.save()
                pt.init(node.x + node.width - 24.0, node.y + 2.5)
                snapshot.translate(pt)
                snapshot.append_layout(tgl, _gdk_rgba((0.92, 0.96, 1.0, alpha)))
                snapshot.restore()

            ctrl_y, ctrl_h = node.y + 18.0, 20.0
            crect = Graphene.Rect.alloc()
            crect.init(node.x + 6.0, ctrl_y, node.width - 12.0, ctrl_h)
            crr = Gsk.RoundedRect()

            if node.category == "control_switch" and k is not None:
                is_on = any(bool(v) for v in k.values)
                crr.init_from_rect(crect, 9.5)
                bg = (0.14, 0.56, 0.36, round(0.95 * alpha, 3)) if is_on else (0.22, 0.16, 0.20, round(0.95 * alpha, 3))
                bc = (0.32, 0.92, 0.62, round(0.95 * alpha, 3)) if is_on else (0.65, 0.32, 0.38, round(0.90 * alpha, 3))
                snapshot.push_rounded_clip(crr)
                snapshot.append_color(_gdk_rgba(bg), crect)
                snapshot.pop()
                gbc = _gdk_rgba(bc)
                snapshot.append_border(crr, [1.1, 1.1, 1.1, 1.1], [gbc, gbc, gbc, gbc])

                cx = (node.x + node.width - 16.0) if is_on else (node.x + 16.0)
                kc = (0.65, 1.0, 0.80, alpha) if is_on else (0.75, 0.78, 0.84, alpha)
                self._snapshot_circle(snapshot, cx, ctrl_y + ctrl_h * 0.5, 5.2, _gdk_rgba(kc))
                if self.renderer.zoom >= 0.25:
                    sw_lyt = self._get_pango_layout("ON (Active)" if is_on else "OFF (Muted)", 6.0, True)
                    snapshot.save()
                    pt.init(node.x + (14.0 if is_on else 26.0), ctrl_y + 4.0)
                    snapshot.translate(pt)
                    snapshot.append_layout(sw_lyt, white_a if is_on else _gdk_rgba((0.7, 0.76, 0.84, alpha)))
                    snapshot.restore()
            elif node.category == "control_enum" and k is not None:
                crr.init_from_rect(crect, 4.5)
                snapshot.push_rounded_clip(crr)
                snapshot.append_color(_gdk_rgba((0.08, 0.11, 0.15, round(0.96 * alpha, 3))), crect)
                snapshot.pop()
                gbc = _gdk_rgba((0.86, 0.28, 0.48, round(0.95 * alpha, 3)))
                snapshot.append_border(crr, [1.0, 1.0, 1.0, 1.0], [gbc, gbc, gbc, gbc])
                if self.renderer.zoom >= 0.25:
                    cur_opt = k.formatted_value()
                    disp_opt = cur_opt[:13] + "…" if len(cur_opt) > 14 else cur_opt
                    ol = self._get_pango_layout(disp_opt, 6.0, True)
                    snapshot.save()
                    pt.init(node.x + 10.0, ctrl_y + 4.0)
                    snapshot.translate(pt)
                    snapshot.append_layout(ol, _gdk_rgba((1.0, 0.76, 0.86, alpha)))
                    snapshot.restore()
                    al = self._get_pango_layout("▾", 7.0, True)
                    snapshot.save()
                    pt.init(node.x + node.width - 16.0, ctrl_y + 3.0)
                    snapshot.translate(pt)
                    snapshot.append_layout(al, white_a)
                    snapshot.restore()
            else:
                crr.init_from_rect(crect, 4.5)
                snapshot.push_rounded_clip(crr)
                snapshot.append_color(_gdk_rgba((0.08, 0.12, 0.16, round(0.96 * alpha, 3))), crect)
                snapshot.pop()
                gbc = _gdk_rgba((0.18, 0.68, 0.88, round(0.95 * alpha, 3)))
                snapshot.append_border(crr, [1.0, 1.0, 1.0, 1.0], [gbc, gbc, gbc, gbc])
                if self.renderer.zoom >= 0.25:
                    btxt = k.formatted_value() if k else "bytes"
                    bl = self._get_pango_layout(btxt[:15], 6.0, True)
                    snapshot.save()
                    pt.init(node.x + 10.0, ctrl_y + 4.0)
                    snapshot.translate(pt)
                    snapshot.append_layout(bl, _gdk_rgba((0.68, 0.92, 1.0, alpha)))
                    snapshot.restore()

        if node.has_error:
            bcol, bw = (1.0, 0.28, 0.28, 1.0), 2.2
        elif is_selected:
            bcol, bw = NODE_BORDER_SELECTED, 2.0
        elif is_ucm:
            bcol, bw = NODE_BORDER_UCM, 1.8
        else:
            bcol, bw = hdr_col, 1.1
        gb = _gdk_rgba(bcol)
        snapshot.append_border(rr, [bw, bw, bw, bw], [gb, gb, gb, gb])

    def _snapshot_hud(self, snapshot: Gtk.Snapshot, canvas_w: float) -> None:
        vm = self.renderer.vm
        title = vm.title if vm else "SOF UCM"
        zoom_pct = int(round(self.renderer.zoom * 100.0))
        trace_txt = ""
        if vm and vm.active_ucm_devices:
            trace_txt = f"  •  UCM Active: {', '.join(sorted(vm.active_ucm_devices))}"
        hud_txt = f"{title}  •  {zoom_pct}%{trace_txt}  •  Scroll=Zoom | Drag=Pan | F=Fit"

        lyt = self._get_pango_layout(hud_txt, 8.5, True)
        _, ext = lyt.get_pixel_extents()
        bw = min(canvas_w - 24.0, float(ext.width) + 22.0)
        bh = float(ext.height) + 10.0

        rect = Graphene.Rect.alloc()
        rect.init(12.0, 10.0, bw, bh)
        rr = Gsk.RoundedRect()
        rr.init_from_rect(rect, 6.0)
        snapshot.push_rounded_clip(rr)
        snapshot.append_color(_gdk_rgba((0.08, 0.11, 0.16, 0.88)), rect)
        snapshot.pop()
        bcol = _gdk_rgba((0.26, 0.34, 0.48, 0.90))
        snapshot.append_border(rr, [1.0, 1.0, 1.0, 1.0], [bcol, bcol, bcol, bcol])

        snapshot.save()
        pt = Graphene.Point.alloc()
        pt.init(23.0, 15.0)
        snapshot.translate(pt)
        snapshot.append_layout(lyt, _gdk_rgba(TEXT_WHITE))
        snapshot.restore()
