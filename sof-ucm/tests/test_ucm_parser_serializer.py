# SPDX-License-Identifier: BSD-3-Clause
"""Unit tests for ALSA UCM2 parser, condition evaluator, serializer, and validator."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

from sof_ucm.discovery import LocalTransport
from sof_ucm.model import (
    KcontrolInfo,
    MachineAudioSnapshot,
    PcmDeviceInfo,
    SoundCardInfo,
    UcmProfile,
    UcmSectionDevice,
    UcmSequenceCmd,
    UcmVerb,
)
from sof_ucm.ucm_editor import check_ucm_syntax_balance
from sof_ucm.ucm_parser import load_ucm_profile, parse_ucm_ast, _apply_ucm_items, build_ucm_eval_context
from sof_ucm.ucm_serializer import (
    serialize_master_conf,
    serialize_verb_conf,
    validate_ucm_profile,
)


class TestUcmParserAndSerializer(unittest.TestCase):
    def test_load_real_system_sof_hda_dsp_ucm(self) -> None:
        ucm_file = "/usr/share/alsa/ucm2/Intel/sof-hda-dsp/sof-hda-dsp.conf"
        if not os.path.isfile(ucm_file):
            self.skipTest("System UCM2 sof-hda-dsp.conf not installed")

        kctls = [
            KcontrolInfo(numid=1, name="Dmic0 Capture Volume", ctl_type="INTEGER", max_val=70, values=[50]),
            KcontrolInfo(numid=2, name="Dmic0 Capture Switch", ctl_type="BOOLEAN", values=[1]),
            KcontrolInfo(numid=3, name="Master Playback Switch", ctl_type="BOOLEAN", values=[1]),
            KcontrolInfo(numid=4, name="Headphone Playback Volume", ctl_type="INTEGER", max_val=87, values=[80]),
            KcontrolInfo(numid=5, name="Headphone Playback Switch", ctl_type="BOOLEAN", values=[1]),
            KcontrolInfo(numid=6, name="Speaker Playback Volume", ctl_type="INTEGER", max_val=87, values=[80]),
            KcontrolInfo(numid=7, name="Speaker Playback Switch", ctl_type="BOOLEAN", values=[1]),
        ]
        snap = MachineAudioSnapshot(
            card=SoundCardInfo(
                card_id=0,
                id="sofhdadsp",
                driver="sof-hda-dsp",
                name="sof-hda-dsp",
                longname="sof-hda-dsp",
                components="cfg-dmics:4 iec61937-pcm:3,4,5",
            ),
            pcms=[
                PcmDeviceInfo(card_id=0, device_id=0, direction="playback"),
                PcmDeviceInfo(card_id=0, device_id=3, direction="playback"),
                PcmDeviceInfo(card_id=0, device_id=6, direction="capture"),
            ],
            kcontrols=kctls,
            kcontrols_by_name={k.name: k for k in kctls},
        )

        profile = load_ucm_profile(
            LocalTransport(),
            snap,
            ucm_root="/usr/share/alsa/ucm2",
            master_path_override=ucm_file,
        )
        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertIn("HiFi", profile.verbs)
        hifi = profile.verbs["HiFi"]
        self.assertIn("Mic1", hifi.devices)
        self.assertEqual(hifi.devices["Mic1"].values.get("CaptureVolume"), "Dmic0 Capture Volume")

    def test_roundtrip_serialize_and_validate(self) -> None:
        kctls = [
            KcontrolInfo(numid=1, name="Speaker Playback Volume", ctl_type="INTEGER", min_val=0, max_val=100, values=[80]),
            KcontrolInfo(numid=2, name="Speaker Playback Switch", ctl_type="BOOLEAN", values=[1]),
        ]
        snap = MachineAudioSnapshot(
            card=SoundCardInfo(card_id=0, id="sofcard", driver="sof-test", longname="sof-test-board"),
            pcms=[PcmDeviceInfo(card_id=0, device_id=0, direction="playback")],
            kcontrols=kctls,
            kcontrols_by_name={k.name: k for k in kctls},
        )
        profile = UcmProfile(
            card_name="sof-test",
            card_longname="sof-test-board",
            card_driver="sof-test",
            ucm_root_dir="/tmp/ucm2",
            master_rel_path="conf.d/sof-test/sof-test-board.conf",
            syntax_version=6,
            boot_seq=[UcmSequenceCmd("cset", "name='Speaker Playback Volume' 80")],
        )
        verb = UcmVerb(
            name="HiFi",
            file_path="/sof-test/HiFi.conf",
            comment="HiFi",
            values={"TQ": "HiFi"},
            devices={
                "Speaker": UcmSectionDevice(
                    name="Speaker",
                    comment="Internal Speakers",
                    values={
                        "PlaybackPCM": "hw:${CardId},0",
                        "PlaybackVolume": "Speaker Playback Volume",
                        "PlaybackSwitch": "Speaker Playback Switch",
                    },
                    enable_seq=[UcmSequenceCmd("cset", "name='Speaker Playback Switch' on")],
                    disable_seq=[UcmSequenceCmd("cset", "name='Speaker Playback Switch' off")],
                ),
                "BrokenDev": UcmSectionDevice(
                    name="BrokenDev",
                    values={"PlaybackPCM": "hw:${CardId},9"},
                    enable_seq=[UcmSequenceCmd("cset", "name='NonExistent Switch' on")],
                ),
            },
        )
        profile.verbs["HiFi"] = verb

        master_txt = serialize_master_conf(profile)
        verb_txt = serialize_verb_conf(verb)
        self.assertTrue(check_ucm_syntax_balance(master_txt)[0])
        self.assertTrue(check_ucm_syntax_balance(verb_txt)[0])

        # Re-parse serialized texts
        rebuilt = UcmProfile(ucm_root_dir="/tmp/ucm2")
        ctx = build_ucm_eval_context(snap)
        _apply_ucm_items(parse_ucm_ast(master_txt), rebuilt, ctx, snap, None, "sof-test-board.conf")
        self.assertIn("HiFi", rebuilt.verbs)
        _apply_ucm_items(parse_ucm_ast(verb_txt), rebuilt, ctx, snap, None, "HiFi.conf", cur_verb=rebuilt.verbs["HiFi"])
        self.assertIn("Speaker", rebuilt.verbs["HiFi"].devices)
        self.assertEqual(rebuilt.verbs["HiFi"].devices["Speaker"].values["PlaybackPCM"], "hw:sofcard,0")

        # Validate catches missing PCM9 & missing kcontrol
        diags = validate_ucm_profile(profile, snap)
        errs = [d for d in diags if d.severity == "error"]
        self.assertGreaterEqual(len(errs), 2)


if __name__ == "__main__":
    unittest.main()
