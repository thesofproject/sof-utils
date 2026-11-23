# SPDX-License-Identifier: BSD-3-Clause
"""Entry point for SOF UCM — GTK4 End-to-End Pipeline Visualizer & ALSA UCM2 Editor."""

from __future__ import annotations
import argparse
import sys
from typing import List, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, Gtk

from .discovery import LocalTransport, SshTransport, discover_cards, discover_machine_audio
from .graph_builder import build_end_to_end_graph
from .graph_canvas import UcmCanvasRenderer
from .layout import layout_end_to_end_graph
from .ucm_parser import load_ucm_profile
from .ucm_serializer import generate_ucm_profile_from_graph, validate_ucm_profile
from .window import SofUcmMainWindow


APP_CSS = """
window {
    background-color: #10131a;
    color: #e2e8f0;
}
.heading {
    font-weight: bold;
    color: #93c5fd;
}
.high-level-panel {
    border: 2px solid #475569;
    border-radius: 6px;
    background-color: #141821;
}
.high-level-panel > border {
    border: none;
}
.sub-panel-frame, frame {
    border: 1px solid #334155;
    border-radius: 4px;
}
frame > border {
    border: none;
}
paned > separator {
    background-color: #334155;
    min-width: 5px;
    min-height: 5px;
}
paned > separator:hover {
    background-color: #60a5fa;
}
treeview {
    background-color: #181c26;
    color: #e2e8f0;
}
treeview:selected {
    background-color: #2563eb;
    color: #ffffff;
}
textview {
    background-color: #0f1219;
    color: #cbd5e1;
    font-size: 11px;
}
"""


class SofUcmApp(Gtk.Application):
    def __init__(self, args: argparse.Namespace):
        super().__init__(
            application_id="org.thesofproject.SofUcm",
            flags=Gio.ApplicationFlags.NON_UNIQUE,
        )
        self.args = args
        self.win: Optional[SofUcmMainWindow] = None

    def do_activate(self) -> None:
        display = Gdk.Display.get_default()
        if display:
            provider = Gtk.CssProvider()
            provider.load_from_string(APP_CSS)
            Gtk.StyleContext.add_provider_for_display(
                display,
                provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )

        if not self.win:
            self.win = SofUcmMainWindow(
                self,
                ssh_target=self.args.ssh,
                ssh_port=self.args.ssh_port,
                ssh_key=self.args.ssh_key,
                initial_card=self.args.card,
                tplg_override=self.args.tplg,
                ucm_dir=self.args.ucm_dir,
                sudo_debugfs=self.args.sudo_debugfs,
            )
            self.win.maximize()
        self.win.present()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sof-ucm",
        description="SOF UCM — GTK4 End-to-End Audio Pipeline Visualizer & ALSA UCM2 Editor",
    )
    parser.add_argument("--ssh", metavar="USER@HOST", help="Inspect and configure a remote machine via SSH")
    parser.add_argument("--ssh-port", type=int, default=22, help="SSH port (default: 22)")
    parser.add_argument("--ssh-key", metavar="FILE", help="Optional SSH identity key file")
    parser.add_argument("-c", "--card", type=int, default=None, help="ALSA card index to open")
    parser.add_argument("-t", "--tplg", metavar="FILE", help="Explicit .tplg topology file path")
    parser.add_argument("--ucm-dir", default="/usr/share/alsa/ucm2", help="UCM2 root directory")
    parser.add_argument("--sudo-debugfs", action="store_true", help="Use sudo -n to read /sys/kernel/debug/asoc")
    parser.add_argument("--render-png", metavar="OUT.png", help="Render discovered card pipeline offscreen to PNG and exit")
    parser.add_argument("--width", type=int, default=1600, help="PNG width for --render-png")
    parser.add_argument("--height", type=int, default=900, help="PNG height for --render-png")

    parsed = parser.parse_args(argv if argv is not None else sys.argv[1:])

    if parsed.render_png:
        transport = (
            SshTransport(parsed.ssh, port=parsed.ssh_port, identity_file=parsed.ssh_key)
            if parsed.ssh
            else LocalTransport()
        )
        cards = discover_cards(transport)
        cid = parsed.card if parsed.card is not None else (cards[0].card_id if cards else 0)
        snap = discover_machine_audio(
            transport,
            cid,
            tplg_override=parsed.tplg,
            use_sudo_debugfs=parsed.sudo_debugfs,
        )
        vm = build_end_to_end_graph(snap)
        layout_end_to_end_graph(vm)
        profile = load_ucm_profile(transport, snap, ucm_root=parsed.ucm_dir)
        if profile is None:
            profile = generate_ucm_profile_from_graph(snap, vm, ucm_root=parsed.ucm_dir)
        validate_ucm_profile(profile, snap, vm)
        renderer = UcmCanvasRenderer()
        renderer.vm = vm
        renderer.fit_to_bounds(float(parsed.width), float(parsed.height))
        renderer.render_to_png(parsed.render_png, width=parsed.width, height=parsed.height)
        print(f"[sof-ucm] Rendered {len(vm.nodes)} nodes, {len(vm.edges)} edges -> {parsed.render_png}")
        return 0

    app = SofUcmApp(parsed)
    return app.run([sys.argv[0]])


if __name__ == "__main__":
    raise SystemExit(main())
