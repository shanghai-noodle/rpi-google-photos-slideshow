"""
Hardware Profile Detection & RAM-Only Logging (Zero Disk/Flash Wear)
====================================================================
Auto-detects low-memory / single-core Linux systems (<=600MB RAM) vs standard
multi-core Linux systems (>=1GB RAM) and configures capped rotating logs in `/dev/shm`.
"""

from __future__ import annotations

import datetime
import logging
from logging.handlers import RotatingFileHandler
import os
import platform
import sys
from typing import Any, Dict, Optional, Tuple

_STANDARD_PROFILE: Dict[str, Any] = {
    "name": "Standard Multi-Core Linux System (1GB+ RAM)",
    "portrait_queue_max": 8,
    "landscape_queue_max": 8,
    "slide_history_max": 3,
    "cdn_scale": 1.0,
    "http_timeout": 8.0,
    "sync_sleep_sec": 0.05,
    "ram_log_max_mb": 2,
    "loop_hz": 10,
    "video_fps": 18,
}

_LOWMEM_PROFILE: Dict[str, Any] = {
    "name": "Low-Memory / Single-Core Linux System (<=600MB RAM)",
    "portrait_queue_max": 4,
    "landscape_queue_max": 4,
    "slide_history_max": 1,
    "cdn_scale": 0.75,
    "http_timeout": 10.0,
    "sync_sleep_sec": 0.25,
    "ram_log_max_mb": 1,
    "loop_hz": 5,
    "video_fps": 12,
}

HARDWARE_PROFILES: Dict[str, Dict[str, Any]] = {
    "standard": _STANDARD_PROFILE,
    "lowmem": _LOWMEM_PROFILE,
}


def detect_hardware_profile(
    requested: str = "auto",
    machine_override: Optional[str] = None,
    meminfo_path: str = "/proc/meminfo",
) -> Tuple[str, Dict[str, Any]]:
    """Detects whether the system is a low-memory Linux device (<=600MB RAM) or standard multi-core system."""
    if requested in ("standard", "lowmem"):
        return requested, HARDWARE_PROFILES[requested]

    machine = (machine_override if machine_override is not None else platform.machine()).lower()
    total_ram_mb = 1024
    if meminfo_path and os.path.exists(meminfo_path):
        try:
            with open(meminfo_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        kb = int(line.split()[1])
                        total_ram_mb = kb // 1024
                        break
        except Exception:
            pass

    if "armv6" in machine or total_ram_mb <= 600:
        return "lowmem", HARDWARE_PROFILES["lowmem"]
    return "standard", HARDWARE_PROFILES["standard"]


RAM_DIR = "/dev/shm" if (os.path.isdir("/dev/shm") and os.access("/dev/shm", os.W_OK)) else "/tmp"
DEFAULT_RAM_LOG = os.path.join(RAM_DIR, "slideshow.log")
DEFAULT_RAM_CACHE = os.path.join(RAM_DIR, "gphotos_cache.json")

logger = logging.getLogger("Slideshow")


class CappedRamFileHandler(RotatingFileHandler):
    """Strictly caps the in-RAM log file size and overwrites when full."""

    def doRollover(self):
        if self.stream:
            self.stream.close()
            self.stream = None
        if self.backupCount > 0:
            super().doRollover()
        else:
            self.stream = open(self.baseFilename, "w", encoding=self.encoding)
            try:
                ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                mb = max(1, self.maxBytes // (1024 * 1024))
                self.stream.write(f"[{ts}] [INFO] [Slideshow] Log reached {mb}MB RAM cap; rotated in memory.\n")
                self.stream.flush()
            except Exception:
                pass


def setup_ram_logging(
    log_path: str = DEFAULT_RAM_LOG,
    max_mb: int = 2,
    debug: bool = False,
    quiet: bool = False,
    profile_key: str = "standard",
    profile_info: Optional[Dict[str, Any]] = None,
):
    """Configures logging strictly in RAM (/dev/shm or /tmp tmpfs) + optional stdout."""
    level = logging.DEBUG if debug else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)

    for h in list(root.handlers):
        try:
            h.close()
        except Exception:
            pass
        root.removeHandler(h)

    fmt = logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    if log_path and log_path.lower() != "none":
        try:
            if os.path.isfile(log_path):
                with open(log_path, "r", encoding="utf-8", errors="ignore") as rf:
                    existing = rf.read()
                if "[PiSlideshow]" in existing:
                    with open(log_path, "w", encoding="utf-8") as wf:
                        wf.write(existing.replace("[PiSlideshow]", "[Slideshow]"))
            fh = CappedRamFileHandler(log_path, maxBytes=max_mb * 1024 * 1024, backupCount=0, encoding="utf-8")
            fh.setLevel(level)
            fh.setFormatter(fmt)
            root.addHandler(fh)
        except Exception as e:
            sys.stderr.write(f"Warning: could not open RAM log {log_path}: {e}\n")

    if not quiet:
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(level)
        ch.setFormatter(fmt)
        root.addHandler(ch)

    logging.getLogger("PIL").setLevel(logging.WARNING)
    prof_desc = (profile_info or {}).get("name", profile_key)
    logger.info("=" * 68)
    logger.info("🖼️ Starting Linux Native Google Photos Slideshow (RAM-Only)")
    logger.info("⚙️  Hardware Profile: [%s] %s", profile_key, prof_desc)
    logger.info("📝 RAM Log File: %s (Max: %dMB, tmpfs in memory)", log_path, max_mb)
    logger.info("=" * 68)
