"""
Password-protected Local Web Control Portal (HTTPS Server + 6-language UI).
Supports English (en), Chinese (zh), Japanese (ja), Korean (ko), Spanish (es), and German (de).
"""

import http.cookies
import http.server
import json
import os
import socket
import socketserver
import ssl
import tempfile
import threading
import time
import urllib.parse
from typing import TYPE_CHECKING, Any, Dict, Optional, Type

from .config import FrameConfigStore
from .hardware import logger
from .mdns import MDNS_HOSTNAME, start_mdns_responder

if TYPE_CHECKING:
    from .app import SlideshowApp

SUPPORTED_LANGUAGES = ("en", "zh", "ja", "ko", "es", "de")

WEB_PORTAL_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Photo Frame Control Portal</title>
<style>
  :root {
    --bg: #0b0f19;
    --card: #131b2e;
    --card-border: #23304d;
    --text: #f1f5f9;
    --muted: #94a3b8;
    --accent: #38bdf8;
    --accent-hover: #0284c7;
    --success: #10b981;
    --warn: #f59e0b;
    --danger: #ef4444;
    --input-bg: #0f1626;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.5;
    padding: 20px 14px 48px;
  }
  .container { max-width: 780px; margin: 0 auto; }
  header {
    display: flex; align-items: center; justify-content: space-between;
    flex-wrap: wrap; gap: 12px; margin-bottom: 20px;
    padding-bottom: 14px; border-bottom: 1px solid var(--card-border);
  }
  header h1 { font-size: 1.35rem; font-weight: 700; display: flex; align-items: center; gap: 8px; }
  .badge {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 5px 12px; border-radius: 999px; font-size: 0.82rem; font-weight: 600;
    background: #1e293b; border: 1px solid var(--card-border);
  }
  .badge.active { background: rgba(16, 185, 129, 0.15); color: #34d399; border-color: rgba(16, 185, 129, 0.35); }
  .badge.night { background: rgba(245, 158, 11, 0.15); color: #fbbf24; border-color: rgba(245, 158, 11, 0.35); }
  .card {
    background: var(--card); border: 1px solid var(--card-border);
    border-radius: 14px; padding: 20px; margin-bottom: 18px;
    box-shadow: 0 8px 24px rgba(0,0,0,0.35);
  }
  .card h2 {
    font-size: 1.05rem; font-weight: 600; margin-bottom: 14px;
    color: #e2e8f0; display: flex; align-items: center; justify-content: space-between;
  }
  .stats-grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    gap: 10px; margin-bottom: 14px;
  }
  .stat-box {
    background: var(--input-bg); border: 1px solid var(--card-border);
    border-radius: 10px; padding: 10px 12px;
  }
  .stat-label { font-size: 0.74rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.04em; }
  .stat-val { font-size: 0.98rem; font-weight: 600; margin-top: 2px; }
  .btn-grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px;
  }
  button, .btn {
    appearance: none; border: 1px solid var(--card-border);
    background: #1e293b; color: var(--text); font-weight: 600;
    font-size: 0.9rem; padding: 10px 14px; border-radius: 10px;
    cursor: pointer; transition: all 0.15s ease; display: inline-flex;
    align-items: center; justify-content: center; gap: 7px;
  }
  button:hover { background: #283548; border-color: #3b4f73; }
  button.primary { background: var(--accent); color: #082f49; border-color: transparent; }
  button.primary:hover { background: #7dd3fc; }
  button.success { background: rgba(16, 185, 129, 0.2); color: #34d399; border-color: rgba(16, 185, 129, 0.4); }
  button.warn { background: rgba(245, 158, 11, 0.2); color: #fbbf24; border-color: rgba(245, 158, 11, 0.4); }
  button.danger { background: rgba(239, 68, 68, 0.18); color: #f87171; border-color: rgba(239, 68, 68, 0.4); }
  .form-grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 14px;
  }
  .form-group { display: flex; flex-direction: column; gap: 5px; }
  .form-group.full { grid-column: 1 / -1; }
  label { font-size: 0.84rem; font-weight: 500; color: #cbd5e1; }
  input[type="text"], input[type="password"], input[type="number"], input[type="time"], select {
    width: 100%; padding: 10px 12px; border-radius: 9px;
    border: 1px solid var(--card-border); background: var(--input-bg);
    color: var(--text); font-size: 0.92rem;
  }
  input:focus, select:focus { outline: none; border-color: var(--accent); }
  .checkbox-row {
    display: flex; align-items: center; gap: 9px; padding-top: 6px;
  }
  .checkbox-row input[type="checkbox"] { width: 18px; height: 18px; accent-color: var(--accent); }
  .toast {
    position: fixed; top: 20px; left: 50%; transform: translateX(-50%); z-index: 9999;
    padding: 13px 22px; border-radius: 12px; font-weight: 700; font-size: 0.95rem;
    background: #0f172a; border: 2px solid var(--success); color: #f8fafc;
    box-shadow: 0 12px 32px rgba(0,0,0,0.65); display: none; text-align: center;
    min-width: 280px; max-width: 92vw;
  }
  .confirm-banner {
    margin-top: 12px; padding: 11px 14px; border-radius: 10px;
    font-size: 0.88rem; font-weight: 600; display: none;
    background: rgba(16, 185, 129, 0.16); border: 1px solid rgba(16, 185, 129, 0.5);
    color: #34d399;
  }
  .confirm-banner.error {
    background: rgba(239, 68, 68, 0.16); border-color: rgba(239, 68, 68, 0.5);
    color: #f87171;
  }
  pre.log-box {
    background: #070a12; border: 1px solid var(--card-border);
    border-radius: 10px; padding: 12px; font-size: 0.76rem;
    color: #93c5fd; overflow-x: auto; max-height: 240px; white-space: pre-wrap;
  }
  .warn-banner {
    background: rgba(245, 158, 11, 0.14); border: 1px solid rgba(245, 158, 11, 0.45);
    color: #fcd34d; padding: 11px 14px; border-radius: 10px; font-size: 0.86rem;
    margin-bottom: 16px;
  }
  #login-view { max-width: 400px; margin: 60px auto; }
  .hidden { display: none !important; }
</style>
</head>
<body>
<div class="container">
  <!-- LOGIN VIEW -->
  <div id="login-view" class="card hidden">
    <h2>
      <span data-i18n="login_title">🔒 Photo Frame Login</span>
      <select id="login-lang-select" onchange="setLanguage(this.value, true, true)" style="width:auto; padding:5px 10px; font-size:0.82rem;">
        <option value="en">🌐 English</option>
        <option value="zh">🌐 中文</option>
        <option value="ja">🌐 日本語</option>
        <option value="ko">🌐 한국어</option>
        <option value="es">🌐 Español</option>
        <option value="de">🌐 Deutsch</option>
      </select>
    </h2>
    <div id="default-pw-hint" class="warn-banner hidden" data-i18n-html="default_pw_hint">
      Default password is <b>admin</b>. You can change it after signing in.
    </div>
    <form id="login-form" onsubmit="handleLogin(event)">
      <div class="form-group" style="margin-bottom: 14px;">
        <label for="login-password" data-i18n="lbl_password">Password</label>
        <input type="password" id="login-password" data-i18n-placeholder="ph_password" placeholder="Enter password" required autofocus>
      </div>
      <button type="submit" class="primary" style="width: 100%;" data-i18n="btn_signin">Sign In</button>
    </form>
  </div>

  <!-- MAIN DASHBOARD VIEW -->
  <div id="app-view" class="hidden">
    <header>
      <h1 data-i18n="portal_title">🖼️ Photo Frame Control Portal</h1>
      <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
        <select id="header-lang-select" onchange="setLanguage(this.value, true, true)" style="width:auto; padding:5px 10px; font-size:0.8rem;">
          <option value="en">🌐 English</option>
          <option value="zh">🌐 中文</option>
          <option value="ja">🌐 日本語</option>
          <option value="ko">🌐 한국어</option>
          <option value="es">🌐 Español</option>
          <option value="de">🌐 Deutsch</option>
        </select>
        <span id="header-version-badge" class="badge" style="background:#1e293b; color:#93c5fd; border:1px solid #334155;">v1.0.3</span>
        <span id="state-badge" class="badge">Loading...</span>
        <button onclick="handleLogout()" style="padding:6px 11px; font-size:0.8rem;" data-i18n="btn_signout">Sign Out</button>
      </div>
    </header>

    <div id="default-pw-banner" class="warn-banner hidden" data-i18n-html="default_pw_banner">
      ⚠️ You are currently using the default password (<b>admin</b>). Scroll down to <b>Security & Password</b> to set a custom password.
    </div>

    <!-- LIVE STATUS & REMOTE ACTIONS -->
    <div class="card">
      <h2 data-i18n="sec_remote">⚡ Quick Remote Controls</h2>
      <div class="stats-grid">
        <div class="stat-box">
          <div class="stat-label" data-i18n="stat_album">Album Media</div>
          <div class="stat-val" id="stat-album">—</div>
        </div>
        <div class="stat-box">
          <div class="stat-label" data-i18n="stat_layout">Layout Mode</div>
          <div class="stat-val" id="stat-layout">—</div>
        </div>
        <div class="stat-box">
          <div class="stat-label" data-i18n="stat_night">Night Schedule</div>
          <div class="stat-val" id="stat-night">—</div>
        </div>
        <div class="stat-box">
          <div class="stat-label" data-i18n="stat_display">Display</div>
          <div class="stat-val" id="stat-display">—</div>
        </div>
      </div>

      <div class="btn-grid">
        <button id="btn-wake-sleep" class="warn" onclick="sendAction('toggle_night')" data-i18n="btn_wake_sleep">☀️ Wake / 🌙 Sleep</button>
        <button onclick="sendAction('prev')" data-i18n="btn_prev">⏮️ Previous</button>
        <button onclick="sendAction('pause_toggle')" id="btn-pause" data-i18n="btn_pause">⏯️ Pause / Resume</button>
        <button onclick="sendAction('next')" data-i18n="btn_next">⏭️ Next Slide</button>
        <button onclick="sendAction('resync')" data-i18n="btn_resync">🔄 Sync Album</button>
        <button onclick="playDemoShowcase()" data-i18n="btn_demo">✨ Play Demo Showcase</button>
        <button onclick="sendAction('test_videos')" data-i18n="btn_test_vid">🧪 Test Video Tiles</button>
      </div>
    </div>

    <!-- SLIDESHOW & ALBUM SETTINGS -->
    <div class="card">
      <h2 data-i18n="sec_settings">🎨 Album, Collage & Schedule Settings</h2>
      <form id="settings-form" onsubmit="saveSettings(event)">
        <div class="form-grid">
          <div class="form-group full">
            <label for="cfg-album" data-i18n="lbl_album">Google Photos Shared Album URL (leave blank or 'demo' for Demo Showcase Pack)</label>
            <input type="text" id="cfg-album" data-i18n-placeholder="ph_album" placeholder="https://photos.app.goo.gl/... (or leave blank for Demo Showcase)">
            <div style="font-size: 0.78rem; color: var(--muted); margin-top: 2px;" data-i18n="demo_ai_note">
              ✨ Note: Built-in Demo Showcase images and videos are AI-generated with Google Gemini &amp; Veo.
            </div>
          </div>

          <div class="form-group">
            <label for="cfg-language" data-i18n="lbl_language">Language / 语言</label>
            <select id="cfg-language" onchange="setLanguage(this.value, true, true)">
              <option value="en">English</option>
              <option value="zh">中文 (简体)</option>
              <option value="ja">日本語</option>
              <option value="ko">한국어</option>
              <option value="es">Español</option>
              <option value="de">Deutsch</option>
            </select>
          </div>

          <div class="form-group">
            <label for="cfg-layout" data-i18n="lbl_layout">Layout / Collage Style</label>
            <select id="cfg-layout">
              <option value="collage" data-i18n="opt_layout_collage">Collage Mix (All 9 Grids + Portrait Singles)</option>
              <option value="single" data-i18n="opt_layout_single">Single Full-Screen Only</option>
              <option value="trio" data-i18n="opt_layout_trio">Grid Trio (1 Landscape + 2 Portraits)</option>
              <option value="classic-5" data-i18n="opt_layout_classic5">Grid Classic 5 (1 Hero + 2P + 2L)</option>
              <option value="mosaic-7" data-i18n="opt_layout_mosaic7">Grid Mosaic 7 (Alternating Splits + Feature)</option>
              <option value="sidebar-9" data-i18n="opt_layout_sidebar9">Grid Sidebar 9 (Stacked Left + Feature Right)</option>
              <option value="mondrian-8" data-i18n="opt_layout_mondrian8">Grid Mondrian 8 (Symmetrical Mosaic)</option>
              <option value="grid-3-2-3" data-i18n="opt_layout_323">Grid 3-2-3 (3P + 2L + 3P)</option>
              <option value="stack" data-i18n="opt_layout_stack">Stack 2 (2 Landscapes)</option>
              <option value="triple" data-i18n="opt_layout_triple">Stack 3 (3 Landscapes)</option>
              <option value="bento" data-i18n="opt_layout_bento">Bento 4 (4 Landscapes)</option>
            </select>
          </div>

          <div class="form-group">
            <label for="cfg-duration" data-i18n="lbl_duration">Slide Duration (seconds)</label>
            <input type="number" id="cfg-duration" min="3" max="3600" step="1" required>
          </div>

          <div class="form-group">
            <label for="cfg-transition" data-i18n="lbl_transition">Slide Transition</label>
            <select id="cfg-transition">
              <option value="none" data-i18n="opt_trans_none">None (Instant Cut — Default)</option>
              <option value="fade" data-i18n="opt_trans_fade">Crossfade (Dissolve)</option>
              <option value="fade-black" data-i18n="opt_trans_fade_black">Fade Through Black</option>
              <option value="slide-left" data-i18n="opt_trans_slide_left">Slide Left (←)</option>
              <option value="slide-right" data-i18n="opt_trans_slide_right">Slide Right (→)</option>
              <option value="slide-up" data-i18n="opt_trans_slide_up">Slide Up (↑)</option>
              <option value="wipe" data-i18n="opt_trans_wipe">Horizontal Wipe Reveal</option>
              <option value="random" data-i18n="opt_trans_random">Random Transition Mix</option>
            </select>
          </div>

          <div class="form-group">
            <label for="cfg-videos" data-i18n="lbl_videos">Video Mode</label>
            <select id="cfg-videos">
              <option value="play" data-i18n="opt_vid_play">Play Live Video in Tiles (FFmpeg RAM)</option>
              <option value="skip" data-i18n="opt_vid_skip">Skip Videos</option>
              <option value="still" data-i18n="opt_vid_still">Show Static Video Thumbnail</option>
            </select>
          </div>

          <div class="form-group">
            <label for="cfg-max-vid" data-i18n="lbl_max_vid">Max Video Duration (seconds)</label>
            <input type="number" id="cfg-max-vid" min="3" max="120" step="1" required>
          </div>

          <div class="form-group">
            <label for="cfg-fit" data-i18n="lbl_fit">Single-Photo Fit Mode</label>
            <select id="cfg-fit">
              <option value="contain-blur" data-i18n="opt_fit_blur">Contain over Dark Bokeh Backdrop</option>
              <option value="contain" data-i18n="opt_fit_contain">Contain with Black Bars</option>
              <option value="cover" data-i18n="opt_fit_cover">Center-Crop Full Screen (Cover)</option>
            </select>
          </div>

          <div class="form-group">
            <label for="cfg-rotate" data-i18n="lbl_rotate">Screen Rotation</label>
            <select id="cfg-rotate">
              <option value="0" data-i18n="opt_rot_0">0° (Landscape)</option>
              <option value="180" data-i18n="opt_rot_180">180° (Landscape Inverted)</option>
              <option value="270" data-i18n="opt_rot_270">270° (Portrait)</option>
              <option value="90" data-i18n="opt_rot_90">90° (Portrait Flipped)</option>
            </select>
          </div>

          <div class="form-group">
            <label for="cfg-refresh" data-i18n="lbl_refresh">Album Auto-Sync Interval (hours)</label>
            <input type="number" id="cfg-refresh" min="0" max="168" step="0.5" required>
          </div>

          <div class="form-group">
            <label>&nbsp;</label>
            <div class="checkbox-row">
              <input type="checkbox" id="cfg-shuffle">
              <label for="cfg-shuffle" data-i18n="lbl_shuffle">Shuffle Photos & Videos Randomly</label>
            </div>
          </div>

          <div class="form-group">
            <label>&nbsp;</label>
            <div class="checkbox-row">
              <input type="checkbox" id="cfg-night-enabled">
              <label for="cfg-night-enabled" data-i18n="lbl_night_enabled">Enable Scheduled Night Mode</label>
            </div>
          </div>

          <div class="form-group">
            <label for="cfg-night-start" data-i18n="lbl_night_start">Night Mode Start (Sleep)</label>
            <input type="time" id="cfg-night-start" required>
          </div>

          <div class="form-group">
            <label for="cfg-night-end" data-i18n="lbl_night_end">Night Mode End (Wake)</label>
            <input type="time" id="cfg-night-end" required>
          </div>
        </div>

        <div style="margin-top: 18px; display: flex; align-items: center; gap: 12px; flex-wrap: wrap;">
          <button type="submit" id="btn-save-settings" class="primary" data-i18n="btn_save_settings">💾 Save & Apply Settings</button>
        </div>
        <div id="settings-confirm-banner" class="confirm-banner"></div>
      </form>
    </div>

    <!-- PASSWORD CHANGE -->
    <div class="card">
      <h2 data-i18n="sec_security">🔐 Security & Change Password</h2>
      <form id="pw-form" onsubmit="changePassword(event)">
        <div class="form-grid">
          <div class="form-group">
            <label for="pw-current" data-i18n="lbl_pw_current">Current Password</label>
            <input type="password" id="pw-current" required>
          </div>
          <div class="form-group">
            <label for="pw-new" data-i18n="lbl_pw_new">New Password</label>
            <input type="password" id="pw-new" minlength="4" required data-i18n-placeholder="ph_pw_new" placeholder="At least 4 characters">
          </div>
          <div class="form-group">
            <label for="pw-confirm" data-i18n="lbl_pw_confirm">Confirm New Password</label>
            <input type="password" id="pw-confirm" minlength="4" required>
          </div>
        </div>
        <div style="margin-top: 14px;">
          <button type="submit" id="btn-save-pw" class="success" data-i18n="btn_save_pw">🔐 Update Password</button>
        </div>
        <div id="pw-confirm-banner" class="confirm-banner"></div>
      </form>
    </div>

    <!-- SYSTEM POWER & RAM LOGS -->
    <div class="card">
      <h2>
        <span data-i18n="sec_system">🛠️ System Control & RAM Log</span>
        <button onclick="fetchStatus(false)" style="padding:5px 10px; font-size:0.78rem;" data-i18n="btn_refresh_log">🔄 Refresh Log</button>
      </h2>
      <pre class="log-box" id="log-box">Loading RAM log...</pre>
      <div class="btn-grid" style="margin-top: 14px;">
        <button class="warn" onclick="confirmSystemAction('restart')" data-i18n="btn_restart">♻️ Restart Slideshow</button>
        <button class="danger" onclick="confirmSystemAction('reboot')" data-i18n="btn_reboot">⚡ Reboot Device</button>
      </div>
    </div>
  </div>
</div>

<div id="toast" class="toast"></div>

<script>
const I18N = {
  en: {
    login_title: "🔒 Photo Frame Login",
    default_pw_hint: "Default password is <b>admin</b>. You can change it after signing in.",
    lbl_password: "Password",
    ph_password: "Enter password",
    btn_signin: "Sign In",
    portal_title: "🖼️ Photo Frame Control Portal",
    btn_signout: "Sign Out",
    default_pw_banner: "⚠️ You are currently using the default password (<b>admin</b>). Scroll down to <b>Security & Change Password</b> to set a custom password.",
    sec_remote: "⚡ Quick Remote Controls",
    stat_album: "Album Media",
    stat_layout: "Layout Mode",
    stat_night: "Night Schedule",
    stat_display: "Display",
    btn_wake_sleep: "☀️ Wake / 🌙 Sleep",
    btn_wake_now: "☀️ Wake Screen Now",
    btn_sleep_now: "🌙 Put Screen to Sleep",
    btn_prev: "⏮️ Previous",
    btn_pause: "⏯️ Pause / Resume",
    btn_next: "⏭️ Next Slide",
    btn_resync: "🔄 Sync Album",
    btn_demo: "✨ Play Demo Showcase",
    btn_test_vid: "🧪 Test Video Tiles",
    sec_settings: "🎨 Album, Collage & Schedule Settings",
    lbl_album: "Google Photos Shared Album URL (leave blank or 'demo' for Demo Showcase Pack)",
    ph_album: "https://photos.app.goo.gl/... (or leave blank for Demo Showcase)",
    lbl_language: "Language",
    lbl_layout: "Layout / Collage Style",
    opt_layout_collage: "Collage Mix (All 9 Grids + Portrait Singles)",
    opt_layout_single: "Single Full-Screen Only",
    opt_layout_trio: "Grid Trio (1 Landscape + 2 Portraits)",
    opt_layout_classic5: "Grid Classic 5 (1 Hero + 2P + 2L)",
    opt_layout_mosaic7: "Grid Mosaic 7 (Alternating Splits + Feature)",
    opt_layout_sidebar9: "Grid Sidebar 9 (Stacked Left + Feature Right)",
    opt_layout_mondrian8: "Grid Mondrian 8 (Symmetrical Mosaic)",
    opt_layout_323: "Grid 3-2-3 (3P + 2L + 3P)",
    opt_layout_stack: "Stack 2 (2 Landscapes)",
    opt_layout_triple: "Stack 3 (3 Landscapes)",
    opt_layout_bento: "Bento 4 (4 Landscapes)",
    lbl_duration: "Slide Duration (seconds)",
    lbl_transition: "Slide Transition",
    opt_trans_none: "None (Instant Cut — Default)",
    opt_trans_fade: "Crossfade (Dissolve)",
    opt_trans_fade_black: "Fade Through Black",
    opt_trans_slide_left: "Slide Left (←)",
    opt_trans_slide_right: "Slide Right (→)",
    opt_trans_slide_up: "Slide Up (↑)",
    opt_trans_wipe: "Horizontal Wipe Reveal",
    opt_trans_random: "Random Transition Mix",
    lbl_videos: "Video Mode",
    opt_vid_play: "Play Live Video in Tiles (FFmpeg RAM)",
    opt_vid_skip: "Skip Videos",
    opt_vid_still: "Show Static Video Thumbnail",
    lbl_max_vid: "Max Video Duration (seconds)",
    lbl_fit: "Single-Photo Fit Mode",
    opt_fit_blur: "Contain over Dark Bokeh Backdrop",
    opt_fit_contain: "Contain with Black Bars",
    opt_fit_cover: "Center-Crop Full Screen (Cover)",
    lbl_rotate: "Screen Rotation",
    opt_rot_270: "270° (Portrait)",
    opt_rot_90: "90° (Portrait Flipped)",
    opt_rot_0: "0° (Landscape)",
    opt_rot_180: "180° (Landscape Inverted)",
    lbl_refresh: "Album Auto-Sync Interval (hours)",
    lbl_shuffle: "Shuffle Photos & Videos Randomly",
    lbl_night_enabled: "Enable Scheduled Night Mode",
    lbl_night_start: "Night Mode Start (Sleep)",
    lbl_night_end: "Night Mode End (Wake)",
    btn_save_settings: "💾 Save & Apply Settings",
    btn_saving_settings: "⏳ Saving & Applying...",
    btn_saved_settings: "✅ Saved & Applied!",
    sec_security: "🔐 Security & Change Password",
    lbl_pw_current: "Current Password",
    lbl_pw_new: "New Password",
    ph_pw_new: "At least 4 characters",
    lbl_pw_confirm: "Confirm New Password",
    btn_save_pw: "🔐 Update Password",
    sec_system: "🛠️ System Control & RAM Log",
    btn_refresh_log: "🔄 Refresh Log",
    btn_restart: "♻️ Restart Slideshow",
    btn_reboot: "⚡ Reboot Device",
    badge_night: "🌙 Night Mode (Sleeping)",
    badge_paused: "⏸️ Paused",
    badge_active: "🟢 Active (Playing)",
    stat_items_fmt: "{prefix}{photos} items ({videos} videos)",
    stat_night_disabled: "Disabled (24/7 On)",
    msg_signed_in: "Signed in successfully",
    msg_invalid_pw: "Invalid password",
    msg_settings_saved_banner: "✅ Settings saved & applied live at {time}! Active source: {source} • Layout: {layout} ({duration}s)",
    msg_source_album: "Google Photos Album ({album})",
    msg_source_demo: "Built-in Demo Showcase Pack (AI-Generated)",
    demo_ai_note: "✨ Note: Built-in Demo Showcase images and videos are AI-generated with Google Gemini & Veo.",
    msg_pw_mismatch: "New passwords do not match",
    msg_pw_updated: "✅ Password updated successfully! Your session remains active.",
    confirm_restart: "Restart the slideshow service now?",
    confirm_reboot: "Reboot the device now? The screen will reconnect in ~35 seconds."
  },
  zh: {
    login_title: "🔒 数码相框管理登录",
    default_pw_hint: "默认密码为 <b>admin</b>，登录后可修改密码。",
    lbl_password: "管理密码",
    ph_password: "请输入密码",
    btn_signin: "登录",
    portal_title: "🖼️ 数码相框控制台",
    btn_signout: "退出登录",
    default_pw_banner: "⚠️ 您当前使用的是默认密码 (<b>admin</b>)。请向下滚动至 <b>安全与修改密码</b> 设置自定义密码。",
    sec_remote: "⚡ 快捷远程控制",
    stat_album: "相册媒体",
    stat_layout: "拼图布局",
    stat_night: "夜间模式计划",
    stat_display: "显示分辨率",
    btn_wake_sleep: "☀️ 唤醒 / 🌙 休眠",
    btn_wake_now: "☀️ 立即唤醒屏幕",
    btn_sleep_now: "🌙 立即进入休眠",
    btn_prev: "⏮️ 上一页",
    btn_pause: "⏯️ 暂停 / 继续",
    btn_next: "⏭️ 下一页",
    btn_resync: "🔄 同步相册",
    btn_demo: "✨ 播放内置演示集",
    btn_test_vid: "🧪 测试视频拼图",
    sec_settings: "🎨 相册、拼图与定时设置",
    lbl_album: "Google 相册共享链接（留空或填 'demo' 将播放内置演示精选集）",
    ph_album: "https://photos.app.goo.gl/...（留空则播放内置演示）",
    lbl_language: "界面语言 (Language)",
    lbl_layout: "幻灯片 / 拼图样式",
    opt_layout_collage: "智能拼图混排（全部 9 种网格 + 竖屏单张特写）",
    opt_layout_single: "仅单张全屏播放",
    opt_layout_trio: "三宫格 Trio（1 横 + 2 竖）",
    opt_layout_classic5: "经典五宫格（1 主图 + 2 竖 + 2 横）",
    opt_layout_mosaic7: "七宫格马赛克（交替分栏 + 底部主图）",
    opt_layout_sidebar9: "九宫格侧边栏（左侧叠排 + 右侧主图）",
    opt_layout_mondrian8: "八宫格蒙德里安（对称几何拼图）",
    opt_layout_323: "八宫格 3-2-3（3 竖 + 2 横 + 3 竖）",
    opt_layout_stack: "双联上下叠排（2 横）",
    opt_layout_triple: "三联上下叠排（3 横）",
    opt_layout_bento: "四宫格便当盒（4 横）",
    lbl_duration: "单页停留时长（秒）",
    lbl_transition: "幻灯片切换过渡效果",
    opt_trans_none: "无过渡（直接切换 — 默认）",
    opt_trans_fade: "柔和淡入淡出 (Crossfade)",
    opt_trans_fade_black: "黑场渐变过渡 (Fade Black)",
    opt_trans_slide_left: "向左平滑推入 (←)",
    opt_trans_slide_right: "向右平滑推入 (→)",
    opt_trans_slide_up: "向上平滑推入 (↑)",
    opt_trans_wipe: "水平擦除展开 (Wipe)",
    opt_trans_random: "随机过渡动画组合",
    lbl_videos: "视频播放模式",
    opt_vid_play: "在拼图格子中实时播放视频（FFmpeg 内存流）",
    opt_vid_skip: "跳过所有视频",
    opt_vid_still: "仅显示视频静态封面",
    lbl_max_vid: "最长视频播放时间（秒）",
    lbl_fit: "单张照片缩放模式",
    opt_fit_blur: "完整显示 + 暗色散景模糊背景",
    opt_fit_contain: "完整显示（黑边填充）",
    opt_fit_cover: "居中裁剪铺满全屏 (Cover)",
    lbl_rotate: "屏幕旋转角度",
    opt_rot_270: "270°（竖屏 Portrait）",
    opt_rot_90: "90°（反向竖屏）",
    opt_rot_0: "0°（标准横屏）",
    opt_rot_180: "180°（反向横屏）",
    lbl_refresh: "相册自动同步间隔（小时）",
    lbl_shuffle: "随机打乱照片与视频顺序",
    lbl_night_enabled: "启用定时夜间休眠模式",
    lbl_night_start: "夜间休眠开始时间",
    lbl_night_end: "早晨自动唤醒时间",
    btn_save_settings: "💾 保存并立即应用设置",
    btn_saving_settings: "⏳ 正在保存并应用...",
    btn_saved_settings: "✅ 已保存并生效！",
    sec_security: "🔐 安全与修改密码",
    lbl_pw_current: "当前密码",
    lbl_pw_new: "新密码",
    ph_pw_new: "至少 4 个字符",
    lbl_pw_confirm: "确认新密码",
    btn_save_pw: "🔐 更新密码",
    sec_system: "🛠️ 系统控制与内存日志",
    btn_refresh_log: "🔄 刷新日志",
    btn_restart: "♻️ 重启幻灯片服务",
    btn_reboot: "⚡ 重启设备",
    badge_night: "🌙 夜间休眠中",
    badge_paused: "⏸️ 已暂停",
    badge_active: "🟢 正在播放",
    stat_items_fmt: "{prefix}{photos} 项媒体 ({videos} 个视频)",
    stat_night_disabled: "已禁用（24 小时常亮）",
    msg_signed_in: "登录成功",
    msg_invalid_pw: "密码错误",
    msg_settings_saved_banner: "✅ 设置已于 {time} 保存并实时生效！当前媒体源：{source} • 布局：{layout} ({duration}秒)",
    msg_source_album: "Google 共享相册 ({album})",
    msg_source_demo: "内置精选演示集 (AI 生成媒体)",
    demo_ai_note: "✨ 注：内置演示精选集中的图片与视频均由 Google Gemini 与 Veo AI 生成。",
    msg_pw_mismatch: "两次输入的新密码不一致",
    msg_pw_updated: "✅ 密码更新成功！当前登录会话保持有效。",
    confirm_restart: "确定要立即重启幻灯片服务吗？",
    confirm_reboot: "确定要立即重启设备吗？屏幕将在约 35 秒后重新连接。"
  },
  ja: {
    login_title: "🔒 フォトフレーム ログイン",
    default_pw_hint: "初期パスワードは <b>admin</b> です。ログイン後に変更できます。",
    lbl_password: "パスワード",
    ph_password: "パスワードを入力",
    btn_signin: "ログイン",
    portal_title: "🖼️ フォトフレーム管理ポータル",
    btn_signout: "ログアウト",
    default_pw_banner: "⚠️ 現在、初期パスワード (<b>admin</b>) を使用しています。下の<b>セキュリティとパスワード変更</b>から変更してください。",
    sec_remote: "⚡ クイックリモコン操作",
    stat_album: "アルバムメディア",
    stat_layout: "レイアウトモード",
    stat_night: "ナイトモード予定",
    stat_display: "ディスプレイ",
    btn_wake_sleep: "☀️ 起動 / 🌙 スリープ",
    btn_wake_now: "☀️ 今すぐ画面をオン",
    btn_sleep_now: "🌙 画面をスリープにする",
    btn_prev: "⏮️ 前へ",
    btn_pause: "⏯️ 一時停止 / 再開",
    btn_next: "⏭️ 次のスライド",
    btn_resync: "🔄 アルバム同期",
    btn_demo: "✨ デモショーケース再生",
    btn_test_vid: "🧪 動画タイルをテスト",
    sec_settings: "🎨 アルバム・コラージュ・スケジュール設定",
    lbl_album: "Google フォト共有アルバム URL（空欄または 'demo' で内蔵デモ再生）",
    ph_album: "https://photos.app.goo.gl/...（空欄でデモ再生）",
    lbl_language: "表示言語 (Language)",
    lbl_layout: "レイアウト / コラージュスタイル",
    opt_layout_collage: "コラージュミックス（全9種グリッド + 縦画面シングル）",
    opt_layout_single: "シングル全画面のみ",
    opt_layout_trio: "Grid Trio（横1 + 縦2）",
    opt_layout_classic5: "Grid Classic 5（メイン1 + 縦2 + 横2）",
    opt_layout_mosaic7: "Grid Mosaic 7（分割 + フィーチャー）",
    opt_layout_sidebar9: "Grid Sidebar 9（左スタック + 右メイン）",
    opt_layout_mondrian8: "Grid Mondrian 8（対称モザイク）",
    opt_layout_323: "Grid 3-2-3（縦3 + 横2 + 縦3）",
    opt_layout_stack: "Stack 2（横2枚上下）",
    opt_layout_triple: "Stack 3（横3枚上下）",
    opt_layout_bento: "Bento 4（横4枚グリッド）",
    lbl_duration: "スライド表示時間（秒）",
    lbl_transition: "スライド切り替え効果",
    opt_trans_none: "なし（即時切り替え — デフォルト）",
    opt_trans_fade: "クロスフェード（ディゾルブ）",
    opt_trans_fade_black: "ブラックフェード",
    opt_trans_slide_left: "左へスライド (←)",
    opt_trans_slide_right: "右へスライド (→)",
    opt_trans_slide_up: "上へスライド (↑)",
    opt_trans_wipe: "水平ワイプ切り替え",
    opt_trans_random: "ランダム切り替えミックス",
    lbl_videos: "動画再生モード",
    opt_vid_play: "タイル内でライブ動画再生 (FFmpeg RAM)",
    opt_vid_skip: "動画をスキップ",
    opt_vid_still: "静止画サムネイルのみ表示",
    lbl_max_vid: "最大動画再生時間（秒）",
    lbl_fit: "シングル写真のフィットモード",
    opt_fit_blur: "ダークボケ背景 + 全体表示",
    opt_fit_contain: "黒帯付き全体表示",
    opt_fit_cover: "中央クロップ全画面 (Cover)",
    lbl_rotate: "画面の回転",
    opt_rot_270: "270°（縦向き Portrait）",
    opt_rot_90: "90°（縦向き反転）",
    opt_rot_0: "0°（横向き Landscape）",
    opt_rot_180: "180°（横向き反転）",
    lbl_refresh: "アルバム自動同期間隔（時間）",
    lbl_shuffle: "写真と動画をランダムにシャッフル",
    lbl_night_enabled: "スケジュールされたナイトモードを有効化",
    lbl_night_start: "ナイトモード開始（スリープ）",
    lbl_night_end: "ナイトモード終了（ウェイク）",
    btn_save_settings: "💾 設定を保存して適用",
    btn_saving_settings: "⏳ 保存して適用中...",
    btn_saved_settings: "✅ 保存・適用完了！",
    sec_security: "🔐 セキュリティとパスワード変更",
    lbl_pw_current: "現在のパスワード",
    lbl_pw_new: "新しいパスワード",
    ph_pw_new: "4文字以上",
    lbl_pw_confirm: "新しいパスワード（確認）",
    btn_save_pw: "🔐 パスワードを更新",
    sec_system: "🛠️ システム管理 & RAMログ",
    btn_refresh_log: "🔄 ログ更新",
    btn_restart: "♻️ スライドショー再起動",
    btn_reboot: "⚡ デバイスを再起動",
    badge_night: "🌙 ナイトモード（スリープ中）",
    badge_paused: "⏸️ 一時停止中",
    badge_active: "🟢 再生中 (Active)",
    stat_items_fmt: "{prefix}{photos} 件 ({videos} 動画)",
    stat_night_disabled: "無効（24時間オン）",
    msg_signed_in: "ログインしました",
    msg_invalid_pw: "パスワードが正しくありません",
    msg_settings_saved_banner: "✅ {time} に設定を保存・即時適用しました！ソース: {source} • レイアウト: {layout} ({duration}秒)",
    msg_source_album: "Google フォトアルバム ({album})",
    msg_source_demo: "内蔵デモショーケースパック (AI生成)",
    demo_ai_note: "✨ 注：内蔵デモショーケースの画像と動画は Google Gemini & Veo による AI 生成メディアです。",
    msg_pw_mismatch: "新しいパスワードが一致しません",
    msg_pw_updated: "✅ パスワードを更新しました！セッションは維持されます。",
    confirm_restart: "スライドショーサービスを今すぐ再起動しますか？",
    confirm_reboot: "デバイスを今すぐ再起動しますか？約35秒後に再接続されます。"
  },
  ko: {
    login_title: "🔒 디지털 액자 로그인",
    default_pw_hint: "기본 비밀번호는 <b>admin</b>입니다. 로그인 후 변경할 수 있습니다.",
    lbl_password: "비밀번호",
    ph_password: "비밀번호 입력",
    btn_signin: "로그인",
    portal_title: "🖼️ 디지털 액자 제어 포털",
    btn_signout: "로그아웃",
    default_pw_banner: "⚠️ 현재 기본 비밀번호(<b>admin</b>)를 사용 중입니다. 아래 <b>보안 및 비밀번호 변경</b>에서 새 비밀번호를 설정하세요.",
    sec_remote: "⚡ 빠른 원격 제어",
    stat_album: "앨범 미디어",
    stat_layout: "레이아웃 모드",
    stat_night: "야간 모드 일정",
    stat_display: "디스플레이",
    btn_wake_sleep: "☀️ 화면 켜기 / 🌙 절전",
    btn_wake_now: "☀️ 지금 화면 깨우기",
    btn_sleep_now: "🌙 지금 절전 모드로 전환",
    btn_prev: "⏮️ 이전",
    btn_pause: "⏯️ 일시정지 / 재생",
    btn_next: "⏭️ 다음 슬라이드",
    btn_resync: "🔄 앨범 동기화",
    btn_demo: "✨ 데모 쇼케이스 재생",
    btn_test_vid: "🧪 비디오 타일 테스트",
    sec_settings: "🎨 앨범, 콜라주 및 일정 설정",
    lbl_album: "Google 포토 공유 앨범 URL (비워두거나 'demo' 입력 시 데모 팩 재생)",
    ph_album: "https://photos.app.goo.gl/... (비워두면 데모 쇼케이스 재생)",
    lbl_language: "언어 (Language)",
    lbl_layout: "레이아웃 / 콜라주 스타일",
    opt_layout_collage: "콜라주 믹스 (9개 그리드 전체 + 세로 전체화면)",
    opt_layout_single: "단일 전체화면만 표시",
    opt_layout_trio: "Grid Trio (가로 1 + 세로 2)",
    opt_layout_classic5: "Grid Classic 5 (메인 1 + 세로 2 + 가로 2)",
    opt_layout_mosaic7: "Grid Mosaic 7 (교차 분할 + 피처)",
    opt_layout_sidebar9: "Grid Sidebar 9 (좌측 스택 + 우측 피처)",
    opt_layout_mondrian8: "Grid Mondrian 8 (대칭 모자이크)",
    opt_layout_323: "Grid 3-2-3 (세로 3 + 가로 2 + 세로 3)",
    opt_layout_stack: "Stack 2 (가로 2장 상하 배치)",
    opt_layout_triple: "Stack 3 (가로 3장 상하 배치)",
    opt_layout_bento: "Bento 4 (가로 4장 그리드)",
    lbl_duration: "슬라이드 전환 시간 (초)",
    lbl_transition: "슬라이드 전환 효과",
    opt_trans_none: "없음 (즉시 전환 — 기본값)",
    opt_trans_fade: "크로스페이드 (디졸브)",
    opt_trans_fade_black: "블랙 페이드 전환",
    opt_trans_slide_left: "왼쪽으로 슬라이드 (←)",
    opt_trans_slide_right: "오른쪽으로 슬라이드 (→)",
    opt_trans_slide_up: "위로 슬라이드 (↑)",
    opt_trans_wipe: "수평 와이프 전환",
    opt_trans_random: "랜덤 전환 효과 믹스",
    lbl_videos: "동영상 재생 모드",
    opt_vid_play: "타일 내 실시간 동영상 재생 (FFmpeg RAM)",
    opt_vid_skip: "동영상 건너뛰기",
    opt_vid_still: "정지 썸네일만 표시",
    lbl_max_vid: "최대 동영상 재생 시간 (초)",
    lbl_fit: "단일 사진 맞춤 모드",
    opt_fit_blur: "다크 보케 배경 위 전체 표시",
    opt_fit_contain: "검은 여백과 함께 전체 표시",
    opt_fit_cover: "중앙 크롭 전체화면 (Cover)",
    lbl_rotate: "화면 회전",
    opt_rot_270: "270° (세로 모드)",
    opt_rot_90: "90° (세로 모드 반전)",
    opt_rot_0: "0° (가로 모드)",
    opt_rot_180: "180° (가로 모드 반전)",
    lbl_refresh: "앨범 자동 동기화 주기 (시간)",
    lbl_shuffle: "사진 및 동영상 무작위 셔플",
    lbl_night_enabled: "예약된 야간 절전 모드 사용",
    lbl_night_start: "야간 모드 시작 (절전)",
    lbl_night_end: "야간 모드 종료 (기상)",
    btn_save_settings: "💾 설정 저장 및 즉시 적용",
    btn_saving_settings: "⏳ 저장 및 적용 중...",
    btn_saved_settings: "✅ 저장 및 적용 완료!",
    sec_security: "🔐 보안 및 비밀번호 변경",
    lbl_pw_current: "현재 비밀번호",
    lbl_pw_new: "새 비밀번호",
    ph_pw_new: "최소 4자 이상",
    lbl_pw_confirm: "새 비밀번호 확인",
    btn_save_pw: "🔐 비밀번호 업데이트",
    sec_system: "🛠️ 시스템 제어 및 RAM 로그",
    btn_refresh_log: "🔄 로그 새로고침",
    btn_restart: "♻️ 슬라이드쇼 재시작",
    btn_reboot: "⚡ 기기 재부팅",
    badge_night: "🌙 야간 모드 (절전 중)",
    badge_paused: "⏸️ 일시정지됨",
    badge_active: "🟢 재생 중 (Active)",
    stat_items_fmt: "{prefix}{photos}개 항목 (동영상 {videos}개)",
    stat_night_disabled: "비활성화됨 (24시간 켜짐)",
    msg_signed_in: "로그인되었습니다",
    msg_invalid_pw: "비밀번호가 올바르지 않습니다",
    msg_settings_saved_banner: "✅ {time}에 설정이 저장 및 실시간 적용되었습니다! 활성 소스: {source} • 레이아웃: {layout} ({duration}초)",
    msg_source_album: "Google 포토 앨범 ({album})",
    msg_source_demo: "내장 데모 쇼케이스 팩 (AI 생성 미디어)",
    demo_ai_note: "✨ 참고: 내장 데모 쇼케이스의 이미지와 동영상은 Google Gemini 및 Veo AI로 생성되었습니다.",
    msg_pw_mismatch: "새 비밀번호가 일치하지 않습니다",
    msg_pw_updated: "✅ 비밀번호가 성공적으로 변경되었습니다!",
    confirm_restart: "슬라이드쇼 서비스를 지금 재시작하시겠습니까?",
    confirm_reboot: "기기를 지금 재부팅하시겠습니까? 약 35초 후 다시 연결됩니다."
  },
  es: {
    login_title: "🔒 Acceso al Marco Digital",
    default_pw_hint: "La contraseña predeterminada es <b>admin</b>. Puedes cambiarla después de iniciar sesión.",
    lbl_password: "Contraseña",
    ph_password: "Ingresa la contraseña",
    btn_signin: "Iniciar Sesión",
    portal_title: "🖼️ Portal del Marco Digital",
    btn_signout: "Cerrar Sesión",
    default_pw_banner: "⚠️ Estás usando la contraseña predeterminada (<b>admin</b>). Desplázate a <b>Seguridad y Contraseña</b> para cambiarla.",
    sec_remote: "⚡ Controles Remotos Rápidos",
    stat_album: "Multimedia del Álbum",
    stat_layout: "Modo de Diseño",
    stat_night: "Horario Nocturno",
    stat_display: "Pantalla",
    btn_wake_sleep: "☀️ Despertar / 🌙 Dormir",
    btn_wake_now: "☀️ Despertar Pantalla Ahora",
    btn_sleep_now: "🌙 Poner Pantalla en Reposo",
    btn_prev: "⏮️ Anterior",
    btn_pause: "⏯️ Pausar / Reanudar",
    btn_next: "⏭️ Siguiente",
    btn_resync: "🔄 Sincronizar Álbum",
    btn_demo: "✨ Reproducir Demo",
    btn_test_vid: "🧪 Probar Mosaicos de Video",
    sec_settings: "🎨 Configuración de Álbum, Collage y Horario",
    lbl_album: "URL del Álbum Compartido de Google Fotos (dejar vacío o 'demo' para el paquete Demo)",
    ph_album: "https://photos.app.goo.gl/... (o dejar vacío para Demo)",
    lbl_language: "Idioma (Language)",
    lbl_layout: "Estilo de Diseño / Collage",
    opt_layout_collage: "Mezcla de Collages (9 Cuadrículas + Individuales Verticales)",
    opt_layout_single: "Solo Pantalla Completa Individual",
    opt_layout_trio: "Grid Trio (1 Horizontal + 2 Verticales)",
    opt_layout_classic5: "Grid Classic 5 (1 Principal + 2V + 2H)",
    opt_layout_mosaic7: "Grid Mosaic 7 (Divisiones Alternas + Principal)",
    opt_layout_sidebar9: "Grid Sidebar 9 (Columna Izquierda + Principal Derecha)",
    opt_layout_mondrian8: "Grid Mondrian 8 (Mosaico Simétrico)",
    opt_layout_323: "Grid 3-2-3 (3V + 2H + 3V)",
    opt_layout_stack: "Stack 2 (2 Horizontales)",
    opt_layout_triple: "Stack 3 (3 Horizontales)",
    opt_layout_bento: "Bento 4 (4 Horizontales)",
    lbl_duration: "Duración de Diapositiva (segundos)",
    lbl_transition: "Transición de Diapositiva",
    opt_trans_none: "Ninguna (Corte Instantáneo — Predeterminado)",
    opt_trans_fade: "Fundido Cruzado (Disolver)",
    opt_trans_fade_black: "Fundido a Negro",
    opt_trans_slide_left: "Deslizar a la Izquierda (←)",
    opt_trans_slide_right: "Deslizar a la Derecha (→)",
    opt_trans_slide_up: "Deslizar hacia Arriba (↑)",
    opt_trans_wipe: "Barrido Horizontal",
    opt_trans_random: "Mezcla Aleatoria de Transiciones",
    lbl_videos: "Modo de Video",
    opt_vid_play: "Reproducir Video en Vivo en Mosaicos (FFmpeg RAM)",
    opt_vid_skip: "Omitir Videos",
    opt_vid_still: "Mostrar Miniatura Estática",
    lbl_max_vid: "Duración Máxima de Video (segundos)",
    lbl_fit: "Ajuste de Foto Individual",
    opt_fit_blur: "Contener sobre Fondo Bokeh Oscuro",
    opt_fit_contain: "Contener con Barras Negras",
    opt_fit_cover: "Recorte Central Pantalla Completa (Cover)",
    lbl_rotate: "Rotación de Pantalla",
    opt_rot_270: "270° (Vertical / Portrait)",
    opt_rot_90: "90° (Vertical Invertido)",
    opt_rot_0: "0° (Horizontal / Landscape)",
    opt_rot_180: "180° (Horizontal Invertido)",
    lbl_refresh: "Intervalo de Sincronización Automática (horas)",
    lbl_shuffle: "Mezclar Fotos y Videos Aleatoriamente",
    lbl_night_enabled: "Activar Modo Nocturno Programado",
    lbl_night_start: "Inicio del Modo Nocturno (Dormir)",
    lbl_night_end: "Fin del Modo Nocturno (Despertar)",
    btn_save_settings: "💾 Guardar y Aplicar Configuración",
    btn_saving_settings: "⏳ Guardando y Aplicando...",
    btn_saved_settings: "✅ ¡Guardado y Aplicado!",
    sec_security: "🔐 Seguridad y Cambiar Contraseña",
    lbl_pw_current: "Contraseña Actual",
    lbl_pw_new: "Nueva Contraseña",
    ph_pw_new: "Al menos 4 caracteres",
    lbl_pw_confirm: "Confirmar Nueva Contraseña",
    btn_save_pw: "🔐 Actualizar Contraseña",
    sec_system: "🛠️ Control del Sistema y Registro RAM",
    btn_refresh_log: "🔄 Actualizar Registro",
    btn_restart: "♻️ Reiniciar Presentación",
    btn_reboot: "⚡ Reiniciar dispositivo",
    badge_night: "🌙 Modo Nocturno (En Reposo)",
    badge_paused: "⏸️ En Pausa",
    badge_active: "🟢 Activo (Reproduciendo)",
    stat_items_fmt: "{prefix}{photos} elementos ({videos} videos)",
    stat_night_disabled: "Desactivado (24/7 Encendido)",
    msg_signed_in: "Sesión iniciada correctamente",
    msg_invalid_pw: "Contraseña incorrecta",
    msg_settings_saved_banner: "✅ ¡Configuración guardada y aplicada a las {time}! Fuente: {source} • Diseño: {layout} ({duration}s)",
    msg_source_album: "Álbum de Google Fotos ({album})",
    msg_source_demo: "Paquete Demo Integrado (Generado por IA)",
    demo_ai_note: "✨ Nota: Las imágenes y videos del paquete Demo integrado son generados por IA con Google Gemini y Veo.",
    msg_pw_mismatch: "Las contraseñas nuevas no coinciden",
    msg_pw_updated: "✅ ¡Contraseña actualizada correctamente! Tu sesión sigue activa.",
    confirm_restart: "¿Reiniciar el servicio de presentación ahora?",
    confirm_reboot: "¿Reiniciar el dispositivo ahora? La pantalla se reconectará en ~35 segundos."
  },
  de: {
    login_title: "🔒 Bilderrahmen-Anmeldung",
    default_pw_hint: "Das Standardpasswort lautet <b>admin</b>. Sie können es nach der Anmeldung ändern.",
    lbl_password: "Passwort",
    ph_password: "Passwort eingeben",
    btn_signin: "Anmelden",
    portal_title: "🖼️ Bilderrahmen-Steuerportal",
    btn_signout: "Abmelden",
    default_pw_banner: "⚠️ Sie verwenden derzeit das Standardpasswort (<b>admin</b>). Scrollen Sie zu <b>Sicherheit & Passwort ändern</b>, um ein eigenes Passwort festzulegen.",
    sec_remote: "⚡ Schnellsteuerung",
    stat_album: "Album-Medien",
    stat_layout: "Layout-Modus",
    stat_night: "Nachtmodus-Zeitplan",
    stat_display: "Anzeige",
    btn_wake_sleep: "☀️ Aufwecken / 🌙 Ruhemodus",
    btn_wake_now: "☀️ Bildschirm jetzt aufwecken",
    btn_sleep_now: "🌙 Bildschirm in Ruhemodus versetzen",
    btn_prev: "⏮️ Zurück",
    btn_pause: "⏯️ Pause / Fortsetzen",
    btn_next: "⏭️ Nächstes Bild",
    btn_resync: "🔄 Album synchronisieren",
    btn_demo: "✨ Demo-Showcase abspielen",
    btn_test_vid: "🧪 Videokacheln testen",
    sec_settings: "🎨 Album-, Collagen- & Zeitplaneinstellungen",
    lbl_album: "Google Fotos Geteilte Album-URL (leer lassen oder 'demo' für integriertes Demo-Paket)",
    ph_album: "https://photos.app.goo.gl/... (oder leer lassen für Demo-Paket)",
    lbl_language: "Sprache (Language)",
    lbl_layout: "Layout / Collagen-Stil",
    opt_layout_collage: "Collagen-Mix (Alle 9 Raster + Hochformat-Einzelbilder)",
    opt_layout_single: "Nur Einzelbild-Vollbild",
    opt_layout_trio: "Grid Trio (1 Quer + 2 Hoch)",
    opt_layout_classic5: "Grid Classic 5 (1 Hauptbild + 2H + 2Q)",
    opt_layout_mosaic7: "Grid Mosaic 7 (Wechselnde Teilung + Hauptbild)",
    opt_layout_sidebar9: "Grid Sidebar 9 (Links gestapelt + Rechts Hauptbild)",
    opt_layout_mondrian8: "Grid Mondrian 8 (Symmetrisches Mosaik)",
    opt_layout_323: "Grid 3-2-3 (3H + 2Q + 3H)",
    opt_layout_stack: "Stack 2 (2 Querformat)",
    opt_layout_triple: "Stack 3 (3 Querformat)",
    opt_layout_bento: "Bento 4 (4 Querformat)",
    lbl_duration: "Anzeigedauer pro Folie (Sekunden)",
    lbl_transition: "Folienübergang",
    opt_trans_none: "Keiner (Direkter Schnitt — Standard)",
    opt_trans_fade: "Überblenden (Crossfade)",
    opt_trans_fade_black: "Schwarzblende",
    opt_trans_slide_left: "Nach links schieben (←)",
    opt_trans_slide_right: "Nach rechts schieben (→)",
    opt_trans_slide_up: "Nach oben schieben (↑)",
    opt_trans_wipe: "Horizontaler Wischübergang",
    opt_trans_random: "Zufälliger Übergangs-Mix",
    lbl_videos: "Videomodus",
    opt_vid_play: "Live-Video in Kacheln abspielen (FFmpeg RAM)",
    opt_vid_skip: "Videos überspringen",
    opt_vid_still: "Statisches Vorschaubild anzeigen",
    lbl_max_vid: "Maximale Videodauer (Sekunden)",
    lbl_fit: "Einzelbild-Anpassung",
    opt_fit_blur: "Einpassen über dunklem Bokeh-Hintergrund",
    opt_fit_contain: "Einpassen mit schwarzen Balken",
    opt_fit_cover: "Mittig zuschneiden / Vollbild (Cover)",
    lbl_rotate: "Bildschirmdrehung",
    opt_rot_270: "270° (Hochformat / Portrait)",
    opt_rot_90: "90° (Hochformat gedreht)",
    opt_rot_0: "0° (Querformat / Landscape)",
    opt_rot_180: "180° (Querformat gedreht)",
    lbl_refresh: "Auto-Sync-Intervall des Albums (Stunden)",
    lbl_shuffle: "Fotos & Videos zufällig mischen",
    lbl_night_enabled: "Geplanten Nachtmodus aktivieren",
    lbl_night_start: "Nachtmodus Start (Ruhemodus)",
    lbl_night_end: "Nachtmodus Ende (Aufwachen)",
    btn_save_settings: "💾 Einstellungen speichern & anwenden",
    btn_saving_settings: "⏳ Speichern & Anwenden...",
    btn_saved_settings: "✅ Gespeichert & Angewendet!",
    sec_security: "🔐 Sicherheit & Passwort ändern",
    lbl_pw_current: "Aktuelles Passwort",
    lbl_pw_new: "Neues Passwort",
    ph_pw_new: "Mindestens 4 Zeichen",
    lbl_pw_confirm: "Neues Passwort bestätigen",
    btn_save_pw: "🔐 Passwort aktualisieren",
    sec_system: "🛠️ Systemsteuerung & RAM-Protokoll",
    btn_refresh_log: "🔄 Protokoll aktualisieren",
    btn_restart: "♻️ Diashow neu starten",
    btn_reboot: "⚡ Gerät neu starten",
    badge_night: "🌙 Nachtmodus (Schlafend)",
    badge_paused: "⏸️ Pausiert",
    badge_active: "🟢 Aktiv (Wiedergabe)",
    stat_items_fmt: "{prefix}{photos} Elemente ({videos} Videos)",
    stat_night_disabled: "Deaktiviert (24/7 An)",
    msg_signed_in: "Erfolgreich angemeldet",
    msg_invalid_pw: "Ungültiges Passwort",
    msg_settings_saved_banner: "✅ Einstellungen um {time} gespeichert & live angewendet! Quelle: {source} • Layout: {layout} ({duration}s)",
    msg_source_album: "Google Fotos-Album ({album})",
    msg_source_demo: "Integriertes Demo-Showcase-Paket (KI-generiert)",
    demo_ai_note: "✨ Hinweis: Die Bilder und Videos des integrierten Demo-Pakets wurden mit Google Gemini & Veo KI-generiert.",
    msg_pw_mismatch: "Neue Passwörter stimmen nicht überein",
    msg_pw_updated: "✅ Passwort erfolgreich aktualisiert! Ihre Sitzung bleibt aktiv.",
    confirm_restart: "Diashow-Dienst jetzt neu starten?",
    confirm_reboot: "Gerät jetzt neu starten? Der Bildschirm verbindet sich in ca. 35 Sekunden neu."
  }
};

let currentLang = localStorage.getItem('frame_lang') || 'en';
let formPopulated = false;
let lastStatusSnapshot = null;

function t(key, vars = {}) {
  const dict = I18N[currentLang] || I18N.en;
  let str = dict[key] || I18N.en[key] || key;
  for (const [k, v] of Object.entries(vars)) {
    str = str.replace(`{${k}}`, v);
  }
  return str;
}

function setLanguage(lang, syncSelectors = true, userPicked = false) {
  if (!I18N[lang]) lang = 'en';
  currentLang = lang;
  localStorage.setItem('frame_lang', lang);
  if (userPicked) {
    localStorage.setItem('frame_lang_user_set', '1');
  }
  document.documentElement.lang = lang;
  if (syncSelectors) {
    ['login-lang-select', 'header-lang-select', 'cfg-language'].forEach(id => {
      const el = document.getElementById(id);
      if (el && el.value !== lang) el.value = lang;
    });
  }
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.getAttribute('data-i18n');
    el.textContent = t(key);
  });
  document.querySelectorAll('[data-i18n-html]').forEach(el => {
    const key = el.getAttribute('data-i18n-html');
    el.innerHTML = t(key);
  });
  document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
    const key = el.getAttribute('data-i18n-placeholder');
    el.setAttribute('placeholder', t(key));
  });
  if (lastStatusSnapshot) {
    renderStatusSnapshot(lastStatusSnapshot);
  }
}

function renderStatusSnapshot(st) {
  lastStatusSnapshot = st;
  const badge = document.getElementById('state-badge');
  if (st.is_night_mode) {
    badge.textContent = t('badge_night');
    badge.className = 'badge night';
    document.getElementById('btn-wake-sleep').textContent = t('btn_wake_now');
  } else if (st.is_paused) {
    badge.textContent = t('badge_paused');
    badge.className = 'badge';
    document.getElementById('btn-wake-sleep').textContent = t('btn_sleep_now');
  } else {
    badge.textContent = t('badge_active');
    badge.className = 'badge active';
    document.getElementById('btn-wake-sleep').textContent = t('btn_sleep_now');
  }

  const modePrefix = st.is_demo_mode ? '✨ Demo: ' : '';
  document.getElementById('stat-album').textContent = t('stat_items_fmt', {
    prefix: modePrefix,
    photos: st.photo_count,
    videos: st.video_count
  });
  document.getElementById('stat-layout').textContent = `${st.landscape_mode} (${st.slide_duration}s)`;
  document.getElementById('stat-night').textContent = st.night_mode_enabled ? `${st.night_start} – ${st.night_end}` : t('stat_night_disabled');
  document.getElementById('stat-display').textContent = `${st.canvas_w}×${st.canvas_h} @ ${st.rotation}°`;
  const hdrVerBadge = document.getElementById('header-version-badge');
  if (hdrVerBadge && st.app_version) {
    hdrVerBadge.textContent = `v${st.app_version} (Build #${st.app_version_code ?? 1})`;
  }
}

function showToast(msg, isError = false) {
  const tEl = document.getElementById('toast');
  tEl.textContent = (isError ? '⚠️ ' : '✅ ') + msg;
  tEl.style.borderColor = isError ? '#ef4444' : '#10b981';
  tEl.style.display = 'block';
  clearTimeout(window._toastTimer);
  window._toastTimer = setTimeout(() => { tEl.style.display = 'none'; }, 4500);
}

function showInlineBanner(elId, msg, isError = false) {
  const el = document.getElementById(elId);
  if (!el) return;
  el.textContent = msg;
  el.className = isError ? 'confirm-banner error' : 'confirm-banner';
  el.style.display = 'block';
  clearTimeout(el._hideTimer);
  el._hideTimer = setTimeout(() => { el.style.display = 'none'; }, 7000);
}

async function fetchStatus(populateForm = false) {
  try {
    const res = await fetch('/api/status');
    const data = await res.json();
    if (data.language && !localStorage.getItem('frame_lang_user_set')) {
      setLanguage(data.language);
    }
    if (!data.authenticated) {
      document.getElementById('login-view').classList.remove('hidden');
      document.getElementById('app-view').classList.add('hidden');
      document.getElementById('default-pw-hint').classList.toggle('hidden', !data.is_default_password);
      return;
    }
    document.getElementById('login-view').classList.add('hidden');
    document.getElementById('app-view').classList.remove('hidden');
    document.getElementById('default-pw-banner').classList.toggle('hidden', !data.is_default_password);

    renderStatusSnapshot(data.status);
    document.getElementById('log-box').textContent = data.log_tail || '(No log entries)';

    if (populateForm || !formPopulated) {
      const cfg = data.settings;
      if (cfg.language) {
        setLanguage(cfg.language);
      }
      document.getElementById('cfg-album').value = (cfg.album && cfg.album !== 'demo') ? cfg.album : '';
      document.getElementById('cfg-language').value = cfg.language || currentLang || 'en';
      document.getElementById('cfg-layout').value = cfg.landscape_mode || 'collage';
      document.getElementById('cfg-duration').value = cfg.duration ?? 15;
      document.getElementById('cfg-transition').value = cfg.transition || 'none';
      document.getElementById('cfg-videos').value = cfg.videos || 'play';
      document.getElementById('cfg-max-vid').value = cfg.max_video_seconds ?? 20;
      document.getElementById('cfg-fit').value = cfg.fit || 'contain-blur';
      document.getElementById('cfg-rotate').value = String(cfg.rotate ?? 0);
      document.getElementById('cfg-refresh').value = cfg.refresh_hours ?? 12;
      document.getElementById('cfg-shuffle').checked = Boolean(cfg.shuffle);
      document.getElementById('cfg-night-enabled').checked = Boolean(cfg.night_mode_enabled);
      document.getElementById('cfg-night-start').value = cfg.night_start || '22:00';
      document.getElementById('cfg-night-end').value = cfg.night_end || '06:00';
      formPopulated = true;
    }
  } catch (err) {
    console.error(err);
  }
}

async function handleLogin(e) {
  e.preventDefault();
  const password = document.getElementById('login-password').value;
  const res = await fetch('/api/login', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({password})
  });
  const data = await res.json();
  if (res.ok && data.ok) {
    document.getElementById('login-password').value = '';
    formPopulated = false;
    fetchStatus(true);
    showToast(t('msg_signed_in'));
  } else {
    showToast(t('msg_invalid_pw'), true);
  }
}

async function handleLogout() {
  await fetch('/api/logout', {method: 'POST'});
  formPopulated = false;
  fetchStatus(false);
}

async function sendAction(action) {
  const bodyObj = {action};
  if (action === 'resync') {
    const albumEl = document.getElementById('cfg-album');
    if (albumEl && albumEl.value.trim()) {
      bodyObj.album = albumEl.value.trim();
    }
  }
  const res = await fetch('/api/action', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(bodyObj)
  });
  const data = await res.json();
  if (res.ok && data.ok) {
    showToast(data.message || 'Action executed');
    setTimeout(() => fetchStatus(false), 600);
  } else {
    showToast(data.error || 'Action failed', true);
  }
}

