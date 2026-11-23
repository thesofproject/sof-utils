# SPDX-License-Identifier: BSD-3-Clause
"""End-to-end layered super-graph + Pipeline Swimlane layout engine (zero container overlap)."""

from __future__ import annotations
from collections import defaultdict, deque
from typing import Dict, List, Set, Tuple

from .model import GraphEdge, GraphNode, GraphPort, UcmGraphViewModel


NODE_WIDTH = 200.0
NODE_BASE_HEIGHT = 86.0
CTRL_VOL_WIDTH = 56.0
CTRL_VOL_HEIGHT = 92.0
CTRL_COMPACT_WIDTH = 118.0
CTRL_COMPACT_HEIGHT = 42.0
CTRL_TOP_GAP = 22.0
CTRL_H_GAP = 6.0
CTRL_V_GAP = 6.0
CTRL_GROUP_PAD = 6.0
CTRL_GROUP_HDR = 15.0
H_GAP = 72.0
V_GAP = 28.0
CONTAINER_PAD_X = 24.0
CONTAINER_PAD_TOP = 42.0
CONTAINER_PAD_BOTTOM = 20.0
SUPER_GAP_X = 78.0
SUPER_GAP_Y = 34.0
LANE_GAP_Y = 46.0


def _compute_node_geometry(node: GraphNode, mirror_capture: bool = False) -> None:
    if node.class_category == "Control":
        if node.category == "control_mixer":
            node.width = CTRL_VOL_WIDTH
            node.height = CTRL_VOL_HEIGHT
        else:
            node.width = CTRL_COMPACT_WIDTH
            node.height = CTRL_COMPACT_HEIGHT
        for port in node.in_ports + node.out_ports:
            port.rel_x = node.width / 2.0
            port.rel_y = 0.0
        return

    left_ports: List[Tuple[str, object]] = []
    right_ports: List[Tuple[str, object]] = []
    if node.class_category == "PCM":
        # PCMs always form the left vertical column; ports face right toward the center.
        for p in node.out_ports:
            right_ports.append(("out", p))
        for p in node.in_ports:
            right_ports.append(("in", p))
    elif node.class_category == "Endpoint":
        # Endpoints (speakers, mics, jacks, line in/out, HDMI, etc.) always form
        # the right vertical column; ports face left toward the center.
        for p in node.in_ports:
            left_ports.append(("in", p))
        for p in node.out_ports:
            left_ports.append(("out", p))
    else:
        # Middle DSP/codec nodes: playback flows Left->Right, capture flows Right->Left.
        is_rev = node.direction == "capture"
        if is_rev:
            for p in node.out_ports:
                left_ports.append(("out", p))
            for p in node.in_ports:
                right_ports.append(("in", p))
        else:
            for p in node.in_ports:
                left_ports.append(("in", p))
            for p in node.out_ports:
                right_ports.append(("out", p))

    num_ports = max(len(left_ports), len(right_ports), 1)
    has_bottom_row = bool(node.in_fmt_badge or node.out_fmt_badge or node.badges or node.ucm_badges)
    badge_extra = 20.0 if has_bottom_row else 0.0
    title_w = len(node.title) * 7.5 + 32.0
    sub_w = len(node.subtitle) * 6.5 + 28.0
    badge_w = sum(len(b) * 6.4 + 18.0 for b in (node.badges + node.ucm_badges)[:3]) + 24.0
    in_fmt_w = (len(node.in_fmt_badge) * 6.5 + 16.0) if node.in_fmt_badge else 0.0
    out_fmt_w = (len(node.out_fmt_badge) * 6.5 + 16.0) if node.out_fmt_badge else 0.0
    fmt_row_w = in_fmt_w + out_fmt_w + (34.0 if (in_fmt_w and out_fmt_w) else 24.0)

    node.width = max(NODE_WIDTH, min(320.0, max(title_w, sub_w, badge_w, fmt_row_w)))
    node.height = max(NODE_BASE_HEIGHT, 54.0 + num_ports * 18.0 + badge_extra)

    l_count = len(left_ports)
    for i, (_kind, port) in enumerate(left_ports):
        port.rel_x = 0.0
        port.rel_y = 34.0 + (i + 0.5) * ((node.height - 38.0) / max(l_count, 1))

    r_count = len(right_ports)
    for i, (_kind, port) in enumerate(right_ports):
        port.rel_x = node.width
        port.rel_y = 34.0 + (i + 0.5) * ((node.height - 38.0) / max(r_count, 1))


