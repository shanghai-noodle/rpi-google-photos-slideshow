"""
Modular Web Slideshow Viewer (/frame) Controller & Composer
===========================================================
Hosts a lightweight browser-based photo frame on the same HTTP server (`:8080/frame`)
that can be enabled/disabled live from the Admin Portal, sharing the synced photo
album while advancing slides in each browser's own independent order and screen
orientation (videos are rendered as high-resolution static poster frames inside
single slides and collage tiles).

Specifically engineered for both modern screens and legacy device stability
(iPad 2 / iOS 9 Safari, Android 4.x+ WebView/Chrome, Kindle Silk, and smart TV browsers):
- Pure ES5 JavaScript (`var`, `XMLHttpRequest`, zero ES6 syntax)
- Server-side slide & Smart Collage composition into baseline RGB JPEGs over
  plain LAN HTTP (bypassing modern CDN TLS 1.3 handshake failures and 4K OOMs)
- Selectable Picture Quality & Device Profiles (`auto` Smart Match, `hd` 1080p HD,
  and `legacy` 768p Standard memory-safe mode)
- Aggressive DOM & GPU texture cleanup to prevent WebKit Jetsam / OOM tab crashes
"""

from __future__ import annotations

import collections
import http.server
import io
import json
import os
import random
import re
import socketserver
import threading
import time
import urllib.parse
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

try:
    from PIL import Image, ImageDraw
    PIL_AVAILABLE = True
except ImportError:
    Image = ImageDraw = None
    PIL_AVAILABLE = False

from .hardware import logger
from .mdns import get_active_mdns_hostname

VALID_WEB_FRAME_PROFILES = ("auto", "legacy", "hd")
DEFAULT_WEB_FRAME_PORT = 0

# Minimal valid 1x1 black baseline JPEG fallback when Pillow is unavailable
_MINIMAL_1X1_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00"
    b"\xff\xdb\x00C\x00\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff"
    b"\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff"
    b"\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff"
    b"\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff"
    b"\xff\xff\xff\xff\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11"
    b"\x00\xff\xc4\x00\x14\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\xff\xc4\x00\x14\x10\x01\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xff\xda\x00"
    b"\x08\x01\x01\x00\x00?\x00?\x00\xff\xd9"
)

_LEGACY_UA_PATTERNS = re.compile(
    r"(?:"
    r"CPU (?:iPhone )?OS (?:[1-9]|1[0-2])_[0-9]|"
    r"iPad; U; CPU OS [1-9]_|"
    r"Android [1-7]\.[0-9]|"
    r"Android [1-7];|"
    r"Silk/|Kindle/|Opera Mini|Opera Mobi|UCBrowser|"
    r"NetCast|Maple|Presto/|MSIE [1-9]\.|Trident/[1-7]\.|CrKey/"
    r")",
    re.IGNORECASE,
)


def normalize_web_frame_profile(profile: Optional[str]) -> str:
    """Normalizes a web frame profile string into `'auto'`, `'legacy'`, or `'hd'`."""
    if not profile:
        return "auto"
    val = str(profile).strip().lower()
    if val in VALID_WEB_FRAME_PROFILES:
        return val
    return "auto"


def detect_web_client_profile(
    user_agent: Optional[str],
    configured_profile: str = "auto",
    query_profile: Optional[str] = None,
) -> str:
    """
    Determines the effective rendering profile (`'legacy'` or `'hd'`) for a browser client.
    Priority:
      1. Explicit `query_profile` (`?profile=legacy` or `?profile=hd`)
      2. Server `configured_profile` (`legacy` or `hd`)
      3. `User-Agent` heuristic when `'auto'` (detects iOS <= 12 / iPad 2, Android <= 7,
         Kindle/Silk, old Smart TVs, and legacy engines -> `'legacy'`; otherwise `'hd'`).
    """
    q_norm = normalize_web_frame_profile(query_profile) if query_profile else "auto"
    if q_norm in ("legacy", "hd"):
        return q_norm

    cfg_norm = normalize_web_frame_profile(configured_profile)
    if cfg_norm in ("legacy", "hd"):
        return cfg_norm

    ua = str(user_agent or "").strip()
    if not ua:
        return "hd"
    if _LEGACY_UA_PATTERNS.search(ua):
        return "legacy"
    return "hd"


