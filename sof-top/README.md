# SOF Top (`sof-top`)

Hardware-accelerated **GTK4 + Cairo** real-time Sound Open Firmware (SOF) DSP resource, memory, and task monitor for Linux host systems and remote DUTs (`sof_remote_server`).

`sof-top` connects over `SOFZ`-compressed TCP (`sof_remote_server`) or a local character device (`/dev/sof-dsp0` + DebugFS/SysFS) to provide an immediate, `htop`/`btop`-style view of DSP load, memory footprint, and running tasks.

---

## Features

- **Moving DSP Core Utilization Graph**:
  - Rolling 60-second time-series graph of per-core CPU load (`Core 0..N` in `%` of DSP clock and `KCPS`) plus aggregate peak/average load.
  - Per-core summary cards showing clock frequency (`MHz`), active state, average/peak `KCPS`, and utilization bars.
- **Moving DSP Memory Utilization Graph (SRAM + DRAM)**:
  - Rolling 60-second multi-trace time-series graph tracking:
    - **HPSRAM** (`L2 High-Performance SRAM` powered banks, e.g., `24 x 128 KB = 3072 KB`)
    - **LPSRAM** (`L2 Low-Power SRAM` powered banks, e.g., `1 x 64 KB = 64 KB`)
    - **DRAM (`L3 / IMR`)** (`IMR / L3` backing storage, e.g., `16,384 KB` budget + `.l3_rodata` footprint + LLEXT DRAM heaps)
    - **DSP Heaps** (Combined active allocations across System, Runtime, Module, Shared, Buffer, and LLEXT heaps)
  - Side-by-side capacity gauges for `HPSRAM`, `LPSRAM`, `DRAM (L3/IMR)`, and `DSP Heaps`.
- **Unified DSP Task List with Resources & Status**:
  - Consolidates **Scheduler Domains (`LL` / `DP`)**, **Zephyr RTOS Threads (`TEL2`)**, **Audio Processing Modules (`IPC4` / `PMW2`)**, and **DAI Gateways** into a single filterable, sortable table (`Sort: CPU %`, `Memory KB`, `Type`, `Name`) showing Task ID, Name/UUID, Type, Core, Status (`ACTIVE`, `READY`, `RESIDENT`, `IDLE`, `GATED`), `CPU %`, `KCPS` (avg/max), `SRAM (KB)`, `DRAM (KB)`, and Stack/Cycles/XRUN details.

---

## Python & System Dependencies

### 1. Python Dependencies

`sof-top` requires **Python 3.10+** and the following Python packages (listed in [`requirements.txt`](requirements.txt) and [`pyproject.toml`](pyproject.toml)):

| Python Package | Version | Purpose |
| :--- | :--- | :--- |
| **`PyGObject`** | `>= 3.42.0` | Python GObject Introspection bindings (`Gtk 4.0`, `Gdk 4.0`, `GLib 2.0`, `Pango 1.0`, `PangoCairo 1.0`) |
| **`pycairo`** | `>= 1.20.0` | Cairo 2D vector graphics bindings for hardware-accelerated custom GTK4 widgets and headless PNG rendering |

### 2. System Packages (Ubuntu / Debian)
```bash
sudo apt update
sudo apt install \
    python3 \
    python3-pip \
    python3-gi \
    python3-gi-cairo \
    gir1.2-gtk-4.0 \
    gir1.2-pango-1.0
```

---

## Usage

`sof-top` is directly executable (`#!/usr/bin/env python3`, `chmod +x`) — no need to prefix `python3`:

```bash
# Launch connected to remote DUT (e.g. aphid):
./sof-top/sof-top --host aphid --wake

# Connect to a local character device (/dev/sof-dsp0):
./sof-top/sof-top --local /dev/sof-dsp0

# Headless high-DPI PNG snapshot export (for CI or reports):
./sof-top/sof-top --host aphid --wake --render-png /tmp/softop_user
```

### Command-Line Options

| Option | Default | Description |
| :--- | :--- | :--- |
| `-H`, `--host HOST` | `aphid` | Remote DUT hostname or IP running `sof_remote_server` |
| `-p`, `--port PORT` | `9998` | Remote TCP port for `sof_remote_server` |
| `-l`, `--local DEV` | *(empty)* | Local character device path (e.g. `/dev/sof-dsp0`) instead of TCP |
| `-i`, `--interval MS` | `250` | Telemetry poll interval in milliseconds |
| `-w`, `--wake` | `false` | Hold/wake DSP via PM runtime when polling telemetry |
| `--render-png PATH` | *(empty)* | Render dashboard headlessly to PNG file and exit |

---

## License

SPDX-License-Identifier: `BSD-3-Clause` — see [`../LICENSE`](../LICENSE).