function playDemoShowcase() {
  const albumEl = document.getElementById('cfg-album');
  if (albumEl) albumEl.value = '';
  sendAction('demo');
}

function confirmSystemAction(action) {
  const promptKey = action === 'reboot' ? 'confirm_reboot' : 'confirm_restart';
  if (confirm(t(promptKey))) {
    sendAction(action);
  }
}

async function saveSettings(e) {
  e.preventDefault();
  const btn = document.getElementById('btn-save-settings');
  if (btn) {
    btn.disabled = true;
    btn.textContent = t('btn_saving_settings');
  }
  const selectedLang = document.getElementById('cfg-language').value || currentLang || 'en';
  localStorage.setItem('frame_lang_user_set', '1');
  setLanguage(selectedLang);

  const payload = {
    album: document.getElementById('cfg-album').value.trim(),
    language: selectedLang,
    landscape_mode: document.getElementById('cfg-layout').value,
    duration: parseFloat(document.getElementById('cfg-duration').value),
    transition: document.getElementById('cfg-transition').value,
    videos: document.getElementById('cfg-videos').value,
    max_video_seconds: parseFloat(document.getElementById('cfg-max-vid').value),
    fit: document.getElementById('cfg-fit').value,
    rotate: parseInt(document.getElementById('cfg-rotate').value, 10),
    refresh_hours: parseFloat(document.getElementById('cfg-refresh').value),
    shuffle: document.getElementById('cfg-shuffle').checked,
    night_mode_enabled: document.getElementById('cfg-night-enabled').checked,
    night_start: document.getElementById('cfg-night-start').value,
    night_end: document.getElementById('cfg-night-end').value,
  };
  try {
    const res = await fetch('/api/settings', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (res.ok && data.ok) {
      const nowStr = new Date().toLocaleTimeString();
      const albumDesc = payload.album
        ? t('msg_source_album', {album: payload.album})
        : t('msg_source_demo');
      const confirmMsg = t('msg_settings_saved_banner', {
        time: nowStr,
        source: albumDesc,
        layout: payload.landscape_mode,
        duration: payload.duration
      });
      showToast(t('btn_saved_settings'));
      showInlineBanner('settings-confirm-banner', confirmMsg, false);
      if (btn) {
        btn.textContent = t('btn_saved_settings');
        setTimeout(() => { btn.textContent = t('btn_save_settings'); btn.disabled = false; }, 2500);
      }
      setTimeout(() => fetchStatus(true), 600);
    } else {
      const errMsg = data.error || 'Failed to save settings';
      showToast(errMsg, true);
      showInlineBanner('settings-confirm-banner', '⚠️ ' + errMsg, true);
      if (btn) { btn.textContent = t('btn_save_settings'); btn.disabled = false; }
    }
  } catch (err) {
    showToast('Network error while saving settings', true);
    showInlineBanner('settings-confirm-banner', '⚠️ Network error while saving settings', true);
    if (btn) { btn.textContent = t('btn_save_settings'); btn.disabled = false; }
  }
}

async function changePassword(e) {
  e.preventDefault();
  const current_password = document.getElementById('pw-current').value;
  const new_password = document.getElementById('pw-new').value;
  const confirm_password = document.getElementById('pw-confirm').value;
  if (new_password !== confirm_password) {
    showToast(t('msg_pw_mismatch'), true);
    showInlineBanner('pw-confirm-banner', '⚠️ ' + t('msg_pw_mismatch'), true);
    return;
  }
  const res = await fetch('/api/password', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({current_password, new_password})
  });
  const data = await res.json();
  if (res.ok && data.ok) {
    document.getElementById('pw-form').reset();
    showToast(t('msg_pw_updated'));
    showInlineBanner('pw-confirm-banner', t('msg_pw_updated'), false);
    fetchStatus(false);
  } else {
    const errMsg = data.error || 'Password update failed';
    showToast(errMsg, true);
    showInlineBanner('pw-confirm-banner', '⚠️ ' + errMsg, true);
  }
}

