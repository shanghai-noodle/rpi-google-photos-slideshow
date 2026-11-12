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
from .fleet import FleetManager, verify_peer_request
from .hardware import logger
from .mdns import MDNS_HOSTNAME, get_active_mdns_hostname, start_mdns_responder

if TYPE_CHECKING:
    from .app import SlideshowApp

SUPPORTED_LANGUAGES = ("en", "zh", "ja", "ko", "es", "de")

WEB_PORTAL_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Photo Frame Control Portal</title>
<script>
(function() {
  try {
    var saved = localStorage.getItem('frame_theme');
    var theme = (saved === 'light' || saved === 'dark') ? saved : 'dark';
    document.documentElement.setAttribute('data-theme', theme);
  } catch (e) {
    document.documentElement.setAttribute('data-theme', 'dark');
  }
})();
</script>
<style>
  :root {
    color-scheme: dark;
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
  :root[data-theme="light"] {
    color-scheme: light;
    --bg: #f1f5f9;
    --card: #ffffff;
    --card-border: #cbd5e1;
    --text: #0f172a;
    --muted: #64748b;
    --accent: #0284c7;
    --accent-hover: #0369a1;
    --success: #059669;
    --warn: #d97706;
    --danger: #dc2626;
    --input-bg: #f8fafc;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.5;
    padding: 20px 14px 48px;
    transition: background-color 0.18s ease, color 0.18s ease;
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
  .badge.offline { background: rgba(239, 68, 68, 0.16); color: #fca5a5; border-color: rgba(239, 68, 68, 0.42); }
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
  .fleet-grid {
    display: grid; grid-template-columns: repeat(auto-fill, minmax(215px, 1fr));
    gap: 10px; margin-top: 10px;
  }
  .fleet-pill {
    background: var(--input-bg); border: 1.5px solid var(--card-border);
    border-radius: 11px; padding: 10px 12px; cursor: pointer;
    transition: all 0.15s ease; position: relative; text-align: left;
    display: flex; flex-direction: column; gap: 3px;
  }
  .fleet-pill:hover { border-color: #475569; background: #152036; }
  .fleet-pill.selected {
    border-color: var(--accent); background: rgba(56, 189, 248, 0.12);
    box-shadow: 0 0 0 1px rgba(56, 189, 248, 0.35);
  }
  .fleet-pill-top {
    display: flex; align-items: center; justify-content: space-between;
    font-weight: 700; font-size: 0.9rem; color: #f8fafc; gap: 6px;
  }
  .fleet-pill-meta { font-size: 0.75rem; color: #93c5fd; font-weight: 500; }
  .fleet-pill-sub { font-size: 0.74rem; color: var(--muted); }
  .fleet-remove-btn {
    background: transparent; border: none; color: var(--muted);
    padding: 0 4px; font-size: 0.95rem; line-height: 1; cursor: pointer;
  }
  .fleet-remove-btn:hover { color: var(--danger); background: transparent; }
  .target-banner {
    margin-top: 10px; padding: 9px 12px; border-radius: 9px;
    font-size: 0.83rem; font-weight: 600;
    background: rgba(56, 189, 248, 0.1); border: 1px solid rgba(56, 189, 248, 0.3);
    color: #bae6fd;
  }
  .saved-albums-grid {
    display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 8px; margin-top: 4px;
  }
  .saved-album-pill {
    display: flex; align-items: center; justify-content: space-between; gap: 10px;
    background: #0f172a; border: 1px solid #334155; border-radius: 10px;
    padding: 9px 12px; transition: all 0.15s; cursor: pointer;
  }
  .saved-album-pill:hover { border-color: #60a5fa; background: #152036; }
  .saved-album-pill.active {
    border-color: var(--success); background: rgba(16, 185, 129, 0.10);
    box-shadow: 0 0 0 1px rgba(16, 185, 129, 0.3);
  }
  .saved-album-info { display: flex; flex-direction: column; gap: 2px; min-width: 0; flex: 1; }
  .saved-album-title {
    font-weight: 700; font-size: 0.88rem; color: #f8fafc;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .saved-album-meta {
    font-size: 0.74rem; color: #94a3b8;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .saved-album-actions { display: flex; align-items: center; gap: 6px; flex-shrink: 0; }
  .saved-use-btn {
    padding: 5px 10px; font-size: 0.76rem; border-radius: 7px;
    background: #1e293b; border: 1px solid #475569; color: #bae6fd;
  }
  .saved-use-btn:hover { background: var(--accent); color: #0f172a; border-color: var(--accent); }
  .modal-backdrop {
    position: fixed; inset: 0; z-index: 9500;
    background: rgba(5, 8, 16, 0.78); backdrop-filter: blur(3px);
    display: flex; align-items: center; justify-content: center; padding: 18px;
  }
  .modal-card {
    width: 100%; max-width: 440px; background: var(--card);
    border: 1.5px solid rgba(239, 68, 68, 0.45); border-radius: 16px;
    padding: 22px; box-shadow: 0 20px 50px rgba(0, 0, 0, 0.75);
  }
  .modal-card h3 {
    font-size: 1.08rem; font-weight: 700; color: #f8fafc;
    margin-bottom: 10px; display: flex; align-items: center; gap: 8px;
  }
  .modal-card p {
    font-size: 0.9rem; color: #cbd5e1; line-height: 1.5; margin-bottom: 18px;
  }
  .modal-actions {
    display: flex; justify-content: flex-end; gap: 10px; flex-wrap: wrap;
  }
  .badge.host-badge { background: #0f172a; color: #38bdf8; border-color: #1e293b; }
  .badge.ver-badge { background: #1e293b; color: #93c5fd; border-color: #334155; }
  .theme-toggle-btn { padding: 5px 10px; font-size: 0.8rem; border-radius: 8px; white-space: nowrap; }
  .webframe-url-pill {
    color: #60a5fa; font-size: 0.85rem; font-weight: 600; text-decoration: none;
    background: #0f172a; padding: 8px 13px; border-radius: 8px; border: 1px solid #334155;
    display: inline-block; word-break: break-all;
  }
  :root[data-theme="light"] .badge { background: #e2e8f0; color: #334155; border-color: #cbd5e1; }
  :root[data-theme="light"] .badge.host-badge { background: #e0f2fe; color: #0369a1; border-color: #bae6fd; }
  :root[data-theme="light"] .badge.ver-badge { background: #eef2ff; color: #3730a3; border-color: #c7d2fe; }
  :root[data-theme="light"] .badge.active { background: rgba(16, 185, 129, 0.14); color: #047857; border-color: rgba(16, 185, 129, 0.4); }
  :root[data-theme="light"] .badge.night { background: rgba(245, 158, 11, 0.16); color: #b45309; border-color: rgba(245, 158, 11, 0.45); }
  :root[data-theme="light"] .badge.offline { background: rgba(239, 68, 68, 0.12); color: #b91c1c; border-color: rgba(239, 68, 68, 0.4); }
  :root[data-theme="light"] .card { box-shadow: 0 4px 16px rgba(15, 23, 42, 0.07); }
  :root[data-theme="light"] .card h2 { color: #0f172a; }
  :root[data-theme="light"] button, :root[data-theme="light"] .btn { background: #f1f5f9; color: #0f172a; border-color: #cbd5e1; }
  :root[data-theme="light"] button:hover, :root[data-theme="light"] .btn:hover { background: #e2e8f0; border-color: #94a3b8; }
  :root[data-theme="light"] button.primary, :root[data-theme="light"] .btn.primary { background: var(--accent); color: #ffffff; border-color: transparent; }
  :root[data-theme="light"] button.primary:hover, :root[data-theme="light"] .btn.primary:hover { background: var(--accent-hover); color: #ffffff; }
  :root[data-theme="light"] button.success { background: rgba(16, 185, 129, 0.14); color: #047857; border-color: rgba(16, 185, 129, 0.42); }
  :root[data-theme="light"] button.success:hover { background: rgba(16, 185, 129, 0.22); border-color: rgba(16, 185, 129, 0.55); }
  :root[data-theme="light"] button.warn { background: rgba(245, 158, 11, 0.16); color: #b45309; border-color: rgba(245, 158, 11, 0.45); }
  :root[data-theme="light"] button.warn:hover { background: rgba(245, 158, 11, 0.25); border-color: rgba(245, 158, 11, 0.6); }
  :root[data-theme="light"] button.danger { background: rgba(239, 68, 68, 0.12); color: #dc2626; border-color: rgba(239, 68, 68, 0.4); }
  :root[data-theme="light"] button.danger:hover { background: rgba(239, 68, 68, 0.2); border-color: rgba(239, 68, 68, 0.55); }
  :root[data-theme="light"] label { color: #334155; }
  :root[data-theme="light"] .toast { background: #ffffff; color: #0f172a; box-shadow: 0 12px 32px rgba(15, 23, 42, 0.18); }
  :root[data-theme="light"] .confirm-banner { background: rgba(16, 185, 129, 0.12); border-color: rgba(16, 185, 129, 0.45); color: #047857; }
  :root[data-theme="light"] .confirm-banner.error { background: rgba(239, 68, 68, 0.12); border-color: rgba(239, 68, 68, 0.45); color: #dc2626; }
  :root[data-theme="light"] pre.log-box { background: #f8fafc; color: #1e3a8a; border-color: #cbd5e1; }
  :root[data-theme="light"] .warn-banner { background: #fffbeb; border-color: #f59e0b; color: #92400e; }
  :root[data-theme="light"] .fleet-pill:hover { border-color: #94a3b8; background: #f1f5f9; }
  :root[data-theme="light"] .fleet-pill.selected { border-color: var(--accent); background: rgba(2, 132, 199, 0.09); box-shadow: 0 0 0 1px rgba(2, 132, 199, 0.28); }
  :root[data-theme="light"] .fleet-pill-top { color: #0f172a; }
  :root[data-theme="light"] .fleet-pill-meta { color: #0369a1; }
  :root[data-theme="light"] .target-banner { background: rgba(2, 132, 199, 0.08); border-color: rgba(2, 132, 199, 0.28); color: #0c4a6e; }
  :root[data-theme="light"] .saved-album-pill { background: #f8fafc; border-color: #cbd5e1; }
  :root[data-theme="light"] .saved-album-pill:hover { border-color: var(--accent); background: #f1f5f9; }
  :root[data-theme="light"] .saved-album-pill.active { border-color: var(--success); background: rgba(16, 185, 129, 0.1); }
  :root[data-theme="light"] .saved-album-title { color: #0f172a; }
  :root[data-theme="light"] .saved-album-meta { color: #64748b; }
  :root[data-theme="light"] .saved-use-btn { background: #e0f2fe; border-color: #7dd3fc; color: #0369a1; }
  :root[data-theme="light"] .saved-use-btn:hover { background: var(--accent); color: #ffffff; border-color: var(--accent); }
  :root[data-theme="light"] .webframe-url-pill { background: #f0f9ff; color: #0369a1; border-color: #bae6fd; }
  :root[data-theme="light"] .modal-backdrop { background: rgba(15, 23, 42, 0.48); }
  :root[data-theme="light"] .modal-card { box-shadow: 0 20px 50px rgba(15, 23, 42, 0.22); }
  :root[data-theme="light"] .modal-card h3 { color: #0f172a; }
  :root[data-theme="light"] .modal-card p { color: #334155; }
</style>
</head>
<body>
<div class="container">
  <!-- LOGIN VIEW -->
  <div id="login-view" class="card hidden">
    <h2 style="gap:8px; flex-wrap:wrap;">
      <span data-i18n="login_title">🔒 Photo Frame Login</span>
      <div style="display:flex; align-items:center; gap:6px;">
        <button type="button" id="login-theme-btn" class="theme-toggle-btn" onclick="toggleTheme()">☀️ Light</button>
        <select id="login-lang-select" onchange="setLanguage(this.value, true, true)" style="width:auto; padding:5px 10px; font-size:0.82rem;">
          <option value="en">🌐 English</option>
          <option value="zh">🌐 中文</option>
          <option value="ja">🌐 日本語</option>
          <option value="ko">🌐 한국어</option>
          <option value="es">🌐 Español</option>
          <option value="de">🌐 Deutsch</option>
        </select>
      </div>
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
        <button type="button" id="header-theme-btn" class="theme-toggle-btn" onclick="toggleTheme()">☀️ Light</button>
        <select id="header-lang-select" onchange="setLanguage(this.value, true, true)" style="width:auto; padding:5px 10px; font-size:0.8rem;">
          <option value="en">🌐 English</option>
          <option value="zh">🌐 中文</option>
          <option value="ja">🌐 日本語</option>
          <option value="ko">🌐 한국어</option>
          <option value="es">🌐 Español</option>
          <option value="de">🌐 Deutsch</option>
        </select>
        <span id="header-host-badge" class="badge host-badge">🌐 photoframe.local</span>
        <span id="header-version-badge" class="badge ver-badge">v1.0.3</span>
        <span id="state-badge" class="badge">Loading...</span>
        <button onclick="handleLogout()" style="padding:6px 11px; font-size:0.8rem;" data-i18n="btn_signout">Sign Out</button>
      </div>
    </header>

    <div id="default-pw-banner" class="warn-banner hidden" data-i18n-html="default_pw_banner">
      ⚠️ You are currently using the default password (<b>admin</b>). Scroll down to <b>Security & Password</b> to set a custom password.
    </div>

    <!-- MULTI-FRAME LAN FLEET SELECTOR BAR -->
    <div class="card" id="fleet-card">
      <h2>
        <span data-i18n="sec_fleet">🖧 Connected Photo Frames (LAN Fleet)</span>
        <div style="display:flex; gap:8px; flex-wrap:wrap;">
          <button type="button" onclick="scanFleetNodes()" id="btn-scan-fleet" style="padding:5px 10px; font-size:0.78rem;" data-i18n="btn_scan_fleet">🔍 Scan LAN</button>
          <button type="button" onclick="toggleAddPeerRow()" style="padding:5px 10px; font-size:0.78rem;" data-i18n="btn_add_peer">➕ Add IP</button>
        </div>
      </h2>
      <div id="add-peer-row" class="hidden" style="display:flex; gap:8px; margin-bottom:10px; flex-wrap:wrap;">
        <input type="text" id="add-peer-input" style="flex:1; min-width:200px;" data-i18n-placeholder="ph_add_peer" placeholder="192.168.86.x or photoframe-2.local:8080">
        <button type="button" class="primary" onclick="addManualPeer()" data-i18n="btn_connect_peer">Connect Frame</button>
      </div>
      <div id="fleet-nodes-grid" class="fleet-grid"></div>
      <div id="active-target-banner" class="target-banner"></div>
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

    <!-- WEB SLIDESHOW VIEWER (/frame) -->
    <div class="card" id="webframe-card">
      <h2>
        <span data-i18n="sec_webframe">🖼️ Web Slideshow Viewer (/frame)</span>
        <span id="webframe-status-badge" class="badge">⚪ Disabled</span>
      </h2>
      <div style="font-size: 0.84rem; color: var(--muted); margin-bottom: 12px;" data-i18n="webframe_desc">
        View a live browser slideshow on any tablet, phone, or computer on your Wi-Fi without signing in. Shares the synced album with an independent photo order per browser (videos are displayed as still poster photos).
      </div>
      <div class="form-grid" style="align-items: end; margin-bottom: 12px;">
        <div class="form-group">
          <label for="cfg-webframe-profile" data-i18n="lbl_webframe_profile">Picture Quality &amp; Device Profile</label>
          <select id="cfg-webframe-profile" onchange="updateWebFrameProfileLive(this.value)">
            <option value="auto" data-i18n="opt_wf_auto">Auto — Smart Match (1080p HD on Modern Screens, 768p Standard on Older Tablets)</option>
            <option value="hd" data-i18n="opt_wf_hd">1080p HD — Full Resolution, Smooth Crossfade &amp; All 9 Collage Layouts</option>
            <option value="legacy" data-i18n="opt_wf_legacy">768p Standard — Memory-Safe Mode for Older iPads &amp; Tablets (Up to 4-Tile Collages)</option>
          </select>
        </div>
        <div class="form-group" style="display:flex; flex-direction:row; gap:10px; flex-wrap:wrap;">
          <button type="button" id="btn-webframe-toggle" class="primary" onclick="sendAction('web_frame_toggle')" style="flex:1;" data-i18n="btn_wf_turn_on">🟢 Enable Web Viewer (/frame)</button>
          <a id="btn-webframe-open" href="/frame" target="_blank" rel="noopener" class="btn hidden" style="text-decoration:none; flex:1;" data-i18n="btn_wf_open">↗️ Launch Web Viewer (/frame)</a>
        </div>
      </div>
      <div id="webframe-links-box" class="target-banner hidden" style="font-size:0.81rem;"></div>
    </div>

    <!-- SLIDESHOW & ALBUM SETTINGS -->
    <div class="card">
      <h2 data-i18n="sec_settings">🎨 Album, Collage & Schedule Settings</h2>
      <div id="broadcast-settings-note" class="target-banner hidden" style="margin-bottom: 14px;" data-i18n="broadcast_settings_note">
        🌐 Broadcast Mode: Only shared slideshow, collage, video, and schedule settings that can be broadcast to all frames are shown. Select an individual frame above to modify its Album/Folder URL, Saved Links, Hostname, Screen Rotation, or APK Update.
      </div>
      <form id="settings-form" onsubmit="saveSettings(event)">
        <div class="form-grid">
          <div class="form-group full" id="group-album">
            <label for="cfg-album" id="lbl-album-dynamic" data-i18n="lbl_album">Google Photos Shared Album URL (leave blank or 'demo' for Demo Showcase Pack)</label>
            <input type="text" id="cfg-album" oninput="autoExtractBaiduPwd()" data-i18n-placeholder="ph_album" placeholder="https://photos.app.goo.gl/... (or leave blank for Demo Showcase)">
          </div>

          <div class="form-group hidden" id="group-pwd">
            <label for="cfg-pwd" data-i18n="lbl_pwd">Baidu Pan Extract Code (pwd / 提取码)</label>
            <input type="text" id="cfg-pwd" maxlength="16" data-i18n-placeholder="ph_pwd" placeholder="4-char code (e.g. i5pp)">
          </div>

          <div class="form-group full" id="group-saved-albums">
            <label style="display:flex; align-items:center; justify-content:space-between; gap:8px; flex-wrap:wrap;">
              <span data-i18n="lbl_saved_albums">🔐 Saved Connected Albums &amp; Folders (Encrypted Locally)</span>
              <span id="active-album-title-badge" class="badge active hidden" style="font-size:0.75rem;"></span>
            </label>
            <div id="saved-albums-list" class="saved-albums-grid"></div>
          </div>

          <div class="form-group" id="group-device-name">
            <label for="cfg-device-name" data-i18n="lbl_device_name">Device Hostname (.local — auto-numbers on conflict)</label>
            <input type="text" id="cfg-device-name" data-i18n-placeholder="ph_device_name" placeholder="photoframe (or e.g. living-room)">
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

          <div class="form-group" id="group-rotate">
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

    <!-- APP AUTO-UPDATE & MANUAL APK UPLOAD (Shown when managing Android TV frames) -->
    <div class="card hidden" id="card-apk-update">
      <h2>
        <span data-i18n="sec_update">📦 App Auto-Update &amp; APK Installer (Android TV)</span>
        <span id="stat-app-version" class="badge" style="font-size:0.78rem;">v1.0.0</span>
      </h2>
      <div class="form-grid">
        <div class="form-group full">
          <label for="cfg-update-url" data-i18n="lbl_update_url">APK Update Download Link (checked on startup &amp; every 6h)</label>
          <div style="display:flex; gap:10px; flex-wrap:wrap;">
            <input type="text" id="cfg-update-url" style="flex:1; min-width:240px;" placeholder="https://github.com/shanghai-noodle/androidTV-photo-slideshow-releases/releases/latest/download/...">
            <button type="button" class="primary" onclick="checkUpdateNow()" data-i18n="btn_check_update">🔍 Save Link &amp; Check Update Now</button>
          </div>
          <div id="stat-update-status" style="font-size:0.8rem; color:var(--muted); margin-top:4px;">—</div>
        </div>
        <div class="form-group full" style="margin-top:6px;">
          <label for="apk-file-input" data-i18n="lbl_apk_upload">Or Upload &amp; Install APK File Directly from Browser</label>
          <div style="display:flex; gap:10px; align-items:center; flex-wrap:wrap;">
            <input type="file" id="apk-file-input" accept=".apk,application/vnd.android.package-archive" style="flex:1; min-width:220px; padding:7px;">
            <button type="button" id="btn-upload-apk" class="success" onclick="uploadAndInstallApk()" data-i18n="btn_upload_apk">📲 Upload &amp; Install APK</button>
          </div>
        </div>
      </div>
      <div id="update-confirm-banner" class="confirm-banner"></div>
    </div>

    <!-- SYSTEM POWER & RAM LOGS -->
    <div class="card">
      <h2>
        <span data-i18n="sec_system">🛠️ System Control & RAM Log</span>
        <button id="btn-refresh-log" onclick="fetchStatus(false)" style="padding:5px 10px; font-size:0.78rem;" data-i18n="btn_refresh_log">🔄 Refresh Log</button>
      </h2>
      <pre class="log-box" id="log-box">Loading RAM log...</pre>
      <div class="btn-grid" style="margin-top: 14px;">
        <button type="button" id="btn-exit-app" class="danger" onclick="confirmExitFrame()" data-i18n="btn_exit_app">🚪 Exit Photo Frame</button>
      </div>
      <div id="system-confirm-banner" class="confirm-banner"></div>
    </div>
  </div>
</div>

<div id="exit-confirm-modal" class="modal-backdrop hidden" onclick="if (event.target === this) closeExitModal()">
  <div class="modal-card" role="dialog" aria-modal="true" aria-labelledby="exit-modal-title">
    <h3 id="exit-modal-title" data-i18n="btn_exit_app">🚪 Exit Photo Frame</h3>
    <p id="exit-modal-msg"></p>
    <div class="modal-actions">
      <button type="button" id="btn-exit-cancel" onclick="closeExitModal()" data-i18n="btn_cancel">Cancel</button>
      <button type="button" id="btn-exit-confirm" class="danger" onclick="executeConfirmedExit()" data-i18n="btn_exit_app">🚪 Exit Photo Frame</button>
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
    lbl_device_name: "Device Hostname (.local — auto-numbers on conflict)",
    ph_device_name: "photoframe (or e.g. living-room)",
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
    btn_exit_app: "🚪 Exit Photo Frame",
    badge_night: "🌙 Night Mode (Sleeping)",
    badge_paused: "⏸️ Paused",
    badge_active: "🟢 Active (Playing)",
    stat_items_fmt: "{prefix}{photos} items ({videos} videos)",
    stat_night_disabled: "Disabled (24/7 On)",
    msg_signed_in: "Signed in successfully",
    msg_invalid_pw: "Invalid password",
    msg_settings_saved_banner: "✅ Settings saved & applied live at {time}! Active source: {source} • Layout: {layout} ({duration}s)",
    msg_source_album: "Google Photos Album ({album})",
    msg_source_demo: "Built-in Demo Showcase Pack",
    msg_pw_mismatch: "New passwords do not match",
    msg_pw_updated: "✅ Password updated successfully! Your session remains active.",
    confirm_exit_app: "Exit and close the Photo Frame application now? Its Web Control Portal will go offline until the app is started again.",
    confirm_exit_app_all: "Exit and close the Photo Frame application on ALL connected frames now? Their Web Control Portals will go offline until started again.",
    btn_cancel: "Cancel",
    badge_offline: "🔴 Offline (Closed)",
    msg_exited_offline: "🚪 Photo Frame closed. Its Web Control Portal is now offline until the app is started again.",
    sec_fleet: "🖧 Connected Photo Frames (LAN Fleet)",
    btn_scan_fleet: "🔍 Scan LAN",
    btn_add_peer: "➕ Add IP",
    ph_add_peer: "192.168.86.x or photoframe-2.local:8080",
    btn_connect_peer: "Connect Frame",
    fleet_all_frames: "🌐 All Frames ({count})",
    fleet_all_sub: "Smart Broadcast to all online frames",
    fleet_this_frame: "This Frame",
    target_controlling_self: "🎯 Controlling: {host} ({ip} — This Frame)",
    target_controlling_peer: "🎯 Controlling Remote Frame: {host} ({ip}) — Single-Login LAN Trust",
    target_controlling_all: "🌐 Broadcast Mode: Actions & shared settings apply to all {count} online frames simultaneously!",
    lbl_pwd: "Baidu Pan Extract Code (pwd / 提取码)",
    ph_pwd: "4-char code (optional if ?pwd= is in URL)",
    sec_update: "📦 App Auto-Update & APK Installer (Android TV)",
    lbl_update_url: "APK Update Download Link (checked on startup & every 6h)",
    btn_check_update: "🔍 Save Link & Check Update Now",
    lbl_apk_upload: "Or Upload & Install APK File Directly from Browser",
    btn_upload_apk: "📲 Upload & Install APK",
    msg_select_apk: "Please choose an .apk file first",
    sec_webframe: "🖼️ Web Slideshow Viewer (/frame)",
    webframe_desc: "View a live browser slideshow on any tablet, phone, or computer on your Wi-Fi without signing in. Shares the synced album with an independent photo order per browser (videos are displayed as still poster photos).",
    lbl_webframe_profile: "Picture Quality & Device Profile",
    lbl_webframe_url: "Web Viewer Address",
    opt_wf_auto: "Auto — Smart Match (1080p HD on Modern Screens, 768p Standard on Older Tablets)",
    opt_wf_hd: "1080p HD — Full Resolution, Smooth Crossfade & All 9 Collage Layouts",
    opt_wf_legacy: "768p Standard — Memory-Safe Mode for Older iPads & Tablets (Up to 4-Tile Collages)",
    btn_wf_turn_on: "🟢 Enable Web Viewer (/frame)",
    btn_wf_turn_off: "🛑 Disable Web Viewer (/frame)",
    btn_wf_open: "↗️ Launch Web Viewer (/frame)",
    btn_open_webframe: "🖼️ Launch Web Viewer (/frame)",
    lbl_saved_albums: "🔐 Saved Connected Albums & Folders (Encrypted Locally)",
    saved_albums_empty: "No connected links saved yet. Successfully connected Google Photos albums and Baidu Pan folders are automatically encrypted and stored here.",
    btn_use_saved_album: "Switch",
    badge_saved_active: "Active",
    broadcast_settings_note: "🌐 Broadcast Mode: Only shared slideshow, collage, video, and schedule settings that can be broadcast to all frames are shown. Select an individual frame above to modify its Album/Folder URL, Saved Links, Hostname, Screen Rotation, or APK Update.",
    btn_theme_light: "☀️ Light",
    btn_theme_dark: "🌙 Dark",
    title_toggle_theme: "Switch between Light and Dark mode"
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
    lbl_device_name: "局域网主机名 (.local — 冲突时自动编号)",
    ph_device_name: "photoframe（或例如 living-room）",
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
    btn_exit_app: "🚪 退出并关闭相框",
    badge_night: "🌙 夜间休眠中",
    badge_paused: "⏸️ 已暂停",
    badge_active: "🟢 正在播放",
    stat_items_fmt: "{prefix}{photos} 项媒体 ({videos} 个视频)",
    stat_night_disabled: "已禁用（24 小时常亮）",
    msg_signed_in: "登录成功",
    msg_invalid_pw: "密码错误",
    msg_settings_saved_banner: "✅ 设置已于 {time} 保存并实时生效！当前媒体源：{source} • 布局：{layout} ({duration}秒)",
    msg_source_album: "Google 共享相册 ({album})",
    msg_source_demo: "内置精选演示集",
    msg_pw_mismatch: "两次输入的新密码不一致",
    msg_pw_updated: "✅ 密码更新成功！当前登录会话保持有效。",
    confirm_exit_app: "确定要立即退出并关闭相框程序吗？关闭后网页控制中心将离线，需重新启动应用后方可再次访问。",
    confirm_exit_app_all: "确定要立即退出并关闭所有已连接设备上的相框程序吗？关闭后各设备的网页控制中心将离线。",
    btn_cancel: "取消",
    badge_offline: "🔴 已关闭离线",
    msg_exited_offline: "🚪 相框应用已退出关闭。网页控制中心现已离线，重新启动应用后即可恢复连接。",
    sec_fleet: "🖧 局域网已连接相框 (多设备集群管理)",
    btn_scan_fleet: "🔍 扫描局域网",
    btn_add_peer: "➕ 手动添加 IP",
    ph_add_peer: "192.168.86.x 或 photoframe-2.local:8080",
    btn_connect_peer: "连接相框",
    fleet_all_frames: "🌐 全部相框 ({count} 台)",
    fleet_all_sub: "智能广播同步至所有在线相框",
    fleet_this_frame: "当前相框",
    target_controlling_self: "🎯 当前控制：{host} ({ip} — 本机相框)",
    target_controlling_peer: "🎯 正在远程管理：{host} ({ip}) — 免重复登录",
    target_controlling_all: "🌐 全局广播模式：远程控制与通用设置将同时应用至全部 {count} 台在线相框！",
    lbl_pwd: "百度网盘提取码 (pwd)",
    ph_pwd: "4 位提取码（若链接含 ?pwd= 可留空）",
    sec_update: "📦 应用自动更新与 APK 安装 (Android TV)",
    lbl_update_url: "APK 更新下载链接（启动时及每 6 小时自动检测）",
    btn_check_update: "🔍 保存链接并立即检查更新",
    lbl_apk_upload: "或直接从浏览器上传并安装本地 .apk 安装包",
    btn_upload_apk: "📲 上传并安装 APK",
    msg_select_apk: "请先选择一个 .apk 安装包文件",
    sec_webframe: "🖼️ 网页端相框浏览器 (/frame)",
    webframe_desc: "在同一局域网内的平板、手机或电脑浏览器中免登录直接观看实时幻灯片。共享当前相册，每台浏览器拥有独立的照片播放顺序（视频在网页端以高清静态封面展示）。",
    lbl_webframe_profile: "画质与设备兼容模式",
    lbl_webframe_url: "网页相框访问地址",
    opt_wf_auto: "自动匹配 — 智能识别（现代屏幕 1080p 高清，老款平板 768p 标准）",
    opt_wf_hd: "1080p 高清 — 全高清画质、平滑渐变过渡与全部 9 种拼图布局",
    opt_wf_legacy: "768p 标准 — 老款 iPad 与平板省内存兼容模式（最多 4 宫格拼图）",
    btn_wf_turn_on: "🟢 启用网页端相框 (/frame)",
    btn_wf_turn_off: "🛑 关闭网页端相框 (/frame)",
    btn_wf_open: "↗️ 打开网页端相框 (/frame)",
    btn_open_webframe: "🖼️ 打开网页端相框 (/frame)",
    lbl_saved_albums: "🔐 已保存的历史连接相册与网盘目录（本地加密存储）",
    saved_albums_empty: "暂无已保存的历史链接。成功连接并加载媒体的 Google 相册或百度网盘分享链接将自动加密保存在此处。",
    btn_use_saved_album: "切换",
    badge_saved_active: "当前使用",
    broadcast_settings_note: "🌐 群控广播模式：当前仅显示可同步广播至所有相框的通用播放、拼图、视频与夜间定时设置。如需修改相册/网盘链接、已保存记录、主机名、屏幕旋转或 APK 更新，请在上方选择单个相框。",
    btn_theme_light: "☀️ 浅色",
    btn_theme_dark: "🌙 深色",
    title_toggle_theme: "切换浅色 / 深色外观模式"
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
    lbl_device_name: "デバイスホスト名 (.local — 重複時は自動採番)",
    ph_device_name: "photoframe（例: living-room）",
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
    btn_exit_app: "🚪 フォトフレームを終了",
    badge_night: "🌙 ナイトモード（スリープ中）",
    badge_paused: "⏸️ 一時停止中",
    badge_active: "🟢 再生中 (Active)",
    stat_items_fmt: "{prefix}{photos} 件 ({videos} 動画)",
    stat_night_disabled: "無効（24時間オン）",
    msg_signed_in: "ログインしました",
    msg_invalid_pw: "パスワードが正しくありません",
    msg_settings_saved_banner: "✅ {time} に設定を保存・即時適用しました！ソース: {source} • レイアウト: {layout} ({duration}秒)",
    msg_source_album: "Google フォトアルバム ({album})",
    msg_source_demo: "内蔵デモショーケースパック",
    msg_pw_mismatch: "新しいパスワードが一致しません",
    msg_pw_updated: "✅ パスワードを更新しました！セッションは維持されます。",
    confirm_exit_app: "フォトフレームアプリを今すぐ終了して閉じますか？アプリを再起動するまでWeb管理画面はオフラインになります。",
    confirm_exit_app_all: "接続されているすべてのフレームでフォトフレームアプリを終了しますか？再起動するまでWeb管理画面はオフラインになります。",
    btn_cancel: "キャンセル",
    badge_offline: "🔴 オフライン（終了済）",
    msg_exited_offline: "🚪 フォトフレームを終了しました。アプリを再起動するまでWeb管理画面はオフラインになります。",
    sec_fleet: "🖧 接続済みフォトフレーム (LAN一括管理)",
    btn_scan_fleet: "🔍 LANスキャン",
    btn_add_peer: "➕ IP追加",
    ph_add_peer: "192.168.86.x または photoframe-2.local:8080",
    btn_connect_peer: "接続",
    fleet_all_frames: "🌐 すべてのフレーム ({count})",
    fleet_all_sub: "オンラインの全フレームに一括適用",
    fleet_this_frame: "このフレーム",
    target_controlling_self: "🎯 操作中: {host} ({ip} — このフレーム)",
    target_controlling_peer: "🎯 リモート操作中: {host} ({ip}) — 再ログイン不要",
    target_controlling_all: "🌐 一括配信モード: 操作と共通設定が全 {count} 台のフレームに同時適用されます！",
    lbl_pwd: "Baidu Pan 抽出コード (pwd)",
    ph_pwd: "4文字のコード（URLに ?pwd= がある場合は省略可）",
    sec_update: "📦 アプリ自動更新 & APK インストーラー (Android TV)",
    lbl_update_url: "APK 更新ダウンロードリンク",
    btn_check_update: "🔍 リンクを保存して今すぐ更新確認",
    lbl_apk_upload: "またはブラウザから .apk ファイルを直接アップロードしてインストール",
    btn_upload_apk: "📲 APK をアップロードしてインストール",
    msg_select_apk: "先に .apk ファイルを選択してください",
    sec_webframe: "🖼️ Web スライドショービューア (/frame)",
    webframe_desc: "同じ Wi-Fi 上のタブレット・スマホ・PC のブラウザでログイン不要でスライドショーを表示します。ブラウザごとに独立した再生順でアルバムを共有します（動画は静止画ポスターとして表示）。",
    lbl_webframe_profile: "画質・デバイスプロファイル",
    lbl_webframe_url: "Web ビューアのアドレス",
    opt_wf_auto: "自動 — スマートマッチ（最新画面は 1080p HD、旧型タブレットは 768p 標準）",
    opt_wf_hd: "1080p HD — フル解像度・クロスフェード・全9種コラージュ",
    opt_wf_legacy: "768p 標準 — 旧型 iPad・タブレット向け省メモリモード（最大4分割）",
    btn_wf_turn_on: "🟢 Web ビューアを有効化 (/frame)",
    btn_wf_turn_off: "🛑 Web ビューアを無効化 (/frame)",
    btn_wf_open: "↗️ Web ビューアを開く (/frame)",
    btn_open_webframe: "🖼️ Web ビューアを開く (/frame)",
    lbl_saved_albums: "🔐 保存済みの接続アルバム・フォルダ（ローカル暗号化保存）",
    saved_albums_empty: "保存されたリンクはまだありません。接続に成功した Google フォトアルバムや百度網盤フォルダは自動的に暗号化されてここに保存されます。",
    btn_use_saved_album: "切替",
    badge_saved_active: "使用中",
    broadcast_settings_note: "🌐 一括配信モード：すべてのフレームに一括適用できる共通設定のみ表示しています。アルバムURL、保存済みリンク、ホスト名、画面回転、APK更新を変更するには、上で個別のフレームを選択してください。",
    btn_theme_light: "☀️ ライト",
    btn_theme_dark: "🌙 ダーク",
    title_toggle_theme: "ライトモード / ダークモードを切り替え"
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
    lbl_device_name: "기기 호스트 이름 (.local — 충돌 시 자동 번호 부여)",
    ph_device_name: "photoframe (예: living-room)",
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
    btn_exit_app: "🚪 포토 프레임 종료",
    badge_night: "🌙 야간 모드 (절전 중)",
    badge_paused: "⏸️ 일시정지됨",
    badge_active: "🟢 재생 중 (Active)",
    stat_items_fmt: "{prefix}{photos}개 항목 (동영상 {videos}개)",
    stat_night_disabled: "비활성화됨 (24시간 켜짐)",
    msg_signed_in: "로그인되었습니다",
    msg_invalid_pw: "비밀번호가 올바르지 않습니다",
    msg_settings_saved_banner: "✅ {time}에 설정이 저장 및 실시간 적용되었습니다! 활성 소스: {source} • 레이아웃: {layout} ({duration}초)",
    msg_source_album: "Google 포토 앨범 ({album})",
    msg_source_demo: "내장 데모 쇼케이스 팩",
    msg_pw_mismatch: "새 비밀번호가 일치하지 않습니다",
    msg_pw_updated: "✅ 비밀번호가 성공적으로 변경되었습니다!",
    confirm_exit_app: "지금 포토 프레임 앱을 종료하고 닫으시겠습니까? 앱을 다시 시작할 때까지 웹 제어 포털이 오프라인 상태가 됩니다.",
    confirm_exit_app_all: "연결된 모든 액자에서 포토 프레임 앱을 종료하시겠습니까? 다시 시작할 때까지 웹 제어 포털이 오프라인 상태가 됩니다.",
    btn_cancel: "취소",
    badge_offline: "🔴 오프라인 (종료됨)",
    msg_exited_offline: "🚪 포토 프레임 앱이 종료되었습니다. 앱을 다시 시작할 때까지 웹 제어 포털이 오프라인 상태로 유지됩니다.",
    sec_fleet: "🖧 연결된 디지털 액자 (LAN 통합 관리)",
    btn_scan_fleet: "🔍 LAN 스캔",
    btn_add_peer: "➕ IP 추가",
    ph_add_peer: "192.168.86.x 또는 photoframe-2.local:8080",
    btn_connect_peer: "연결",
    fleet_all_frames: "🌐 모든 액자 ({count})",
    fleet_all_sub: "온라인 상태인 모든 액자에 동시 적용",
    fleet_this_frame: "현재 액자",
    target_controlling_self: "🎯 제어 중: {host} ({ip} — 현재 액자)",
    target_controlling_peer: "🎯 원격 액자 제어 중: {host} ({ip}) — 추가 로그인 불필요",
    target_controlling_all: "🌐 전체 브로드캐스트 모드: 원격 제어 및 공통 설정이 {count}대 액자 전체에 동시에 적용됩니다!",
    lbl_pwd: "Baidu Pan 추출 코드 (pwd)",
    ph_pwd: "4자리 코드 (URL에 ?pwd= 포함 시 생략 가능)",
    sec_update: "📦 앱 자동 업데이트 및 APK 설치 (Android TV)",
    lbl_update_url: "APK 업데이트 다운로드 링크",
    btn_check_update: "🔍 링크 저장 및 지금 업데이트 확인",
    lbl_apk_upload: "또는 브라우저에서 .apk 파일을 직접 업로드하여 설치",
    btn_upload_apk: "📲 APK 업로드 및 설치",
    msg_select_apk: "먼저 .apk 파일을 선택하세요",
    sec_webframe: "🖼️ 웹 슬라이드쇼 뷰어 (/frame)",
    webframe_desc: "동일한 Wi-Fi의 태블릿, 스마트폰, PC 브라우저에서 로그인 없이 실시간 슬라이드쇼를 감상하세요. 브라우저별로 독립된 사진 순서를 사용합니다 (동영상은 정지 포스터 이미지로 표시).",
    lbl_webframe_profile: "화질 및 기기 프로필",
    lbl_webframe_url: "웹 뷰어 주소",
    opt_wf_auto: "자동 — 스마트 매치 (최신 화면 1080p HD, 구형 태블릿 768p 표준)",
    opt_wf_hd: "1080p HD — 전체 해상도, 부드러운 전환 및 9가지 콜라주 레이아웃",
    opt_wf_legacy: "768p 표준 — 구형 iPad 및 태블릿용 메모리 절약 모드 (최대 4분할 콜라주)",
    btn_wf_turn_on: "🟢 웹 뷰어 켜기 (/frame)",
    btn_wf_turn_off: "🛑 웹 뷰어 끄기 (/frame)",
    btn_wf_open: "↗️ 웹 뷰어 열기 (/frame)",
    btn_open_webframe: "🖼️ 웹 뷰어 열기 (/frame)",
    lbl_saved_albums: "🔐 저장된 연결 앨범 및 폴더 (로컬 암호화 저장)",
    saved_albums_empty: "저장된 링크가 없습니다. 연결에 성공한 Google 포토 앨범이나 바이두 클라우드 폴더는 자동으로 암호화되어 여기에 저장됩니다.",
    btn_use_saved_album: "전환",
    badge_saved_active: "사용 중",
    broadcast_settings_note: "🌐 전체 브로드캐스트 모드: 모든 액자에 동시에 적용할 수 있는 공통 설정만 표시됩니다. 앨범/폴더 링크, 저장된 링크, 호스트 이름, 화면 회전, APK 업데이트를 변경하려면 위에서 개별 액자를 선택하세요.",
    btn_theme_light: "☀️ 라이트",
    btn_theme_dark: "🌙 다크",
    title_toggle_theme: "라이트 / 다크 모드 전환"
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
    lbl_device_name: "Nombre de Host (.local — numeración automática si hay conflicto)",
    ph_device_name: "photoframe (o p. ej. sala-estar)",
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
    btn_exit_app: "🚪 Salir del Marco de Fotos",
    badge_night: "🌙 Modo Nocturno (En Reposo)",
    badge_paused: "⏸️ En Pausa",
    badge_active: "🟢 Activo (Reproduciendo)",
    stat_items_fmt: "{prefix}{photos} elementos ({videos} videos)",
    stat_night_disabled: "Desactivado (24/7 Encendido)",
    msg_signed_in: "Sesión iniciada correctamente",
    msg_invalid_pw: "Contraseña incorrecta",
    msg_settings_saved_banner: "✅ ¡Configuración guardada y aplicada a las {time}! Fuente: {source} • Diseño: {layout} ({duration}s)",
    msg_source_album: "Álbum de Google Fotos ({album})",
    msg_source_demo: "Paquete Demo Integrado",
    msg_pw_mismatch: "Las contraseñas nuevas no coinciden",
    msg_pw_updated: "✅ ¡Contraseña actualizada correctamente! Tu sesión sigue activa.",
    confirm_exit_app: "¿Salir y cerrar la aplicación del marco de fotos ahora? El portal web quedará sin conexión hasta volver a iniciarla.",
    confirm_exit_app_all: "¿Salir y cerrar la aplicación en TODOS los marcos conectados ahora? Sus portales web quedarán sin conexión hasta volver a iniciarlos.",
    btn_cancel: "Cancelar",
    badge_offline: "🔴 Sin Conexión (Cerrado)",
    msg_exited_offline: "🚪 Marco de fotos cerrado. El portal web está ahora sin conexión hasta que se vuelva a iniciar la aplicación.",
    sec_fleet: "🖧 Marcos Conectados en Red (Flota LAN)",
    btn_scan_fleet: "🔍 Escanear LAN",
    btn_add_peer: "➕ Añadir IP",
    ph_add_peer: "192.168.86.x o photoframe-2.local:8080",
    btn_connect_peer: "Conectar",
    fleet_all_frames: "🌐 Todos los Marcos ({count})",
    fleet_all_sub: "Difusión inteligente a todos los marcos activos",
    fleet_this_frame: "Este Marco",
    target_controlling_self: "🎯 Controlando: {host} ({ip} — Este Marco)",
    target_controlling_peer: "🎯 Controlando Marco Remoto: {host} ({ip}) — Sin inicio de sesión adicional",
    target_controlling_all: "🌐 Modo Difusión: ¡Las acciones y ajustes compartidos se aplicarán a los {count} marcos activos simultáneamente!",
    lbl_pwd: "Código de Extracción Baidu Pan (pwd)",
    ph_pwd: "Código de 4 caracteres (opcional si ?pwd= está en la URL)",
    sec_update: "📦 Actualización Automática e Instalador APK (Android TV)",
    lbl_update_url: "Enlace de Descarga de Actualización APK",
    btn_check_update: "🔍 Guardar Enlace y Buscar Actualización",
    lbl_apk_upload: "O Subir e Instalar Archivo .apk Directamente desde el Navegador",
    btn_upload_apk: "📲 Subir e Instalar APK",
    msg_select_apk: "Por favor selecciona un archivo .apk primero",
    sec_webframe: "🖼️ Visor Web de Presentación (/frame)",
    webframe_desc: "Ve la presentación en vivo desde cualquier tablet, teléfono o PC en tu Wi-Fi sin iniciar sesión. Comparte el álbum con un orden independiente por navegador (los videos se muestran como pósters estáticos).",
    lbl_webframe_profile: "Calidad de Imagen y Perfil de Dispositivo",
    lbl_webframe_url: "Dirección del Visor Web",
    opt_wf_auto: "Auto — Ajuste Inteligente (1080p HD en Pantallas Modernas, 768p Estándar en Tablets Antiguas)",
    opt_wf_hd: "1080p HD — Resolución Completa, Fundido Suave y los 9 Diseños de Collage",
    opt_wf_legacy: "768p Estándar — Modo Ligero para iPads y Tablets Antiguas (Hasta 4 Mosaicos)",
    btn_wf_turn_on: "🟢 Activar Visor Web (/frame)",
    btn_wf_turn_off: "🛑 Desactivar Visor Web (/frame)",
    btn_wf_open: "↗️ Abrir Visor Web (/frame)",
    btn_open_webframe: "🖼️ Abrir Visor Web (/frame)",
    lbl_saved_albums: "🔐 Álbumes y Carpetas Conectados Guardados (Cifrados Localmente)",
    saved_albums_empty: "Aún no hay enlaces guardados. Los álbumes de Google Fotos y carpetas de Baidu Pan conectados con éxito se cifran y guardan aquí automáticamente.",
    btn_use_saved_album: "Cambiar",
    badge_saved_active: "Activo",
    broadcast_settings_note: "🌐 Modo Difusión: Solo se muestran los ajustes compartidos que pueden enviarse a todos los marcos. Selecciona un marco individual arriba para cambiar su URL de Álbum/Carpeta, Enlaces Guardados, Nombre de Host, Rotación de Pantalla o Actualización APK.",
    btn_theme_light: "☀️ Claro",
    btn_theme_dark: "🌙 Oscuro",
    title_toggle_theme: "Cambiar entre modo claro y oscuro"
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
    lbl_device_name: "Geräte-Hostname (.local — automatische Nummerierung bei Konflikt)",
    ph_device_name: "photoframe (oder z. B. wohnzimmer)",
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
    btn_exit_app: "🚪 Bilderrahmen beenden",
    badge_night: "🌙 Nachtmodus (Schlafend)",
    badge_paused: "⏸️ Pausiert",
    badge_active: "🟢 Aktiv (Wiedergabe)",
    stat_items_fmt: "{prefix}{photos} Elemente ({videos} Videos)",
    stat_night_disabled: "Deaktiviert (24/7 An)",
    msg_signed_in: "Erfolgreich angemeldet",
    msg_invalid_pw: "Ungültiges Passwort",
    msg_settings_saved_banner: "✅ Einstellungen um {time} gespeichert & live angewendet! Quelle: {source} • Layout: {layout} ({duration}s)",
    msg_source_album: "Google Fotos-Album ({album})",
    msg_source_demo: "Integriertes Demo-Showcase-Paket",
    msg_pw_mismatch: "Neue Passwörter stimmen nicht überein",
    msg_pw_updated: "✅ Passwort erfolgreich aktualisiert! Ihre Sitzung bleibt aktiv.",
    confirm_exit_app: "Bilderrahmen-App jetzt beenden und schließen? Das Web-Steuerportal bleibt offline, bis die App wieder gestartet wird.",
    confirm_exit_app_all: "Bilderrahmen-App auf ALLEN verbundenen Rahmen jetzt beenden und schließen? Die Web-Steuerportale bleiben bis zum Neustart offline.",
    btn_cancel: "Abbrechen",
    badge_offline: "🔴 Offline (Beendet)",
    msg_exited_offline: "🚪 Bilderrahmen beendet. Das Web-Steuerportal ist jetzt offline, bis die App wieder gestartet wird.",
    sec_fleet: "🖧 Verbundene Bilderrahmen (LAN-Flotte)",
    btn_scan_fleet: "🔍 LAN scannen",
    btn_add_peer: "➕ IP hinzufügen",
    ph_add_peer: "192.168.86.x oder photoframe-2.local:8080",
    btn_connect_peer: "Verbinden",
    fleet_all_frames: "🌐 Alle Rahmen ({count})",
    fleet_all_sub: "Smart-Broadcast an alle aktiven Rahmen",
    fleet_this_frame: "Dieser Rahmen",
    target_controlling_self: "🎯 Steuerung: {host} ({ip} — Dieser Rahmen)",
    target_controlling_peer: "🎯 Fernsteuerung: {host} ({ip}) — Kein separates Login nötig",
    target_controlling_all: "🌐 Broadcast-Modus: Aktionen & gemeinsame Einstellungen gelten gleichzeitig für alle {count} Rahmen!",
    lbl_pwd: "Baidu Pan Extraktionscode (pwd)",
    ph_pwd: "4-stelliger Code (optional wenn ?pwd= in URL)",
    sec_update: "📦 App-Auto-Update & APK-Installation (Android TV)",
    lbl_update_url: "APK-Update-Download-Link",
    btn_check_update: "🔍 Link speichern & jetzt auf Update prüfen",
    lbl_apk_upload: "Oder .apk-Datei direkt über den Browser hochladen & installieren",
    btn_upload_apk: "📲 APK hochladen & installieren",
    msg_select_apk: "Bitte wählen Sie zuerst eine .apk-Datei aus",
    sec_webframe: "🖼️ Web-Diashow-Viewer (/frame)",
    webframe_desc: "Zeigen Sie die Live-Diashow ohne Anmeldung auf jedem Tablet, Smartphone oder PC im WLAN an. Teilt das Album mit unabhängiger Bildreihenfolge pro Browser (Videos werden als Standbilder angezeigt).",
    lbl_webframe_profile: "Bildqualität & Geräteprofil",
    lbl_webframe_url: "Web-Viewer-Adresse",
    opt_wf_auto: "Auto — Smart Match (1080p HD auf modernen Bildschirmen, 768p Standard auf älteren Tablets)",
    opt_wf_hd: "1080p HD — Volle Auflösung, sanftes Überblenden & alle 9 Collagen-Layouts",
    opt_wf_legacy: "768p Standard — Speicherschonender Modus für ältere iPads & Tablets (bis zu 4 Kacheln)",
    btn_wf_turn_on: "🟢 Web-Viewer aktivieren (/frame)",
    btn_wf_turn_off: "🛑 Web-Viewer deaktivieren (/frame)",
    btn_wf_open: "↗️ Web-Viewer öffnen (/frame)",
    btn_open_webframe: "🖼️ Web-Viewer öffnen (/frame)",
    lbl_saved_albums: "🔐 Gespeicherte verbundene Alben & Ordner (Lokal verschlüsselt)",
    saved_albums_empty: "Noch keine verbundenen Links gespeichert. Erfolgreich verbundene Google Fotos-Alben und Baidu Pan-Ordner werden hier automatisch verschlüsselt gespeichert.",
    btn_use_saved_album: "Wechseln",
    badge_saved_active: "Aktiv",
    broadcast_settings_note: "🌐 Broadcast-Modus: Es werden nur gemeinsame Einstellungen angezeigt, die an alle Rahmen gesendet werden können. Wählen Sie oben einen einzelnen Rahmen aus, um Album-/Ordner-URL, Gespeicherte Links, Hostname, Bildschirmdrehung oder APK-Update zu ändern.",
    btn_theme_light: "☀️ Hell",
    btn_theme_dark: "🌙 Dunkel",
    title_toggle_theme: "Zwischen hellem und dunklem Modus wechseln"
  }
};

let currentLang = localStorage.getItem('frame_lang') || 'en';
let currentTheme = (function() {
  try {
    const s = localStorage.getItem('frame_theme');
    return (s === 'light' || s === 'dark') ? s : 'dark';
  } catch (e) {
    return 'dark';
  }
})();
let formPopulated = false;
let lastStatusSnapshot = null;
let selectedTarget = 'self';
let fleetNodes = [];
let activeTargetEdition = 'gphotos';
let activeTargetPlatform = 'linux';
let savedAlbumsList = [];
let lastActiveAlbumUrl = '';
let lastActiveAlbumTitle = '';
let portalExitedOffline = false;
let consecutiveStatusFailures = 0;

function escapeHtml(s) {
  return String(s || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function renderSavedAlbums(list, currentAlbum, currentTitle) {
  savedAlbumsList = Array.isArray(list) ? list : [];
  if (typeof currentAlbum === 'string') lastActiveAlbumUrl = currentAlbum;
  if (typeof currentTitle === 'string') lastActiveAlbumTitle = currentTitle;

  const badgeEl = document.getElementById('active-album-title-badge');
  if (badgeEl) {
    if (lastActiveAlbumTitle && lastActiveAlbumUrl && lastActiveAlbumUrl !== 'demo') {
      badgeEl.textContent = `📂 ${lastActiveAlbumTitle}`;
      badgeEl.classList.remove('hidden');
    } else {
      badgeEl.classList.add('hidden');
    }
  }

  const container = document.getElementById('saved-albums-list');
  if (!container) return;
  if (!savedAlbumsList.length) {
    container.innerHTML = `<div style="font-size:0.79rem; color:var(--muted); grid-column:1/-1;">${escapeHtml(t('saved_albums_empty'))}</div>`;
    return;
  }

  const normCur = String(lastActiveAlbumUrl || '').trim();
  container.innerHTML = savedAlbumsList.map(item => {
    const id = escapeHtml(item.id || item.album || '');
    const url = String(item.album || '');
    const pwd = String(item.pwd || '');
    const isBaidu = item.edition === 'baidupan' || url.toLowerCase().includes('pan.baidu.com');
    const icon = isBaidu ? '☁️' : '📸';
    const rawName = String(item.name || '').trim();
    const shortUrl = url.replace(/^https?:\/\//i, '');
    const displayTitle = rawName || shortUrl;
    const isCurrent = normCur && normCur !== 'demo' && (url === normCur || shortUrl === normCur.replace(/^https?:\/\//i, ''));
    const pwdTag = pwd ? ` • pwd: ${escapeHtml(pwd)}` : '';
    const subLine = rawName ? `${escapeHtml(shortUrl)}${pwdTag}` : (pwd ? `pwd: ${escapeHtml(pwd)}` : (isBaidu ? 'Baidu Pan' : 'Google Photos'));
    const activeBadge = isCurrent
      ? `<span class="badge active" style="font-size:0.7rem; padding:2px 7px;">🟢 ${escapeHtml(t('badge_saved_active'))}</span>`
      : `<button type="button" class="saved-use-btn" onclick="applySavedAlbum(event, '${id}')">${escapeHtml(t('btn_use_saved_album'))}</button>`;
    return `<div class="saved-album-pill ${isCurrent ? 'active' : ''}" onclick="applySavedAlbum(event, '${id}')">
      <div class="saved-album-info">
        <div class="saved-album-title" title="${escapeHtml(displayTitle)}">${icon} ${escapeHtml(displayTitle)}</div>
        <div class="saved-album-meta" title="${escapeHtml(url)}">${subLine}</div>
      </div>
      <div class="saved-album-actions">
        ${activeBadge}
        <button type="button" class="fleet-remove-btn" title="Remove" onclick="deleteSavedAlbum(event, '${id}')">&times;</button>
      </div>
    </div>`;
  }).join('');
}

async function applySavedAlbum(ev, entryId) {
  if (ev) ev.stopPropagation();
  const entry = savedAlbumsList.find(x => (x.id || x.album) === entryId);
  if (!entry) return;
  const albumEl = document.getElementById('cfg-album');
  const pwdEl = document.getElementById('cfg-pwd');
  if (albumEl) albumEl.value = entry.album || '';
  if (pwdEl) pwdEl.value = entry.pwd || '';
  await sendAction('resync');
}

async function deleteSavedAlbum(ev, entryId) {
  if (ev) ev.stopPropagation();
  const res = await fetch('/api/action', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({action: 'delete_saved_album', album: entryId, target: selectedTarget})
  });
  const data = await res.json();
  if (res.ok && data.ok) {
    savedAlbumsList = savedAlbumsList.filter(x => (x.id || x.album) !== entryId);
    renderSavedAlbums(savedAlbumsList, lastActiveAlbumUrl, lastActiveAlbumTitle);
    showToast(data.message || 'Removed saved link');
    fetchStatus(false);
  } else {
    showToast(data.error || 'Failed to remove link', true);
  }
}

function t(key, vars = {}) {
  const dict = I18N[currentLang] || I18N.en;
  let str = dict[key] || I18N.en[key] || key;
  for (const [k, v] of Object.entries(vars)) {
    str = str.replace(`{${k}}`, v);
  }
  return str;
}

function autoExtractBaiduPwd() {
  const albumEl = document.getElementById('cfg-album');
  const pwdEl = document.getElementById('cfg-pwd');
  if (!albumEl || !pwdEl) return;
  const raw = albumEl.value || '';
  const m = raw.match(/[?&]pwd=([a-zA-Z0-9]{4})/) || raw.match(/(?:提取码|密码|pwd)\s*[:：=]?\s*([a-zA-Z0-9]{4})/i);
  if (m && !pwdEl.value.trim()) {
    pwdEl.value = m[1];
  }
}

function updateThemeButtons() {
  const label = currentTheme === 'dark' ? t('btn_theme_light') : t('btn_theme_dark');
  const tip = t('title_toggle_theme');
  ['login-theme-btn', 'header-theme-btn'].forEach(id => {
    const btn = document.getElementById(id);
    if (btn) {
      btn.textContent = label;
      btn.title = tip;
      btn.setAttribute('aria-label', tip);
    }
  });
}

function applyTheme(theme, persist = true) {
  currentTheme = (theme === 'light') ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', currentTheme);
  if (persist) {
    try { localStorage.setItem('frame_theme', currentTheme); } catch (e) {}
  }
  updateThemeButtons();
}

function toggleTheme() {
  applyTheme(currentTheme === 'dark' ? 'light' : 'dark', true);
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
  updateThemeButtons();
  if (lastStatusSnapshot) {
    renderStatusSnapshot(lastStatusSnapshot);
  }
  if (fleetNodes && fleetNodes.length > 0) {
    renderFleetBar(fleetNodes);
  }
  renderSavedAlbums(savedAlbumsList, lastActiveAlbumUrl, lastActiveAlbumTitle);
}

function renderFleetBar(nodes) {
  fleetNodes = Array.isArray(nodes) ? nodes : [];
  const grid = document.getElementById('fleet-nodes-grid');
  const banner = document.getElementById('active-target-banner');
  if (!grid) return;

  const onlineNodes = fleetNodes.filter(n => n.online !== false);
  const selfNode = fleetNodes.find(n => n.is_self) || fleetNodes[0] || {};
  if (selectedTarget !== 'all' && selectedTarget !== 'self') {
    const exists = fleetNodes.some(n => n.id === selectedTarget);
    if (!exists) selectedTarget = 'self';
  }

  let html = '';
  if (fleetNodes.length > 1) {
    const isAll = (selectedTarget === 'all');
    html += `<div class="fleet-pill ${isAll ? 'selected' : ''}" onclick="selectFleetTarget('all')">
      <div class="fleet-pill-top">
        <span>${t('fleet_all_frames', {count: onlineNodes.length})}</span>
      </div>
      <div class="fleet-pill-meta">⚡ Broadcast Mode</div>
      <div class="fleet-pill-sub">${t('fleet_all_sub')}</div>
    </div>`;
  }

  fleetNodes.forEach(node => {
    const targetKey = node.is_self ? 'self' : node.id;
    const isSelected = (selectedTarget === targetKey) || (node.is_self && selectedTarget === node.id);
    const dot = node.online === false ? '🔴' : (node.is_night_mode ? '🌙' : (node.is_paused ? '⏸️' : '🟢'));
    const platIcon = node.platform === 'android_tv' ? '📺 TV' : '🍓 Linux';
    const edLabel = node.edition === 'baidupan' ? 'Baidu' : 'Google';
    const selfTag = node.is_self ? ` <span style="font-size:0.72rem; color:var(--accent);">(${t('fleet_this_frame')})</span>` : '';
    const removeBtn = (!node.is_self && (node.is_manual || node.online === false))
      ? `<button type="button" class="fleet-remove-btn" title="Remove" onclick="removePeer(event, '${node.id}')">&times;</button>`
      : '';
    const hostTitle = node.mdns_hostname || node.ip || node.id;
    const mediaSummary = node.online === false
      ? 'Offline'
      : `${node.is_demo_mode ? '✨ Demo • ' : ''}${node.photo_count ?? 0} items • ${node.landscape_mode || 'collage'}`;
    html += `<div class="fleet-pill ${isSelected ? 'selected' : ''}" onclick="selectFleetTarget('${targetKey}')">
      <div class="fleet-pill-top">
        <span>${dot} ${hostTitle}${selfTag}</span>
        ${removeBtn}
      </div>
      <div class="fleet-pill-meta">${node.ip}:${node.port || 8080} • ${platIcon} (${edLabel}) • v${node.app_version || '1.0'}</div>
      <div class="fleet-pill-sub">${mediaSummary}</div>
    </div>`;
  });
  grid.innerHTML = html;

  if (banner) {
    if (selectedTarget === 'all') {
      banner.textContent = t('target_controlling_all', {count: onlineNodes.length});
    } else if (selectedTarget === 'self' || selectedTarget === selfNode.id) {
      const selfPortSuffix = (selfNode.port && selfNode.port !== 8080) ? `:${selfNode.port}` : '';
      banner.textContent = t('target_controlling_self', {
        host: `${selfNode.mdns_hostname || 'photoframe.local'}${selfPortSuffix}`,
        ip: `${selfNode.ip || '127.0.0.1'}${selfPortSuffix}`
      });
    } else {
      const peer = fleetNodes.find(n => n.id === selectedTarget) || {};
      const peerPortSuffix = (peer.port && peer.port !== 8080) ? `:${peer.port}` : '';
      banner.textContent = t('target_controlling_peer', {
        host: `${peer.mdns_hostname || selectedTarget}${peerPortSuffix}`,
        ip: peer.id || `${peer.ip || selectedTarget}${peerPortSuffix}`
      });
    }
  }

  updateDynamicFormVisibility();
}

function updateDynamicFormVisibility() {
  const isAll = (selectedTarget === 'all');
  const showBaiduPwd = !isAll && (activeTargetEdition === 'baidupan');
  const showTvApkCard = !isAll && (activeTargetPlatform === 'android_tv');

  const bcastNote = document.getElementById('broadcast-settings-note');
  if (bcastNote) bcastNote.classList.toggle('hidden', !isAll);

  const albumGroup = document.getElementById('group-album');
  if (albumGroup) albumGroup.classList.toggle('hidden', isAll);
  const albumInput = document.getElementById('cfg-album');
  if (albumInput) albumInput.disabled = isAll;

  const pwdGroup = document.getElementById('group-pwd');
  if (pwdGroup) pwdGroup.classList.toggle('hidden', !showBaiduPwd);
  const pwdInput = document.getElementById('cfg-pwd');
  if (pwdInput) pwdInput.disabled = !showBaiduPwd;

  const savedGroup = document.getElementById('group-saved-albums');
  if (savedGroup) savedGroup.classList.toggle('hidden', isAll);

  const devGroup = document.getElementById('group-device-name');
  if (devGroup) devGroup.classList.toggle('hidden', isAll);
  const devInput = document.getElementById('cfg-device-name');
  if (devInput) devInput.disabled = isAll;

  const rotGroup = document.getElementById('group-rotate');
  if (rotGroup) rotGroup.classList.toggle('hidden', isAll);
  const rotInput = document.getElementById('cfg-rotate');
  if (rotInput) rotInput.disabled = isAll;

  const apkCard = document.getElementById('card-apk-update');
  if (apkCard) apkCard.classList.toggle('hidden', !showTvApkCard);

  const logBox = document.getElementById('log-box');
  if (logBox) logBox.classList.toggle('hidden', isAll);
  const btnRefreshLog = document.getElementById('btn-refresh-log');
  if (btnRefreshLog) btnRefreshLog.classList.toggle('hidden', isAll);
}

function selectFleetTarget(target) {
  selectedTarget = target;
  formPopulated = false;
  renderFleetBar(fleetNodes);
  fetchStatus(true, false);
}

async function scanFleetNodes() {
  const btn = document.getElementById('btn-scan-fleet');
  if (btn) btn.disabled = true;
  try {
    await fetchStatus(false, true);
    showToast('LAN scan complete');
  } finally {
    if (btn) btn.disabled = false;
  }
}

function toggleAddPeerRow() {
  const row = document.getElementById('add-peer-row');
  if (!row) return;
  row.classList.toggle('hidden');
  if (!row.classList.contains('hidden')) {
    document.getElementById('add-peer-input')?.focus();
  }
}

async function addManualPeer() {
  const inp = document.getElementById('add-peer-input');
  const host = (inp?.value || '').trim();
  if (!host) return;
  const res = await fetch('/api/fleet/add_peer', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({host})
  });
  const data = await res.json();
  if (res.ok && data.ok) {
    if (inp) inp.value = '';
    document.getElementById('add-peer-row')?.classList.add('hidden');
    if (data.fleet) renderFleetBar(data.fleet);
    showToast(data.message || 'Frame connected');
  } else {
    showToast(data.error || 'Could not reach frame', true);
  }
}

async function removePeer(ev, targetId) {
  ev.stopPropagation();
  const res = await fetch('/api/fleet/remove_peer', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({target: targetId})
  });
  const data = await res.json();
  if (res.ok && data.ok) {
    if (selectedTarget === targetId) selectedTarget = 'self';
    if (data.fleet) renderFleetBar(data.fleet);
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

  const titlePrefix = (!st.is_demo_mode && st.album_title) ? `📂 ${st.album_title} • ` : '';
  const modePrefix = st.is_demo_mode ? '✨ Demo: ' : titlePrefix;
  document.getElementById('stat-album').textContent = t('stat_items_fmt', {
    prefix: modePrefix,
    photos: st.photo_count,
    videos: st.video_count
  });
  document.getElementById('stat-layout').textContent = `${st.landscape_mode} (${st.slide_duration}s)`;
  document.getElementById('stat-night').textContent = st.night_mode_enabled ? `${st.night_start} – ${st.night_end}` : t('stat_night_disabled');
  document.getElementById('stat-display').textContent = `${st.canvas_w}×${st.canvas_h} @ ${st.rotation}°`;
  const hdrHostBadge = document.getElementById('header-host-badge');
  if (hdrHostBadge && st.mdns_hostname) {
    hdrHostBadge.textContent = `🌐 ${st.mdns_hostname}`;
  }
  const hdrVerBadge = document.getElementById('header-version-badge');
  if (hdrVerBadge && st.app_version) {
    hdrVerBadge.textContent = `v${st.app_version} (Build #${st.app_version_code ?? 1})`;
  }
  const verBadge = document.getElementById('stat-app-version');
  if (verBadge && st.app_version) {
    const shaSuffix = st.app_sha256 ? ` • ${st.app_sha256}` : '';
    verBadge.textContent = `v${st.app_version} (Build #${st.app_version_code ?? 1}${shaSuffix})`;
  }
  const upStatus = document.getElementById('stat-update-status');
  if (upStatus && st.update_status) {
    upStatus.textContent = st.update_status;
  }

  // Render Web Slideshow Viewer (/frame) status
  const wfEnabled = Boolean(st.web_frame_enabled);
  const wfClients = st.web_frame_clients || 0;
  const wfBadge = document.getElementById('webframe-status-badge');
  const wfBtn = document.getElementById('btn-webframe-toggle');
  const wfOpen = document.getElementById('btn-webframe-open');
  const wfLinks = document.getElementById('webframe-links-box');
  const hostName = st.mdns_hostname || window.location.hostname || 'photoframe.local';
  const hostIp = st.lan_ip || window.location.hostname || 'photoframe.local';
  const peerObj = (selectedTarget && selectedTarget !== 'self' && selectedTarget !== 'all')
    ? (fleetNodes.find(n => n.id === selectedTarget) || {})
    : (fleetNodes.find(n => n.is_self) || {});
  const targetPort = st.web_port || st.port || peerObj.port || window.location.port || 8080;
  if (wfBadge) {
    if (wfEnabled) {
      wfBadge.textContent = `🟢 Enabled (${wfClients} active viewer${wfClients === 1 ? '' : 's'})`;
      wfBadge.className = 'badge active';
    } else {
      wfBadge.textContent = '⚪ Disabled';
      wfBadge.className = 'badge';
    }
  }
  if (wfBtn) {
    wfBtn.textContent = wfEnabled ? t('btn_wf_turn_off') : t('btn_wf_turn_on');
    wfBtn.className = wfEnabled ? 'danger' : 'primary';
  }
  if (wfOpen) {
    wfOpen.classList.toggle('hidden', !wfEnabled || selectedTarget === 'all');
    wfOpen.href = (selectedTarget && selectedTarget !== 'self' && selectedTarget !== 'all')
      ? `http://${hostIp}:${targetPort}/frame`
      : '/frame';
  }
  if (wfLinks) {
    wfLinks.classList.toggle('hidden', !wfEnabled || selectedTarget === 'all');
    if (wfEnabled && selectedTarget !== 'all') {
      wfLinks.innerHTML = `📺 <b>Web Slideshow Viewer URL (no login required):</b> <a href="http://${hostName}:${targetPort}/frame" target="_blank" style="color:var(--accent);">http://${hostName}:${targetPort}/frame</a> &nbsp;or&nbsp; <a href="http://${hostIp}:${targetPort}/frame" target="_blank" style="color:var(--accent);">http://${hostIp}:${targetPort}/frame</a>`;
    }
  }
}

async function updateWebFrameProfileLive(profileVal) {
  try {
    const res = await fetch('/api/settings', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({target: selectedTarget, web_frame_profile: profileVal})
    });
    const data = await res.json();
    if (res.ok && data.ok) {
      const labelMap = {auto: 'Auto (Smart Match)', hd: '1080p HD', legacy: '768p Standard'};
      showToast(`Picture quality profile set to ${labelMap[profileVal] || profileVal}`);
      fetchStatus(false);
    }
  } catch (e) {}
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

function markPortalOfflineUI(bannerMsg) {
  const badge = document.getElementById('state-badge');
  if (badge) {
    badge.textContent = t('badge_offline');
    badge.className = 'badge offline';
  }
  if (bannerMsg) {
    const el = document.getElementById('system-confirm-banner');
    if (el) {
      clearTimeout(el._hideTimer);
      el.textContent = bannerMsg;
      el.className = 'confirm-banner';
      el.style.display = 'block';
    }
  }
  const exitBtn = document.getElementById('btn-exit-app');
  if (exitBtn && portalExitedOffline) {
    exitBtn.disabled = true;
  }
}

async function fetchStatus(populateForm = false, triggerScan = false) {
  try {
    const q = new URLSearchParams();
    if (selectedTarget && selectedTarget !== 'self' && selectedTarget !== 'all') {
      q.set('target', selectedTarget);
    }
    if (triggerScan) q.set('scan', '1');
    const url = '/api/status' + (q.toString() ? `?${q.toString()}` : '');
    const res = await fetch(url);
    const data = await res.json();
    consecutiveStatusFailures = 0;
    if (portalExitedOffline) {
      portalExitedOffline = false;
      const exitBtn = document.getElementById('btn-exit-app');
      if (exitBtn) exitBtn.disabled = false;
      const sysBanner = document.getElementById('system-confirm-banner');
      if (sysBanner) sysBanner.style.display = 'none';
    }
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

    if (selectedTarget && selectedTarget !== 'self' && selectedTarget !== 'all' && data.target === 'self') {
      selectedTarget = 'self';
    }
    activeTargetEdition = data.target_edition || data.status?.edition || 'gphotos';
    activeTargetPlatform = data.target_platform || data.status?.platform || 'linux';
    if (data.fleet) {
      renderFleetBar(data.fleet);
    }
    renderStatusSnapshot(data.status);
    document.getElementById('log-box').textContent = data.log_tail || '(No log entries)';
    if (data.settings && Array.isArray(data.settings.saved_albums)) {
      renderSavedAlbums(
        data.settings.saved_albums,
        data.settings.album || '',
        data.status?.album_title || data.settings?.album_title || ''
      );
    }

    if (populateForm || !formPopulated) {
      const cfg = data.settings;
      if (cfg.language) {
        setLanguage(cfg.language);
      }
      document.getElementById('cfg-album').value = (cfg.album && cfg.album !== 'demo') ? cfg.album : '';
      const pwdEl = document.getElementById('cfg-pwd');
      if (pwdEl) pwdEl.value = cfg.pwd || '';
      const devEl = document.getElementById('cfg-device-name');
      if (devEl) devEl.value = cfg.device_name || '';
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
      const upUrlEl = document.getElementById('cfg-update-url');
      if (upUrlEl) upUrlEl.value = cfg.update_url || '';
      const wfProfEl = document.getElementById('cfg-webframe-profile');
      if (wfProfEl && cfg.web_frame_profile) wfProfEl.value = cfg.web_frame_profile;
      formPopulated = true;
    }
  } catch (err) {
    consecutiveStatusFailures += 1;
    if (portalExitedOffline || consecutiveStatusFailures >= 2) {
      markPortalOfflineUI(portalExitedOffline ? t('msg_exited_offline') : null);
    }
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
  const bodyObj = {action, target: selectedTarget};
  if (action === 'resync' && selectedTarget !== 'all') {
    autoExtractBaiduPwd();
    const albumEl = document.getElementById('cfg-album');
    const pwdEl = document.getElementById('cfg-pwd');
    if (albumEl && albumEl.value.trim()) {
      bodyObj.album = albumEl.value.trim();
    }
    if (pwdEl && pwdEl.value.trim()) {
      bodyObj.pwd = pwdEl.value.trim();
    }
  }
  const exitsThisPortal = (action === 'exit_app') && (!selectedTarget || selectedTarget === 'self' || selectedTarget === 'all');
  try {
    const res = await fetch('/api/action', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(bodyObj)
    });
    let data = {};
    try {
      data = await res.json();
    } catch (parseErr) {
      if (exitsThisPortal && res.ok) {
        data = {ok: true, message: t('msg_exited_offline')};
      } else {
        throw parseErr;
      }
    }
    if (res.ok && data.ok) {
      if (action === 'exit_app') {
        if (exitsThisPortal) {
          portalExitedOffline = true;
          showToast(t('msg_exited_offline'));
          markPortalOfflineUI(t('msg_exited_offline'));
        } else {
          showToast(data.message || t('msg_exited_offline'));
          showInlineBanner('system-confirm-banner', t('msg_exited_offline'), false);
          setTimeout(() => fetchStatus(false, true), 1100);
        }
      } else {
        showToast(data.message || 'Action executed');
        setTimeout(() => fetchStatus(false), 600);
      }
    } else {
      const errMsg = data.error || 'Action failed';
      showToast(errMsg, true);
      if (action === 'exit_app') {
        showInlineBanner('system-confirm-banner', '⚠️ ' + errMsg, true);
      }
    }
  } catch (err) {
    if (exitsThisPortal) {
      portalExitedOffline = true;
      showToast(t('msg_exited_offline'));
      markPortalOfflineUI(t('msg_exited_offline'));
    } else {
      const errMsg = (err && err.message) ? err.message : 'Network request failed';
      showToast(errMsg, true);
      if (action === 'exit_app') {
        showInlineBanner('system-confirm-banner', '⚠️ ' + errMsg, true);
      }
    }
  }
}

function playDemoShowcase() {
  const albumEl = document.getElementById('cfg-album');
  const pwdEl = document.getElementById('cfg-pwd');
  if (albumEl) albumEl.value = '';
  if (pwdEl) pwdEl.value = '';
  sendAction('demo');
}

function confirmExitFrame() {
  const modal = document.getElementById('exit-confirm-modal');
  const msgEl = document.getElementById('exit-modal-msg');
  const titleEl = document.getElementById('exit-modal-title');
  const cancelBtn = document.getElementById('btn-exit-cancel');
  const confirmBtn = document.getElementById('btn-exit-confirm');
  const promptKey = selectedTarget === 'all' ? 'confirm_exit_app_all' : 'confirm_exit_app';
  if (titleEl) titleEl.textContent = t('btn_exit_app');
  if (msgEl) msgEl.textContent = t(promptKey);
  if (cancelBtn) cancelBtn.textContent = t('btn_cancel');
  if (confirmBtn) {
    confirmBtn.textContent = t('btn_exit_app');
    confirmBtn.disabled = false;
  }
  if (modal) {
    modal.classList.remove('hidden');
  }
}

function closeExitModal() {
  const modal = document.getElementById('exit-confirm-modal');
  if (modal) {
    modal.classList.add('hidden');
  }
}

async function executeConfirmedExit() {
  const confirmBtn = document.getElementById('btn-exit-confirm');
  if (confirmBtn) confirmBtn.disabled = true;
  closeExitModal();
  await sendAction('exit_app');
}

document.addEventListener('keydown', function(ev) {
  if (ev.key === 'Escape') {
    const modal = document.getElementById('exit-confirm-modal');
    if (modal && !modal.classList.contains('hidden')) {
      closeExitModal();
    }
  }
});

async function saveSettings(e) {
  e.preventDefault();
  autoExtractBaiduPwd();
  const btn = document.getElementById('btn-save-settings');
  if (btn) {
    btn.disabled = true;
    btn.textContent = t('btn_saving_settings');
  }
  const selectedLang = document.getElementById('cfg-language').value || currentLang || 'en';
  localStorage.setItem('frame_lang_user_set', '1');
  setLanguage(selectedLang);

  const isAll = (selectedTarget === 'all');
  const payload = {
    target: selectedTarget,
    language: selectedLang,
    landscape_mode: document.getElementById('cfg-layout').value,
    duration: parseFloat(document.getElementById('cfg-duration').value),
    transition: document.getElementById('cfg-transition').value,
    videos: document.getElementById('cfg-videos').value,
    max_video_seconds: parseFloat(document.getElementById('cfg-max-vid').value),
    fit: document.getElementById('cfg-fit').value,
    refresh_hours: parseFloat(document.getElementById('cfg-refresh').value),
    shuffle: document.getElementById('cfg-shuffle').checked,
    night_mode_enabled: document.getElementById('cfg-night-enabled').checked,
    night_start: document.getElementById('cfg-night-start').value,
    night_end: document.getElementById('cfg-night-end').value,
    timezone: (typeof Intl !== 'undefined' && Intl.DateTimeFormat ? (Intl.DateTimeFormat().resolvedOptions().timeZone || '') : ''),
    web_frame_profile: document.getElementById('cfg-webframe-profile')?.value || 'auto',
  };
  if (!isAll) {
    payload.album = document.getElementById('cfg-album').value.trim();
    payload.pwd = (document.getElementById('cfg-pwd')?.value || '').trim();
    payload.device_name = (document.getElementById('cfg-device-name')?.value || '').trim();
    payload.rotate = parseInt(document.getElementById('cfg-rotate').value, 10);
    payload.update_url = (document.getElementById('cfg-update-url')?.value || '').trim();
  }
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
      const confirmMsg = data.message && selectedTarget === 'all'
        ? data.message
        : t('msg_settings_saved_banner', {
            time: nowStr,
            source: albumDesc,
            layout: payload.landscape_mode,
            duration: payload.duration
          });
      showToast(data.message || t('btn_saved_settings'));
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
    body: JSON.stringify({target: selectedTarget, current_password, new_password})
  });
  const data = await res.json();
  if (res.ok && data.ok) {
    document.getElementById('pw-form').reset();
    showToast(data.message || t('msg_pw_updated'));
    showInlineBanner('pw-confirm-banner', data.message || t('msg_pw_updated'), false);
    fetchStatus(false);
  } else {
    const errMsg = data.error || 'Password update failed';
    showToast(errMsg, true);
    showInlineBanner('pw-confirm-banner', '⚠️ ' + errMsg, true);
  }
}

async function checkUpdateNow() {
  const urlVal = (document.getElementById('cfg-update-url')?.value || '').trim();
  const res = await fetch('/api/action', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({target: selectedTarget, action: 'check_update', album: urlVal})
  });
  const data = await res.json();
  if (res.ok && data.ok) {
    showToast(data.message || 'Checking for updates...');
    showInlineBanner('update-confirm-banner', '🔍 ' + (data.message || 'Checking for updates...'), false);
    setTimeout(() => fetchStatus(false), 1200);
    setTimeout(() => fetchStatus(false), 3500);
  } else {
    showToast(data.error || 'Update check failed', true);
  }
}

function uploadAndInstallApk() {
  const fileInput = document.getElementById('apk-file-input');
  const btn = document.getElementById('btn-upload-apk');
  if (!fileInput || !fileInput.files || fileInput.files.length === 0) {
    showToast(t('msg_select_apk'), true);
    showInlineBanner('update-confirm-banner', '⚠️ ' + t('msg_select_apk'), true);
    return;
  }
  const file = fileInput.files[0];
  if (btn) {
    btn.disabled = true;
    btn.textContent = '⏳ Uploading 0%...';
  }
  const xhr = new XMLHttpRequest();
  xhr.open('POST', `/api/update_upload?target=${encodeURIComponent(selectedTarget)}`, true);
  xhr.setRequestHeader('Content-Type', 'application/vnd.android.package-archive');
  xhr.upload.onprogress = function(ev) {
    if (ev.lengthComputable && btn) {
      const pct = Math.round((ev.loaded * 100) / ev.total);
      btn.textContent = `⏳ Uploading ${pct}%...`;
    }
  };
  xhr.onload = function() {
    if (btn) {
      btn.disabled = false;
      btn.textContent = t('btn_upload_apk');
    }
    try {
      const data = JSON.parse(xhr.responseText || '{}');
      if (xhr.status >= 200 && xhr.status < 300 && data.ok) {
        showToast(data.message || 'APK uploaded & installing');
        showInlineBanner('update-confirm-banner', '✅ ' + (data.message || 'APK uploaded & installing'), false);
        fileInput.value = '';
        setTimeout(() => fetchStatus(false), 1000);
      } else {
        const err = data.error || ('Upload failed (HTTP ' + xhr.status + ')');
        showToast(err, true);
        showInlineBanner('update-confirm-banner', '⚠️ ' + err, true);
      }
    } catch (e) {
      showToast('Invalid response from server', true);
    }
  };
  xhr.onerror = function() {
    if (btn) {
      btn.disabled = false;
      btn.textContent = t('btn_upload_apk');
    }
    showToast('Network error during APK upload', true);
    showInlineBanner('update-confirm-banner', '⚠️ Network error during APK upload', true);
  };
  xhr.send(file);
}

applyTheme(currentTheme, false);
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
    port: int = 8080,
) -> Type[http.server.BaseHTTPRequestHandler]:
    """
    Factory that returns a `BaseHTTPRequestHandler` class bound to `app` and `config_store`.
    Can be passed directly to `_ThreadingHTTPServer` in production or unit tests.
    """
    fleet_manager = FleetManager(
        app=app,
        config_store=config_store,
        port=port,
        edition="gphotos",
        platform="linux",
        enable_background_scan=(login_fail_delay >= 0.5),
    )

    class PortalHandler(http.server.BaseHTTPRequestHandler):
        fleet = fleet_manager

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

        def _is_peer_authed(self) -> bool:
            client_ip = self.client_address[0] if self.client_address else ""
            return verify_peer_request(self.headers, client_ip)

        def _is_authenticated(self) -> bool:
            return config_store.verify_session_token(self._get_session_token()) or self._is_peer_authed()

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

        def _sync_bound_port(self):
            try:
                srv_port = int(self.server.server_address[1])
                if srv_port > 0:
                    fleet_manager.port = srv_port
            except Exception:
                pass

        def do_GET(self):
            self._sync_bound_port()
            if hasattr(app, "web_frame") and app.web_frame is not None:
                if app.web_frame.handle_http_get(self, is_dedicated_port=False):
                    return
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

            if parsed.path == "/api/fleet/ping":
                self._send_json(200, fleet_manager.build_ping_payload())
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

                qs = urllib.parse.parse_qs(parsed.query)
                target = (qs.get("target", [""])[0] or "").strip()
                trigger_scan = (qs.get("scan", ["0"])[0] == "1")

                # Avoid recursive fleet scan when another peer is querying our /api/status
                is_peer_call = self._is_peer_authed()
                fleet_nodes = [] if is_peer_call else fleet_manager.get_fleet_nodes(trigger_scan=trigger_scan)

                if target and not fleet_manager.is_self_target(target) and target != "all":
                    remote = fleet_manager.get_target_status(target)
                    if remote.get("ok"):
                        self._send_json(200, {
                            "authenticated": True,
                            "is_default_password": config_store.is_default_password(),
                            "language": cur_settings.get("language", "en"),
                            "target": target,
                            "target_edition": remote.get("edition", "gphotos"),
                            "target_platform": remote.get("platform", "linux"),
                            "status": remote["status"],
                            "settings": remote["settings"],
                            "log_tail": remote.get("log_tail", ""),
                            "fleet": fleet_nodes,
                        })
                        return

                st = app.get_status_snapshot()
                self._send_json(200, {
                    "authenticated": True,
                    "is_default_password": config_store.is_default_password(),
                    "language": cur_settings.get("language", "en"),
                    "target": "self",
                    "target_edition": st.get("edition", "gphotos"),
                    "target_platform": st.get("platform", "linux"),
                    "status": st,
                    "settings": cur_settings,
                    "log_tail": app.get_ram_log_tail(35),
                    "fleet": fleet_nodes,
                })
                return

            self._send_json(404, {"error": "Not found"})

        def do_POST(self):
            self._sync_bound_port()
            parsed = urllib.parse.urlparse(self.path)

            if parsed.path == "/api/update_upload":
                if not self._is_authenticated():
                    self._send_json(401, {"ok": False, "error": "Authentication required"})
                    return
                qs = urllib.parse.parse_qs(parsed.query)
                target = (qs.get("target", ["self"])[0] or "self").strip()
                length = int(self.headers.get("Content-Length", "0") or "0")
                if length <= 10240 or length > 160 * 1024 * 1024:
                    self._send_json(400, {"ok": False, "error": f"Invalid APK size ({length} bytes)"})
                    return
                apk_bytes = self.rfile.read(length)
                code, resp = fleet_manager.proxy_apk_upload(target, apk_bytes)
                self._send_json(code, resp)
                return

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

            if parsed.path == "/api/fleet/add_peer":
                host = str(data.get("host", "")).strip()
                ok, msg, node = fleet_manager.add_manual_peer(host)
                if ok:
                    self._send_json(200, {"ok": True, "message": msg, "node": node, "fleet": fleet_manager.get_fleet_nodes()})
                else:
                    self._send_json(400, {"ok": False, "error": msg})
                return

            if parsed.path == "/api/fleet/remove_peer":
                target = str(data.get("target", "")).strip()
                fleet_manager.remove_peer(target)
                self._send_json(200, {"ok": True, "fleet": fleet_manager.get_fleet_nodes()})
                return

            if parsed.path == "/api/fleet/sync_password":
                new_pw = str(data.get("new_password", ""))
                if len(new_pw) < 4:
                    self._send_json(400, {"ok": False, "error": "New password must be at least 4 characters"})
                    return
                config_store.change_password(new_pw)
                logger.info("🔐 Web portal password synced from LAN peer.")
                self._send_json(200, {"ok": True})
                return

            if parsed.path == "/api/password":
                target = str(data.get("target", "self")).strip()
                cur_pw = str(data.get("current_password", ""))
                new_pw = str(data.get("new_password", ""))
                code, resp, cookie = fleet_manager.execute_password(target, cur_pw, new_pw)
                self._send_json(code, resp, cookie_header=cookie)
                return

            if parsed.path == "/api/settings":
                target = str(data.get("target", "self")).strip()
                code, resp = fleet_manager.execute_settings(target, data)
                self._send_json(code, resp)
                return

            if parsed.path == "/api/action":
                target = str(data.get("target", "self")).strip()
                action = str(data.get("action", ""))
                album_arg = str(data.get("album", "")).strip()
                pwd_arg = str(data.get("pwd", "")).strip()
                code, resp = fleet_manager.execute_action(target, action, album_arg=album_arg, pwd_arg=pwd_arg)
                self._send_json(code, resp)
                return

            self._send_json(404, {"error": "Unknown endpoint"})

    return PortalHandler


def start_web_config_server(app: "SlideshowApp", config_store: FrameConfigStore, port: int = 8080):
    if port <= 0:
        return

    initial_host = getattr(app.args, "device_name", "") or MDNS_HOSTNAME
    start_mdns_responder(app._get_local_ip, hostname=initial_host)

    def _server_worker():
        ssl_ctx = create_portal_ssl_context()
        candidates = [port] + [p for p in (8080, 8081, 8082, 8088) if p != port]
        for cand_port in candidates:
            try:
                handler_cls = create_portal_handler(app, config_store, port=cand_port)
                srv = _ThreadingHTTPServer(("0.0.0.0", cand_port), handler_cls, ssl_context=ssl_ctx)
                try:
                    app.args.web_port = cand_port
                    app._cached_demo_pill = None
                    app.full_redraw_needed = True
                except Exception:
                    pass
                logger.info(
                    "🌐 Password-protected Web Control Portal listening on http://%s:%d (http://0.0.0.0:%d)",
                    get_active_mdns_hostname(),
                    cand_port,
                    cand_port,
                )
                srv.serve_forever()
                return
            except Exception as e:
                logger.warning("Could not bind Web Control Portal on port %d (%s); trying next candidate port...", cand_port, e)
        logger.error("Failed to start Web Control Portal on any candidate port %s", candidates)

    threading.Thread(target=_server_worker, name="WebConfigServerThread", daemon=True).start()
