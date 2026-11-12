# SOF Teensy 4.1 Hardware Utilities (`sof-teensy`)

Host utilities for flashing, inspecting, and validating PJRC Teensy 4.1 (NXP i.MX RT1062) boards running Sound Open Firmware (SOF) over USB Audio Class 2.0 (UAC2).

## Included Scripts

| Script | Installed CLI | Description |
| :--- | :--- | :--- |
| [`teensy_flash.py`](teensy_flash.py) | `sof-teensy-flash` | Triggers PJRC HalfKay bootloader entry on a specific Teensy 4.1 board (`--serial a` or `--serial b`) and flashes an Intel HEX image via `teensy_loader_cli` |
| [`test_teensy_loopback.py`](test_teensy_loopback.py) | `sof-teensy-loopback` | Runs automated cross-board digital audio loopback verification (S/PDIF or SAI1 I2S) between Board A (Master Tx) and Board B (Slave Rx), computing FFT peak frequency, SNR, and RMS amplitude |
| [`teensy_diag.py`](teensy_diag.py) | `sof-teensy-diag` | Queries diagnostic builds exposing optional USB vendor status reports |

## Usage

### 1. Flash Board A and Board B
```bash
./sof-teensy/teensy_flash.py --serial a --hex /path/to/build-teensya/zephyr/zephyr.hex
./sof-teensy/teensy_flash.py --serial b --hex /path/to/build-teensyb/zephyr/zephyr.hex
```

### 2. Run Digital Loopback Verification
```bash
# S/PDIF transceiver loopback (Board A Pin 14 -> Board B Pin 15)
./sof-teensy/test_teensy_loopback.py --interface spdif

# SAI1 I2S loopback (Pins 7, 8, 20, 21, 23)
./sof-teensy/test_teensy_loopback.py --interface i2s
```