setLanguage(currentLang);
fetchStatus(true);
setInterval(() => fetchStatus(false), 5000);
</script>
</body>
</html>
"""


_SELF_SIGNED_TLS_PEM = """-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEArNweH+Jh6YOYndjp70j5MsH/uLVYE9QvrD01m7XWAR5oZw5z
oU81klEnMZtRQet/oZGi7MuqjUk490HmhlGhYf6psKIUbFFV/jdXL0Y4MJNX43DZ
E9maD/nL7pKLKxjANTgVSEbevRfFCRBNPOWmRDDzLMljfp5ucWelqSQzsg+ra6Pr
IBfrlSwY8eAf/1qwTUpOAW4DEQYZx/Y2mwHgQqxBg1m765gcd2CYEtuL71fExhTu
rFOey/FzJAv3iVPpzs+NBl8y9YimgJUjzp8FQleSWWJcLA/CKaLpEXNT1Tey4Tbq
4U7g+E/1DsaIRbExLxqcMvL+PH1FnVR5tewzlQIDAQABAoIBAAiBQgguksOzAljN
2eC4le495k+XuJAtMfVHA7K4FSHz9Y4Qg3bt10RuMr/5lNtp81snN71v3eHatu9P
ckCsloSkS4QRzXBw5gq+fU8ppM0bCsl5fWzBGZfsofCIjElgC+Dyc/Wc6wuhtkOW
Zj53TCFxyR0sgXUy5Vs2DswOKG77yqGZxh1BDvk9HIDD7mCrpEN2V7oQOCaGYAaO
85VICQZmLCKbEbMOYH6sasN2vNIoY12n4E72SKsz3R6fe/s+b8kF7foBaFkuPPPg
LTpiHyLoI+dKyDvC8tvUvbOEqQbmIcc7AOHkcfbQ1cyP76oFpIq4Iuvn85QGGr8N
DzpYDRcCgYEA4jGj7RiCPqvmS5ZNrAy+o3t3mvrZ+8ZU99AQWT+C4NiifK31krTF
c5PKqQkViwprdoIhnM0+xWiDPCDgXKjkeEDk/oiB50sPCvI0gHjWelAOkVkrmUBx
EfOrY7aeVMvfWAKWs4l0UKBmcH6GmV/ZiOmltoQ1u3V9XJRvopC3dq8CgYEAw6NS
Obr1IF0PM20VUc0OOKLGVr1iCL26p95V4DJw4ZuQLVTMqBKnWOW8e89XOpS4a8u7
RZQWld3Pmn3nAHsEstFh7Dggq+HYHQklrl2ntmlPzpeZmkSkyr7KlRf0gub0qf6t
i4v9CAvKgY4CEZhzTPEMchWP7d6QipXevqkYCvsCgYBA+5nop73jW2dSF+BAqXir
ZvE5qQ+u8Y+/khrgSQrEpfgocaG3gA+p4K+8WEvEIj41UbVuYtjBxgDN6XQLqsnY
cmxXY8hONpYfwu2qawzTHuTQhRmtcYIKckKu5uwd2GRV2IiLE9ZCw5Iqh28L6SmJ
1/YvtVPqgxp82ngn8AOFKQKBgQCGK7IuqYuHYIsWR8+BRFJmgSvpPQTI3eXvTsoy
ciV8dIXSsP+SnQhsLVKwzqgSIwC/3P9YXpGGwpKob4RHjhbQ1EyGBpBRkRf4Adf3
13832M3E76+GX2FtMqAY1E76n45YL7OxqpYkOIHvjAbbLmSU9hQFAcit6B/VC6ex
tqIolQKBgQC4tRyxkEm+s7j9/4qpx4ctNyEi/16VvcrjYkYizuYsEh8PBHBxsl06
KJFx1NrdegjTfcjEI3XtCR7VcP61pHSh5RefU4KE4UZ+OeKJWPpO3Qzh0EI4nNYg
9OYJAkgm4WezskU91Q1Y70xsWUfnwr+g93YZHqzv4ZAgke6JaMOVYA==
-----END RSA PRIVATE KEY-----
-----BEGIN CERTIFICATE-----
MIIDTDCCAjSgAwIBAgIJAJ4F2erM+qmDMA0GCSqGSIb3DQEBCwUAMD0xCzAJBgNV
BAYTAlVTMRMwEQYDVQQKEwpQaG90b0ZyYW1lMRkwFwYDVQQDExBwaG90b2ZyYW1l
LmxvY2FsMB4XDTI2MTExMTAyMTAxOFoXDTM2MTEwODAyMTAxOFowPTELMAkGA1UE
BhMCVVMxEzARBgNVBAoTClBob3RvRnJhbWUxGTAXBgNVBAMTEHBob3RvZnJhbWUu
bG9jYWwwggEiMA0GCSqGSIb3DQEBAQUAA4IBDwAwggEKAoIBAQCs3B4f4mHpg5id
2OnvSPkywf+4tVgT1C+sPTWbtdYBHmhnDnOhTzWSUScxm1FB63+hkaLsy6qNSTj3
QeaGUaFh/qmwohRsUVX+N1cvRjgwk1fjcNkT2ZoP+cvukosrGMA1OBVIRt69F8UJ
EE085aZEMPMsyWN+nm5xZ6WpJDOyD6tro+sgF+uVLBjx4B//WrBNSk4BbgMRBhnH
9jabAeBCrEGDWbvrmBx3YJgS24vvV8TGFO6sU57L8XMkC/eJU+nOz40GXzL1iKaA
lSPOnwVCV5JZYlwsD8IpoukRc1PVN7LhNurhTuD4T/UOxohFsTEvGpwy8v48fUWd
VHm17DOVAgMBAAGjTzBNMB0GA1UdDgQWBBTfKpv/CIt5XxufvyDzJMaE9arXaTAs
BgNVHREEJTAjghBwaG90b2ZyYW1lLmxvY2Fsgglsb2NhbGhvc3SHBH8AAAEwDQYJ
KoZIhvcNAQELBQADggEBABJyV6FIOID7NhI72LyVmuxUtjGytOcob9GdY08DyBSK
irCgdZnV609d9Kv/pfP/Br6g1koeI9bG7CLHtPvjzi0xeGDsFyx1uKexTZXJeb+P
EVTn+i9AOWddQ1o5O5bNlEylO0jVBZ1r0JBqMkEbifqxHXNfdJhqgSf6nbGnmfni
5BK5QIV+BoOkJ/xI2dBZwbIPtqyv8gof4X4mhzsG7odqzbTUFckhbQpm/7FGLNn4
9gE6LOBvSw30+TEZnSHry/j5/cl5gRHq84Mfzam0HfR2KHO55uYCNP7LklGDKFfm
cZzxaR6uBnlMiLbqYlH9mXmdOHqonxDHzAua8xbbLr8=
-----END CERTIFICATE-----
"""


def create_portal_ssl_context() -> ssl.SSLContext:
    """
    Creates an `ssl.SSLContext` configured for the HTTPS Web Control Portal
    using the bundled self-signed TLS certificate & key in memory (via `/dev/shm`
    when available, unlinked immediately after loading into OpenSSL).
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    shm_dir = "/dev/shm" if os.path.isdir("/dev/shm") and os.access("/dev/shm", os.W_OK) else None
    fd, tmp_pem = tempfile.mkstemp(prefix=".portal_tls_", suffix=".pem", dir=shm_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(_SELF_SIGNED_TLS_PEM)
        ctx.load_cert_chain(certfile=tmp_pem)
    finally:
        try:
            os.unlink(tmp_pem)
        except Exception:
            pass
    return ctx


class _ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: Any,
        RequestHandlerClass: Any,
        bind_and_activate: bool = True,
        ssl_context: Optional[ssl.SSLContext] = None,
    ):
        self.ssl_context = ssl_context
        super().__init__(server_address, RequestHandlerClass, bind_and_activate=bind_and_activate)

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        if self.ssl_context is not None:
            try:
                request.settimeout(5.0)
                first_byte = request.recv(1, socket.MSG_PEEK)
                request.settimeout(None)
                if first_byte == b"\x16":
                    request = self.ssl_context.wrap_socket(request, server_side=True)
            except Exception:
                self.shutdown_request(request)
                return
        super().process_request_thread(request, client_address)