def _measure_and_place_module_controls(
    vm: UcmGraphViewModel,
    ctrls: List[str],
    origin_x: float = 0.0,
    origin_y: float = 0.0,
    apply_pos: bool = False,
) -> Tuple[float, float]:
    if not ctrls:
        return (0.0, 0.0)
    vol_ids = [cid for cid in ctrls if vm.nodes[cid].category == "control_mixer"]
    compact_ids = [cid for cid in ctrls if vm.nodes[cid].category != "control_mixer"]

    cx = origin_x
    max_h = 0.0

    for cid in vol_ids:
        cnode = vm.nodes[cid]
        if apply_pos:
            cnode.x = cx
            cnode.y = origin_y
        cx += cnode.width + CTRL_H_GAP
        max_h = max(max_h, cnode.height)

    for col_start in range(0, len(compact_ids), 2):
        col_slice = compact_ids[col_start:col_start + 2]
        col_w = max(vm.nodes[cid].width for cid in col_slice)
        cy = origin_y
        for idx, cid in enumerate(col_slice):
            cnode = vm.nodes[cid]
            if apply_pos:
                cnode.x = cx
                cnode.y = cy
            cy += cnode.height + (CTRL_V_GAP if idx < len(col_slice) - 1 else 0.0)
        max_h = max(max_h, cy - origin_y)
        cx += col_w + CTRL_H_GAP

    total_inner_w = max(0.0, cx - origin_x - CTRL_H_GAP)
    return (total_inner_w, max_h)


def _assign_edge_ports(vm: UcmGraphViewModel) -> None:
    out_edges_by_node: Dict[str, List[GraphEdge]] = defaultdict(list)
    in_edges_by_node: Dict[str, List[GraphEdge]] = defaultdict(list)

    for edge in vm.edges:
        u = vm.nodes.get(edge.source_node)
        v = vm.nodes.get(edge.sink_node)
        if not u or not v or u.class_category == "Control":
            continue
        out_edges_by_node[u.id].append(edge)
        in_edges_by_node[v.id].append(edge)

    for uid, edges in out_edges_by_node.items():
        u = vm.nodes[uid]
        if not u.out_ports:
            continue
        sorted_out = sorted(
            edges,
            key=lambda e: (vm.nodes[e.sink_node].y + vm.nodes[e.sink_node].height / 2.0, vm.nodes[e.sink_node].x, e.id),
        )
        for idx, edge in enumerate(sorted_out):
            edge.source_port = u.out_ports[min(idx, len(u.out_ports) - 1)].id

    for vid, edges in in_edges_by_node.items():
        v = vm.nodes[vid]
        if not v.in_ports:
            continue
        sorted_in = sorted(
            edges,
            key=lambda e: (vm.nodes[e.source_node].y + vm.nodes[e.source_node].height / 2.0, vm.nodes[e.source_node].x, e.id),
        )
        for idx, edge in enumerate(sorted_in):
            edge.sink_port = v.in_ports[min(idx, len(v.in_ports) - 1)].id


