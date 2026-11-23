# SPDX-License-Identifier: BSD-3-Clause
"""Unit and headless Cairo rendering tests for sof-ucm graph discovery, layout, and PNG export."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

from sof_ucm.discovery import LocalTransport, discover_machine_audio, parse_amixer_contents
from sof_ucm.graph_builder import (
    apply_ucm_overlay_to_graph,
    build_end_to_end_graph,
    trace_reachable_from_node,
)
from sof_ucm.graph_canvas import UcmCanvasRenderer
from sof_ucm.layout import layout_end_to_end_graph
from sof_ucm.model import (
    DapmEdgeInfo,
    DapmWidgetInfo,
    KcontrolInfo,
    MachineAudioSnapshot,
    PcmDeviceInfo,
    SoundCardInfo,
)
from sof_ucm.tplg_loader import parse_tplg_file
from sof_ucm.ucm_serializer import generate_ucm_profile_from_graph, validate_ucm_profile


SAMPLE_AMIXER = """
numid=1,iface=MIXER,name='Master Playback Volume'
  ; type=INTEGER,access=rw---R--,values=1,min=0,max=87,step=0
  : values=64
  | dBscale-min=-65.25dB,step=0.75dB,mute=0
numid=2,iface=MIXER,name='Master Playback Switch'
  ; type=BOOLEAN,access=rw------,values=1
  : values=on
numid=3,iface=MIXER,name='Headphone Playback Switch'
  ; type=BOOLEAN,access=rw------,values=2
  : values=on,on
numid=4,iface=MIXER,name='Speaker Playback Switch'
  ; type=BOOLEAN,access=rw------,values=2
  : values=off,off
numid=5,iface=MIXER,name='DAC Source Mux'
  ; type=ENUMERATED,access=rw------,values=1,items=2
  ; Item #0 'DAC1'
  ; Item #1 'DAC2'
  : values=0
numid=6,iface=CARD,name='Front Headphone Jack'
  ; type=BOOLEAN,access=r-------,values=1
  : values=on
