# SOF Utilities (`sof-utils`)

Developer utilities, visual tools, and host applications for the [Sound Open Firmware (SOF)](https://thesofproject.github.io/) ecosystem.

---

## Included Tools

| Tool | Directory | Description |
| :--- | :--- | :--- |
| **[`sof-builder`](sof-builder/)** | [`sof-builder/`](sof-builder/) | GTK4 Topology2 Visual Graph Editor, IPC4 `.tplg` Compiler & Real-Time `sof-testbench4` Simulation / Telemetry Studio |

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

## License

Licensed under the **BSD 3-Clause License**. See [`LICENSE`](LICENSE) for details.