def create_portal_handler(
    app: "SlideshowApp",
    config_store: FrameConfigStore,
    login_fail_delay: float = 1.0,
) -> Type[http.server.BaseHTTPRequestHandler]:
    """
    Factory that returns a `BaseHTTPRequestHandler` class bound to `app` and `config_store`.
    Can be passed directly to `_ThreadingHTTPServer` in production or unit tests.
    """

    class PortalHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any):
            # Silence access logs completely so zero disk/RAM log spam occurs
            pass

        def _get_session_token(self) -> str:
            cookie_hdr = self.headers.get("Cookie", "")
            if not cookie_hdr:
                return ""
            c = http.cookies.SimpleCookie()
            try:
                c.load(cookie_hdr)
                if "frame_session" in c:
                    return c["frame_session"].value
            except Exception:
                pass
            return ""

        def _is_authenticated(self) -> bool:
            return config_store.verify_session_token(self._get_session_token())

        def _send_json(self, status_code: int, payload: Dict[str, Any], cookie_header: Optional[str] = None):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if cookie_header:
                self.send_header("Set-Cookie", cookie_header)
            self.end_headers()
            self.wfile.write(body)

        def _read_json_body(self) -> Dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0 or length > 65536:
                return {}
            raw = self.rfile.read(length)
            try:
                return json.loads(raw.decode("utf-8"))
            except Exception:
                return {}

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path in ("/", "/index.html"):
                body = WEB_PORTAL_HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return

            if parsed.path == "/api/status":
                authed = self._is_authenticated()
                cur_settings = app.get_current_settings_dict()
                if not authed:
                    self._send_json(200, {
                        "authenticated": False,
                        "is_default_password": config_store.is_default_password(),
                        "language": cur_settings.get("language", "en"),
                    })
                    return
                self._send_json(200, {
                    "authenticated": True,
                    "is_default_password": config_store.is_default_password(),
                    "language": cur_settings.get("language", "en"),
                    "status": app.get_status_snapshot(),
                    "settings": cur_settings,
                    "log_tail": app.get_ram_log_tail(35),
                })
                return

            self._send_json(404, {"error": "Not found"})

        def do_POST(self):
            parsed = urllib.parse.urlparse(self.path)
            data = self._read_json_body()

            if parsed.path == "/api/login":
                pw = str(data.get("password", ""))
                if config_store.verify_password(pw):
                    token = config_store.create_session_token()
                    cookie = f"frame_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000"
                    self._send_json(200, {"ok": True}, cookie_header=cookie)
                else:
                    if login_fail_delay > 0:
                        time.sleep(login_fail_delay)
                    self._send_json(401, {"ok": False, "error": "Invalid password"})
                return

            if parsed.path == "/api/logout":
                cookie = "frame_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"
                self._send_json(200, {"ok": True}, cookie_header=cookie)
                return

            if not self._is_authenticated():
                self._send_json(401, {"ok": False, "error": "Authentication required"})
                return

            if parsed.path == "/api/password":
                cur_pw = str(data.get("current_password", ""))
                new_pw = str(data.get("new_password", ""))
                if not config_store.verify_password(cur_pw):
                    self._send_json(400, {"ok": False, "error": "Current password is incorrect"})
                    return
                if len(new_pw) < 4:
                    self._send_json(400, {"ok": False, "error": "New password must be at least 4 characters"})
                    return
                config_store.change_password(new_pw)
                token = config_store.create_session_token()
                cookie = f"frame_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000"
                logger.info("🔐 Web portal password updated by user.")
                self._send_json(200, {"ok": True}, cookie_header=cookie)
                return

            if parsed.path == "/api/settings":
                msg = app.update_and_persist_settings(data)
                self._send_json(200, {"ok": True, "message": msg})
                return

            if parsed.path == "/api/action":
                action = str(data.get("action", ""))
                album_arg = str(data.get("album", "")).strip()
                msg = app.queue_web_action(action, album_arg=album_arg)
                self._send_json(200, {"ok": True, "message": msg})
                return

            self._send_json(404, {"error": "Unknown endpoint"})

    return PortalHandler


def start_web_config_server(app: "SlideshowApp", config_store: FrameConfigStore, port: int = 8080):
    if port <= 0:
        return

    handler_cls = create_portal_handler(app, config_store)
    start_mdns_responder(app._get_local_ip, hostname=MDNS_HOSTNAME)

    def _server_worker():
        try:
            ssl_ctx = create_portal_ssl_context()
            srv = _ThreadingHTTPServer(("0.0.0.0", port), handler_cls, ssl_context=ssl_ctx)
            logger.info(
                "🌐 Password-protected Web Control Portal listening on http://%s:%d (http://0.0.0.0:%d)",
                MDNS_HOSTNAME,
                port,
                port,
            )
            srv.serve_forever()
        except Exception as e:
            logger.error("Failed to start Web Control Portal on port %d: %s", port, e)

    threading.Thread(target=_server_worker, name="WebConfigServerThread", daemon=True).start()