"""


class TestGraphAndRender(unittest.TestCase):
    def test_parse_amixer_contents(self) -> None:
        kctls = parse_amixer_contents(SAMPLE_AMIXER)
        self.assertEqual(len(kctls), 6)
        self.assertEqual(kctls[0].name, "Master Playback Volume")
        self.assertEqual(kctls[0].max_val, 87)
        self.assertEqual(kctls[0].values, [64])
        self.assertAlmostEqual(kctls[0].db_min or 0.0, -65.25)
        self.assertEqual(kctls[4].ctl_type, "ENUMERATED")
        self.assertEqual(kctls[4].enum_items, ["DAC1", "DAC2"])
        self.assertEqual(kctls[4].formatted_value(), "DAC1")

    def test_end_to_end_tplg_plus_dapm_and_render_png(self) -> None:
        tplg_path = "/lib/firmware/intel/sof-ipc4-tplg/sof-hda-generic.tplg"
        tplg = parse_tplg_file(tplg_path) if os.path.isfile(tplg_path) else None

        card = SoundCardInfo(
            card_id=0,
            id="sofhdadsp",
            driver="sof-hda-dsp",
            name="sof-hda-dsp",
            longname="LENOVO-ThinkPad-sof-hda-dsp",
            components="HDA:10ec0287 cfg-dmics:4",
            tplg_path=tplg_path if tplg else "",
        )
        kctls = parse_amixer_contents(SAMPLE_AMIXER)
        snap = MachineAudioSnapshot(
            target_label="test",
            card=card,
            pcms=[
                PcmDeviceInfo(card_id=0, device_id=0, direction="playback", id="HDA Analog", name="HDA Analog", active_rate=48000, active_format="S32_LE", active_channels=2),
                PcmDeviceInfo(card_id=0, device_id=6, direction="capture", id="DMIC", name="DMIC", active_rate=48000, active_format="S32_LE", active_channels=4),
            ],
            kcontrols=kctls,
            kcontrols_by_name={k.name: k for k in kctls},
            tplg=tplg,
            tplg_available=tplg is not None,
            dapm_widgets=[
                DapmWidgetInfo(name="dai-copier.HDA.Analog.playback", component="0000:00:1f.3", widget_type="dai_in", power=1),
                DapmWidgetInfo(name="DAC1", component="ehdaudio0D0", widget_type="dac", power=1),
                DapmWidgetInfo(name="Speaker", component="card", widget_type="spk", power=1),
                DapmWidgetInfo(name="Headphones", component="card", widget_type="hp", power=0),
            ],
            dapm_edges=[
                DapmEdgeInfo(src_name="dai-copier.HDA.Analog.playback", src_comp="0000:00:1f.3", dst_name="DAC1", dst_comp="ehdaudio0D0", active=True),
                DapmEdgeInfo(src_name="DAC1", src_comp="ehdaudio0D0", dst_name="Speaker", dst_comp="card", control_name="Speaker Playback Switch", active=True),
                DapmEdgeInfo(src_name="DAC1", src_comp="ehdaudio0D0", dst_name="Headphones", dst_comp="card", control_name="Headphone Playback Switch", active=False),
            ],
            dapm_available=True,
        )

        vm = build_end_to_end_graph(snap)
        layout_end_to_end_graph(vm)
        self.assertGreater(len(vm.nodes), 4)
        self.assertGreater(len(vm.edges), 2)

        # Generate UCM profile and overlay onto graph
        profile = generate_ucm_profile_from_graph(snap, vm)
        self.assertIn("HiFi", profile.verbs)
        diags = validate_ucm_profile(profile, snap, vm)
        self.assertIsInstance(diags, list)

        first_dev = next(iter(profile.verbs["HiFi"].devices.keys()))
        apply_ucm_overlay_to_graph(vm, profile, "HiFi", {first_dev})
        self.assertTrue(vm.has_active_trace)

        # Trace from PCM0
        vis_n, vis_e = trace_reachable_from_node(vm, "pcm:playback:0")
        self.assertIn("pcm:playback:0", vis_n)

        # Headless Cairo PNG render smoke test
        with tempfile.TemporaryDirectory() as tmpd:
            out_png = os.path.join(tmpd, "test_graph.png")
            r = UcmCanvasRenderer()
            r.vm = vm
            r.zoom = 0.75
            r.render_to_png(out_png, width=1600, height=900)
            self.assertTrue(os.path.isfile(out_png))
            self.assertGreater(os.path.getsize(out_png), 4000)

    def test_local_machine_card_discovery_and_render(self) -> None:
        transport = LocalTransport()
        snap = discover_machine_audio(transport, 2)
        vm = build_end_to_end_graph(snap)
        layout_end_to_end_graph(vm)
        self.assertGreater(len(vm.nodes), 0)
        pcm_nodes = [n for n in vm.nodes.values() if n.class_category == "PCM"]
        ep_nodes = [n for n in vm.nodes.values() if n.class_category == "Endpoint"]
        mid_nodes = [n for n in vm.nodes.values() if n.class_category not in ("PCM", "Endpoint", "Control")]
        self.assertGreater(len(pcm_nodes), 0)
        self.assertGreater(len(ep_nodes), 0)
        # All PCMs share one left vertical column; all Endpoints share one right vertical column
        self.assertEqual(len({round(n.x, 2) for n in pcm_nodes}), 1)
        self.assertEqual(len({round(n.x, 2) for n in ep_nodes}), 1)
        max_pcm_right = max(n.x + n.width for n in pcm_nodes)
        min_ep_left = min(n.x for n in ep_nodes)
        self.assertGreater(min_ep_left, max_pcm_right)
        if mid_nodes:
            max_mid_right = max(n.x + n.width for n in mid_nodes)
            min_mid_left = min(n.x for n in mid_nodes)
            self.assertGreater(min_mid_left, max_pcm_right)
            self.assertGreater(min_ep_left, max_mid_right)
        with tempfile.TemporaryDirectory() as tmpd:
            out_png = os.path.join(tmpd, "local_card.png")
            r = UcmCanvasRenderer()
            r.vm = vm
            r.render_to_png(out_png, width=1400, height=800)
            self.assertTrue(os.path.isfile(out_png))

    def test_columnar_layout_pcm_left_endpoints_right(self) -> None:
        snap = MachineAudioSnapshot(
            target_label="test-col",
            card=SoundCardInfo(card_id=0, id="testcard", driver="snd_hda_intel", name="testcard", longname="testcard", components=""),
            pcms=[
                PcmDeviceInfo(card_id=0, device_id=0, direction="playback", id="Audio", name="Audio"),
                PcmDeviceInfo(card_id=0, device_id=0, direction="capture", id="Audio", name="Audio"),
                PcmDeviceInfo(card_id=0, device_id=1, direction="capture", id="DMIC", name="DMIC"),
            ],
            dapm_widgets=[
                DapmWidgetInfo(name="DAC1", component="ehdaudio0D0", widget_type="dac", power=1),
                DapmWidgetInfo(name="ADC1", component="ehdaudio0D0", widget_type="adc", power=1),
                DapmWidgetInfo(name="Speaker", component="card", widget_type="spk", power=1),
                DapmWidgetInfo(name="Headphone Jack", component="card", widget_type="hp", power=0),
                DapmWidgetInfo(name="Mic Jack", component="card", widget_type="mic", power=0),
                DapmWidgetInfo(name="Line In", component="card", widget_type="line", power=0),
                DapmWidgetInfo(name="Line Out", component="card", widget_type="line", power=0),
            ],
            dapm_edges=[
                DapmEdgeInfo(src_name="DAC1", src_comp="ehdaudio0D0", dst_name="Speaker", dst_comp="card", active=True),
                DapmEdgeInfo(src_name="DAC1", src_comp="ehdaudio0D0", dst_name="Headphone Jack", dst_comp="card"),
                DapmEdgeInfo(src_name="DAC1", src_comp="ehdaudio0D0", dst_name="Line Out", dst_comp="card"),
                DapmEdgeInfo(src_name="Mic Jack", src_comp="card", dst_name="ADC1", dst_comp="ehdaudio0D0"),
                DapmEdgeInfo(src_name="Line In", src_comp="card", dst_name="ADC1", dst_comp="ehdaudio0D0"),
            ],
            dapm_available=True,
        )
        vm = build_end_to_end_graph(snap)
        layout_end_to_end_graph(vm)
        pcms = [n for n in vm.nodes.values() if n.class_category == "PCM"]
        eps = [n for n in vm.nodes.values() if n.class_category == "Endpoint"]
        self.assertEqual(len(pcms), 3)
        self.assertEqual(len(eps), 5)
        self.assertEqual(len({round(n.x, 2) for n in pcms}), 1)
        self.assertEqual(len({round(n.x, 2) for n in eps}), 1)
        self.assertGreater(min(n.x for n in eps), max(n.x + n.width for n in pcms))
        for p in pcms:
            for port in p.in_ports + p.out_ports:
                self.assertEqual(round(port.rel_x, 2), round(p.width, 2))
        for ep in eps:
            for port in ep.in_ports + ep.out_ports:
                self.assertEqual(round(port.rel_x, 2), 0.0)


if __name__ == "__main__":
    unittest.main()
