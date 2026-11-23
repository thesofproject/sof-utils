# SOF UCM (`sof-ucm`)

**SOF UCM** is a GTK4 end-to-end audio pipeline visualizer and ALSA UCM2 profile editor/generator for Sound Open Firmware (SOF) systems — supporting both the **local machine** and **remote DUTs via SSH**.

---

## Features

- **Full Audio Stack Discovery (Local or Remote SSH)**:
  - Enumerates ALSA sound cards (`/proc/asound/cards`, `/sys/class/sound`, DMI) and PCM/Compress streams.
  - Auto-detects and parses the active binary topology (`.tplg`) including DSP pipelines, modules, pin formats, routes, and widget-owned kcontrols.
  - Reads all ALSA kcontrols (`amixer -c N contents`: volumes, switches, enums, bytes, TLV dB scales, jacks).
  - Reads live ASoC DAPM widgets, routes, bias levels, and DPCM FE→BE states from `/sys/kernel/debug/asoc` (with automatic synthesis fallback when debugfs is unprivileged).
- **GTK4 + `Gtk.Snapshot` + Cairo End-to-End Graph**:
  - Renders each pipeline from **ALSA PCM → Host Copier → DSP Modules/Mixers/Gains → DAI Copier → Codec DAPM → Hardware Endpoint** in the visual style of `sof-builder`.
  - Interactive vertical `VOL` faders, `SW` toggles, and `ENUM` popovers anchored beneath their owning DSP/Codec/Endpoint nodes.
- **ALSA UCM2 Visualization, Editing & Creation**:
  - Parses and evaluates UCM2 profiles (`Syntax 4..7`, `SectionUseCase`, `SectionVerb`, `SectionDevice`, `SectionModifier`, `BootSequence`, `If`/`Condition`, `Include`).
  - Highlights the exact PCM, DSP modules, kcontrols, and hardware endpoints claimed by checked UCM devices.
  - **New UCM Wizard**: Auto-generates a UCM2 profile by tracing PCM-to-endpoint paths in the live graph.
  - **Record → UCM**: Dragging sliders/toggles on the canvas records `cset` steps into the selected device's `EnableSequence`.
  - **Validate & Test (`alsaucm`)**: Stages edits in a temporary `ALSA_CONFIG_UCM2` directory and tests them live without overwriting system files.

---

## Usage

```bash
# Inspect local machine:
./sof-ucm

# Inspect a specific local card or explicit .tplg:
./sof-ucm --card 2 --tplg /lib/firmware/intel/sof-ipc4-tplg/sof-hda-generic.tplg

# Inspect & configure a remote DUT via SSH:
./sof-ucm --ssh root@dut-host

# Headless offscreen PNG export:
./sof-ucm --card 2 --render-png pipeline.png
```

---

## License

SPDX-License-Identifier: `BSD-3-Clause`
