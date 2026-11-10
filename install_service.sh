#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_USER="${SUDO_USER:-$(whoami)}"

echo "1/3 Ensuring system packages and GPU/DRM permissions for '${RUN_USER}'..."
if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get install -y python3-pygame python3-pil ffmpeg libegl1 libgbm1 libgles2 libgl1-mesa-dri >/dev/null 2>&1 || true
elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y python3-pygame python3-pillow ffmpeg mesa-libEGL mesa-libgbm mesa-libGLES mesa-dri-drivers >/dev/null 2>&1 || true
elif command -v pacman >/dev/null 2>&1; then
    sudo pacman -S --noconfirm --needed python-pygame python-pillow ffmpeg mesa >/dev/null 2>&1 || true
elif command -v zypper >/dev/null 2>&1; then
    sudo zypper --non-interactive install python3-pygame python3-Pillow ffmpeg Mesa-libEGL1 libgbm1 Mesa-libGLESv2-2 >/dev/null 2>&1 || true
fi
sudo usermod -aG video,render "${RUN_USER}" 2>/dev/null || true

echo "2/3 Installing /etc/systemd/system/slideshow.service (WorkingDirectory=${REPO_DIR})..."
sudo tee /etc/systemd/system/slideshow.service >/dev/null <<EOF
[Unit]
Description=Linux Native Google Photos Slideshow (RAM-only, Zero Disk/Flash Wear)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
SupplementaryGroups=video render
WorkingDirectory=${REPO_DIR}
# Auto-detects Wayland/X11 Desktop or direct KMS/DRM / Framebuffer
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
Nice=-5
# Starts in Demo Showcase Mode until configured via the Web Portal (http://<device-ip>:8080)
ExecStart=/usr/bin/python3 -B ${REPO_DIR}/slideshow.py --duration 15 --rotate 0 --landscape-mode collage --quiet
Restart=always
RestartSec=5
StandardOutput=null
StandardError=null

[Install]
WantedBy=multi-user.target
Alias=photoframe.service
EOF

echo "3/3 Reloading systemd and starting slideshow.service..."
sudo systemctl daemon-reload
sudo systemctl enable slideshow.service
sudo systemctl restart slideshow.service
sleep 2
echo "✅ slideshow.service is installed, enabled, and running!"
sudo systemctl status slideshow.service --no-pager -l