def _layout_dag_block(
    vm: UcmGraphViewModel,
    node_ids: List[str],
    ctrl_by_parent: Dict[str, List[str]],
    edges: List[GraphEdge],
    mirror_capture: bool = False,
) -> Tuple[float, float]:
    """Lay out `node_ids` + their kcontrol clusters relative to (0, 0). Returns (width, height)."""
    if not node_ids:
        return (0.0, 0.0)
    comp_set = set(node_ids)

    def group_width(nid: str) -> float:
        w = vm.nodes[nid].width
        ctrls = ctrl_by_parent.get(nid, [])
        if ctrls:
            inner_w, _ = _measure_and_place_module_controls(vm, ctrls, apply_pos=False)
            w = max(w, inner_w + 2.0 * CTRL_GROUP_PAD)
        return w

    def group_height(nid: str) -> float:
        h = vm.nodes[nid].height
        ctrls = ctrl_by_parent.get(nid, [])
        if ctrls:
            _, inner_h = _measure_and_place_module_controls(vm, ctrls, apply_pos=False)
            h += CTRL_TOP_GAP + inner_h + CTRL_GROUP_PAD
        return h

    adj: Dict[str, List[str]] = defaultdict(list)
    in_deg: Dict[str, int] = {nid: 0 for nid in node_ids}
    for e in edges:
        if e.source_node in comp_set and e.sink_node in comp_set and e.source_node != e.sink_node:
            u, v = (
                (e.sink_node, e.source_node)
                if (mirror_capture and vm.nodes[e.source_node].direction == "capture")
                else (e.source_node, e.sink_node)
            )
            adj[u].append(v)
            in_deg[v] += 1

    layer_of: Dict[str, int] = {nid: 0 for nid in node_ids}
    q = deque([nid for nid in node_ids if in_deg[nid] == 0])
    seen: Set[str] = set()
    while q:
        u = q.popleft()
        seen.add(u)
        for v in adj[u]:
            layer_of[v] = max(layer_of[v], layer_of[u] + 1)
            in_deg[v] -= 1
            if in_deg[v] == 0:
                q.append(v)
    for nid in node_ids:
        if nid not in seen:
            layer_of[nid] = max(layer_of.values(), default=0) + 1

    col_map: Dict[int, List[str]] = defaultdict(list)
    for nid in node_ids:
        col_map[layer_of[nid]].append(nid)
    sorted_cols = [col_map[c] for c in sorted(col_map.keys())]
    for col in sorted_cols:
        col.sort(key=lambda nid: vm.nodes[nid].title)

    col_heights = [
        sum(group_height(nid) for nid in col) + max(0, len(col) - 1) * V_GAP
        for col in sorted_cols
    ]
    total_h = max(col_heights, default=80.0)

    cur_x = 0.0
    for c_idx, col in enumerate(sorted_cols):
        col_w = max(group_width(nid) for nid in col)
        cur_y = (total_h - col_heights[c_idx]) / 2.0
        for nid in col:
            n = vm.nodes[nid]
            gw = group_width(nid)
            n.x = cur_x + (gw - n.width) / 2.0
            n.y = cur_y
            ctrls = ctrl_by_parent.get(nid, [])
            if ctrls:
                inner_w, _ = _measure_and_place_module_controls(vm, ctrls, apply_pos=False)
                ctrl_x = n.x + (n.width - inner_w) / 2.0
                ctrl_y = n.y + n.height + CTRL_TOP_GAP
                _measure_and_place_module_controls(vm, ctrls, origin_x=ctrl_x, origin_y=ctrl_y, apply_pos=True)
            cur_y += group_height(nid) + V_GAP
        cur_x += col_w + H_GAP

    total_w = max(0.0, cur_x - H_GAP)
    return total_w, total_h


