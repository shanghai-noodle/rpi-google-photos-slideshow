#!/usr/bin/env python3
"""
Linux Native Google Photos Slideshow (v1.0.0)
=============================================
Thin CLI entry point & backward-compatible re-export module for the modular
`photoframe` package.

Created with Google Antigravity by shanghai-noodle.
License: MIT
"""

from photoframe import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_RAM_CACHE,
    DEFAULT_RAM_LOG,
    HARDWARE_PROFILES,
    RAM_DIR,
    SUPPORTED_LANGUAGES,
    WEB_PORTAL_HTML,
    CappedRamFileHandler,
    FrameConfigStore,
    GooglePhotosMixin,
    PhotoManager,
    SlideshowApp,
    __author__,
    __created_with__,
    __version__,
    create_portal_handler,
    detect_hardware_profile,
    logger,
    main,
    parse_args,
    setup_ram_logging,
    start_web_config_server,
)

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
    "parse_args",
    "main",
]

if __name__ == "__main__":
    main()