def compute_web_canvas_dimensions(
    req_w: Any,
    req_h: Any,
    effective_profile: str = "hd",
    fallback_w: int = 1024,
    fallback_h: int = 768,
) -> Tuple[int, int]:
    """
    Computes safe, even-numbered `(target_w, target_h)` dimensions for server-side slide
    composition that preserve the client browser's aspect ratio & orientation while capping
    maximum resolution to prevent browser OOM crashes:
      - `'legacy'`: max longest edge `1024` (e.g. `1024x768` or `768x1024` on iPad 2)
      - `'hd'`: max longest edge `1920` (e.g. `1920x1080` or `1080x1920`)
    """
    try:
        w = int(float(req_w))
    except (ValueError, TypeError):
        w = fallback_w
    try:
        h = int(float(req_h))
    except (ValueError, TypeError):
        h = fallback_h

    if w <= 0 or h <= 0:
        w, h = fallback_w, fallback_h

    w = max(240, min(4096, w))
    h = max(240, min(4096, h))

    max_edge = 1024 if effective_profile == "legacy" else 1920
    longest = max(w, h)
    if longest > max_edge:
        scale = max_edge / float(longest)
        w = int(round(w * scale))
        h = int(round(h * scale))

    # Ensure even dimensions >= 240
    w = max(240, (w // 2) * 2)
    h = max(240, (h // 2) * 2)
    return w, h


DISABLED_WEB_FRAME_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Web Slideshow Viewer Disabled</title>
<style>
  body {
    margin: 0; padding: 32px 16px; background: #070b14; color: #e2e8f0;
    font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif;
    text-align: center;
  }
  .box {
    max-width: 460px; margin: 80px auto; padding: 28px 22px;
    background: #131b2e; border: 1px solid #23304d; border-radius: 14px;
  }
  h1 { font-size: 20px; margin: 0 0 12px; color: #f8fafc; }
  p { font-size: 14px; color: #94a3b8; line-height: 1.5; margin: 0 0 20px; }
  a.btn {
    display: inline-block; padding: 10px 18px; border-radius: 8px;
    background: #38bdf8; color: #082f49; font-weight: bold;
    text-decoration: none; font-size: 14px;
  }
</style>
</head>
<body>
  <div class="box">
    <h1>🖼️ Web Slideshow Viewer is Off</h1>
    <p>The browser slideshow viewer (/frame) is currently disabled. An administrator can enable it anytime from the Photo Frame Control Portal.</p>
    <a class="btn" id="portal-link" href="/">Open Admin Control Portal</a>
  </div>
  <script type="text/javascript">
    (function() {
      var loc = window.location;
      if (loc.port && loc.port !== "8080") {
        var link = document.getElementById("portal-link");
        if (link) {
          link.href = loc.protocol + "//" + loc.hostname + ":8080/";
        }
      }
    })();
  </script>
</body>
</html>
"""


WEB_FRAME_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="mobile-web-app-capable" content="yes">
<title>Web Slideshow Viewer</title>
<style type="text/css">
  * { box-sizing: border-box; margin: 0; padding: 0; -webkit-tap-highlight-color: transparent; }
  html, body {
    width: 100%; height: 100%; overflow: hidden;
    background-color: #000000; color: #f8fafc;
    font-family: -apple-system, "Helvetica Neue", Helvetica, Arial, sans-serif;
    -webkit-user-select: none; user-select: none;
  }
  #stage {
    position: fixed; top: 0; left: 0; right: 0; bottom: 0;
    width: 100%; height: 100%; overflow: hidden; background-color: #000000;
  }
  .slide-img {
    position: absolute; top: 0; left: 0; width: 100%; height: 100%;
    object-fit: contain; display: block; border: 0;
    -webkit-backface-visibility: hidden; backface-visibility: hidden;
  }
  .fade-transition {
    -webkit-transition: opacity 0.65s ease-in-out;
    transition: opacity 0.65s ease-in-out;
  }
  #night-overlay {
    position: fixed; top: 0; left: 0; right: 0; bottom: 0;
    background-color: #000000; color: #475569; z-index: 50;
    display: none; text-align: center; padding-top: 42vh; font-size: 15px;
  }
  #hud {
    position: fixed; left: 0; right: 0; bottom: 0; z-index: 60;
    background: rgba(11, 15, 25, 0.85); border-top: 1px solid rgba(255, 255, 255, 0.16);
    padding: 10px 14px; display: none;
  }
  .hud-row {
    max-width: 820px; margin: 0 auto;
    display: table; width: 100%;
  }
  .hud-left, .hud-right {
    display: table-cell; vertical-align: middle;
  }
  .hud-right { text-align: right; }
  .hud-title {
    font-size: 13px; font-weight: bold; color: #38bdf8; margin-right: 10px;
  }
  .hud-meta {
    font-size: 12px; color: #cbd5e1;
  }
  .hud-btn, .hud-select {
    background: #1e293b; color: #f8fafc; border: 1px solid #334155;
    border-radius: 7px; padding: 7px 11px; font-size: 13px; font-weight: bold;
    margin-left: 5px; cursor: pointer;
  }
  .hud-btn:active { background: #38bdf8; color: #082f49; }
  #clock-pill {
    position: fixed; bottom: 14px; right: 18px; z-index: 40;
    background: rgba(0, 0, 0, 0.52); color: rgba(255, 255, 255, 0.88);
    padding: 5px 11px; border-radius: 8px; font-size: 16px; font-weight: bold;
    pointer-events: none;
  }
</style>
</head>
<body>
  <div id="stage" onclick="toggleHud()">
    <img id="slide-a" class="slide-img" alt="" src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7">
    <img id="slide-b" class="slide-img" alt="" style="opacity:0; display:none;" src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7">
  </div>

  <div id="clock-pill"></div>

  <div id="night-overlay" onclick="toggleHud()">
    <div id="night-msg">🌙 Night Mode Active</div>
  </div>

  <div id="hud">
    <div class="hud-row">
      <div class="hud-left">
        <span class="hud-title" id="hud-host">🖼️ Photo Frame</span>
        <span class="hud-meta" id="hud-info">Loading...</span>
      </div>
      <div class="hud-right">
        <select id="hud-profile" class="hud-select" onchange="changeClientProfile(this.value)">
          <option value="auto">Quality: Auto</option>
          <option value="hd">Quality: 1080p HD</option>
          <option value="legacy">Quality: 768p Standard</option>
        </select>
        <button type="button" class="hud-btn" onclick="loadSlide('prev')">⏮</button>
        <button type="button" class="hud-btn" id="btn-pause" onclick="toggleLocalPause()">⏸</button>
        <button type="button" class="hud-btn" onclick="loadSlide('next')">⏭</button>
        <button type="button" class="hud-btn" onclick="toggleFullscreen()">⛶</button>
      </div>
    </div>
  </div>

<script type="text/javascript">
(function() {
  var BLANK_GIF = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7";
  var clientId = "wf_" + Math.floor(Math.random() * 1000000000) + "_" + (new Date().getTime());
  var profileOverride = "auto";
  var effectiveProfile = "legacy";
  var slideDurationMs = 15000;
  var transitionMode = "none";
  var localPaused = false;
  var serverPaused = false;
  var isNightMode = false;
  var isEnabled = true;
  var activeSlot = "a";
  var slideCount = 0;
  var isFetchingSlide = false;
  var slideTimer = null;
  var hudTimer = null;
  var preloader = new Image();

  function getQueryParam(name) {
    var s = window.location.search || "";
    var re = new RegExp("[?&]" + name + "=([^&#]*)");
    var m = re.exec(s);
    return m ? decodeURIComponent(m[1]) : "";
  }

  function initProfile() {
    var qp = getQueryParam("profile");
    var saved = "";
    try { saved = localStorage.getItem("webframe_profile") || ""; } catch (e) {}
    if (qp === "legacy" || qp === "hd" || qp === "auto") {
      profileOverride = qp;
    } else if (saved === "legacy" || saved === "hd" || saved === "auto") {
      profileOverride = saved;
    }
    var sel = document.getElementById("hud-profile");
    if (sel) sel.value = profileOverride;
  }

  window.changeClientProfile = function(val) {
    profileOverride = val || "auto";
    try { localStorage.setItem("webframe_profile", profileOverride); } catch (e) {}
    pollState(function() {
      loadSlide("next");
    });
  };

  function getViewportSize() {
    var w = window.innerWidth || document.documentElement.clientWidth || document.body.clientWidth || 1024;
    var h = window.innerHeight || document.documentElement.clientHeight || document.body.clientHeight || 768;
    return { w: w, h: h };
  }

  function updateClock() {
    var el = document.getElementById("clock-pill");
    if (!el) return;
    var now = new Date();
    var hh = now.getHours();
    var mm = now.getMinutes();
    var hStr = (hh < 10 ? "0" : "") + hh;
    var mStr = (mm < 10 ? "0" : "") + mm;
    el.innerHTML = hStr + ":" + mStr;
  }

  window.toggleHud = function() {
    var hud = document.getElementById("hud");
    if (!hud) return;
    if (hud.style.display === "block") {
      hud.style.display = "none";
      if (hudTimer) { clearTimeout(hudTimer); hudTimer = null; }
    } else {
      hud.style.display = "block";
      resetHudTimer();
    }
  };

  function resetHudTimer() {
    if (hudTimer) clearTimeout(hudTimer);
    hudTimer = setTimeout(function() {
      var hud = document.getElementById("hud");
      if (hud) hud.style.display = "none";
    }, 5000);
  }

  window.toggleLocalPause = function() {
    localPaused = !localPaused;
    var btn = document.getElementById("btn-pause");
    if (btn) btn.innerHTML = localPaused ? "▶" : "⏸";
    resetHudTimer();
    if (!localPaused) {
      scheduleNextSlide();
    } else if (slideTimer) {
      clearTimeout(slideTimer);
      slideTimer = null;
    }
  };

  window.toggleFullscreen = function() {
    resetHudTimer();
    var doc = document;
    var el = doc.documentElement;
    if (!doc.fullscreenElement && !doc.webkitFullscreenElement && !doc.msFullscreenElement) {
      if (el.requestFullscreen) el.requestFullscreen();
      else if (el.webkitRequestFullscreen) el.webkitRequestFullscreen();
      else if (el.msRequestFullscreen) el.msRequestFullscreen();
    } else {
      if (doc.exitFullscreen) doc.exitFullscreen();
      else if (doc.webkitExitFullscreen) doc.webkitExitFullscreen();
      else if (doc.msExitFullscreen) doc.msExitFullscreen();
    }
  };

  function resetImgElement(id, visible) {
    var oldEl = document.getElementById(id);
    if (!oldEl || !oldEl.parentNode) return oldEl;
    oldEl.src = BLANK_GIF;
    var fresh = document.createElement("img");
    fresh.id = id;
    fresh.className = "slide-img";
    fresh.alt = "";
    fresh.src = BLANK_GIF;
    if (!visible) {
      fresh.style.opacity = "0";
      fresh.style.display = "none";
    }
    oldEl.parentNode.replaceChild(fresh, oldEl);
    return fresh;
  }

  function applySlideUrl(url) {
    var imgA = document.getElementById("slide-a");
    var imgB = document.getElementById("slide-b");
    if (!imgA) return;

    slideCount += 1;
    var useDualFade = (effectiveProfile === "hd" && transitionMode !== "none" && imgB);

    if (!useDualFade) {
      if (imgB) {
        imgB.src = BLANK_GIF;
        imgB.style.display = "none";
      }
      if (slideCount % 40 === 0) {
        imgA = resetImgElement("slide-a", true);
      } else {
        imgA.src = BLANK_GIF;
      }
      imgA.className = "slide-img";
      imgA.style.display = "block";
      imgA.style.opacity = "1";
      imgA.src = url;
      activeSlot = "a";
      return;
    }

    imgA.className = "slide-img fade-transition";
    imgB.className = "slide-img fade-transition";
    imgB.style.display = "block";

    if (activeSlot === "a") {
      imgB.src = url;
      imgB.style.opacity = "1";
      imgA.style.opacity = "0";
      activeSlot = "b";
      setTimeout(function() {
        if (activeSlot === "b") imgA.src = BLANK_GIF;
      }, 750);
    } else {
      imgA.src = url;
      imgA.style.opacity = "1";
      imgB.style.opacity = "0";
      activeSlot = "a";
      setTimeout(function() {
        if (activeSlot === "a") imgB.src = BLANK_GIF;
      }, 750);
    }
  }

  window.loadSlide = function(dir) {
    if (isFetchingSlide || !isEnabled || isNightMode) return;
    isFetchingSlide = true;
    if (slideTimer) {
      clearTimeout(slideTimer);
      slideTimer = null;
    }

    var vp = getViewportSize();
    var direction = dir || "next";
    var url = "/api/webframe/slide.jpg?cid=" + encodeURIComponent(clientId)
      + "&w=" + vp.w
      + "&h=" + vp.h
      + "&profile=" + encodeURIComponent(profileOverride)
      + "&dir=" + encodeURIComponent(direction)
      + "&t=" + (new Date().getTime());

    preloader.onload = function() {
      preloader.onload = null;
      preloader.onerror = null;
      isFetchingSlide = false;
      applySlideUrl(url);
      scheduleNextSlide();
    };
    preloader.onerror = function() {
      preloader.onload = null;
      preloader.onerror = null;
      isFetchingSlide = false;
      scheduleNextSlide();
    };
    preloader.src = url;
  };

  function scheduleNextSlide() {
    if (slideTimer) {
      clearTimeout(slideTimer);
      slideTimer = null;
    }
    if (localPaused || serverPaused || isNightMode || !isEnabled) return;
    slideTimer = setTimeout(function() {
      loadSlide("next");
    }, slideDurationMs);
  }

  function pollState(callback) {
    var vp = getViewportSize();
    var xhr = new XMLHttpRequest();
    var url = "/api/webframe/state?cid=" + encodeURIComponent(clientId)
      + "&w=" + vp.w
      + "&h=" + vp.h
      + "&profile=" + encodeURIComponent(profileOverride)
      + "&t=" + (new Date().getTime());

    xhr.open("GET", url, true);
    xhr.onreadystatechange = function() {
      if (xhr.readyState !== 4) return;
      if (xhr.status === 200 || xhr.status === 403) {
        try {
          var data = JSON.parse(xhr.responseText || "{}");
          var wasNightOrDisabled = (isNightMode || !isEnabled);
          isEnabled = !!data.enabled;
          isNightMode = !!data.is_night_mode;
          serverPaused = !!data.is_paused;
          if (data.effective_profile) effectiveProfile = data.effective_profile;
          if (data.slide_duration && data.slide_duration >= 3) {
            slideDurationMs = Math.round(parseFloat(data.slide_duration) * 1000);
          }
          if (data.transition) transitionMode = data.transition;

          var hostEl = document.getElementById("hud-host");
          if (hostEl && data.mdns_hostname) {
            hostEl.innerHTML = "🖼️ " + data.mdns_hostname;
          }
          var infoEl = document.getElementById("hud-info");
          if (infoEl) {
            var pLabel = (effectiveProfile === "legacy") ? "768p Standard" : "1080p HD";
            infoEl.innerHTML = (data.photo_count || 0) + " items · " + pLabel + " (" + (data.target_w || vp.w) + "×" + (data.target_h || vp.h) + ")";
          }

          var overlay = document.getElementById("night-overlay");
          var nightMsg = document.getElementById("night-msg");
          if (!isEnabled) {
            if (overlay) overlay.style.display = "block";
            if (nightMsg) nightMsg.innerHTML = "🖼️ Web Slideshow Viewer is Disabled by Admin";
            var imgA = document.getElementById("slide-a");
            if (imgA) imgA.src = BLANK_GIF;
          } else if (isNightMode) {
            if (overlay) overlay.style.display = "block";
            if (nightMsg) nightMsg.innerHTML = "🌙 Night Mode Sleep (" + (data.night_start || "") + " - " + (data.night_end || "") + ")";
            var imgA2 = document.getElementById("slide-a");
            if (imgA2) imgA2.src = BLANK_GIF;
          } else {
            if (overlay) overlay.style.display = "none";
            if (wasNightOrDisabled && slideCount > 0) {
              loadSlide("next");
            }
          }
        } catch (e) {}
      }
      if (callback) callback();
    };
    xhr.send(null);
  }

  var resizeDebounce = null;
  window.onresize = function() {
    if (resizeDebounce) clearTimeout(resizeDebounce);
    resizeDebounce = setTimeout(function() {
      if (isEnabled && !isNightMode) {
        loadSlide("next");
      }
    }, 600);
  };

  initProfile();
  updateClock();
  setInterval(updateClock, 15000);
  pollState(function() {
    if (isEnabled && !isNightMode) {
      loadSlide("next");
    }
  });
  setInterval(function() { pollState(null); }, 10000);
})();
</script>
</body>
</html>
"""


class _ClientSession:
    """Tracks an individual browser viewer's slide cursor and recent JPEG slide history."""

    def __init__(self, client_id: str):
        self.client_id = client_id
        self.last_seen = time.time()
        self.cursor = random.randint(0, 100000)
        self.history: Deque[bytes] = collections.deque(maxlen=4)
        self.effective_profile = "hd"
        self.viewport = (1024, 768)


class WebFrameController:
    """
    Manages the HTTP Web Photo Frame server lifecycle (`start` / `stop` on port `:8081`
    plus `/frame` routing on `:8080`), per-client independent slide cursors, raw photo
    LRU caching, and orientation-matched server-side JPEG slide composition.
    """

    LEGACY_SAFE_STYLES = ("stack-2", "horiz-2p", "stack-3", "grid-trio", "bento-4")

    def __init__(
        self,
        app: Any,
        config_store: Any = None,
        port: int = DEFAULT_WEB_FRAME_PORT,
        enabled: bool = False,
        profile: str = "auto",
        max_sessions: int = 24,
        max_raw_cache: int = 10,
    ):
        self.app = app
        self.config_store = config_store
        self.port = int(port) if port is not None else DEFAULT_WEB_FRAME_PORT
        self.enabled = bool(enabled)
        self.profile = normalize_web_frame_profile(profile)
        self.max_sessions = max(2, int(max_sessions))
        self.max_raw_cache = max(2, int(max_raw_cache))

        self._server_lock = threading.Lock()
        self._httpd: Optional[http.server.HTTPServer] = None
        self._server_thread: Optional[threading.Thread] = None
        self.bound_port: int = self.port

        self._sessions_lock = threading.Lock()
        self._sessions: "collections.OrderedDict[str, _ClientSession]" = collections.OrderedDict()

        self._cache_lock = threading.Lock()
        self._raw_pil_cache: "collections.OrderedDict[str, Any]" = collections.OrderedDict()

    # ------------------------------------------------------------------
    # Server Lifecycle (On / Off Control)
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        with self._server_lock:
            return self._httpd is not None

    def set_enabled(
        self,
        enabled: bool,
        profile: Optional[str] = None,
        port: Optional[int] = None,
        persist: bool = True,
    ) -> Tuple[bool, str]:
        """
        Enables or disables the Web Photo Frame server, starting or stopping the
        dedicated `:8081` HTTP listener and optionally persisting to `config_store`.
        """
        if profile is not None:
            self.profile = normalize_web_frame_profile(profile)
        port_changed = False
        if port is not None and int(port) >= 0 and int(port) != self.port:
            self.port = int(port)
            port_changed = True

        self.enabled = bool(enabled)
        if hasattr(self.app, "args"):
            setattr(self.app.args, "web_frame_enabled", self.enabled)
            setattr(self.app.args, "web_frame_profile", self.profile)
            setattr(self.app.args, "web_frame_port", self.port)

        if persist and self.config_store is not None:
            try:
                self.config_store.save_settings({
                    "web_frame_enabled": self.enabled,
                    "web_frame_profile": self.profile,
                    "web_frame_port": self.port,
                })
            except Exception as e:
                logger.warning("Failed to persist web_frame settings: %s", e)

        if self.enabled:
            if port_changed and self.is_running:
                self.stop_dedicated_server()
            if self.port > 0:
                self.start_dedicated_server()
            host = get_active_mdns_hostname()
            return True, f"Web Slideshow Viewer enabled on http://{host}:8080/frame"
        else:
            self.stop_dedicated_server()
            with self._cache_lock:
                self._raw_pil_cache.clear()
            return True, "Web Slideshow Viewer disabled."

    def start_dedicated_server(self) -> bool:
        """Starts the dedicated `:8081` plain-HTTP listener if `self.enabled` is True."""
        if not self.enabled or self.port < 0:
            return False
        with self._server_lock:
            if self._httpd is not None:
                return True
            try:
                controller = self

                class _DedicatedWebFrameServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
                    daemon_threads = True
                    allow_reuse_address = True

                class _DedicatedWebFrameHandler(http.server.BaseHTTPRequestHandler):
                    def log_message(self, format: str, *args: Any):
                        pass

                    def do_GET(self):
                        controller.handle_http_get(self, is_dedicated_port=True)

                srv = _DedicatedWebFrameServer(("0.0.0.0", self.port), _DedicatedWebFrameHandler)
                self._httpd = srv
                self.bound_port = int(srv.server_address[1])
                t = threading.Thread(
                    target=srv.serve_forever,
                    name="DedicatedWebFrameServerThread",
                    daemon=True,
                )
                self._server_thread = t
                t.start()
                logger.info(
                    "🖼️ Web Photo Frame HTTP Viewer listening on http://%s:%d",
                    get_active_mdns_hostname(),
                    self.bound_port,
                )
                return True
            except Exception as e:
                logger.warning("Could not bind dedicated Web Photo Frame port %d: %s", self.port, e)
                self._httpd = None
                self._server_thread = None
                return False

    def stop_dedicated_server(self) -> None:
        """Cleanly stops and closes the dedicated `:8081` HTTP listener."""
        with self._server_lock:
            srv = self._httpd
            th = self._server_thread
            self._httpd = None
            self._server_thread = None
        if srv is not None:
            try:
                srv.shutdown()
            except Exception:
                pass
            try:
                srv.server_close()
            except Exception:
                pass
        if th is not None and th.is_alive():
            th.join(timeout=1.5)
        logger.info("🖼️ Web Photo Frame dedicated HTTP listener stopped.")

    # ------------------------------------------------------------------
    # Client Session & Status Tracking
    # ------------------------------------------------------------------

    def _touch_session(
        self,
        client_id: str,
        effective_profile: str = "hd",
        viewport: Tuple[int, int] = (1024, 768),
    ) -> _ClientSession:
        cid = (client_id or "default").strip()[:64] or "default"
        now = time.time()
        with self._sessions_lock:
            expired = [k for k, s in self._sessions.items() if now - s.last_seen > 120.0]
            for k in expired:
                self._sessions.pop(k, None)

            sess = self._sessions.get(cid)
            if sess is None:
                while len(self._sessions) >= self.max_sessions:
                    self._sessions.popitem(last=False)
                sess = _ClientSession(cid)
                self._sessions[cid] = sess
            else:
                self._sessions.move_to_end(cid)

            sess.last_seen = now
            sess.effective_profile = effective_profile
            sess.viewport = viewport
            return sess

    def get_active_client_count(self, window_seconds: float = 60.0) -> int:
        now = time.time()
        with self._sessions_lock:
            return sum(1 for s in self._sessions.values() if (now - s.last_seen) <= window_seconds)

    def get_status_dict(self) -> Dict[str, Any]:
        return {
            "web_frame_enabled": self.enabled,
            "web_frame_running": self.is_running,
            "web_frame_port": self.bound_port if self.is_running else self.port,
            "web_frame_profile": self.profile,
            "web_frame_clients": self.get_active_client_count(),
        }

    # ------------------------------------------------------------------
    # Raw Photo Cache & Orientation-Matched Slide Composition
    # ------------------------------------------------------------------

    def _get_source_snapshot(self) -> List[str]:
        pm = getattr(self.app, "photo_manager", None)
        if pm is None:
            return []
        lock = getattr(pm, "_sources_lock", None)
        if lock is not None:
            with lock:
                sources = list(getattr(pm, "photo_sources", []) or [])
        else:
            sources = list(getattr(pm, "photo_sources", []) or [])
        return sources

    def _load_pil_image(self, source: str, max_edge: int = 1280) -> Optional[Any]:
        """Loads and caches a downscaled RGB PIL image from `source` via `photo_manager`."""
        if not source:
            return None
        with self._cache_lock:
            cached = self._raw_pil_cache.get(source)
            if cached is not None:
                self._raw_pil_cache.move_to_end(source)
                return cached.copy() if hasattr(cached, "copy") else cached

        pm = getattr(self.app, "photo_manager", None)
        img = None
        if pm is not None and hasattr(pm, "_download_raw_pil"):
            try:
                img = pm._download_raw_pil(source)
            except Exception:
                img = None
        if img is None and PIL_AVAILABLE and Image and os.path.exists(source):
            try:
                img = Image.open(source).convert("RGB")
            except Exception:
                img = None

        if img is None:
            return None

        try:
            longest = max(img.width, img.height)
            if longest > max_edge:
                scale = max_edge / float(longest)
                nw = max(2, int(round(img.width * scale)))
                nh = max(2, int(round(img.height * scale)))
                img = (
                    img.resize((nw, nh), Image.Resampling.BILINEAR)
                    if (PIL_AVAILABLE and Image and hasattr(Image, "Resampling"))
                    else img.resize((nw, nh))
                )
            with self._cache_lock:
                while len(self._raw_pil_cache) >= self.max_raw_cache:
                    self._raw_pil_cache.popitem(last=False)
                self._raw_pil_cache[source] = img
            return img.copy() if hasattr(img, "copy") else img
        except Exception:
            return img

    @staticmethod
    def _encode_pil_to_jpeg(pil_img: Any, quality: int = 80) -> bytes:
        """
        Encodes a PIL image to a baseline RGB JPEG (`progressive=False` to prevent
        iOS 9 Safari progressive JPEG decoding memory spikes).
        """
        if pil_img is None or not hasattr(pil_img, "save"):
            return _MINIMAL_1X1_JPEG
        try:
            rgb = (
                pil_img.convert("RGB")
                if hasattr(pil_img, "convert") and getattr(pil_img, "mode", "RGB") != "RGB"
                else pil_img
            )
            buf = io.BytesIO()
            rgb.save(buf, format="JPEG", quality=int(quality), optimize=False, progressive=False)
            return buf.getvalue()
        except Exception:
            return _MINIMAL_1X1_JPEG

    @staticmethod
    def _build_placeholder_pil(target_w: int, target_h: int, title: str = "Photo Frame") -> Optional[Any]:
        if not PIL_AVAILABLE or not Image:
            return None
        canvas = Image.new("RGB", (target_w, target_h), (14, 21, 38))
        if ImageDraw:
            draw = ImageDraw.Draw(canvas)
            pad = max(12, min(target_w, target_h) // 24)
            draw.rectangle(
                (pad, pad, target_w - pad - 1, target_h - pad - 1),
                outline=(56, 189, 248),
                width=2,
            )
            cx, cy = target_w // 2, target_h // 2
            r = max(20, min(target_w, target_h) // 10)
            draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(148, 163, 184), width=3)
        return canvas

    def compose_client_slide_jpeg(
        self,
        client_id: str = "default",
        req_w: Any = 1024,
        req_h: Any = 768,
        query_profile: Optional[str] = None,
        user_agent: Optional[str] = None,
        direction: str = "next",
        style_override: Optional[str] = None,
    ) -> Tuple[bytes, str, int, int]:
        """
        Composes a memory-safe JPEG slide for `(req_w, req_h)` tailored to the browser's
        orientation and profile, advancing the client's independent cursor without
        disturbing the host frame's native display queue.
        Returns `(jpeg_bytes, effective_profile, target_w, target_h)`.
        """
        effective_profile = detect_web_client_profile(
            user_agent=user_agent,
            configured_profile=self.profile,
            query_profile=query_profile,
        )
        target_w, target_h = compute_web_canvas_dimensions(
            req_w, req_h, effective_profile=effective_profile
        )
        sess = self._touch_session(client_id, effective_profile, (target_w, target_h))

        if direction == "prev" and len(sess.history) >= 2:
            # Pop current and return previous
            current_bytes = sess.history.pop()
            prev_bytes = sess.history[-1]
            sess.history.appendleft(current_bytes)
            return prev_bytes, effective_profile, target_w, target_h

        jpeg_quality = 78 if effective_profile == "legacy" else 86
        max_raw_edge = 1024 if effective_profile == "legacy" else 1440

        sources = self._get_source_snapshot()
        pm = getattr(self.app, "photo_manager", None)

        if not sources or pm is None:
            ph = self._build_placeholder_pil(target_w, target_h)
            out_bytes = self._encode_pil_to_jpeg(ph, quality=jpeg_quality)
            sess.history.append(out_bytes)
            return out_bytes, effective_profile, target_w, target_h

        landscape_mode = str(getattr(self.app.args, "landscape_mode", "collage") or "collage")
        photo_fit = str(getattr(self.app.args, "fit", "contain-blur") or "contain-blur")
        video_mode = str(getattr(self.app.args, "videos", "play") or "play")

        def pop_client_img() -> Optional[Any]:
            total = len(sources)
            for _ in range(min(total, 10)):
                idx = sess.cursor % total
                sess.cursor = (sess.cursor + 1) % total
                src = sources[idx]
                if video_mode == "skip" and hasattr(pm, "is_video_source") and pm.is_video_source(src):
                    continue
                im = self._load_pil_image(src, max_edge=max_raw_edge)
                if im is not None:
                    return im
            return None

        first_img = pop_client_img()
        if first_img is None:
            ph = self._build_placeholder_pil(target_w, target_h)
            out_bytes = self._encode_pil_to_jpeg(ph, quality=jpeg_quality)
            sess.history.append(out_bytes)
            return out_bytes, effective_profile, target_w, target_h

        # Use a lightweight helper clone/view of PhotoManager geometry for `(target_w, target_h)`
        canvas = self._render_slide_for_dimensions(
            pm=pm,
            first_img=first_img,
            pop_more_fn=pop_client_img,
            target_w=target_w,
            target_h=target_h,
            landscape_mode=landscape_mode,
            photo_fit=photo_fit,
            effective_profile=effective_profile,
            style_override=style_override,
        )
        out_bytes = self._encode_pil_to_jpeg(canvas, quality=jpeg_quality)
        sess.history.append(out_bytes)
        return out_bytes, effective_profile, target_w, target_h

    def _render_slide_for_dimensions(
        self,
        pm: Any,
        first_img: Any,
        pop_more_fn: Callable[[], Optional[Any]],
        target_w: int,
        target_h: int,
        landscape_mode: str,
        photo_fit: str,
        effective_profile: str,
        style_override: Optional[str] = None,
    ) -> Any:
        """
        Renders either a single fitted photo or an orientation-matched grid collage
        for `(target_w, target_h)` without mutating `pm.target_w` / `pm.target_h`.
        """
        is_horiz = target_w >= target_h
        is_first_land = first_img.width >= first_img.height

        if landscape_mode == "single" and not style_override:
            return self._compose_single_for_size(pm, first_img, target_w, target_h, photo_fit)

        # In collage mode, occasionally show full-screen single when orientation matches
        if not style_override and landscape_mode in ("collage", "random", "auto"):
            if (is_horiz and is_first_land and random.random() < 0.25) or (
                not is_horiz and not is_first_land and random.random() < 0.50
            ):
                return self._compose_single_for_size(pm, first_img, target_w, target_h, photo_fit)

        style = style_override or self._pick_collage_style(landscape_mode, is_horiz, is_first_land, effective_profile)
        cells = self._compute_cells_for_size(pm, style, target_w, target_h)
        need_l = sum(1 for (_, _, cw, ch) in cells if cw >= ch)
        need_p = len(cells) - need_l

        l_imgs: List[Any] = []
        p_imgs: List[Any] = []
        if is_first_land and need_l > 0:
            l_imgs.append(first_img)
        elif not is_first_land and need_p > 0:
            p_imgs.append(first_img)
        else:
            # Keep as fallback
            pass

        spare_imgs: List[Any] = []
        if not l_imgs and not p_imgs:
            spare_imgs.append(first_img)

        max_tries = min(14, max(4, len(cells) * 3))
        tries = 0
        while (len(l_imgs) < need_l or len(p_imgs) < need_p) and tries < max_tries:
            tries += 1
            cand = pop_more_fn()
            if cand is None:
                break
            if cand.width >= cand.height:
                if len(l_imgs) < need_l:
                    l_imgs.append(cand)
                else:
                    spare_imgs.append(cand)
            else:
                if len(p_imgs) < need_p:
                    p_imgs.append(cand)
                else:
                    spare_imgs.append(cand)

        # If we didn't gather enough strictly oriented images, try a 2-tile stack or single photo
        if len(l_imgs) < need_l or len(p_imgs) < need_p:
            all_pool = l_imgs + p_imgs + spare_imgs
            if len(all_pool) >= 2:
                two_style = "horiz-2p" if (is_horiz and len(p_imgs) >= 2) else "stack-2"
                cells = self._compute_cells_for_size(pm, two_style, target_w, target_h)
                chosen_tiles = all_pool[: len(cells)]
            else:
                return self._compose_single_for_size(pm, first_img, target_w, target_h, photo_fit)
        else:
            chosen_tiles = []
            li = 0
            pi = 0
            for (_, _, cw, ch) in cells:
                if cw >= ch:
                    chosen_tiles.append(l_imgs[li])
                    li += 1
                else:
                    chosen_tiles.append(p_imgs[pi])
                    pi += 1

        canvas = self._make_bokeh_for_size(first_img, target_w, target_h, brightness=0.32)
        radius = 6 if effective_profile == "legacy" else 10
        for idx, (x, y, cw, ch) in enumerate(cells):
            tile_img = chosen_tiles[idx % len(chosen_tiles)]
            pm._paste_rounded_tile(canvas, tile_img, x, y, cw, ch, radius=radius, border=True)
        return canvas

    @classmethod
    def _pick_collage_style(
        cls,
        landscape_mode: str,
        is_horiz: bool,
        is_first_land: bool,
        effective_profile: str,
    ) -> str:
        mode_map = {
            "stack": "stack-2",
            "triple": "stack-3",
            "bento": "bento-4",
            "trio": "grid-trio",
            "classic-5": "grid-classic-5",
            "mosaic-7": "grid-mosaic-7",
            "sidebar-9": "grid-sidebar-9",
            "mondrian-8": "grid-mondrian-8",
            "grid-3-2-3": "grid-3-2-3",
        }
        if landscape_mode in mode_map:
            mapped = mode_map[landscape_mode]
            if effective_profile == "legacy" and mapped not in cls.LEGACY_SAFE_STYLES:
                return "grid-trio" if not is_first_land else "bento-4"
            return mapped

        if effective_profile == "legacy":
            if is_horiz and not is_first_land:
                return random.choice(["horiz-2p", "stack-2", "grid-trio"])
            return random.choice(["stack-2", "stack-3", "grid-trio", "bento-4"])

        if not is_first_land:
            return random.choice(["grid-trio", "stack-2", "grid-classic-5", "bento-4"])
        return random.choice(["bento-4", "grid-trio", "stack-3", "grid-classic-5", "stack-2"])

    @staticmethod
    def _compute_cells_for_size(pm: Any, style: str, target_w: int, target_h: int) -> List[Tuple[int, int, int, int]]:
        """Computes grid template cells for `(target_w, target_h)` using a temporary proxy object."""
        class _SizeProxy:
            pass

        proxy = _SizeProxy()
        proxy.target_w = target_w
        proxy.target_h = target_h
        proxy._get_grid_template_cells = lambda s: pm.__class__._get_grid_template_cells(proxy, s)
        return pm.__class__._get_grid_template_cells(proxy, style)

    @staticmethod
    def _make_bokeh_for_size(img: Any, target_w: int, target_h: int, brightness: float = 0.45) -> Any:
        try:
            thumb_w = 48
            thumb_h = max(1, int(round(thumb_w * (target_h / float(max(1, target_w))))))
            small = (
                img.resize((thumb_w, thumb_h), Image.Resampling.BILINEAR)
                if (PIL_AVAILABLE and Image and hasattr(Image, "Resampling"))
                else img.resize((thumb_w, thumb_h))
            )
            if hasattr(small, "point"):
                small = small.point(lambda p: int(p * brightness))
            return (
                small.resize((target_w, target_h), Image.Resampling.BILINEAR)
                if (PIL_AVAILABLE and Image and hasattr(Image, "Resampling"))
                else small.resize((target_w, target_h))
            )
        except Exception:
            if PIL_AVAILABLE and Image:
                return Image.new("RGB", (target_w, target_h), (12, 16, 24))
            return img

    def _compose_single_for_size(
        self,
        pm: Any,
        img: Any,
        target_w: int,
        target_h: int,
        photo_fit: str,
    ) -> Any:
        if photo_fit == "cover":
            return pm._smart_crop_fit(img, target_w, target_h)

        scale = min(target_w / float(max(1, img.width)), target_h / float(max(1, img.height)))
        fit_w = min(target_w, max(2, (int(round(img.width * scale)) // 2) * 2))
        fit_h = min(target_h, max(2, (int(round(img.height * scale)) // 2) * 2))
        if (img.width, img.height) != (fit_w, fit_h):
            fit_img = (
                img.resize((fit_w, fit_h), Image.Resampling.BILINEAR)
                if (PIL_AVAILABLE and Image and hasattr(Image, "Resampling"))
                else img.resize((fit_w, fit_h))
            )
        else:
            fit_img = img

        if fit_w == target_w and fit_h == target_h:
            return fit_img

        if photo_fit == "contain" and PIL_AVAILABLE and Image:
            canvas = Image.new("RGB", (target_w, target_h), (0, 0, 0))
        else:
            canvas = self._make_bokeh_for_size(img, target_w, target_h, brightness=0.45)

        fx = (target_w - fit_w) // 2
        fy = (target_h - fit_h) // 2
        if hasattr(canvas, "paste"):
            canvas.paste(fit_img, (fx, fy))
        return canvas

    # ------------------------------------------------------------------
    # HTTP Request Routing (Shared between :8080/frame and :8081/)
    # ------------------------------------------------------------------

    def build_state_payload(
        self,
        client_id: str = "default",
        req_w: Any = 1024,
        req_h: Any = 768,
        query_profile: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Dict[str, Any]:
        effective_profile = detect_web_client_profile(
            user_agent=user_agent,
            configured_profile=self.profile,
            query_profile=query_profile,
        )
        target_w, target_h = compute_web_canvas_dimensions(
            req_w, req_h, effective_profile=effective_profile
        )
        if self.enabled:
            self._touch_session(client_id, effective_profile, (target_w, target_h))

        st = self.app.get_status_snapshot() if hasattr(self.app, "get_status_snapshot") else {}
        return {
            "ok": True,
            "enabled": self.enabled,
            "running": self.is_running,
            "port": self.bound_port if self.is_running else self.port,
            "configured_profile": self.profile,
            "effective_profile": effective_profile,
            "target_w": target_w,
            "target_h": target_h,
            "is_night_mode": bool(st.get("is_night_mode", False)),
            "is_paused": bool(st.get("is_paused", False)),
            "slide_duration": float(st.get("slide_duration", 15.0)),
            "transition": str(st.get("transition", "none")),
            "landscape_mode": str(st.get("landscape_mode", "collage")),
            "night_start": str(st.get("night_start", "22:00")),
            "night_end": str(st.get("night_end", "06:00")),
            "photo_count": int(st.get("photo_count", 0)),
            "video_count": int(st.get("video_count", 0)),
            "mdns_hostname": str(st.get("mdns_hostname", get_active_mdns_hostname())),
            "app_version": str(st.get("app_version", "1.0.7")),
            "active_clients": self.get_active_client_count(),
        }

    def handle_http_get(
        self,
        handler: http.server.BaseHTTPRequestHandler,
        is_dedicated_port: bool = False,
    ) -> bool:
        """
        Handles GET requests for `/frame` (or `/` when `is_dedicated_port=True`) and
        `/api/webframe/*`. Returns `True` if the request path matched and was handled.
        """
        parsed = urllib.parse.urlparse(handler.path)
        path = parsed.path.rstrip("/") or "/"
        qs = urllib.parse.parse_qs(parsed.query)

        is_frame_page = (path == "/frame") or (is_dedicated_port and path in ("/", "/index.html"))
        if is_frame_page:
            if not self.enabled:
                body = DISABLED_WEB_FRAME_HTML.encode("utf-8")
                handler.send_response(403)
                handler.send_header("Content-Type", "text/html; charset=utf-8")
                handler.send_header("Content-Length", str(len(body)))
                handler.send_header("Cache-Control", "no-store")
                handler.end_headers()
                handler.wfile.write(body)
                return True

            body = WEB_FRAME_HTML.encode("utf-8")
            handler.send_response(200)
            handler.send_header("Content-Type", "text/html; charset=utf-8")
            handler.send_header("Content-Length", str(len(body)))
            handler.send_header("Cache-Control", "no-store")
            handler.end_headers()
            handler.wfile.write(body)
            return True

        if path == "/api/webframe/state":
            cid = (qs.get("cid", ["default"])[0] or "default").strip()
            req_w = qs.get("w", ["1024"])[0]
            req_h = qs.get("h", ["768"])[0]
            q_prof = qs.get("profile", [None])[0]
            ua = handler.headers.get("User-Agent", "") if hasattr(handler, "headers") and handler.headers else ""
            payload = self.build_state_payload(
                client_id=cid,
                req_w=req_w,
                req_h=req_h,
                query_profile=q_prof,
                user_agent=ua,
            )
            code = 200 if self.enabled else 403
            body = json.dumps(payload).encode("utf-8")
            handler.send_response(code)
            handler.send_header("Content-Type", "application/json; charset=utf-8")
            handler.send_header("Content-Length", str(len(body)))
            handler.send_header("Cache-Control", "no-store")
            handler.end_headers()
            handler.wfile.write(body)
            return True

        if path == "/api/webframe/slide.jpg":
            if not self.enabled:
                body = json.dumps({"ok": False, "enabled": False, "error": "Web Photo Frame is disabled"}).encode("utf-8")
                handler.send_response(403)
                handler.send_header("Content-Type", "application/json; charset=utf-8")
                handler.send_header("Content-Length", str(len(body)))
                handler.send_header("Cache-Control", "no-store")
                handler.end_headers()
                handler.wfile.write(body)
                return True

            cid = (qs.get("cid", ["default"])[0] or "default").strip()
            req_w = qs.get("w", ["1024"])[0]
            req_h = qs.get("h", ["768"])[0]
            q_prof = qs.get("profile", [None])[0]
            direction = (qs.get("dir", ["next"])[0] or "next").strip().lower()
            style_ov = (qs.get("style", [""])[0] or "").strip() or None
            ua = handler.headers.get("User-Agent", "") if hasattr(handler, "headers") and handler.headers else ""

            jpeg_bytes, eff_prof, tw, th = self.compose_client_slide_jpeg(
                client_id=cid,
                req_w=req_w,
                req_h=req_h,
                query_profile=q_prof,
                user_agent=ua,
                direction=direction,
                style_override=style_ov,
            )
            handler.send_response(200)
            handler.send_header("Content-Type", "image/jpeg")
            handler.send_header("Content-Length", str(len(jpeg_bytes)))
            handler.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            handler.send_header("X-WebFrame-Profile", eff_prof)
            handler.send_header("X-WebFrame-Dimensions", f"{tw}x{th}")
            handler.end_headers()
            handler.wfile.write(jpeg_bytes)
            return True

        return False