def layout_end_to_end_graph(vm: UcmGraphViewModel, mirror_capture: bool = False) -> None:
    """Layout end-to-end pipelines in 3 columnar zones: PCMs (left) -> Middle DSP/Codecs -> Endpoints (right)."""
    if not vm.nodes:
        return

    for e in vm.edges:
        u = vm.nodes.get(e.source_node)
        v = vm.nodes.get(e.sink_node)
        if u and u.class_category != "Control" and not u.out_ports:
            u.out_ports.append(GraphPort(id="out0", label="out", direction="out"))
        if v and v.class_category != "Control" and not v.in_ports:
            v.in_ports.append(GraphPort(id="in0", label="in", direction="in"))

    for node in vm.nodes.values():
        _compute_node_geometry(node, mirror_capture=mirror_capture)

    ctrl_by_parent: Dict[str, List[str]] = defaultdict(list)
    pcm_ids: List[str] = []
    ep_ids: List[str] = []
    mid_ids: List[str] = []
    orphan_ctrl_ids: List[str] = []
    for nid, n in vm.nodes.items():
        if n.class_category == "Control":
            if n.parent_node_id and n.parent_node_id in vm.nodes:
                ctrl_by_parent[n.parent_node_id].append(nid)
            else:
                orphan_ctrl_ids.append(nid)
        elif n.class_category == "PCM":
            pcm_ids.append(nid)
        elif n.class_category == "Endpoint":
            ep_ids.append(nid)
        else:
            mid_ids.append(nid)

    for pid in ctrl_by_parent:
        ctrl_by_parent[pid].sort(key=lambda cid: (0 if vm.nodes[cid].category == "control_mixer" else 1, vm.nodes[cid].title))

    margin_x = 56.0
    margin_y = 56.0

    # --- Zone 1 (Left Vertical Column): ALL PCM nodes ---
    def pcm_sort_key(nid: str) -> Tuple[int, int, str, str]:
        n = vm.nodes[nid]
        dev = n.pcm_info.device_id if n.pcm_info is not None else 999
        d_ord = 0 if n.direction == "playback" else 1
        return (d_ord, dev, n.title, nid)

    pcm_ids.sort(key=pcm_sort_key)

    single_dims: Dict[str, Tuple[float, float]] = {}
    orig_x: Dict[str, float] = {}
    for nid in pcm_ids + ep_ids:
        bw, bh = _layout_dag_block(vm, [nid], ctrl_by_parent, [], mirror_capture=True)
        single_dims[nid] = (bw, bh)
        orig_x[nid] = vm.nodes[nid].x

    def shift_single_node(nid: str, dx: float, dy: float) -> None:
        vm.nodes[nid].x += dx
        vm.nodes[nid].y += dy
        for cid in ctrl_by_parent.get(nid, []):
            vm.nodes[cid].x += dx
            vm.nodes[cid].y += dy

    pcm_left_overhang = max((orig_x[nid] for nid in pcm_ids), default=0.0)
    pcm_card_x = margin_x + pcm_left_overhang
    cur_pcm_y = 0.0
    left_col_right_edge = margin_x
    for nid in pcm_ids:
        bw, bh = single_dims[nid]
        dx = pcm_card_x - orig_x[nid]
        shift_single_node(nid, dx, cur_pcm_y)
        left_col_right_edge = max(left_col_right_edge, dx + bw)
        cur_pcm_y += bh + SUPER_GAP_Y
    pcm_total_h = max(0.0, cur_pcm_y - SUPER_GAP_Y) if pcm_ids else 0.0

    # Preliminary PCM vertical rank / y for ordering middle lanes cleanly
    pcm_center_y: Dict[str, float] = {
        nid: vm.nodes[nid].y + vm.nodes[nid].height * 0.5 for nid in pcm_ids
    }

    # --- Zone 2 (Middle Columns): PipelineContainers + DSP/Codec widgets ---
    def mid_super_id_of(nid: str) -> str:
        n = vm.nodes[nid]
        if n.pipeline_id is not None and n.pipeline_id in vm.containers:
            return f"pipe:{n.pipeline_id}"
        return f"node:{nid}"

    mid_set = set(mid_ids)
    super_members: Dict[str, List[str]] = defaultdict(list)
    for nid in mid_ids:
        super_members[mid_super_id_of(nid)].append(nid)

    super_dims: Dict[str, Tuple[float, float]] = {}
    for sid, members in super_members.items():
        if sid.startswith("pipe:"):
            bw, bh = _layout_dag_block(vm, members, ctrl_by_parent, vm.edges, mirror_capture=True)
            for nid in members:
                vm.nodes[nid].x += CONTAINER_PAD_X
                vm.nodes[nid].y += CONTAINER_PAD_TOP
                for cid in ctrl_by_parent.get(nid, []):
                    vm.nodes[cid].x += CONTAINER_PAD_X
                    vm.nodes[cid].y += CONTAINER_PAD_TOP
            super_dims[sid] = (
                max(220.0, bw + 2.0 * CONTAINER_PAD_X),
                max(110.0, bh + CONTAINER_PAD_TOP + CONTAINER_PAD_BOTTOM),
            )
        else:
            nid = members[0]
            bw, bh = _layout_dag_block(vm, [nid], ctrl_by_parent, [], mirror_capture=True)
            super_dims[sid] = (bw, bh)

    # Full node adjacency to associate middle nodes/lanes with connected PCMs and Endpoints
    all_nbrs: Dict[str, Set[str]] = defaultdict(set)
    mid_undir: Dict[str, Set[str]] = defaultdict(set)
    super_edges: Set[Tuple[str, str]] = set()
    for e in vm.edges:
        if e.source_node not in vm.nodes or e.sink_node not in vm.nodes:
            continue
        all_nbrs[e.source_node].add(e.sink_node)
        all_nbrs[e.sink_node].add(e.source_node)
        if e.source_node in mid_set and e.sink_node in mid_set:
            su = mid_super_id_of(e.source_node)
            sv = mid_super_id_of(e.sink_node)
            if su != sv:
                mid_undir[su].add(sv)
                mid_undir[sv].add(su)
                is_cap_edge = vm.nodes[e.source_node].direction == "capture"
                if is_cap_edge:
                    super_edges.add((sv, su))
                else:
                    super_edges.add((su, sv))

    # Find connected PCMs reachable from any node without crossing other PCMs/Endpoints
    def connected_pcm_ys_for_member(nid: str) -> List[float]:
        ys: List[float] = []
        seen_n: Set[str] = {nid}
        dq = deque([nid])
        while dq:
            u = dq.popleft()
            if u in pcm_center_y:
                ys.append(pcm_center_y[u])
                continue
            for v in all_nbrs[u]:
                if v not in seen_n and vm.nodes[v].class_category != "Endpoint":
                    seen_n.add(v)
                    dq.append(v)
        return ys

    def sid_pcm_bary(sid: str) -> float:
        ys: List[float] = []
        for nid in super_members[sid]:
            ys.extend(connected_pcm_ys_for_member(nid))
        return (sum(ys) / len(ys)) if ys else 1e9

    visited: Set[str] = set()
    mid_lanes: List[List[str]] = []
    for sid in sorted(super_members.keys()):
        if sid in visited:
            continue
        lane: List[str] = []
        q = deque([sid])
        visited.add(sid)
        while q:
            u = q.popleft()
            lane.append(u)
            for v in sorted(mid_undir[u]):
                if v not in visited:
                    visited.add(v)
                    q.append(v)
        mid_lanes.append(lane)

    def mid_lane_sort_key(lane: List[str]) -> Tuple[float, int, int, str]:
        all_nodes = [vm.nodes[nid] for sid in lane for nid in super_members[sid]]
        has_pb = any(n.direction == "playback" for n in all_nodes)
        ys = [sid_pcm_bary(sid) for sid in lane]
        finite_ys = [y for y in ys if y < 1e8]
        avg_y = (sum(finite_ys) / len(finite_ys)) if finite_ys else 1e9
        pcm_devs = [n.pcm_info.device_id for n in all_nodes if n.pcm_info is not None]
        return (avg_y, 0 if has_pb else 1, min(pcm_devs) if pcm_devs else 999, lane[0])

    mid_lanes.sort(key=mid_lane_sort_key)

    def shift_super_node(sid: str, dx: float, dy: float) -> None:
        for nid in super_members[sid]:
            shift_single_node(nid, dx, dy)

    cur_mid_y = 0.0
    mid_total_w = 0.0
    for lane in mid_lanes:
        lset = set(lane)
        adj: Dict[str, List[str]] = defaultdict(list)
        in_deg: Dict[str, int] = {sid: 0 for sid in lane}
        for su, sv in super_edges:
            if su in lset and sv in lset:
                adj[su].append(sv)
                in_deg[sv] += 1

        col_of: Dict[str, int] = {sid: 0 for sid in lane}
        q = deque([sid for sid in lane if in_deg[sid] == 0])
        seen: Set[str] = set()
        while q:
            u = q.popleft()
            seen.add(u)
            for v in adj[u]:
                col_of[v] = max(col_of[v], col_of[u] + 1)
                in_deg[v] -= 1
                if in_deg[v] == 0:
                    q.append(v)
        for sid in lane:
            if sid not in seen:
                col_of[sid] = max(col_of.values(), default=0) + 1

        cols: Dict[int, List[str]] = defaultdict(list)
        for sid in lane:
            cols[col_of[sid]].append(sid)
        sorted_cols = [cols[c] for c in sorted(cols.keys())]
        for col in sorted_cols:
            col.sort(key=lambda s: (sid_pcm_bary(s), s))

        col_heights = [
            sum(super_dims[sid][1] for sid in col) + max(0, len(col) - 1) * SUPER_GAP_Y
            for col in sorted_cols
        ]
        lane_h = max(col_heights, default=120.0)

        cur_x = 0.0
        for c_idx, col in enumerate(sorted_cols):
            col_w = max(super_dims[sid][0] for sid in col)
            cur_y = cur_mid_y + (lane_h - col_heights[c_idx]) / 2.0
            for sid in col:
                sw, sh = super_dims[sid]
                dx = cur_x + (col_w - sw) / 2.0
                dy = cur_y
                shift_super_node(sid, dx, dy)
                cur_y += sh + SUPER_GAP_Y
            cur_x += col_w + SUPER_GAP_X
        if sorted_cols:
            mid_total_w = max(mid_total_w, cur_x - SUPER_GAP_X)
        cur_mid_y += lane_h + LANE_GAP_Y

    mid_total_h = max(0.0, cur_mid_y - LANE_GAP_Y) if mid_lanes else 0.0
    ep_total_h = (
        sum(single_dims[nid][1] for nid in ep_ids) + max(0, len(ep_ids) - 1) * SUPER_GAP_Y
        if ep_ids
        else 0.0
    )

    max_zone_h = max(pcm_total_h, mid_total_h, ep_total_h, 120.0)

    # Apply horizontal + vertical centering offsets to Zone 1 (PCMs) and Zone 2 (Middle)
    pcm_y_off = margin_y + (max_zone_h - pcm_total_h) * 0.5
    for nid in pcm_ids:
        shift_single_node(nid, 0.0, pcm_y_off)

    zone2_left_x = (left_col_right_edge + SUPER_GAP_X) if pcm_ids else margin_x
    mid_y_off = margin_y + (max_zone_h - mid_total_h) * 0.5
    for sid in super_members:
        shift_super_node(sid, zone2_left_x, mid_y_off)

    if mid_lanes:
        zone2_right_edge = zone2_left_x + mid_total_w
    elif pcm_ids:
        zone2_right_edge = left_col_right_edge
    else:
        zone2_right_edge = margin_x - SUPER_GAP_X

    # --- Zone 3 (Right Vertical Column): ALL Endpoints (speakers, mics, jacks, line in/out, HDMI, etc.) ---
    def ep_bary_y(nid: str) -> float:
        nbr_ys = [
            vm.nodes[nb].y + vm.nodes[nb].height * 0.5
            for nb in all_nbrs[nid]
            if vm.nodes[nb].class_category != "Endpoint"
        ]
        return (sum(nbr_ys) / len(nbr_ys)) if nbr_ys else 1e9

    ep_ids.sort(key=lambda nid: (ep_bary_y(nid), 0 if vm.nodes[nid].direction == "playback" else 1, vm.nodes[nid].title, nid))

    ep_left_overhang = max((orig_x[nid] for nid in ep_ids), default=0.0)
    ep_card_x = zone2_right_edge + SUPER_GAP_X + ep_left_overhang
    cur_ep_y = margin_y + (max_zone_h - ep_total_h) * 0.5
    for nid in ep_ids:
        _bw, bh = single_dims[nid]
        dx = ep_card_x - orig_x[nid]
        shift_single_node(nid, dx, cur_ep_y)
        cur_ep_y += bh + SUPER_GAP_Y

    # Place any unparented orphan controls neatly along a bottom row
    if orphan_ctrl_ids:
        cur_ox = margin_x
        oy = margin_y + max_zone_h + LANE_GAP_Y
        for cid in sorted(orphan_ctrl_ids):
            c = vm.nodes[cid]
            c.x = cur_ox
            c.y = oy
            cur_ox += c.width + CTRL_H_GAP

    update_container_bounds(vm)
    _assign_edge_ports(vm)


def update_container_bounds(vm: UcmGraphViewModel) -> None:
    members_by_pid: Dict[int, List[GraphNode]] = defaultdict(list)
    for n in vm.nodes.values():
        if n.pipeline_id is not None and n.pipeline_id in vm.containers:
            members_by_pid[n.pipeline_id].append(n)

    for pid, cont in list(vm.containers.items()):
        members = members_by_pid.get(pid, [])
        if not members:
            vm.containers.pop(pid, None)
            continue
        x0 = min(n.x for n in members) - CONTAINER_PAD_X
        y0 = min(n.y for n in members) - CONTAINER_PAD_TOP
        x1 = max(n.x + n.width for n in members) + CONTAINER_PAD_X
        y1 = max(n.y + n.height for n in members) + CONTAINER_PAD_BOTTOM
        cont.x = x0
        cont.y = y0
        cont.width = max(220.0, x1 - x0)
        cont.height = max(110.0, y1 - y0)
