# SOF Builder (`sof-builder`)

**SOF Builder** is a GTK4 visual graph editor, ALSA Topology2 (`.conf` / `.tplg`) IDE, IPC4 binary inspector, and real-time Sound Open Firmware (`sof-testbench4`) simulation & telemetry studio.

---

## Features

- **4-Pane Topology2 Workspace Browser**:
  - Browse and search Base Classes (`Class.*`), Processing Modules (`include/components/`), Pipelines (`include/pipelines/`), and full Machine Topologies (`sof-*.conf` and CMake target variants).
- **Interactive GTK4 Topology Graph Canvas**:
  - Visual node-and-wire graph editing for pipelines, widgets, pins, and host/DAI endpoints.
  - Real-time violation indicators (`!` badges) directly on modules that report testbench/firmware errors or xruns.
  - Interactive Kcontrol sliders/switches, Probe Tap injection/extraction points, and Testbench I/O signal generators (`sine`, `chirp`, `white_noise`, `impulse`, custom `.wav`).
- **Bi-Directional ALSA `.conf` Source & Attribute Inspector**:
  - Live syntax-highlighted `alsaconf` source editor synchronized with the visual graph and right-hand hierarchical attribute tree.
- **One-Click IPC4 `.tplg` Compilation (`alsatplg`)**:
  - Compiles full machine topologies or standalone pipelines/modules using an automatic bench harness wrapper and highlights compiler diagnostics directly in the source and attribute inspector.
- **Real-Time `sof-testbench4` Simulation & Telemetry (`SOFR` TCP Protocol)**:
  - Connects to `sof-testbench4` over its binary TCP control & telemetry interface.
  - **Waveform & Spectrum Viewer**: Multi-channel input/output PCM waveforms and FFT spectrum analysis.
  - **Scheduling, Core, Pipeline & Pin Buffer Telemetry**:
    - Per-core CPU utilization, LL tick deadlines, and DP thread metrics.
    - Per-module cycle budgets (CPC), KCPS, and input/output pin ring-buffer watermarks.
    - Interactive expandable violation trees and a searchable **Telemetry Log** notebook tab that links directly to the graph timeline and offending module.

---

## Python & System Dependencies

### 1. Python Dependencies

`sof-builder` requires **Python 3.10+** and the following Python packages (listed in [`requirements.txt`](requirements.txt) and [`pyproject.toml`](pyproject.toml)):

| Python Package | Version | Purpose |
| :--- | :--- | :--- |
| **`PyGObject`** | `>= 3.42.0` | Python GObject Introspection bindings (`gi.repository.Gtk`, `Gdk`, `Gio`, `GLib`, `GObject`, `Graphene`, `Gsk`, `Pango`) |
| **`pycairo`** | `>= 1.20.0` | Cairo 2D graphics bindings required by `PyGObject` / GTK4 |

All other modules used by `sof-builder` (`argparse`, `dataclasses`, `json`, `math`, `pathlib`, `re`, `shutil`, `socket`, `struct`, `subprocess`, `tempfile`, `threading`, `wave`, etc.) are part of the Python standard library.

### 2. System Packages (GTK4 Typelibs & ALSA Tools)

Because `PyGObject` dynamically loads native GTK4 GObject Introspection typelibs (`Gtk-4.0.typelib`), install the GTK4 runtime and ALSA topology tools from your Linux distribution:

#### Ubuntu / Debian
```bash
sudo apt update
sudo apt install \
    python3 \
    python3-pip \
    python3-venv \
    python3-gi \
    python3-gi-cairo \
    gir1.2-gtk-4.0 \
    gir1.2-pango-1.0 \
    alsa-utils \
    alsa-topology-conf

# Optional: only needed if compiling PyGObject/pycairo from source inside an isolated virtualenv
sudo apt install \
    build-essential \
    pkg-config \
    libcairo2-dev \
    libgirepository1.0-dev \
    libgtk-4-dev
```

#### Fedora / RHEL
```bash
sudo dnf install \
    python3 \
    python3-pip \
    python3-gobject \
     python3-cairo \
    gtk4 \
    pango \
    alsa-utils \
    alsa-topology
```

#### Arch Linux
```bash
sudo pacman -S \
    python \
    python-pip \
    python-gobject \
    python-cairo \
    gtk4 \
    pango \
    alsa-utils \
    alsa-topology-conf
```

---

## Installation

### Option A: Run Directly from Checkout (Using System `python3-gi`)
```bash
./sof-builder /path/to/sof/tools/topology/topology2
```

### Option B: Install via `pip`
If using a virtual environment, creating the venv with `--system-site-packages` allows reuse of the distribution's prebuilt `python3-gi` / `gir1.2-gtk-4.0` packages without compiling C extensions:

```bash
cd sof-builder
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .

# Launch from anywhere:
sof-builder /path/to/sof/tools/topology/topology2
```

---

## Usage & Environment Variables

```bash
usage: sof-builder [-h] [-d] [--debug-file FILE] [initial_dir]
```

| Argument / Environment Variable | Description |
| :--- | :--- |
| `initial_dir` / `SOF_TOPOLOGY2_DIR` | Path to the SOF `tools/topology/topology2` directory to load on startup. |
| `SOF_ROOT` | Path to the root of an SOF repository checkout (used to locate `tools/topology/topology2`, `sof_versions.h`, and `sof-testbench4`). |
| `ALSATPLG` | Optional explicit path to the `alsatplg` binary (defaults to `PATH` or workspace build). |
| `SOF_TESTBENCH_BIN` | Optional explicit path to the `sof-testbench4` binary (defaults to `<SOF_ROOT>/tools/testbench/build_testbench/sof-testbench4` or `PATH`). |
| `-d`, `--debug` | Enable action & command debug logging to `sof-builder-debug.log`. |
| `--debug-file FILE` | Write debug log output to `FILE`. |

---

## License

SPDX-License-Identifier: `BSD-3-Clause` — see [`../LICENSE`](../LICENSE).
