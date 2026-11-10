"""
Linux Native Google Photos Slideshow & Multi-Language Web Control Portal
========================================================================
Created with Google Antigravity by shanghai-noodle.
"""

from __future__ import annotations

__version__ = "1.0.0"
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
from .web_portal import (
    SUPPORTED_LANGUAGES,
    WEB_PORTAL_HTML,
    create_portal_handler,
    start_web_config_server,
)
from .app import PiSlideshowApp, SlideshowApp, main, parse_args

__all__ = [
    "__version__",
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
    "SUPPORTED_LANGUAGES",
    "WEB_PORTAL_HTML",
    "create_portal_handler",
    "start_web_config_server",
    "SlideshowApp",
    "PiSlideshowApp",
    "parse_args",
    "main",
]
