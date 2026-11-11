"""
Linux Native Google Photos Slideshow & Multi-Language Web Control Portal
========================================================================
Created with Google Antigravity by shanghai-noodle.
"""

from __future__ import annotations

import os


def _load_version_info() -> tuple[str, int]:
    ver_major, ver_minor, build_num = 1, 0, 3
    props_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "version.properties")
    if os.path.exists(props_path):
        try:
            with open(props_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if "=" in line and not line.startswith("#"):
                        k, v = line.split("=", 1)
                        k, v = k.strip(), v.strip()
                        if k == "VERSION_MAJOR":
                            ver_major = int(v)
                        elif k == "VERSION_MINOR":
                            ver_minor = int(v)
                        elif k == "BUILD_NUMBER":
                            build_num = int(v)
        except Exception:
            pass
    return f"{ver_major}.{ver_minor}.{build_num}", build_num


__version__, __build__ = _load_version_info()
__author__ = "shanghai-noodle"
__created_with__ = "Google Antigravity"

from .hardware import (
    CappedRamFileHandler,
    DEFAULT_RAM_CACHE,
    DEFAULT_RAM_LOG,
    HARDWARE_PROFILES,
    RAM_DIR,
    detect_hardware_profile,
    logger,
    setup_ram_logging,
)
from .config import DEFAULT_CONFIG_PATH, FrameConfigStore
from .gphotos import GooglePhotosMixin
from .collage import PhotoManager
from .qrcode_gen import encode_qr_matrix
from .mdns import MDNS_HOSTNAME, build_mdns_a_response, start_mdns_responder
from .web_portal import (
    SUPPORTED_LANGUAGES,
    WEB_PORTAL_HTML,
    create_portal_handler,
    start_web_config_server,
)
from .app import SlideshowApp, main, parse_args

__all__ = [
    "__version__",
    "__build__",
    "__author__",
    "__created_with__",
    "RAM_DIR",
    "DEFAULT_RAM_LOG",
    "DEFAULT_RAM_CACHE",
    "DEFAULT_CONFIG_PATH",
    "HARDWARE_PROFILES",
    "detect_hardware_profile",
    "CappedRamFileHandler",
    "setup_ram_logging",
    "logger",
    "FrameConfigStore",
    "GooglePhotosMixin",
    "PhotoManager",
    "encode_qr_matrix",
    "MDNS_HOSTNAME",
    "build_mdns_a_response",
    "start_mdns_responder",
    "SUPPORTED_LANGUAGES",
    "WEB_PORTAL_HTML",
    "create_portal_handler",
    "start_web_config_server",
    "SlideshowApp",
    "parse_args",
    "main",
]
