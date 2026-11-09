# SOF Utilities (`sof-utils`)

Developer utilities, visual tools, and host applications for the [Sound Open Firmware (SOF)](https://thesofproject.github.io/) ecosystem.

---

## Included Tools

| Tool | Directory | Description |
| :--- | :--- | :--- |
| **[`sof-top`](sof-top/sof-top)** | [`sof-top/`](sof-top/) | GTK4 Live DSP Resource Monitor — Moving Core Utilization Graph, Moving Memory Utilization Graph (HPSRAM, LPSRAM, DRAM L3/IMR, Heaps), and Unified DSP Task List |
| **[`sof-remote-server`](sof-remote-server/)** | [`sof-remote-server/`](sof-remote-server/) | Lightweight C TCP Telemetry & Control Daemon (Port `9998`) for DUTs via `/dev/sof-dsp0` or direct PCIe BAR4 MMIO + DebugFS/SysFS fallback |
| **[`sof-builder`](sof-builder/)** | [`sof-builder/`](sof-builder/) | GTK4 Topology2 Visual Graph Editor, IPC4 `.tplg` Compiler & Real-Time `sof-testbench4` Simulation / Telemetry Studio |
| **[`sof-testbench`](sof-testbench/)** | [`sof-testbench/`](sof-testbench/) | Multi-Core LL/DP `sof-testbench4` Simulator, `sof-tb-ctl` TCP Control CLI, Host Platform Runtime (`platform/library`), and 70-Test Suite |
| **[`sof-tplg-parser`](sof-tplg-parser/)** | [`sof-tplg-parser/`](sof-tplg-parser/) | ALSA Topology (`.tplg`) IPC3/IPC4 Parser Library (`libsof_tplg_parser.a`) |

---

## Build, Install & Package (`make`, `.deb`, `.rpm`)

The top-level [`Makefile`](Makefile) builds, installs, and packages all `sof-utils` tools (`sof-builder`, `sof-top`, `sof_remote_server`, `sof-remote`, `sof-testbench4`, `sof-tb-ctl`, and `libsof_tplg_parser.a`):

```bash
# Build all tools (auto-detects SOF_SRC_DIR or pass SOF_SRC_DIR=/path/to/sof):
make

# Install all binaries, libraries, headers, Python apps, and .desktop files:
sudo make install PREFIX=/usr

# Build Debian (.deb) package into dist/:
make deb

# Build RPM (.rpm) package into dist/:
make rpm

# Build both .deb and .rpm packages into dist/:
make packages
```

---

## Quick Start: `sof-testbench4` & `sof-tb-ctl`

`sof-testbench/` builds `sof-testbench4` and `sof-tb-ctl` in a local build directory by compiling `sof-utils`'s host platform runtime (`sof-testbench/platform/library` and `sof-testbench/arch/host`) together with the firmware audio modules, pipeline engine, and IPC code directly from an SOF checkout (`${SOF_SRC_DIR}/src/`), leaving the `sof` checkout completely untouched.

### 1. Align Versions with `west` (Optional)
```bash
west init -l sof-utils
west update
```

### 2. Configure & Build
```bash
cmake -B sof-testbench/build -S sof-testbench -DSOF_SRC_DIR=/path/to/sof
cmake --build sof-testbench/build -j$(nproc)
```
*(If `-DSOF_SRC_DIR` is omitted, CMake auto-detects `sof` via `west list -f "{abspath}" sof`, `$SOF_SRC_DIR`, or `../sof`.)*

### 3. Run the 5-Tier (70-Test) Verification Suite
```bash
ctest --test-dir sof-testbench/build --output-on-failure
```

---

## Quick Start: `sof-builder`

### 1. Install Dependencies

**Ubuntu / Debian:**
```bash
sudo apt update
sudo apt install python3 python3-pip python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-pango-1.0 alsa-utils
```

**Python Packages (`pip`):**
```bash
pip install -r sof-builder/requirements.txt
```
*(Required Python packages: `PyGObject>=3.42.0`, `pycairo>=1.20.0`)*

### 2. Run or Install `sof-builder`

```bash
# Run directly:
./sof-builder/sof-builder /path/to/sof/tools/topology/topology2

# Or install as a Python CLI package:
pip install -e ./sof-builder
sof-builder /path/to/sof/tools/topology/topology2
```

See [`sof-builder/README.md`](sof-builder/README.md) for full documentation, environment variables (`SOF_ROOT`, `SOF_TOPOLOGY2_DIR`, `SOF_TESTBENCH_BIN`, `ALSATPLG`), and feature guides.

---

## Quick Start: `sof-top`

```bash
# Build sof_remote_server for DUTs:
make -C sof-remote-server

# Launch sof-top (Moving Core Graph + Moving SRAM/DRAM Memory Graph + DSP Task List):
./sof-top/sof-top --host aphid --wake
```

---

## License

Licensed under the **BSD 3-Clause License**. See [`LICENSE`](LICENSE) for details.
