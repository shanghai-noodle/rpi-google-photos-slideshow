# 🖼️ Linux Native Google Photos Slideshow

[![Version](https://img.shields.io/badge/version-v1.0.0-38bdf8)](https://github.com/shanghai-noodle/rpi-google-photos-slideshow)
[![License: MIT](https://img.shields.io/badge/License-MIT-10b981.svg)](LICENSE)
[![Created with Google Antigravity](https://img.shields.io/badge/Created%20with-Google%20Antigravity-8b5cf6)](https://deepmind.google/)

**[English](README.md) | [简体中文](README.zh-CN.md)**

A lightweight, native Linux digital photo frame application (`x86_64` & `ARM64`/`ARMv7`) that streams directly from a **Google Photos Shared Album** (or built-in **30-item Demo Showcase Pack**) with **9 even-gap portrait & landscape collage templates**, **subject-aware center cropping**, **live FFmpeg video tiles**, a **password-protected 6-language Web Control Portal**, and **zero disk/flash wear** (`100%` RAM logging & caching).

> ✨ **Created with Google Antigravity** by **shanghai-noodle**.

---

## ✨ Key Features

- **🎨 9 Even-Gap Collage Templates + Portrait Single-Slide Priority**:
  - **Strict Orientation Matching & Center-Crop Fitting**: Portrait photos/videos (`h >= w`) are placed exclusively into portrait tiles (`h > w`) and landscape photos/videos (`w > h`) into landscape tiles (`w > h`), using center-crop (`ImageOps.fit`) so no photo is ever stretched or mismatched.
  - **Dark Bokeh Backdrop + Thin `2px` White Borders**: All multi-tile collages are rendered over a rich dark bokeh background with crisp `2px` white gallery borders and uniform `16px` spacing:
    - `collage` *(default)*: Weighted mix (~32% full-screen portrait/landscape single slides + all 9 even-gap grid templates).
    - `trio`: 3-tile grid (1 top landscape + 2 bottom portraits).
    - `classic-5`: 5-tile grid (1 top hero landscape + 2 middle portraits + 2 bottom landscapes).
    - `mosaic-7`: 7-tile alternating mosaic with bottom-right feature portrait.
    - `sidebar-9`: 9-tile stacked left column + right feature portrait.
    - `mondrian-8`: 8-tile symmetrical Mondrian mosaic.
    - `grid-3-2-3`: 8-tile 3-row layout (3 portraits + 2 landscapes + 3 portraits).
    - `stack`: 2-tile vertical landscape stack.
    - `triple`: 3-tile vertical landscape stack.
    - `bento`: 4-tile Bento grid (1 hero + 2 middle + 1 bottom banner).
    - `single`: 1 full-screen photo or video per slide.
- **🎬 Live Video Streaming in Any Tile (`--videos play | skip | still`)**:
  - Automatically detects videos in Google Photos shared albums (`76647426` metadata key) and local `.mp4`/`.mov` files.
  - Prefetches transcoded MP4 streams (`=m18`) into `/dev/shm` (`tmpfs` RAM disk) and streams them smoothly via `ffmpeg` directly into any single or multi-tile collage slot (sequencing multiple video tiles on the same slide one after another).
- **🌐 Password-Protected Local Web Configuration Portal (`http://photoframe.local:8080`)**:
  - **Friendly mDNS URL & ~1×1 Inch On-Screen QR Code**: Advertises `http://photoframe.local:8080` via a built-in zero-config mDNS responder and displays a bottom-right translucent card (with the friendly URL, fallback IP `http://<device-ip>:8080`, and a **~1×1 inch scannable QR code**) **on top of both photos and videos** for the **first 60 seconds** of startup and **at all times in Demo Showcase Mode**.
  - **Multi-Language UI**: Full localization for **English**, **中文 (Chinese)**, **日本語 (Japanese)**, **한국어 (Korean)**, **Español (Spanish)**, and **Deutsch (German)**.
  - **Security**: Protected by `PBKDF2-HMAC-SHA256` password hashing (default password: `admin`, changeable in the Web UI).
  - **Live Remote Control & Settings**: Change the Google Photos shared album URL, switch collage layouts, adjust slide/video durations, toggle or disable Night Mode, advance/pause slides, switch to the built-in **Demo Showcase Pack**, view live RAM logs, restart the slideshow, or reboot the device.
- **✨ Built-in Demo Showcase Pack (`demo_media/`)**:
  - Bundled with **18 curated personal photos & videos** (**12 high-resolution photos** — 8 landscape + 4 portrait — plus **6 optimized 720p H.264 MP4 video clips**), stripped of all metadata and optimized under 20 MB total so a newly installed device works out of the box—even offline—before a Google Photos album is configured. Includes automatic fallback to local `demo_media/` photos if any remote URL ever fails.
- **🛡️ Zero Disk/Flash Wear & 24/7 USB Wi-Fi Keepalive**:
  - Disables `.pyc` bytecode writes (`sys.dont_write_bytecode = True`), logs strictly to `/dev/shm/slideshow.log` (capped at 2 MB in RAM), and caches album URLs in `/dev/shm/gphotos_cache.json`.
  - Includes a built-in USB Wi-Fi keepalive daemon (`iw dev wlan0 set power_save off` + 45-second gateway heartbeat) so wireless adapters never enter sleep mode during Night Mode.

---

## 🛠️ Step-by-Step Installation & Setup

### 1. Install System Dependencies
On **Debian / Ubuntu / Linux Mint / Armbian** (and other `apt`-based Linux distributions):

```bash
sudo apt update
sudo apt install -y git python3-pygame python3-pil ffmpeg libegl1 libgbm1 libgles2 libgl1-mesa-dri
sudo usermod -aG video,render $USER
```

> **💡 Note for Other Linux Distributions**:
> The application runs natively on any modern Linux distribution (Wayland, X11 Desktop, or headless KMS/DRM console). On non-Debian distributions, install the equivalent packages using your system package manager before running `./install_service.sh`:
> - **Fedora / RHEL / CentOS Stream / AlmaLinux / Rocky Linux (`dnf`)**:
>   ```bash
>   sudo dnf install -y git python3-pygame python3-pillow ffmpeg mesa-libEGL mesa-libgbm mesa-libGLES mesa-dri-drivers
>   sudo usermod -aG video,render $USER
>   ```
> - **Arch Linux / Manjaro / EndeavourOS (`pacman`)**:
>   ```bash
>   sudo pacman -S --needed git python-pygame python-pillow ffmpeg mesa
>   sudo usermod -aG video,render $USER
>   ```
> - **openSUSE Tumbleweed / Leap (`zypper`)**:
>   ```bash
>   sudo zypper install -y git python3-pygame python3-Pillow ffmpeg Mesa-libEGL1 libgbm1 Mesa-libGLESv2-2
>   sudo usermod -aG video,render $USER
>   ```

### 2. Clone the Repository
```bash
cd ~
git clone https://github.com/shanghai-noodle/rpi-google-photos-slideshow.git
cd rpi-google-photos-slideshow
```

### 3. Run & Configure via the Web Control Portal
Start the slideshow (boots into **Demo Showcase Mode** at `0°` horizontal `1920×1080` by default):

```bash
python3 -B slideshow.py --duration 15 --rotate 0 --landscape-mode collage --web-port 8080
```

1. Scan the **on-screen QR code** in the bottom-right corner of the display, or open **`http://photoframe.local:8080`** (fallback: `http://<your-device-ip>:8080`) in any phone or desktop browser on your network.
   > **⚠️ Firewall Reminder**: The Web Control Portal uses TCP port **`8080`** by default. If you have a firewall enabled on your Linux host (such as `ufw`, `firewalld`, or `iptables`/`nftables`) or Wi-Fi router AP isolation, make sure incoming LAN traffic on port `8080` is allowed (e.g., `sudo ufw allow 8080/tcp` or `sudo firewall-cmd --add-port=8080/tcp --permanent && sudo firewall-cmd --reload`).
2. Sign in with the default password **`admin`** (you can change the password under **Security & Change Password**).
3. Paste your **Google Photos Shared Album URL** (`https://photos.app.goo.gl/...`), choose your preferred collage layout, screen rotation (`0°`, `90°`, `180°`, or `270°`), and Night Mode schedule, then click **Save & Apply Settings**.

### 4. Auto-Start on Boot (`systemd` Service)
To run the photo frame automatically on boot, run the included installer script (it automatically detects your username, repository path, and package manager):

```bash
./install_service.sh
```

*(Or manually copy the unit file)*:
```bash
sed -i "s|/opt/photo-slideshow|$(pwd)|g; s|User=linuxuser|User=$USER|g" slideshow.service
sudo cp slideshow.service /etc/systemd/system/slideshow.service
sudo systemctl daemon-reload
sudo systemctl enable --now slideshow.service
```

Check service status at any time with:
```bash
sudo systemctl status slideshow.service
./view_logs.sh
```

---

## 🚀 Quick Start & Command-Line Usage

Start the slideshow (boots into **Demo Showcase Mode** by default until you set your album in the Web Portal at `http://<device-ip>:8080`):

```bash
python3 -B slideshow.py \
  --duration 15 \
  --rotate 0 \
  --landscape-mode collage \
  --web-port 8080
```

Or specify a Google Photos shared album URL directly via CLI:

```bash
python3 -B slideshow.py \
  --album "https://photos.app.goo.gl/YOUR_SHARED_ALBUM_ID" \
  --duration 15 \
  --rotate 0 \
  --landscape-mode collage
```

### CLI Options

| Flag | Default | Description |
| :--- | :--- | :--- |
| `-p`, `--profile` | `auto` | Hardware profile: `auto` (detects standard vs low-memory system), `standard` (1GB+ RAM, full `1080×1920` fetch), or `lowmem` (<=600MB RAM / single-core tuning) |
| `-a`, `--album` | `demo` | Google Photos shared album URL (`https://photos.app.goo.gl/...`), ID, or `demo` (built-in 30-item showcase pack) |
| `--web-port` | `8080` | Port for the password-protected local Web Configuration Portal (`0` to disable) |
| `-d`, `--duration` | `15.0` | Seconds to display each slide |
| `-t`, `--transition` | `none` | Slide transition effect: `none` (default instant cut), `fade`, `fade-black`, `slide-left`, `slide-right`, `slide-up`, `wipe`, or `random` |
| `-l`, `--landscape-mode` | `collage` | `collage` (weighted mix of all 9 grids + singles), `single`, `trio`, `classic-5`, `mosaic-7`, `sidebar-9`, `mondrian-8`, `grid-3-2-3`, `stack`, `triple`, or `bento` |
| `-v`, `--videos` | `play` | `play` (stream album videos in RAM via `ffmpeg`), `skip` (ignore videos), or `still` (show static thumbnail) |
| `--max-video-seconds` | `20.0` | Maximum seconds to play a video clip before advancing |
| `--rotate` | `0` | Screen rotation in degrees (`0`, `90`, `180`, `270`; default `0` horizontal) |
| `--fit` | `contain-blur` | Single-photo fit mode: `contain-blur` (dark bokeh backdrop), `contain`, or `cover` |
| `--night-start` | `22:00` | Night mode sleep start time (`HH:MM` or `10pm`) |
| `--night-end` | `06:00` | Night mode wake time (`HH:MM` or `6am`) |
| `--disable-night-mode` | `False` | Keep display active 24/7 |
| `--no-shuffle` | `False` | Play photos in album order instead of shuffling |
| `--refresh-hours` | `12.0` | Hours between background album syncs (`0` to disable) |
| `--log-file` | `/dev/shm/slideshow.log` | Capped RAM log path (`tmpfs`) |
| `--cache-file` | `/dev/shm/gphotos_cache.json` | RAM cache path for album URLs (`tmpfs`) |

---

## ⌨️ Keyboard Controls

| Key | Action |
| :--- | :--- |
| **Right Arrow** | Next slide |
| **Left Arrow / P** | Previous slide |
| **Spacebar** | Pause / Resume slideshow |
| **N** | Toggle Night Mode |
| **R** | Rotate screen 90° |
| **Q / Escape** | Quit application |

---

## 📄 Viewing Live RAM Logs

Run the included helper script to check process status and tail the RAM log (`/dev/shm/slideshow.log`):

```bash
./view_logs.sh
```

---

## 🧩 Modular Package Architecture (`v1.0.0`)

```text
├── slideshow.py              # CLI entry point & backward-compatible re-export module
├── photoframe/               # Core modular Python package
│   ├── __init__.py           # Package exports & version metadata (v1.0.0)
│   ├── hardware.py           # Hardware profile auto-detection & capped RAM logging
│   ├── config.py             # FrameConfigStore (PBKDF2-HMAC-SHA256 auth & persistent JSON settings)
│   ├── gphotos.py            # GooglePhotosMixin (shared album scraper, RPC paginator & RAM cache)
│   ├── collage.py            # PhotoManager (9 even-gap templates & subject-aware smart cropper)
│   ├── web_portal.py         # Multi-language Web Control Portal (en, zh, ja, ko, es, de)
│   └── app.py                # SlideshowApp (Wayland/X11/KMSDRM display, rotation, FFmpeg video & Night Mode)
├── tests/                    # 100% offline headless unit test suite
│   ├── test_collage.py       # Collage geometry, aspect ratio & subject-aware center-crop tests
│   ├── test_web_portal.py    # PBKDF2 auth, session cookies, 6-language i18n & HTTP API tests
│   ├── test_gphotos.py       # Google Photos HTML/RPC parser & RAM cache roundtrip tests
│   └── test_app_display.py   # Rotation math, FFmpeg transpose mapping & Night Mode schedule tests
└── demo_media/               # Built-in 18-item Demo Showcase Pack (12 photos + 6 MP4 videos)
```

---

## 🧪 Running Unit Tests

The repository includes an offline, headless `unittest` suite covering collage geometry across horizontal (`1920×1080`) and portrait (`1080×1920`) orientations, subject-aware center cropping, Web Portal authentication & 6-language localization, Google Photos scraping/caching, and display rotation/Night Mode logic:

```bash
python3 -m unittest discover -s tests -v
```

---

## 🙌 Credits & License

- **Created with**: **Google Antigravity**
- **Author**: **shanghai-noodle**
- **Version**: `v1.0.0`
- **License**: Released under the [MIT License](LICENSE).

