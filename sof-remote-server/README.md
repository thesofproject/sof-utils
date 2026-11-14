# `sof-remote-server` & `sof-remote`

Lightweight C TCP telemetry, IPC, LLEXT, mixer, and audio debug daemon (`sof_remote_server`, default TCP port `9998`) and CLI client (`sof-remote`) for Linux and Android SOF targets.

---

## Building for Linux

```bash
# From top-level sof-utils/
make remote-server

# Or directly in sof-remote-server/
make -C sof-remote-server
```

Install dependencies on Debian/Ubuntu if building with ALSA/TinyALSA/TinyCompress:
```bash
sudo apt install libasound2-dev
```

### CMake (Linux)

```bash
cmake -B sof-remote-server/build -S sof-remote-server
cmake --build sof-remote-server/build -j$(nproc)
```

---

## Building for Android

`sof-remote-server` supports three Android build workflows using Bionic + `NO_ALSA` + TinyALSA + TinyCompress (`<tinyalsa/asoundlib.h>` or `<tinyalsa/pcm.h>`/`mixer.h`).

### 1. Standalone `make android` (Android NDK or AOSP Tree)

#### With Android NDK
```bash
export ANDROID_NDK_HOME=/path/to/android-ndk

# Default (x86_64, API 34)
make android

# ARM64
make android ANDROID_ABI=arm64-v8a ANDROID_API=34
```

#### With an AOSP Checkout
When `AOSP_DIR` is set (and `ANDROID_NDK_HOME` is unset), the Makefile automatically discovers the prebuilt Clang toolchain, Soong NDK sysroot (`out/soong/ndk/sysroot`), Soong Bionic CRT objects (`crtbegin_dynamic.o` / `crtend_android.o` mapped via `ANDROID_SOONG_ARCH`: `arm64`, `arm`, `x86`, `x86_64`), and `external/tinyalsa` + `external/tinycompress`:

```bash
make android AOSP_DIR=/path/to/aosp ANDROID_ABI=x86_64 ANDROID_API=34
make android AOSP_DIR=/path/to/aosp ANDROID_ABI=arm64-v8a ANDROID_API=34
```

Output binaries are written to `sof-remote-server/build-android-<ANDROID_ABI>/` (`sof_remote_server` and `sof-remote`).

#### Push & Run Over ADB
```bash
make android-push ANDROID_ABI=arm64-v8a
adb shell /data/local/tmp/sof_remote_server -p 9998
```
`android-push` pushes both binaries to `/data/local/tmp/` and runs `adb forward tcp:9998 tcp:9998`.

---

### 2. CMake with Android NDK Toolchain

```bash
cmake -B sof-remote-server/build-android-cmake \
  -S sof-remote-server \
  -DCMAKE_TOOLCHAIN_FILE=$ANDROID_NDK_HOME/build/cmake/android.toolchain.cmake \
  -DANDROID_ABI=arm64-v8a \
  -DANDROID_PLATFORM=android-34
cmake --build sof-remote-server/build-android-cmake -j$(nproc)
```

Optional TinyALSA / TinyCompress CMake variables:
- `-DTINYALSA_INC=/path/to/tinyalsa/include` and `-DTINYALSA_LIB=/path/to/libtinyalsa.so`
- `-DTINYCOMPRESS_INC=/path/to/tinycompress/include` and `-DTINYCOMPRESS_LIB=/path/to/libtinycompress.so`

*(On `ANDROID`, CMake automatically omits `-lpthread` since pthreads are integrated into Bionic `libc`.)*

---

### 3. In-Tree AOSP Soong Build (`Android.bp`)

Place or vendor-map `sof-remote-server/` inside the AOSP source tree and build directly with Soong:

```bash
m sof_remote_server sof-remote
```

[`Android.bp`](Android.bp) builds `vendor`-proprietary targets linking `libtinyalsa`, `libtinycompress`, and `liblog`.

---

## Runtime Options & Android Security Model

```text
sof_remote_server [-b <bind_ip>] [-d /dev/sof-dsp0] [-p 9998] [-D] [-E|-S]
```

| Flag | Default (Linux) | Default (Android) | Description |
| :--- | :--- | :--- | :--- |
| `-b <addr>` | `0.0.0.0` | `127.0.0.1` | IPv4 bind address. Android defaults to loopback for use with `adb forward tcp:9998 tcp:9998`. |
| `-d <dev>` | `/dev/sof-dsp0` | `/dev/sof-dsp0` | SOF character device path (falls back to DebugFS/SysFS + TinyALSA when absent). |
| `-p <port>` | `9998` | `9998` | TCP listening port. |
| `-D` | off | off | Daemonize (fork to background). |
| `-E` / `-S` | `-E` (enabled) | `-S` (disabled) | Enable (`-E`) or disable (`-S`) `host:` shell command execution (`SOF_NET_OP_SHELL_EXEC` via `popen()`). |

### Android Init Service (`sof_remote_server.rc`)

[`sof_remote_server.rc`](sof_remote_server.rc) defines `vendor.sof_remote_server` (`disabled` by default), binding `127.0.0.1:9998` without `host:` shell execution, and starts only when both `ro.debuggable=1` and the explicit opt-in property are set:

```bash
adb shell setprop persist.vendor.sof.remote_server.enable 1   # start
adb shell setprop persist.vendor.sof.remote_server.enable 0   # stop
adb forward tcp:9998 tcp:9998
```

---

## Reference SELinux Policy (`sepolicy/`)

A reference vendor policy for `userdebug` / `eng` builds is provided in [`sepolicy/`](sepolicy/):
- [`sepolicy/file_contexts`](sepolicy/file_contexts) (`/vendor/bin/sof_remote_server`)
- [`sepolicy/property.te`](sepolicy/property.te) & [`sepolicy/property_contexts`](sepolicy/property_contexts) (`persist.vendor.sof.remote_server.enable`)
- [`sepolicy/sof_remote_server.te`](sepolicy/sof_remote_server.te) (wrapped in `userdebug_or_eng(...)`)

Include it in a device `BoardConfig.mk`:
```makefile
BOARD_VENDOR_SEPOLICY_DIRS += <path-to-sof-utils>/sof-remote-server/sepolicy
```
If your kernel/product exposes a dedicated `/dev/sof-dsp0` character device node under SELinux enforcing mode, add a product `file_contexts` device label and corresponding `rw_file_perms` allow rule for `sof_remote_server`.
