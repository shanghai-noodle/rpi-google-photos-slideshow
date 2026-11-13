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
from .m3_assets import get_m3_asset
from .mdns import MDNS_HOSTNAME, get_active_mdns_hostname, start_mdns_responder

if TYPE_CHECKING:
    from .app import SlideshowApp

SUPPORTED_LANGUAGES = ("en", "zh", "ja", "ko", "es", "de")

WEB_PORTAL_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Photo Frame Control Portal</title>
<script>
(function() {
  var theme = 'dark';
  try {
    var saved = localStorage.getItem('frame_theme');
    if (saved === 'light' || saved === 'dark') {
      theme = saved;
    } else if (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches) {
      theme = 'light';
    }
  } catch (e) {}
  document.documentElement.setAttribute('data-theme', theme);
})();
</script>
<script type="module" src="/assets/m3/index.js"></script>
<style>
  :root, :root[data-theme="dark"] {
    color-scheme: dark;
    --md-sys-color-surface: #121214;
    --md-sys-color-surface-container-low: #242428;
    --md-sys-color-surface-container: #1C1C1E;
    --md-sys-color-surface-container-high: #2A2A2F;
    --md-sys-color-surface-container-highest: #323238;
    --md-sys-color-on-surface: #F2F2F7;
    --md-sys-color-on-surface-variant: #AEAEB6;
    --md-sys-color-primary: #1B65C6;
    --md-sys-color-on-primary: #FFFFFF;
    --md-sys-color-primary-container: #1E3452;
    --md-sys-color-on-primary-container: #D6E6FF;
    --md-sys-color-accent-text: #7AB0F5;
    --md-sys-color-secondary: #B0B5BD;
    --md-sys-color-secondary-container: #2E3136;
    --md-sys-color-on-secondary-container: #E5E7EB;
    --md-sys-color-outline: #6E6E78;
    --md-sys-color-outline-variant: #38383F;
    --md-sys-color-card-border: #38383F;
    --md-sys-color-subtle-border: #414149;
    --md-sys-color-control-border: #54545E;
    --md-sys-color-hover-outline: #70707C;
    --md-sys-color-outlined-border: #526070;
    --md-sys-color-tonal-border: #4B5058;
    --md-sys-color-error: #E07A76;
    --md-sys-color-on-error: #FFFFFF;
    --md-sys-color-error-container: #2E1E20;
    --md-sys-color-on-error-container: #E2A3A0;
    --md-sys-color-danger-border: #63393C;
    --md-sys-color-success: #52B788;
    --md-sys-color-success-container: #1D2B22;
    --md-sys-color-on-success-container: #9BD0AF;
    --md-sys-color-success-border: #3B5C47;
    --md-sys-color-warn-container: #2B251C;
    --md-sys-color-on-warn-container: #D8BF92;
    --md-sys-color-warn-border: #5C4B32;
    --md-sys-color-shadow: #000000;
    --md-sys-color-neutral-btn-bg: #2A2A2E;
    --md-sys-color-neutral-btn-hover: #343439;
    --md-sys-color-neutral-btn-active: #3D3D44;
    --md-sys-color-primary-hover: #2270D6;
    --md-sys-color-primary-active: #1656AA;
    --md-sys-color-tonal-hover: #383C42;
    --md-sys-color-tonal-active: #41464E;
    --md-sys-color-outlined-hover: rgba(122, 176, 245, 0.10);
    --md-sys-color-outlined-active: rgba(122, 176, 245, 0.16);
    --md-sys-color-success-hover: #25372C;
    --md-sys-color-warn-hover: #362E23;
    --md-sys-color-danger-hover: #3A2528;
    --md-sys-color-subtle-hover: rgba(242, 242, 247, 0.05);
    --md-switch-track-color: #2C2C32;
    --md-switch-hover-track-color: #34343B;
    --md-switch-handle-color: #B8B8C2;
    --md-switch-hover-handle-color: #D1D1DA;
    --md-switch-icon-color: #1C1C1E;
    --md-switch-hover-icon-color: #1C1C1E;
    --md-switch-selected-handle-color: #FFFFFF;
    --md-switch-selected-hover-handle-color: #F2F2F7;
    --md-switch-selected-focus-handle-color: #FFFFFF;
    --md-switch-selected-pressed-handle-color: #E5E7EB;
    --md-switch-selected-icon-color: #1B65C6;
    --md-switch-selected-hover-icon-color: #1B65C6;
    --md-switch-selected-focus-icon-color: #1B65C6;
    --md-switch-selected-pressed-icon-color: #1B65C6;
    --md-switch-hover-state-layer-opacity: 0.06;
    --md-switch-selected-hover-state-layer-opacity: 0.06;
    --card-shadow: 0 2px 10px rgba(0, 0, 0, 0.28);
    --focus-ring-halo: rgba(122, 176, 245, 0.22);
    --bg: var(--md-sys-color-surface);
    --card: var(--md-sys-color-surface-container);
    --card-border: var(--md-sys-color-card-border);
    --text: var(--md-sys-color-on-surface);
    --muted: var(--md-sys-color-on-surface-variant);
    --accent: var(--md-sys-color-accent-text);
    --input-bg: var(--md-sys-color-surface-container-low);
    --nav-bg: rgba(28, 28, 30, 0.94);
    --backdrop-bg: rgba(0, 0, 0, 0.68);
  }
  :root[data-theme="light"] {
    color-scheme: light;
    --md-sys-color-surface: #F2F2F7;
    --md-sys-color-surface-container-low: #F6F6FA;
    --md-sys-color-surface-container: #FFFFFF;
    --md-sys-color-surface-container-high: #EAEAEF;
    --md-sys-color-surface-container-highest: #DEDEE6;
    --md-sys-color-on-surface: #1D1D1F;
    --md-sys-color-on-surface-variant: #515158;
    --md-sys-color-primary: #0066CC;
    --md-sys-color-on-primary: #FFFFFF;
    --md-sys-color-primary-container: #D9EAFF;
    --md-sys-color-on-primary-container: #002B5C;
    --md-sys-color-accent-text: #0066CC;
    --md-sys-color-secondary: #4F606F;
    --md-sys-color-secondary-container: #E8ECF2;
    --md-sys-color-on-secondary-container: #1D242B;
    --md-sys-color-outline: #74747E;
    --md-sys-color-outline-variant: #BDBDC7;
    --md-sys-color-card-border: #BDBDC7;
    --md-sys-color-subtle-border: #AEAEB8;
    --md-sys-color-control-border: #8A8A94;
    --md-sys-color-hover-outline: #5E5E68;
    --md-sys-color-outlined-border: #6E8299;
    --md-sys-color-tonal-border: #A0AAB8;
    --md-sys-color-error: #C62828;
    --md-sys-color-on-error: #FFFFFF;
    --md-sys-color-error-container: #FDF0EF;
    --md-sys-color-on-error-container: #A61E18;
    --md-sys-color-danger-border: #D98782;
    --md-sys-color-success: #18794E;
    --md-sys-color-success-container: #EAF5EE;
    --md-sys-color-on-success-container: #155E34;
    --md-sys-color-success-border: #76B58E;
    --md-sys-color-warn-container: #FCF4E4;
    --md-sys-color-on-warn-container: #7A4806;
    --md-sys-color-warn-border: #C99B49;
    --md-sys-color-shadow: #000000;
    --md-sys-color-neutral-btn-bg: #F2F2F7;
    --md-sys-color-neutral-btn-hover: #E5E5EC;
    --md-sys-color-neutral-btn-active: #D8D8E2;
    --md-sys-color-primary-hover: #0055B3;
    --md-sys-color-primary-active: #004799;
    --md-sys-color-tonal-hover: #DCE2EC;
    --md-sys-color-tonal-active: #CFD7E4;
    --md-sys-color-outlined-hover: rgba(0, 102, 204, 0.07);
    --md-sys-color-outlined-active: rgba(0, 102, 204, 0.13);
    --md-sys-color-success-hover: #DCEEE2;
    --md-sys-color-warn-hover: #F6E8CC;
    --md-sys-color-danger-hover: #F9DFDD;
    --md-sys-color-subtle-hover: rgba(23, 28, 34, 0.05);
    --md-switch-track-color: #E2E2E8;
    --md-switch-hover-track-color: #D6D6DE;
    --md-switch-handle-color: #63636B;
    --md-switch-hover-handle-color: #48484F;
    --md-switch-icon-color: #FFFFFF;
    --md-switch-hover-icon-color: #FFFFFF;
    --md-switch-selected-handle-color: #FFFFFF;
    --md-switch-selected-hover-handle-color: #F2F2F7;
    --md-switch-selected-focus-handle-color: #FFFFFF;
    --md-switch-selected-pressed-handle-color: #E5E7EB;
    --md-switch-selected-icon-color: #0055B3;
    --md-switch-selected-hover-icon-color: #0055B3;
    --md-switch-selected-focus-icon-color: #0055B3;
    --md-switch-selected-pressed-icon-color: #0055B3;
    --md-switch-hover-state-layer-opacity: 0.06;
    --md-switch-selected-hover-state-layer-opacity: 0.06;
    --card-shadow: 0 1px 3px rgba(0, 0, 0, 0.06), 0 4px 12px rgba(0, 0, 0, 0.03);
    --focus-ring-halo: rgba(0, 102, 204, 0.18);
    --bg: var(--md-sys-color-surface);
    --card: var(--md-sys-color-surface-container);
    --card-border: var(--md-sys-color-card-border);
    --text: var(--md-sys-color-on-surface);
    --muted: var(--md-sys-color-on-surface-variant);
    --accent: var(--md-sys-color-accent-text);
    --input-bg: var(--md-sys-color-surface-container-low);
    --nav-bg: rgba(255, 255, 255, 0.95);
    --backdrop-bg: rgba(15, 23, 42, 0.45);
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html { -webkit-text-size-adjust: 100%; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    background: var(--md-sys-color-surface);
    color: var(--md-sys-color-on-surface);
    line-height: 1.5;
    padding: 16px 14px 48px;
    transition: background-color 0.18s ease, color 0.18s ease;
    -webkit-font-smoothing: antialiased;
  }
  .skip-link {
    position: absolute; left: -9999px; top: 8px; z-index: 10000;
    background: var(--md-sys-color-primary); color: var(--md-sys-color-on-primary);
    padding: 12px 18px; border-radius: 999px; font-weight: 600; font-size: 0.9rem;
    text-decoration: none;
  }
  .skip-link:focus { left: 16px; outline: 3px solid var(--md-sys-color-on-surface); outline-offset: 2px; }
  :focus-visible {
    outline: 2.5px solid var(--md-sys-color-accent-text);
    outline-offset: 2px;
  }
  .m3-icon { width: 19px; height: 19px; flex-shrink: 0; display: inline-block; }
  .m3-icon-brand { width: 24px; height: 24px; color: var(--md-sys-color-accent-text); }
  .m3-icon-sm { width: 15px; height: 15px; flex-shrink: 0; color: var(--md-sys-color-on-surface-variant); }
  .container { max-width: 820px; margin: 0 auto; }
  .top-app-bar {
    display: flex; align-items: center; justify-content: space-between;
    flex-wrap: wrap; gap: 12px; margin-bottom: 16px;
    padding: 8px 4px 14px;
    border-bottom: 1.5px solid var(--md-sys-color-card-border);
  }
  .brand-cluster { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; min-width: 0; }
  .brand-cluster h1 {
    font-size: 1.25rem; font-weight: 700; letter-spacing: -0.015em;
    color: var(--md-sys-color-on-surface);
  }
  .header-actions {
    display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  }
  .theme-switch-wrap {
    display: inline-flex; align-items: center; gap: 8px;
    min-height: 48px; padding: 4px 8px; border-radius: 999px;
    cursor: pointer; user-select: none;
  }
  md-switch {
    --md-switch-track-height: 32px;
    --md-switch-track-width: 52px;
  }
  md-switch:not(:defined) {
    position: relative; display: inline-flex; align-items: center;
    width: 52px; height: 32px; border-radius: 999px; flex-shrink: 0;
    background: var(--md-switch-track-color);
    border: 2px solid var(--md-sys-color-outline);
    cursor: pointer; transition: background-color 0.16s ease, border-color 0.16s ease;
  }
  md-switch:not(:defined)::after {
    content: ""; position: absolute; left: 5px; width: 18px; height: 18px;
    border-radius: 50%; background: var(--md-switch-handle-color);
    transition: transform 0.16s ease, background-color 0.16s ease;
  }
  md-switch:not(:defined)[aria-checked="true"],
  md-switch:not(:defined)[selected] {
    background: var(--md-sys-color-primary);
    border-color: var(--md-sys-color-primary);
  }
  md-switch:not(:defined)[aria-checked="true"]::after,
  md-switch:not(:defined)[selected]::after {
    transform: translateX(20px); width: 22px; height: 22px; left: 3px;
    background: #FFFFFF;
  }
  md-switch [slot="on-icon"], md-switch [slot="off-icon"] { width: 16px; height: 16px; }
  md-switch:not(:defined) [slot] { display: none; }

  /* Desktop Material 3 Top Tabs */
  .desktop-tabs-wrap {
    margin-bottom: 18px;
    border-bottom: 1.5px solid var(--md-sys-color-card-border);
  }
  md-tabs {
    --md-primary-tab-container-color: transparent;
    --md-primary-tab-active-indicator-color: var(--md-sys-color-accent-text);
    --md-primary-tab-active-label-text-color: var(--md-sys-color-accent-text);
    --md-primary-tab-active-hover-label-text-color: var(--md-sys-color-accent-text);
    --md-primary-tab-active-focus-label-text-color: var(--md-sys-color-accent-text);
    --md-primary-tab-inactive-label-text-color: var(--md-sys-color-on-surface-variant);
    --md-primary-tab-inactive-hover-label-text-color: var(--md-sys-color-on-surface);
    --md-primary-tab-active-icon-color: var(--md-sys-color-accent-text);
    --md-primary-tab-inactive-icon-color: var(--md-sys-color-on-surface-variant);
    --md-primary-tab-hover-state-layer-color: var(--md-sys-color-on-surface);
    --md-primary-tab-hover-state-layer-opacity: 0.06;
    --md-primary-tab-active-hover-state-layer-color: var(--md-sys-color-accent-text);
    --md-primary-tab-active-hover-state-layer-opacity: 0.06;
    width: 100%;
  }
  md-tabs:not(:defined) {
    display: grid; grid-template-columns: repeat(3, 1fr); gap: 4px;
  }
  md-primary-tab:not(:defined) {
    display: inline-flex; align-items: center; justify-content: center; gap: 8px;
    min-height: 48px; padding: 10px 14px; cursor: pointer;
    font-size: 0.9rem; font-weight: 600; color: var(--md-sys-color-on-surface-variant);
    border-bottom: 3px solid transparent; user-select: none;
  }
  md-primary-tab:not(:defined)[active] {
    color: var(--md-sys-color-accent-text);
    border-bottom-color: var(--md-sys-color-accent-text);
  }
  .tab-inline-icon { width: 18px; height: 18px; display: inline-flex; align-items: center; }

  /* Mobile Material 3 Bottom Navigation Bar */
  .m3-nav-bar { display: none; }

  /* Cards & Surfaces */
  .m3-card {
    background: var(--md-sys-color-surface-container);
    border: 1.5px solid var(--md-sys-color-card-border);
    box-shadow: var(--card-shadow);
    border-radius: 20px; padding: 20px; margin-bottom: 16px;
  }
  .card-header {
    display: flex; align-items: center; justify-content: space-between;
    flex-wrap: wrap; gap: 10px; margin-bottom: 16px;
  }
  .m3-card h2 {
    font-size: 1.05rem; font-weight: 600; color: var(--md-sys-color-on-surface);
    display: flex; align-items: center; gap: 8px; letter-spacing: -0.01em;
  }
  .card-sub-desc {
    font-size: 0.86rem; color: var(--md-sys-color-on-surface-variant);
    margin-bottom: 14px; line-height: 1.5;
  }

  /* Status Badges */
  .badge {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 5px 12px; border-radius: 999px; font-size: 0.78rem; font-weight: 600;
    background: var(--md-sys-color-surface-container-high);
    color: var(--md-sys-color-on-surface-variant);
    border: 1.5px solid var(--md-sys-color-subtle-border);
  }
  .status-dot {
    width: 8px; height: 8px; border-radius: 50%; background: currentColor; flex-shrink: 0;
  }
  .badge.active {
    background: var(--md-sys-color-success-container);
    color: var(--md-sys-color-on-success-container);
    border-color: var(--md-sys-color-success-border);
  }
  .badge.night {
    background: var(--md-sys-color-warn-container);
    color: var(--md-sys-color-on-warn-container);
    border-color: var(--md-sys-color-warn-border);
  }
  .badge.offline {
    background: var(--md-sys-color-error-container);
    color: var(--md-sys-color-on-error-container);
    border-color: var(--md-sys-color-danger-border);
  }
  .badge.host-badge {
    background: var(--md-sys-color-secondary-container);
    color: var(--md-sys-color-on-secondary-container);
    border-color: var(--md-sys-color-tonal-border);
  }
  .badge.ver-badge {
    background: var(--md-sys-color-surface-container-high);
    color: var(--md-sys-color-on-surface-variant);
  }

  /* M3 Buttons (custom properties prevent :hover specificity collisions) */
  button, .m3-btn {
    --btn-bg: var(--md-sys-color-neutral-btn-bg);
    --btn-fg: var(--md-sys-color-on-surface);
    --btn-border: var(--md-sys-color-control-border);
    --btn-hover-bg: var(--md-sys-color-neutral-btn-hover);
    --btn-hover-fg: var(--btn-fg);
    --btn-hover-border: var(--md-sys-color-hover-outline);
    --btn-active-bg: var(--md-sys-color-neutral-btn-active);
    appearance: none; font-family: inherit;
    border: 1.5px solid var(--btn-border);
    background: var(--btn-bg);
    color: var(--btn-fg);
    font-weight: 600; font-size: 0.88rem;
    min-height: 44px; padding: 10px 18px; border-radius: 999px;
    cursor: pointer; transition: background-color 0.15s ease, border-color 0.15s ease, color 0.15s ease;
    display: inline-flex; align-items: center; justify-content: center; gap: 8px;
    text-decoration: none;
  }
  button .m3-icon-sm, .m3-btn .m3-icon-sm { color: currentColor; }
  button:hover:not(:disabled), .m3-btn:hover:not(:disabled) {
    background: var(--btn-hover-bg);
    color: var(--btn-hover-fg);
    border-color: var(--btn-hover-border);
  }
  button:active:not(:disabled), .m3-btn:active:not(:disabled) {
    background: var(--btn-active-bg);
  }
  button:disabled { opacity: 0.55; cursor: not-allowed; }
  button.primary, .m3-btn.primary {
    --btn-bg: var(--md-sys-color-primary);
    --btn-fg: var(--md-sys-color-on-primary);
    --btn-border: var(--md-sys-color-primary);
    --btn-hover-bg: var(--md-sys-color-primary-hover);
    --btn-hover-fg: var(--md-sys-color-on-primary);
    --btn-hover-border: var(--md-sys-color-primary-hover);
    --btn-active-bg: var(--md-sys-color-primary-active);
  }
  button.tonal, .m3-btn.tonal {
    --btn-bg: var(--md-sys-color-secondary-container);
    --btn-fg: var(--md-sys-color-on-secondary-container);
    --btn-border: var(--md-sys-color-tonal-border);
    --btn-hover-bg: var(--md-sys-color-tonal-hover);
    --btn-hover-fg: var(--md-sys-color-on-secondary-container);
    --btn-hover-border: var(--md-sys-color-hover-outline);
    --btn-active-bg: var(--md-sys-color-tonal-active);
  }
  button.outlined, .m3-btn.outlined {
    --btn-bg: transparent;
    --btn-fg: var(--md-sys-color-accent-text);
    --btn-border: var(--md-sys-color-outlined-border);
    --btn-hover-bg: var(--md-sys-color-outlined-hover);
    --btn-hover-fg: var(--md-sys-color-accent-text);
    --btn-hover-border: var(--md-sys-color-accent-text);
    --btn-active-bg: var(--md-sys-color-outlined-active);
  }
  button.success {
    --btn-bg: var(--md-sys-color-success-container);
    --btn-fg: var(--md-sys-color-on-success-container);
    --btn-border: var(--md-sys-color-success-border);
    --btn-hover-bg: var(--md-sys-color-success-hover);
    --btn-hover-fg: var(--md-sys-color-on-success-container);
    --btn-hover-border: var(--md-sys-color-success-border);
    --btn-active-bg: var(--md-sys-color-success-hover);
  }
  button.warn {
    --btn-bg: var(--md-sys-color-warn-container);
    --btn-fg: var(--md-sys-color-on-warn-container);
    --btn-border: var(--md-sys-color-warn-border);
    --btn-hover-bg: var(--md-sys-color-warn-hover);
    --btn-hover-fg: var(--md-sys-color-on-warn-container);
    --btn-hover-border: var(--md-sys-color-warn-border);
    --btn-active-bg: var(--md-sys-color-warn-hover);
  }
  button.danger {
    --btn-bg: var(--md-sys-color-error-container);
    --btn-fg: var(--md-sys-color-on-error-container);
    --btn-border: var(--md-sys-color-danger-border);
    --btn-hover-bg: var(--md-sys-color-danger-hover);
    --btn-hover-fg: var(--md-sys-color-on-error-container);
    --btn-hover-border: var(--md-sys-color-danger-border);
    --btn-active-bg: var(--md-sys-color-danger-hover);
  }
  button.btn-sm { min-height: 40px; padding: 6px 14px; font-size: 0.82rem; }
  button.m3-chip, .m3-chip {
    --btn-bg: var(--md-sys-color-surface-container-low);
    --btn-fg: var(--md-sys-color-on-surface);
    --btn-border: var(--md-sys-color-control-border);
    --btn-hover-bg: var(--md-sys-color-neutral-btn-bg);
    --btn-hover-fg: var(--md-sys-color-on-surface);
    --btn-hover-border: var(--md-sys-color-hover-outline);
    --btn-active-bg: var(--md-sys-color-neutral-btn-hover);
    min-height: 40px; padding: 6px 14px; border-radius: 10px;
    font-size: 0.82rem; gap: 6px;
  }
  button.m3-chip[aria-expanded="true"], .m3-chip[aria-expanded="true"] {
    --btn-bg: var(--md-sys-color-secondary-container);
    --btn-fg: var(--md-sys-color-on-secondary-container);
    --btn-border: var(--md-sys-color-tonal-border);
    --btn-hover-bg: var(--md-sys-color-tonal-hover);
    --btn-hover-fg: var(--md-sys-color-on-secondary-container);
    --btn-hover-border: var(--md-sys-color-hover-outline);
    --btn-active-bg: var(--md-sys-color-tonal-active);
  }

  /* Stats & Transport Grids */
  .stats-grid {
    display: grid; grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 10px; margin-bottom: 16px;
  }
  .stat-box {
    background: var(--md-sys-color-surface-container-low);
    border-radius: 14px; padding: 12px 14px;
    border: 1.5px solid var(--md-sys-color-subtle-border);
    min-width: 0;
  }
  .stat-label {
    font-size: 0.72rem; font-weight: 600; color: var(--md-sys-color-on-surface-variant);
    text-transform: uppercase; letter-spacing: 0.04em;
  }
  .stat-val {
    font-size: 0.92rem; font-weight: 600; margin-top: 3px;
    color: var(--md-sys-color-on-surface);
    word-break: break-word;
  }
  .transport-row {
    display: grid; grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 10px; margin-bottom: 10px;
  }
  .btn-grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    gap: 10px;
  }

  /* Forms & Outlined Inputs */
  .form-grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
    gap: 14px;
  }
  .form-group { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
  .form-group.full { grid-column: 1 / -1; }
  label {
    font-size: 0.84rem; font-weight: 600; color: var(--md-sys-color-on-surface-variant);
  }
  input[type="text"], input[type="password"], input[type="number"], input[type="time"], select {
    width: 100%; min-height: 46px; padding: 10px 14px; border-radius: 12px;
    border: 1.5px solid var(--md-sys-color-control-border);
    background: var(--md-sys-color-surface-container-low);
    color: var(--md-sys-color-on-surface);
    font-size: 0.94rem; font-family: inherit;
    transition: border-color 0.15s ease, background-color 0.15s ease, box-shadow 0.15s ease;
  }
  input:hover:not(:focus):not(:disabled), select:hover:not(:focus):not(:disabled) {
    border-color: var(--md-sys-color-hover-outline);
  }
  input:focus, select:focus {
    outline: none;
    border-color: var(--md-sys-color-accent-text);
    box-shadow: 0 0 0 3px var(--focus-ring-halo);
  }
  .switch-field-row {
    display: flex; align-items: center; justify-content: space-between; gap: 14px;
    min-height: 52px; padding: 10px 14px; border-radius: 14px;
    background: var(--md-sys-color-surface-container-low);
    border: 1.5px solid var(--md-sys-color-subtle-border);
  }
  .switch-field-row label {
    color: var(--md-sys-color-on-surface); font-size: 0.9rem; font-weight: 600;
    cursor: pointer; flex: 1;
  }
  .m3-toggle {
    appearance: none; -webkit-appearance: none; position: relative;
    width: 52px; height: 32px; border-radius: 999px; flex-shrink: 0;
    background: var(--md-switch-track-color);
    border: 2px solid var(--md-sys-color-outline);
    cursor: pointer; transition: background-color 0.16s ease, border-color 0.16s ease;
  }
  .m3-toggle::after {
    content: ""; position: absolute; top: 5px; left: 5px;
    width: 18px; height: 18px; border-radius: 50%;
    background: var(--md-switch-handle-color);
    transition: transform 0.16s ease, background-color 0.16s ease, width 0.16s ease, height 0.16s ease;
  }
  .m3-toggle:checked {
    background: var(--md-sys-color-primary);
    border-color: var(--md-sys-color-primary);
  }
  .m3-toggle:checked::after {
    top: 3px; left: 3px; width: 22px; height: 22px;
    transform: translateX(20px);
    background: #FFFFFF;
  }

  /* Collapsible M3 Disclosure */
  details.m3-disclosure {
    margin-top: 16px;
    border: 1.5px solid var(--md-sys-color-subtle-border);
    border-radius: 16px;
    background: var(--md-sys-color-surface-container-low);
    overflow: hidden;
  }
  details.m3-disclosure > summary {
    list-style: none; display: flex; align-items: center; justify-content: space-between;
    gap: 10px; min-height: 52px; padding: 12px 16px;
    cursor: pointer; font-weight: 600; font-size: 0.92rem;
    color: var(--md-sys-color-on-surface); user-select: none;
  }
  details.m3-disclosure > summary::-webkit-details-marker { display: none; }
  details.m3-disclosure[open] > summary {
    border-bottom: 1.5px solid var(--md-sys-color-subtle-border);
  }
  details.m3-disclosure > summary:hover {
    background: var(--md-sys-color-subtle-hover);
  }
  details.m3-disclosure > summary .m3-chevron {
    width: 22px; height: 22px; transition: transform 0.2s ease;
  }
  details.m3-disclosure[open] > summary .m3-chevron { transform: rotate(180deg); }
  .disclosure-body { padding: 16px; }

  /* Fleet & Saved Albums */
  .fleet-grid {
    display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 10px; margin-top: 10px;
  }
  .fleet-pill {
    background: var(--md-sys-color-surface-container-low);
    border: 1.5px solid var(--md-sys-color-subtle-border);
    border-radius: 14px; padding: 12px 14px; cursor: pointer;
    transition: border-color 0.15s ease, background-color 0.15s ease;
    display: flex; flex-direction: column; gap: 4px; text-align: left;
  }
  .fleet-pill:hover {
    border-color: var(--md-sys-color-hover-outline);
    background: var(--md-sys-color-subtle-hover);
  }
  .fleet-pill.selected {
    border-color: var(--md-sys-color-accent-text);
    background: var(--md-sys-color-secondary-container);
  }
  .fleet-pill.selected:hover {
    border-color: var(--md-sys-color-accent-text);
    background: var(--md-sys-color-tonal-hover);
  }
  .fleet-pill-top {
    display: flex; align-items: center; justify-content: space-between;
    font-weight: 700; font-size: 0.9rem; color: var(--md-sys-color-on-surface); gap: 6px;
  }
  .fleet-pill-meta { font-size: 0.76rem; color: var(--md-sys-color-accent-text); font-weight: 600; }
  .fleet-pill-sub { font-size: 0.76rem; color: var(--md-sys-color-on-surface-variant); }
  button.icon-remove-btn, .icon-remove-btn {
    --btn-bg: transparent;
    --btn-fg: var(--md-sys-color-on-surface-variant);
    --btn-border: transparent;
    --btn-hover-bg: var(--md-sys-color-error-container);
    --btn-hover-fg: var(--md-sys-color-on-error-container);
    --btn-hover-border: var(--md-sys-color-danger-border);
    --btn-active-bg: var(--md-sys-color-danger-hover);
    min-width: 40px; min-height: 40px; padding: 8px;
  }
  .target-banner {
    margin-top: 12px; padding: 10px 14px; border-radius: 12px;
    font-size: 0.84rem; font-weight: 600;
    background: var(--md-sys-color-secondary-container);
    color: var(--md-sys-color-on-secondary-container);
    border: 1.5px solid var(--md-sys-color-tonal-border);
  }
  .saved-albums-grid {
    display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
    gap: 10px; margin-top: 4px;
  }
  .saved-album-pill {
    display: flex; align-items: center; justify-content: space-between; gap: 10px;
    background: var(--md-sys-color-surface-container-low);
    border: 1.5px solid var(--md-sys-color-subtle-border);
    border-radius: 14px; padding: 10px 12px; cursor: pointer;
    transition: border-color 0.15s ease, background-color 0.15s ease;
  }
  .saved-album-pill:hover {
    border-color: var(--md-sys-color-hover-outline);
    background: var(--md-sys-color-subtle-hover);
  }
  .saved-album-pill.active {
    border-color: var(--md-sys-color-success);
    background: var(--md-sys-color-surface-container-high);
  }
  .saved-album-info { display: flex; flex-direction: column; gap: 2px; min-width: 0; flex: 1; }
  .saved-album-title {
    display: flex; align-items: center; gap: 6px;
    font-weight: 600; font-size: 0.88rem; color: var(--md-sys-color-on-surface);
  }
  .saved-album-title-text { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .saved-album-meta {
    font-size: 0.75rem; color: var(--md-sys-color-on-surface-variant);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .saved-album-actions { display: flex; align-items: center; gap: 4px; flex-shrink: 0; }
  .saved-use-btn { min-height: 36px; padding: 6px 12px; font-size: 0.78rem; }

  /* Banners, Logs & Modals */
  .warn-banner {
    background: var(--md-sys-color-warn-container);
    color: var(--md-sys-color-on-warn-container);
    border: 1.5px solid var(--md-sys-color-warn-border);
    padding: 12px 16px; border-radius: 14px; font-size: 0.86rem;
    margin-bottom: 16px; cursor: pointer;
  }
  .confirm-banner {
    margin-top: 12px; padding: 12px 14px; border-radius: 12px;
    font-size: 0.88rem; font-weight: 600; display: none;
    background: var(--md-sys-color-success-container);
    color: var(--md-sys-color-on-success-container);
    border: 1.5px solid var(--md-sys-color-success-border);
  }
  .confirm-banner.error {
    background: var(--md-sys-color-error-container);
    color: var(--md-sys-color-on-error-container);
    border-color: var(--md-sys-color-danger-border);
  }
  .toast {
    position: fixed; top: 18px; left: 50%; transform: translateX(-50%); z-index: 9999;
    padding: 12px 22px; border-radius: 999px; font-weight: 600; font-size: 0.9rem;
    background: var(--md-sys-color-surface-container-highest);
    color: var(--md-sys-color-on-surface);
    border: 1.5px solid var(--md-sys-color-success);
    box-shadow: 0 10px 28px rgba(0, 0, 0, 0.35);
    display: none; text-align: center; min-width: 240px; max-width: 92vw;
  }
  pre.log-box {
    background: var(--md-sys-color-surface-container-low);
    border: 1.5px solid var(--md-sys-color-subtle-border);
    border-radius: 14px; padding: 14px; font-size: 0.78rem;
    color: var(--md-sys-color-on-surface-variant);
    overflow-x: auto; max-height: 260px; white-space: pre-wrap;
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  }
  .webframe-url-pill {
    color: var(--md-sys-color-accent-text); font-size: 0.86rem; font-weight: 600;
    text-decoration: none; background: var(--md-sys-color-surface-container-low);
    padding: 10px 14px; border-radius: 12px;
    border: 1.5px solid var(--md-sys-color-control-border);
    display: inline-block; word-break: break-all;
    transition: border-color 0.15s ease, background-color 0.15s ease;
  }
  a.webframe-url-pill:hover {
    border-color: var(--md-sys-color-hover-outline);
    background: var(--md-sys-color-subtle-hover);
  }
  .modal-backdrop {
    position: fixed; inset: 0; z-index: 9500;
    background: var(--backdrop-bg); backdrop-filter: blur(4px);
    display: flex; align-items: center; justify-content: center; padding: 18px;
  }
  .modal-card {
    width: 100%; max-width: 440px;
    background: var(--md-sys-color-surface-container);
    border: 1.5px solid var(--md-sys-color-card-border);
    border-radius: 24px; padding: 24px;
    box-shadow: 0 20px 48px rgba(0, 0, 0, 0.45);
  }
  .modal-card h3 {
    font-size: 1.12rem; font-weight: 700; color: var(--md-sys-color-on-surface);
    margin-bottom: 10px; display: flex; align-items: center; gap: 8px;
  }
  .modal-card p {
    font-size: 0.92rem; color: var(--md-sys-color-on-surface-variant);
    line-height: 1.55; margin-bottom: 20px;
  }
  .modal-actions { display: flex; justify-content: flex-end; gap: 10px; flex-wrap: wrap; }
  .sys-meta-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 14px; }
  #login-view { max-width: 420px; margin: 48px auto; }
  .hidden { display: none !important; }

  /* Mobile Ergonomics (<= 680px) */
  @media (max-width: 680px) {
    body {
      padding: 12px 12px calc(92px + env(safe-area-inset-bottom, 0px));
    }
    .top-app-bar { margin-bottom: 12px; padding: 4px 2px 12px; gap: 10px; }
    .brand-cluster h1 { font-size: 1.12rem; }
    .desktop-tabs-wrap { display: none; }
    .m3-nav-bar {
      position: fixed; left: 0; right: 0; bottom: 0; z-index: 8000;
      display: grid; grid-template-columns: repeat(3, 1fr);
      height: calc(74px + env(safe-area-inset-bottom, 0px));
      padding: 6px 8px env(safe-area-inset-bottom, 0px);
      background: var(--nav-bg); backdrop-filter: blur(12px);
      border-top: 1.5px solid var(--md-sys-color-card-border);
    }
    button.m3-nav-tab, .m3-nav-tab {
      --btn-bg: transparent;
      --btn-fg: var(--md-sys-color-on-surface-variant);
      --btn-border: transparent;
      --btn-hover-bg: transparent;
      --btn-hover-fg: var(--md-sys-color-on-surface);
      --btn-hover-border: transparent;
      --btn-active-bg: transparent;
      border-radius: 14px;
      padding: 4px 2px; min-height: 56px;
      display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 4px;
      font-size: 0.75rem; font-weight: 600;
    }
    .m3-nav-tab:hover:not([aria-selected="true"]) .m3-nav-pill {
      background: var(--md-sys-color-subtle-hover);
    }
    .m3-nav-pill {
      width: 64px; height: 32px; border-radius: 999px;
      display: inline-flex; align-items: center; justify-content: center;
      transition: background-color 0.16s ease, color 0.16s ease;
    }
    .m3-nav-tab[aria-selected="true"] { color: var(--md-sys-color-on-surface); }
    .m3-nav-tab[aria-selected="true"] .m3-nav-pill {
      background: var(--md-sys-color-secondary-container);
      color: var(--md-sys-color-on-secondary-container);
    }
    .stats-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .form-grid { grid-template-columns: 1fr; }
    input[type="text"], input[type="password"], input[type="number"], input[type="time"], select {
      font-size: 16px; min-height: 48px;
    }
    button, .m3-btn { min-height: 48px; }
    .icon-remove-btn { min-width: 48px; min-height: 48px; }
    .m3-card { padding: 16px; border-radius: 18px; }
  }
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after { transition: none !important; animation: none !important; }
  }
</style>
</head>
<body>
<a href="#main-content" class="skip-link" data-i18n="skip_to_main">Skip to main content</a>
<div class="container">
  <!-- LOGIN VIEW -->
  <div id="login-view" class="m3-card hidden">
    <div class="card-header">
      <h2><svg class="m3-icon" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path fill="currentColor" d="M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zm-6 9c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm3.1-9H8.9V6c0-1.71 1.39-3.1 3.1-3.1s3.1 1.39 3.1 3.1v2z"/></svg><span data-i18n="login_title">Photo Frame Sign In</span></h2>
      <div style="display:flex; align-items:center; gap:6px;">
        <label class="theme-switch-wrap" for="login-theme-switch" title="Toggle Dark Theme" data-i18n-title="title_toggle_theme">
          <svg class="m3-icon-sm" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path fill="currentColor" d="M6.76 4.84l-1.8-1.79-1.41 1.41 1.79 1.79 1.42-1.41zM4 10.5H1v2h3v-2zm9-9.95h-2V3.5h2V.55zm7.45 3.91l-1.41-1.41-1.79 1.79 1.41 1.41 1.79-1.79zm-3.21 13.7l1.79 1.8 1.41-1.41-1.8-1.79-1.4 1.4zM20 10.5v2h3v-2h-3zm-8-5c-3.31 0-6 2.69-6 6s2.69 6 6 6 6-2.69 6-6-2.69-6-6-6zm-1 16.95h2V19.5h-2v2.95zm-7.45-3.91l1.41 1.41 1.79-1.8-1.41-1.41-1.79 1.8z"/></svg>
          <md-switch id="login-theme-switch" icons role="switch" aria-checked="true" aria-label="Dark theme" onchange="onThemeSwitchChange(this)" onclick="onThemeSwitchFallbackClick(event, this)" onkeydown="onThemeSwitchFallbackKey(event, this)">
            <svg class="m3-switch-icon" aria-hidden="true" focusable="false" viewBox="0 0 24 24" slot="off-icon"><path fill="currentColor" d="M6.76 4.84l-1.8-1.79-1.41 1.41 1.79 1.79 1.42-1.41zM4 10.5H1v2h3v-2zm9-9.95h-2V3.5h2V.55zm7.45 3.91l-1.41-1.41-1.79 1.79 1.41 1.41 1.79-1.79zm-3.21 13.7l1.79 1.8 1.41-1.41-1.8-1.79-1.4 1.4zM20 10.5v2h3v-2h-3zm-8-5c-3.31 0-6 2.69-6 6s2.69 6 6 6 6-2.69 6-6-2.69-6-6-6zm-1 16.95h2V19.5h-2v2.95zm-7.45-3.91l1.41 1.41 1.79-1.8-1.41-1.41-1.79 1.8z"/></svg>
            <svg class="m3-switch-icon" aria-hidden="true" focusable="false" viewBox="0 0 24 24" slot="on-icon"><path fill="currentColor" d="M12 3a9 9 0 1 0 9 9c0-.46-.04-.92-.1-1.36a5.389 5.389 0 0 1-4.4 2.26 5.403 5.403 0 0 1-3.14-9.8c-.44-.06-.9-.1-1.36-.1z"/></svg>
          </md-switch>
          <svg class="m3-icon-sm" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path fill="currentColor" d="M12 3a9 9 0 1 0 9 9c0-.46-.04-.92-.1-1.36a5.389 5.389 0 0 1-4.4 2.26 5.403 5.403 0 0 1-3.14-9.8c-.44-.06-.9-.1-1.36-.1z"/></svg>
        </label>
        <select id="login-lang-select" aria-label="Language" onchange="setLanguage(this.value, true, true)" style="width:auto; min-height:42px; padding:6px 10px; font-size:0.84rem;">
          <option value="en">English</option>
          <option value="zh">中文</option>
          <option value="ja">日本語</option>
          <option value="ko">한국어</option>
          <option value="es">Español</option>
          <option value="de">Deutsch</option>
        </select>
      </div>
    </div>
    <div id="default-pw-hint" class="warn-banner hidden" data-i18n-html="default_pw_hint">
      Default password is <b>admin</b>. You can change it after signing in.
    </div>
    <form id="login-form" onsubmit="handleLogin(event)">
      <div class="form-group" style="margin-bottom: 16px;">
        <label for="login-password" data-i18n="lbl_password">Password</label>
        <input type="password" id="login-password" data-i18n-placeholder="ph_password" placeholder="Enter password" required autocomplete="current-password">
      </div>
      <button type="submit" class="primary" style="width: 100%;" data-i18n="btn_signin">Sign In</button>
    </form>
  </div>

  <!-- MAIN DASHBOARD VIEW -->
  <div id="app-view" class="hidden">
    <header class="top-app-bar" role="banner">
      <div class="brand-cluster">
        <svg class="m3-icon m3-icon-brand" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path fill="currentColor" d="M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm0 16H5V5h14v14zm-5.04-6.71-2.75 3.54-1.96-2.36L6.5 17h11l-3.54-4.71z"/></svg>
        <h1 data-i18n="portal_title">Photo Frame Portal</h1>
        <span id="state-badge" class="badge" role="status" aria-live="polite"><span class="status-dot" aria-hidden="true"></span><span class="badge-text">Loading...</span></span>
      </div>
      <div class="header-actions">
        <button type="button" id="header-fleet-chip" class="m3-chip" aria-expanded="false" aria-controls="fleet-card" onclick="toggleFleetDrawer()">
          <svg class="m3-icon" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path fill="currentColor" d="M4 6h18V4H4c-1.1 0-2 .9-2 2v11H0v3h14v-3H4V6zm19 2h-6c-.55 0-1 .45-1 1v10c0 .55.45 1 1 1h6c.55 0 1-.45 1-1V9c0-.55-.45-1-1-1zm-1 9h-4v-7h4v7z"/></svg>
          <span id="header-fleet-chip-label" data-i18n="chip_fleet_single">LAN Frames</span>
        </button>
        <label class="theme-switch-wrap" for="header-theme-switch" title="Toggle Dark Theme" data-i18n-title="title_toggle_theme">
          <svg class="m3-icon-sm" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path fill="currentColor" d="M6.76 4.84l-1.8-1.79-1.41 1.41 1.79 1.79 1.42-1.41zM4 10.5H1v2h3v-2zm9-9.95h-2V3.5h2V.55zm7.45 3.91l-1.41-1.41-1.79 1.79 1.41 1.41 1.79-1.79zm-3.21 13.7l1.79 1.8 1.41-1.41-1.8-1.79-1.4 1.4zM20 10.5v2h3v-2h-3zm-8-5c-3.31 0-6 2.69-6 6s2.69 6 6 6 6-2.69 6-6-2.69-6-6-6zm-1 16.95h2V19.5h-2v2.95zm-7.45-3.91l1.41 1.41 1.79-1.8-1.41-1.41-1.79 1.8z"/></svg>
          <md-switch id="header-theme-switch" icons role="switch" aria-checked="true" aria-label="Dark theme" onchange="onThemeSwitchChange(this)" onclick="onThemeSwitchFallbackClick(event, this)" onkeydown="onThemeSwitchFallbackKey(event, this)">
            <svg class="m3-switch-icon" aria-hidden="true" focusable="false" viewBox="0 0 24 24" slot="off-icon"><path fill="currentColor" d="M6.76 4.84l-1.8-1.79-1.41 1.41 1.79 1.79 1.42-1.41zM4 10.5H1v2h3v-2zm9-9.95h-2V3.5h2V.55zm7.45 3.91l-1.41-1.41-1.79 1.79 1.41 1.41 1.79-1.79zm-3.21 13.7l1.79 1.8 1.41-1.41-1.8-1.79-1.4 1.4zM20 10.5v2h3v-2h-3zm-8-5c-3.31 0-6 2.69-6 6s2.69 6 6 6 6-2.69 6-6-2.69-6-6-6zm-1 16.95h2V19.5h-2v2.95zm-7.45-3.91l1.41 1.41 1.79-1.8-1.41-1.41-1.79 1.8z"/></svg>
            <svg class="m3-switch-icon" aria-hidden="true" focusable="false" viewBox="0 0 24 24" slot="on-icon"><path fill="currentColor" d="M12 3a9 9 0 1 0 9 9c0-.46-.04-.92-.1-1.36a5.389 5.389 0 0 1-4.4 2.26 5.403 5.403 0 0 1-3.14-9.8c-.44-.06-.9-.1-1.36-.1z"/></svg>
          </md-switch>
          <svg class="m3-icon-sm" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path fill="currentColor" d="M12 3a9 9 0 1 0 9 9c0-.46-.04-.92-.1-1.36a5.389 5.389 0 0 1-4.4 2.26 5.403 5.403 0 0 1-3.14-9.8c-.44-.06-.9-.1-1.36-.1z"/></svg>
        </label>
        <select id="header-lang-select" aria-label="Language" onchange="setLanguage(this.value, true, true)" style="width:auto; min-height:42px; padding:6px 10px; font-size:0.84rem;">
          <option value="en">English</option>
          <option value="zh">中文</option>
          <option value="ja">日本語</option>
          <option value="ko">한국어</option>
          <option value="es">Español</option>
          <option value="de">Deutsch</option>
        </select>
        <button type="button" id="btn-signout" class="btn-sm outlined" onclick="handleLogout()">
          <svg class="m3-icon" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path fill="currentColor" d="M17 7l-1.41 1.41L18.17 11H8v2h10.17l-2.58 2.58L17 17l5-5zM4 5h8V3H4c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h8v-2H4V5z"/></svg><span class="btn-label" data-i18n="btn_signout">Sign Out</span>
        </button>
      </div>
    </header>

    <div id="default-pw-banner" class="warn-banner hidden" role="button" tabindex="0" onclick="openPasswordTab()" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();openPasswordTab();}" data-i18n-html="default_pw_banner">
      You are currently using the default password (<b>admin</b>). Open the <b>System</b> tab to set a custom password.
    </div>

    <!-- DESKTOP M3 TOP TABS -->
    <div class="desktop-tabs-wrap">
      <md-tabs id="desktop-m3-tabs" aria-label="Portal sections" onchange="onDesktopTabChange(this)">
        <md-primary-tab id="tab-control" active onclick="onDesktopTabFallback('control', 0)">
          <span class="tab-inline-icon" aria-hidden="true"><svg class="m3-icon" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path fill="currentColor" d="M15 7.5V2H9v5.5l3 3 3-3zM7.5 9H2v6h5.5l3-3-3-3zM9 16.5V22h6v-5.5l-3-3-3 3zM16.5 9l-3 3 3 3H22V9h-5.5z"/></svg></span>
          <span data-i18n="nav_control">Control</span>
        </md-primary-tab>
        <md-primary-tab id="tab-display" onclick="onDesktopTabFallback('display', 1)">
          <span class="tab-inline-icon" aria-hidden="true"><svg class="m3-icon" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path fill="currentColor" d="M3 17v2h6v-2H3zM3 5v2h10V5H3zm10 16v-2h8v-2h-8v-2h-2v6h2zM7 9v2H3v2h4v2h2V9H7zm14 4v-2H11v2h10zm-6-4h2V7h4V5h-4V3h-2v6z"/></svg></span>
          <span data-i18n="nav_display">Album &amp; Display</span>
        </md-primary-tab>
        <md-primary-tab id="tab-system" onclick="onDesktopTabFallback('system', 2)">
          <span class="tab-inline-icon" aria-hidden="true"><svg class="m3-icon" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path fill="currentColor" d="M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58a.49.49 0 0 0 .12-.61l-1.92-3.32a.488.488 0 0 0-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54a.484.484 0 0 0-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96c-.22-.08-.47 0-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.05.3-.09.63-.09.94s.02.64.07.94l-2.03 1.58a.49.49 0 0 0-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6c-1.98 0-3.6-1.62-3.6-3.6s1.62-3.6 3.6-3.6 3.6 1.62 3.6 3.6-1.62 3.6-3.6 3.6z"/></svg></span>
          <span data-i18n="nav_system">System</span>
        </md-primary-tab>
      </md-tabs>
    </div>

    <main id="main-content" tabindex="-1">
      <!-- COLLAPSIBLE / MULTI-FRAME LAN FLEET CARD -->
      <section class="m3-card hidden" id="fleet-card" aria-labelledby="sec-fleet-heading">
        <div class="card-header">
          <h2 id="sec-fleet-heading"><svg class="m3-icon" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path fill="currentColor" d="M4 6h18V4H4c-1.1 0-2 .9-2 2v11H0v3h14v-3H4V6zm19 2h-6c-.55 0-1 .45-1 1v10c0 .55.45 1 1 1h6c.55 0 1-.45 1-1V9c0-.55-.45-1-1-1zm-1 9h-4v-7h4v7z"/></svg><span data-i18n="sec_fleet">Connected Photo Frames (LAN Fleet)</span></h2>
          <div style="display:flex; gap:8px; flex-wrap:wrap;">
            <button type="button" onclick="scanFleetNodes()" id="btn-scan-fleet" class="btn-sm tonal">
              <svg class="m3-icon" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path fill="currentColor" d="M15.5 14h-.79l-.28-.27A6.471 6.471 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/></svg><span class="btn-label" data-i18n="btn_scan_fleet">Scan LAN</span>
            </button>
            <button type="button" onclick="toggleAddPeerRow()" id="btn-add-peer" class="btn-sm outlined">
              <svg class="m3-icon" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path fill="currentColor" d="M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z"/></svg><span class="btn-label" data-i18n="btn_add_peer">Add IP</span>
            </button>
          </div>
        </div>
        <div id="add-peer-row" class="hidden" style="display:flex; gap:8px; margin-bottom:12px; flex-wrap:wrap;">
          <input type="text" id="add-peer-input" style="flex:1; min-width:200px;" data-i18n-placeholder="ph_add_peer" placeholder="192.168.86.x or photoframe-2.local:8080">
          <button type="button" class="primary" onclick="addManualPeer()" data-i18n="btn_connect_peer">Connect Frame</button>
        </div>
        <div id="fleet-nodes-grid" class="fleet-grid"></div>
        <div id="active-target-banner" class="target-banner" role="status" aria-live="polite"></div>
      </section>

      <!-- TAB 1: CONTROL -->
      <section id="panel-control" role="tabpanel" aria-labelledby="tab-control">
        <div class="m3-card">
          <div class="card-header">
            <h2><svg class="m3-icon" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path fill="currentColor" d="M15 7.5V2H9v5.5l3 3 3-3zM7.5 9H2v6h5.5l3-3-3-3zM9 16.5V22h6v-5.5l-3-3-3 3zM16.5 9l-3 3 3 3H22V9h-5.5z"/></svg><span data-i18n="sec_remote">Quick Remote Controls</span></h2>
          </div>
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

          <div class="transport-row">
            <button type="button" onclick="sendAction('prev')">
              <svg class="m3-icon" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path fill="currentColor" d="M6 6h2v12H6zm3.5 6l8.5 6V6z"/></svg><span class="btn-label" data-i18n="btn_prev">Previous</span>
            </button>
            <button type="button" class="primary" onclick="sendAction('pause_toggle')" id="btn-pause">
              <svg class="m3-icon" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path fill="currentColor" d="M8 5v14l11-7z M6 19h4V5H6v14zm8-14v14h4V5h-4z"/></svg><span class="btn-label" data-i18n="btn_pause">Pause / Resume</span>
            </button>
            <button type="button" onclick="sendAction('next')">
              <svg class="m3-icon" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path fill="currentColor" d="M6 18l8.5-6L6 6v12zM16 6v12h2V6h-2z"/></svg><span class="btn-label" data-i18n="btn_next">Next Slide</span>
            </button>
          </div>
          <div class="btn-grid">
            <button type="button" id="btn-wake-sleep" class="warn" onclick="sendAction('toggle_night')">
              <svg class="m3-icon-sm" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path fill="currentColor" d="M12 3a9 9 0 1 0 9 9c0-.46-.04-.92-.1-1.36a5.389 5.389 0 0 1-4.4 2.26 5.403 5.403 0 0 1-3.14-9.8c-.44-.06-.9-.1-1.36-.1z"/></svg><span class="btn-label" data-i18n="btn_wake_sleep">Wake / Sleep</span>
            </button>
            <button type="button" class="tonal" onclick="sendAction('resync')">
              <svg class="m3-icon" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path fill="currentColor" d="M12 4V1L8 5l4 4V6c3.31 0 6 2.69 6 6 0 1.01-.25 1.97-.7 2.8l1.46 1.46C19.54 15.03 20 13.57 20 12c0-4.42-3.58-8-8-8zm0 14c-3.31 0-6-2.69-6-6 0-1.01.25-1.97.7-2.8L5.24 7.74C4.46 8.97 4 10.43 4 12c0 4.42 3.58 8 8 8v3l4-4-4-4v3z"/></svg><span class="btn-label" data-i18n="btn_resync">Sync Album</span>
            </button>
            <button type="button" class="tonal" onclick="playDemoShowcase()">
              <svg class="m3-icon" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path fill="currentColor" d="M19 9l1.25-2.75L23 5l-2.75-1.25L19 1l-1.25 2.75L15 5l2.75 1.25L19 9zm-7.5.5L9 4 6.5 9.5 1 12l5.5 2.5L9 20l2.5-5.5L17 12l-5.5-2.5zM19 15l-1.25 2.75L15 19l2.75 1.25L19 23l1.25-2.75L23 19l-2.75-1.25L19 15z"/></svg><span class="btn-label" data-i18n="btn_demo">Play Demo Showcase</span>
            </button>
            <button type="button" onclick="sendAction('test_videos')">
              <svg class="m3-icon" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path fill="currentColor" d="M18 4l2 4h-3l-2-4h-2l2 4h-3l-2-4H8l2 4H7L5 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V4h-4z"/></svg><span class="btn-label" data-i18n="btn_test_vid">Test Video Tiles</span>
            </button>
          </div>
        </div>

        <div class="m3-card" id="webframe-card">
          <div class="card-header">
            <h2><svg class="m3-icon" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path fill="currentColor" d="M21 3H3c-1.1 0-2 .9-2 2v3h2V5h18v14h-7v2h7c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zM1 18v3h3c0-1.66-1.34-3-3-3zm0-4v2c2.76 0 5 2.24 5 5h2c0-3.87-3.13-7-7-7zm0-4v2c4.97 0 9 4.03 9 9h2c0-6.08-4.93-11-11-11z"/></svg><span data-i18n="sec_webframe">Web Slideshow Viewer (/frame)</span></h2>
            <div style="display:flex; align-items:center; gap:10px;">
              <span id="webframe-state-badge" class="badge"><span class="status-dot" aria-hidden="true"></span><span class="badge-text">OFF</span></span>
              <md-switch id="sw-webframe-power" role="switch" aria-checked="false" aria-label="Toggle Web Slideshow Viewer" onchange="onWebFrameSwitchChange(this)" onclick="onWebFrameSwitchFallbackClick(event, this)" onkeydown="onWebFrameSwitchFallbackKey(event, this)"></md-switch>
            </div>
          </div>
          <p class="card-sub-desc" data-i18n="webframe_desc">
            Stream your album directly in any browser on your Wi-Fi without signing in. Each browser gets an independent order (videos render as static poster frames).
          </p>
          <div class="form-grid" style="margin-bottom:14px;">
            <div class="form-group">
              <label for="cfg-webframe-profile" data-i18n="lbl_webframe_profile">Picture Quality &amp; Device Profile</label>
              <select id="cfg-webframe-profile" onchange="updateWebFrameProfileLive(this.value)">
                <option value="auto" data-i18n="opt_wf_auto">Auto — Smart Match (1080p HD on Modern Screens, 768p on Legacy Tablets)</option>
                <option value="hd" data-i18n="opt_wf_hd">1080p HD — Full Resolution, Crossfade &amp; All 9 Collages</option>
                <option value="legacy" data-i18n="opt_wf_legacy">768p Standard — Lightweight Mode for Older Tablets</option>
              </select>
            </div>
            <div class="form-group" id="group-webframe-url">
              <label data-i18n="lbl_webframe_url">Web Viewer Address</label>
              <a id="webframe-path-link" href="/frame" target="_blank" rel="noopener" class="webframe-url-pill">http://localhost:8080/frame</a>
            </div>
          </div>
          <div class="btn-grid">
            <button type="button" id="btn-webframe-toggle" class="primary" onclick="toggleWebFramePower()">
              <svg class="m3-icon" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path fill="currentColor" d="M21 3H3c-1.1 0-2 .9-2 2v3h2V5h18v14h-7v2h7c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zM1 18v3h3c0-1.66-1.34-3-3-3zm0-4v2c2.76 0 5 2.24 5 5h2c0-3.87-3.13-7-7-7zm0-4v2c4.97 0 9 4.03 9 9h2c0-6.08-4.93-11-11-11z"/></svg><span class="btn-label" data-i18n="btn_wf_turn_on">Enable Web Viewer</span>
            </button>
            <button type="button" id="btn-open-webframe" class="tonal" onclick="openWebFrameViewer()">
              <svg class="m3-icon" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path fill="currentColor" d="M19 19H5V5h7V3H5c-1.11 0-2 .9-2 2v14c0 1.1.89 2 2 2h14c1.1 0 2-.9 2-2v-7h-2v7zM14 3v2h3.59l-9.83 9.83 1.41 1.41L19 6.41V10h2V3h-7z"/></svg><span class="btn-label" data-i18n="btn_open_webframe">Launch Web Viewer (/frame)</span>
            </button>
          </div>
        </div>
      </section>

      <!-- TAB 2: ALBUM & DISPLAY -->
      <section id="panel-display" role="tabpanel" aria-labelledby="tab-display" class="hidden">
        <div class="m3-card">
          <div class="card-header">
            <h2><svg class="m3-icon" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path fill="currentColor" d="M3 17v2h6v-2H3zM3 5v2h10V5H3zm10 16v-2h8v-2h-8v-2h-2v6h2zM7 9v2H3v2h4v2h2V9H7zm14 4v-2H11v2h10zm-6-4h2V7h4V5h-4V3h-2v6z"/></svg><span data-i18n="sec_settings">Album, Collage &amp; Schedule</span></h2>
          </div>
          <div id="broadcast-settings-note" class="target-banner hidden" style="margin-bottom: 14px;" data-i18n="broadcast_settings_note">
            Broadcast Mode: Showing shared slideshow, collage, video, and schedule settings. Select an individual frame to edit its Album URL, Hostname, Rotation, or APK Update.
          </div>
          <form id="settings-form" onsubmit="saveSettings(event)">
            <div class="form-grid">
              <div class="form-group full" id="group-album">
                <label for="cfg-album" id="lbl-album-dynamic" data-i18n="lbl_album">Google Photos Shared Album URL (leave blank or 'demo' for Demo Showcase Pack)</label>
                <input type="text" id="cfg-album" oninput="autoExtractBaiduPwd()" data-i18n-placeholder="ph_album" placeholder="https://photos.app.goo.gl/... (or leave blank for Demo Showcase)">
              </div>

              <div class="form-group hidden" id="group-pwd">
                <label for="cfg-pwd" data-i18n="lbl_pwd">Baidu Pan Extract Code (pwd)</label>
                <input type="text" id="cfg-pwd" maxlength="16" data-i18n-placeholder="ph_pwd" placeholder="4-char code (optional if ?pwd= is in URL)">
              </div>

              <div class="form-group full" id="group-saved-albums">
                <label style="display:flex; align-items:center; justify-content:space-between; gap:8px; flex-wrap:wrap;">
                  <span data-i18n="lbl_saved_albums">Saved Connected Albums &amp; Folders (Encrypted Locally)</span>
                  <span id="active-album-title-badge" class="badge active hidden" style="font-size:0.75rem;"></span>
                </label>
                <div id="saved-albums-list" class="saved-albums-grid"></div>
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
                <input type="number" id="cfg-duration" min="3" max="3600" step="1" value="15" required>
              </div>

              <div class="form-group full">
                <div class="switch-field-row">
                  <label for="cfg-shuffle" data-i18n="lbl_shuffle">Shuffle Photos &amp; Videos Randomly</label>
                  <input type="checkbox" id="cfg-shuffle" class="m3-toggle" role="switch">
                </div>
              </div>

              <div class="form-group full">
                <div class="switch-field-row">
                  <label for="cfg-night-enabled" data-i18n="lbl_night_enabled">Enable Scheduled Night Mode</label>
                  <input type="checkbox" id="cfg-night-enabled" class="m3-toggle" role="switch" onchange="syncNightFieldsVisibility()">
                </div>
              </div>

              <div class="form-group" id="group-night-start">
                <label for="cfg-night-start" data-i18n="lbl_night_start">Night Mode Start (Sleep)</label>
                <input type="time" id="cfg-night-start" value="22:00" required>
              </div>

              <div class="form-group" id="group-night-end">
                <label for="cfg-night-end" data-i18n="lbl_night_end">Night Mode End (Wake)</label>
                <input type="time" id="cfg-night-end" value="06:00" required>
              </div>
            </div>

            <details id="advanced-settings-disclosure" class="m3-disclosure">
              <summary>
                <span data-i18n="sec_advanced_options">Advanced Display &amp; Network Options</span>
                <svg class="m3-chevron" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path fill="currentColor" d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6 1.41-1.41z"/></svg>
              </summary>
              <div class="disclosure-body">
                <div class="form-grid">
                  <div class="form-group">
                    <label for="cfg-transition" data-i18n="lbl_transition">Slide Transition</label>
                    <select id="cfg-transition">
                      <option value="none" data-i18n="opt_trans_none">None (Instant Cut — Default)</option>
                      <option value="fade" data-i18n="opt_trans_fade">Crossfade (Dissolve)</option>
                      <option value="fade-black" data-i18n="opt_trans_fade_black">Fade Through Black</option>
                      <option value="slide-left" data-i18n="opt_trans_slide_left">Slide Left</option>
                      <option value="slide-right" data-i18n="opt_trans_slide_right">Slide Right</option>
                      <option value="slide-up" data-i18n="opt_trans_slide_up">Slide Up</option>
                      <option value="wipe" data-i18n="opt_trans_wipe">Horizontal Wipe Reveal</option>
                      <option value="random" data-i18n="opt_trans_random">Random Transition Mix</option>
                    </select>
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
                    <label for="cfg-videos" data-i18n="lbl_videos">Video Mode</label>
                    <select id="cfg-videos">
                      <option value="play" data-i18n="opt_vid_play">Play Live Video in Tiles (FFmpeg RAM)</option>
                      <option value="skip" data-i18n="opt_vid_skip">Skip Videos</option>
                      <option value="still" data-i18n="opt_vid_still">Show Static Video Thumbnail</option>
                    </select>
                  </div>

                  <div class="form-group">
                    <label for="cfg-max-vid" data-i18n="lbl_max_vid">Max Video Duration (seconds)</label>
                    <input type="number" id="cfg-max-vid" min="3" max="120" step="1" value="20" required>
                  </div>

                  <div class="form-group">
                    <label for="cfg-refresh" data-i18n="lbl_refresh">Album Auto-Sync Interval (hours)</label>
                    <input type="number" id="cfg-refresh" min="0" max="168" step="0.5" value="12" required>
                  </div>

                  <div class="form-group" id="group-device-name">
                    <label for="cfg-device-name" data-i18n="lbl_device_name">Device Hostname (.local — auto-numbers on conflict)</label>
                    <input type="text" id="cfg-device-name" data-i18n-placeholder="ph_device_name" placeholder="photoframe (or e.g. living-room)">
                  </div>

                  <div class="form-group">
                    <label for="cfg-language" data-i18n="lbl_language">Language</label>
                    <select id="cfg-language" onchange="setLanguage(this.value, true, true)">
                      <option value="en">English</option>
                      <option value="zh">中文 (简体)</option>
                      <option value="ja">日本語</option>
                      <option value="ko">한국어</option>
                      <option value="es">Español</option>
                      <option value="de">Deutsch</option>
                    </select>
                  </div>
                </div>
              </div>
            </details>

            <div style="margin-top: 18px; display: flex; align-items: center; gap: 12px; flex-wrap: wrap;">
              <button type="submit" id="btn-save-settings" class="primary">
                <svg class="m3-icon" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path fill="currentColor" d="M17 3H5c-1.11 0-2 .9-2 2v14c0 1.1.89 2 2 2h14c1.1 0 2-.9 2-2V7l-4-4zm-5 16c-1.66 0-3-1.34-3-3s1.34-3 3-3 3 1.34 3 3-1.34 3-3 3zm3-10H5V5h10v4z"/></svg><span class="btn-label" data-i18n="btn_save_settings">Save &amp; Apply Settings</span>
              </button>
            </div>
            <div id="settings-confirm-banner" class="confirm-banner" role="status" aria-live="polite"></div>
          </form>
        </div>
      </section>

      <!-- TAB 3: SYSTEM -->
      <section id="panel-system" role="tabpanel" aria-labelledby="tab-system" class="hidden">
        <div class="m3-card">
          <div class="card-header">
            <h2><svg class="m3-icon" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path fill="currentColor" d="M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4zm0 10.99h7c-.53 4.12-3.28 7.79-7 8.94V12H5V6.3l7-3.11v8.8z"/></svg><span data-i18n="sec_security">Security &amp; Change Password</span></h2>
          </div>
          <form id="pw-form" onsubmit="changePassword(event)">
            <div class="form-grid">
              <div class="form-group">
                <label for="pw-current" data-i18n="lbl_pw_current">Current Password</label>
                <input type="password" id="pw-current" required autocomplete="current-password">
              </div>
              <div class="form-group">
                <label for="pw-new" data-i18n="lbl_pw_new">New Password</label>
                <input type="password" id="pw-new" minlength="4" required data-i18n-placeholder="ph_pw_new" placeholder="At least 4 characters" autocomplete="new-password">
              </div>
              <div class="form-group">
                <label for="pw-confirm" data-i18n="lbl_pw_confirm">Confirm New Password</label>
                <input type="password" id="pw-confirm" minlength="4" required autocomplete="new-password">
              </div>
            </div>
            <div style="margin-top: 16px;">
              <button type="submit" id="btn-save-pw" class="success">
                <svg class="m3-icon" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path fill="currentColor" d="M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4zm0 10.99h7c-.53 4.12-3.28 7.79-7 8.94V12H5V6.3l7-3.11v8.8z"/></svg><span class="btn-label" data-i18n="btn_save_pw">Update Password</span>
              </button>
            </div>
            <div id="pw-confirm-banner" class="confirm-banner" role="status" aria-live="polite"></div>
          </form>
        </div>

        <div class="m3-card hidden" id="card-apk-update">
          <div class="card-header">
            <h2><svg class="m3-icon" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path fill="currentColor" d="M17 1.01L7 1c-1.1 0-2 .9-2 2v18c0 1.1.9 2 2 2h10c1.1 0 2-.9 2-2V3c0-1.1-.9-1.99-2-1.99zM17 19H7V5h10v14zm-1-6h-3V8h-2v5H8l4 4 4-4z"/></svg><span data-i18n="sec_update">App Auto-Update &amp; APK Installer</span></h2>
            <span id="stat-app-version" class="badge">v1.0.0</span>
          </div>
          <div class="form-grid">
            <div class="form-group full">
              <label for="cfg-update-url" data-i18n="lbl_update_url">APK Update Download Link (checked on startup &amp; every 6h)</label>
              <div style="display:flex; gap:10px; flex-wrap:wrap;">
                <input type="text" id="cfg-update-url" style="flex:1; min-width:220px;" placeholder="https://github.com/shanghai-noodle/androidTV-photo-slideshow-releases/releases/latest/download/...">
                <button type="button" class="primary" onclick="checkUpdateNow()">
                  <svg class="m3-icon" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path fill="currentColor" d="M15.5 14h-.79l-.28-.27A6.471 6.471 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/></svg><span class="btn-label" data-i18n="btn_check_update">Save Link &amp; Check Update</span>
                </button>
              </div>
              <div id="stat-update-status" style="font-size:0.82rem; color:var(--md-sys-color-on-surface-variant); margin-top:4px;">—</div>
            </div>
            <div class="form-group full" style="margin-top:4px;">
              <label for="apk-file-input" data-i18n="lbl_apk_upload">Or Upload &amp; Install APK File Directly</label>
              <div style="display:flex; gap:10px; align-items:center; flex-wrap:wrap;">
                <input type="file" id="apk-file-input" accept=".apk,application/vnd.android.package-archive" style="flex:1; min-width:220px;">
                <button type="button" id="btn-upload-apk" class="success" onclick="uploadAndInstallApk()">
                  <svg class="m3-icon" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path fill="currentColor" d="M9 16h6v-6h4l-7-7-7 7h4zm-4 2h14v2H5z"/></svg><span class="btn-label" data-i18n="btn_upload_apk">Upload &amp; Install APK</span>
                </button>
              </div>
            </div>
          </div>
          <div id="update-confirm-banner" class="confirm-banner" role="status" aria-live="polite"></div>
        </div>

        <div class="m3-card">
          <div class="card-header">
            <h2><svg class="m3-icon" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path fill="currentColor" d="M20 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 14H4V8h16v10zm-2-1h-6v-2h6v2zM7.5 17l-1.41-1.41L8.67 13l-2.59-2.59L7.5 9l4 4-4 4z"/></svg><span data-i18n="sec_system">System Control &amp; RAM Log</span></h2>
            <button type="button" id="btn-refresh-log" class="btn-sm tonal" onclick="fetchStatus(false)">
              <svg class="m3-icon" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path fill="currentColor" d="M12 4V1L8 5l4 4V6c3.31 0 6 2.69 6 6 0 1.01-.25 1.97-.7 2.8l1.46 1.46C19.54 15.03 20 13.57 20 12c0-4.42-3.58-8-8-8zm0 14c-3.31 0-6-2.69-6-6 0-1.01.25-1.97.7-2.8L5.24 7.74C4.46 8.97 4 10.43 4 12c0 4.42 3.58 8 8 8v3l4-4-4-4v3z"/></svg><span class="btn-label" data-i18n="btn_refresh_log">Refresh Log</span>
            </button>
          </div>
          <div class="sys-meta-row">
            <span id="header-host-badge" class="badge host-badge">photoframe.local</span>
            <span id="header-version-badge" class="badge ver-badge">v1.0.0</span>
          </div>
          <pre class="log-box" id="log-box">Loading RAM log...</pre>
          <div class="btn-grid" style="margin-top: 16px;">
            <button type="button" id="btn-exit-app" class="danger" onclick="confirmExitFrame()">
              <svg class="m3-icon" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path fill="currentColor" d="M13 3h-2v10h2V3zm4.83 2.17-1.42 1.42A6.92 6.92 0 0 1 19 12c0 3.87-3.13 7-7 7A6.995 6.995 0 0 1 7.58 6.58L6.17 5.17A8.932 8.932 0 0 0 3 12a9 9 0 0 0 18 0c0-2.74-1.23-5.18-3.17-6.83z"/></svg><span class="btn-label" data-i18n="btn_exit_app">Exit Photo Frame</span>
            </button>
          </div>
          <div id="system-confirm-banner" class="confirm-banner" role="status" aria-live="polite"></div>
        </div>
      </section>
    </main>

    <!-- MOBILE M3 BOTTOM NAVIGATION BAR -->
    <nav id="mobile-bottom-nav" class="m3-nav-bar" aria-label="Portal sections">
      <div role="tablist" aria-label="Portal sections" style="display:contents;">
        <button type="button" role="tab" id="mob-tab-control" class="m3-nav-tab" aria-selected="true" aria-controls="panel-control" onclick="switchPortalTab('control')" onkeydown="onTabListKeyDown(event, 'control')">
          <span class="m3-nav-pill" aria-hidden="true"><svg class="m3-icon" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path fill="currentColor" d="M15 7.5V2H9v5.5l3 3 3-3zM7.5 9H2v6h5.5l3-3-3-3zM9 16.5V22h6v-5.5l-3-3-3 3zM16.5 9l-3 3 3 3H22V9h-5.5z"/></svg></span>
          <span data-i18n="nav_control">Control</span>
        </button>
        <button type="button" role="tab" id="mob-tab-display" class="m3-nav-tab" aria-selected="false" aria-controls="panel-display" tabindex="-1" onclick="switchPortalTab('display')" onkeydown="onTabListKeyDown(event, 'display')">
          <span class="m3-nav-pill" aria-hidden="true"><svg class="m3-icon" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path fill="currentColor" d="M3 17v2h6v-2H3zM3 5v2h10V5H3zm10 16v-2h8v-2h-8v-2h-2v6h2zM7 9v2H3v2h4v2h2V9H7zm14 4v-2H11v2h10zm-6-4h2V7h4V5h-4V3h-2v6z"/></svg></span>
          <span data-i18n="nav_display">Album &amp; Display</span>
        </button>
        <button type="button" role="tab" id="mob-tab-system" class="m3-nav-tab" aria-selected="false" aria-controls="panel-system" tabindex="-1" onclick="switchPortalTab('system')" onkeydown="onTabListKeyDown(event, 'system')">
          <span class="m3-nav-pill" aria-hidden="true"><svg class="m3-icon" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path fill="currentColor" d="M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58a.49.49 0 0 0 .12-.61l-1.92-3.32a.488.488 0 0 0-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54a.484.484 0 0 0-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96c-.22-.08-.47 0-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.05.3-.09.63-.09.94s.02.64.07.94l-2.03 1.58a.49.49 0 0 0-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6c-1.98 0-3.6-1.62-3.6-3.6s1.62-3.6 3.6-3.6 3.6 1.62 3.6 3.6-1.62 3.6-3.6 3.6z"/></svg></span>
          <span data-i18n="nav_system">System</span>
        </button>
      </div>
    </nav>
  </div>
</div>

<div id="exit-confirm-modal" class="modal-backdrop hidden" onclick="if (event.target === this) closeExitModal()">
  <div class="modal-card" role="dialog" aria-modal="true" aria-labelledby="exit-modal-title" aria-describedby="exit-modal-msg">
    <h3 id="exit-modal-title"><svg class="m3-icon" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path fill="currentColor" d="M13 3h-2v10h2V3zm4.83 2.17-1.42 1.42A6.92 6.92 0 0 1 19 12c0 3.87-3.13 7-7 7A6.995 6.995 0 0 1 7.58 6.58L6.17 5.17A8.932 8.932 0 0 0 3 12a9 9 0 0 0 18 0c0-2.74-1.23-5.18-3.17-6.83z"/></svg><span class="btn-label" data-i18n="btn_exit_app">Exit Photo Frame</span></h3>
    <p id="exit-modal-msg"></p>
    <div class="modal-actions">
      <button type="button" id="btn-exit-cancel" class="outlined" onclick="closeExitModal()">
        <span class="btn-label" data-i18n="btn_cancel">Cancel</span>
      </button>
      <button type="button" id="btn-exit-confirm" class="danger" onclick="executeConfirmedExit()">
        <svg class="m3-icon" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path fill="currentColor" d="M13 3h-2v10h2V3zm4.83 2.17-1.42 1.42A6.92 6.92 0 0 1 19 12c0 3.87-3.13 7-7 7A6.995 6.995 0 0 1 7.58 6.58L6.17 5.17A8.932 8.932 0 0 0 3 12a9 9 0 0 0 18 0c0-2.74-1.23-5.18-3.17-6.83z"/></svg><span class="btn-label" data-i18n="btn_exit_app">Exit Photo Frame</span>
      </button>
    </div>
  </div>
</div>

<div id="toast" class="toast" role="status" aria-live="polite"></div>

<script>
const I18N = {
  en: {
    skip_to_main: "Skip to main content",
    login_title: "Photo Frame Sign In",
    default_pw_hint: "Default password is <b>admin</b>. You can change it after signing in.",
    lbl_password: "Password",
    ph_password: "Enter password",
    btn_signin: "Sign In",
    portal_title: "Photo Frame Portal",
    btn_signout: "Sign Out",
    default_pw_banner: "You are currently using the default password (<b>admin</b>). Open the <b>System</b> tab to set a custom password.",
    nav_sections_aria: "Portal sections",
    nav_control: "Control",
    nav_display: "Album & Display",
    nav_system: "System",
    sec_remote: "Quick Remote Controls",
    stat_album: "Album Media",
    stat_layout: "Layout Mode",
    stat_night: "Night Schedule",
    stat_display: "Display",
    btn_wake_sleep: "Wake / Sleep",
    btn_wake_now: "Wake Screen Now",
    btn_sleep_now: "Sleep Screen",
    btn_prev: "Previous",
    btn_pause: "Pause / Resume",
    btn_next: "Next Slide",
    btn_resync: "Sync Album",
    btn_demo: "Play Demo Showcase",
    btn_test_vid: "Test Video Tiles",
    sec_settings: "Album, Collage & Schedule",
    sec_advanced_options: "Advanced Display & Network Options",
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
    opt_trans_slide_left: "Slide Left",
    opt_trans_slide_right: "Slide Right",
    opt_trans_slide_up: "Slide Up",
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
    btn_save_settings: "Save & Apply Settings",
    btn_saving_settings: "Saving & Applying...",
    btn_saved_settings: "Saved & Applied!",
    sec_security: "Security & Change Password",
    lbl_pw_current: "Current Password",
    lbl_pw_new: "New Password",
    ph_pw_new: "At least 4 characters",
    lbl_pw_confirm: "Confirm New Password",
    btn_save_pw: "Update Password",
    sec_system: "System Control & RAM Log",
    btn_refresh_log: "Refresh Log",
    btn_exit_app: "Exit Photo Frame",
    badge_night: "Night Mode (Sleeping)",
    badge_paused: "Paused",
    badge_active: "Active (Playing)",
    stat_items_fmt: "{prefix}{photos} items ({videos} videos)",
    stat_night_disabled: "Disabled (24/7 On)",
    msg_signed_in: "Signed in successfully",
    msg_invalid_pw: "Invalid password",
    msg_settings_saved_banner: "Settings saved & applied at {time}! Active source: {source} • Layout: {layout} ({duration}s)",
    msg_source_album: "Shared Album ({album})",
    msg_source_demo: "Built-in Demo Showcase Pack",
    msg_pw_mismatch: "New passwords do not match",
    msg_pw_updated: "Password updated successfully! Your session remains active.",
    confirm_exit_app: "Exit and close the Photo Frame application now? Its Web Control Portal will go offline until the app is started again.",
    confirm_exit_app_all: "Exit and close the Photo Frame application on ALL connected frames now? Their Web Control Portals will go offline until started again.",
    btn_cancel: "Cancel",
    badge_offline: "Offline (Closed)",
    msg_exited_offline: "Photo Frame closed. Its Web Control Portal is now offline until the app is started again.",
    sec_fleet: "Connected Photo Frames (LAN Fleet)",
    btn_scan_fleet: "Scan LAN",
    btn_add_peer: "Add IP",
    ph_add_peer: "192.168.86.x or photoframe-2.local:8080",
    btn_connect_peer: "Connect Frame",
    fleet_all_frames: "All Frames ({count})",
    fleet_all_sub: "Broadcast to all online frames",
    fleet_this_frame: "This Frame",
    chip_fleet_single: "LAN Frames",
    chip_fleet_multi: "{count} Frames",
    target_controlling_self: "Controlling: {host} ({ip} — This Frame)",
    target_controlling_peer: "Controlling Remote Frame: {host} ({ip}) — Single-Login LAN Trust",
    target_controlling_all: "Broadcast Mode: Actions & shared settings apply to all {count} online frames simultaneously.",
    lbl_pwd: "Baidu Pan Extract Code (pwd)",
    ph_pwd: "4-char code (optional if ?pwd= is in URL)",
    sec_update: "App Auto-Update & APK Installer",
    lbl_update_url: "APK Update Download Link (checked on startup & every 6h)",
    btn_check_update: "Save Link & Check Update",
    lbl_apk_upload: "Or Upload & Install APK File Directly",
    btn_upload_apk: "Upload & Install APK",
    msg_select_apk: "Please choose an .apk file first",
    sec_webframe: "Web Slideshow Viewer (/frame)",
    webframe_desc: "Stream your album directly in any browser on your Wi-Fi without signing in. Each browser gets an independent order (videos render as static poster frames).",
    lbl_webframe_switch: "Toggle Web Slideshow Viewer",
    lbl_webframe_profile: "Picture Quality & Device Profile",
    lbl_webframe_url: "Web Viewer Address",
    opt_wf_auto: "Auto — Smart Match (1080p HD on Modern Screens, 768p on Legacy Tablets)",
    opt_wf_hd: "1080p HD — Full Resolution, Crossfade & All 9 Collages",
    opt_wf_legacy: "768p Standard — Lightweight Mode for Older Tablets",
    btn_wf_turn_on: "Enable Web Viewer",
    btn_wf_turn_off: "Disable Web Viewer",
    btn_wf_open: "Launch /frame",
    btn_open_webframe: "Launch Web Viewer (/frame)",
    lbl_saved_albums: "Saved Connected Albums & Folders (Encrypted Locally)",
    saved_albums_empty: "No connected links saved yet. Successfully connected albums and folders are automatically encrypted and stored here.",
    btn_use_saved_album: "Switch",
    badge_saved_active: "Active",
    broadcast_settings_note: "Broadcast Mode: Showing shared slideshow, collage, video, and schedule settings. Select an individual frame to edit its Album URL, Hostname, Rotation, or APK Update.",
    lbl_dark_theme_switch: "Dark theme",
    btn_theme_light: "Light",
    btn_theme_dark: "Dark",
    title_toggle_theme: "Toggle Dark Theme"
  },
  zh: {
    skip_to_main: "跳至主要内容",
    login_title: "数码相框登录",
    default_pw_hint: "默认密码为 <b>admin</b>，登录后可修改密码。",
    lbl_password: "管理密码",
    ph_password: "请输入密码",
    btn_signin: "登录",
    portal_title: "数码相框控制台",
    btn_signout: "退出登录",
    default_pw_banner: "您当前使用的是默认密码 (<b>admin</b>)。请前往 <b>系统</b> 标签页设置自定义密码。",
    nav_sections_aria: "控制台分区导航",
    nav_control: "遥控",
    nav_display: "相册与显示",
    nav_system: "系统",
    sec_remote: "快捷远程控制",
    stat_album: "相册媒体",
    stat_layout: "拼图布局",
    stat_night: "夜间模式计划",
    stat_display: "显示分辨率",
    btn_wake_sleep: "唤醒 / 休眠",
    btn_wake_now: "立即唤醒屏幕",
    btn_sleep_now: "立即进入休眠",
    btn_prev: "上一页",
    btn_pause: "暂停 / 继续",
    btn_next: "下一页",
    btn_resync: "同步相册",
    btn_demo: "播放内置演示集",
    btn_test_vid: "测试视频拼图",
    sec_settings: "相册、拼图与定时设置",
    sec_advanced_options: "高级显示与网络选项",
    lbl_album: "相册或网盘共享链接（留空或填 'demo' 播放内置演示）",
    ph_album: "https://photos.app.goo.gl/...（留空则播放演示）",
    lbl_device_name: "局域网主机名 (.local — 冲突时自动编号)",
    ph_device_name: "photoframe（例如 living-room）",
    lbl_language: "界面语言",
    lbl_layout: "幻灯片 / 拼图样式",
    opt_layout_collage: "智能拼图混排（全部 9 种网格 + 竖屏特写）",
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
    opt_trans_fade_black: "黑场渐变过渡",
    opt_trans_slide_left: "向左平滑推入",
    opt_trans_slide_right: "向右平滑推入",
    opt_trans_slide_up: "向上平滑推入",
    opt_trans_wipe: "水平擦除展开",
    opt_trans_random: "随机过渡动画组合",
    lbl_videos: "视频播放模式",
    opt_vid_play: "在拼图格子中实时播放视频",
    opt_vid_skip: "跳过所有视频",
    opt_vid_still: "仅显示视频静态封面",
    lbl_max_vid: "最长视频播放时间（秒）",
    lbl_fit: "单张照片缩放模式",
    opt_fit_blur: "完整显示 + 暗色散景背景",
    opt_fit_contain: "完整显示（黑边填充）",
    opt_fit_cover: "居中裁剪铺满全屏",
    lbl_rotate: "屏幕旋转角度",
    opt_rot_270: "270°（竖屏）",
    opt_rot_90: "90°（反向竖屏）",
    opt_rot_0: "0°（标准横屏）",
    opt_rot_180: "180°（反向横屏）",
    lbl_refresh: "相册自动同步间隔（小时）",
    lbl_shuffle: "随机打乱照片与视频顺序",
    lbl_night_enabled: "启用定时夜间休眠模式",
    lbl_night_start: "夜间休眠开始时间",
    lbl_night_end: "早晨自动唤醒时间",
    btn_save_settings: "保存并立即应用设置",
    btn_saving_settings: "正在保存并应用...",
    btn_saved_settings: "已保存并生效！",
    sec_security: "安全与修改密码",
    lbl_pw_current: "当前密码",
    lbl_pw_new: "新密码",
    ph_pw_new: "至少 4 个字符",
    lbl_pw_confirm: "确认新密码",
    btn_save_pw: "更新密码",
    sec_system: "系统控制与内存日志",
    btn_refresh_log: "刷新日志",
    btn_exit_app: "退出并关闭相框",
    badge_night: "夜间休眠中",
    badge_paused: "已暂停",
    badge_active: "正在播放",
    stat_items_fmt: "{prefix}{photos} 项媒体 ({videos} 个视频)",
    stat_night_disabled: "已禁用（24 小时常亮）",
    msg_signed_in: "登录成功",
    msg_invalid_pw: "密码错误",
    msg_settings_saved_banner: "设置已于 {time} 保存并实时生效！媒体源：{source} • 布局：{layout} ({duration}秒)",
    msg_source_album: "共享相册 ({album})",
    msg_source_demo: "内置精选演示集",
    msg_pw_mismatch: "两次输入的新密码不一致",
    msg_pw_updated: "密码更新成功！当前登录会话保持有效。",
    confirm_exit_app: "确定要立即退出并关闭相框程序吗？关闭后网页控制中心将离线，需重新启动应用后方可再次访问。",
    confirm_exit_app_all: "确定要立即退出并关闭所有已连接设备上的相框程序吗？关闭后各设备的网页控制中心将离线。",
    btn_cancel: "取消",
    badge_offline: "已关闭离线",
    msg_exited_offline: "相框应用已退出关闭。网页控制中心现已离线，重新启动应用后即可恢复连接。",
    sec_fleet: "局域网已连接相框 (多设备集群)",
    btn_scan_fleet: "扫描局域网",
    btn_add_peer: "添加 IP",
    ph_add_peer: "192.168.86.x 或 photoframe-2.local:8080",
    btn_connect_peer: "连接相框",
    fleet_all_frames: "全部相框 ({count} 台)",
    fleet_all_sub: "同步广播至所有在线相框",
    fleet_this_frame: "当前相框",
    chip_fleet_single: "局域网设备",
    chip_fleet_multi: "{count} 台相框",
    target_controlling_self: "当前控制：{host} ({ip} — 本机相框)",
    target_controlling_peer: "正在远程管理：{host} ({ip}) — 免重复登录",
    target_controlling_all: "全局广播模式：远程控制与通用设置将同时应用至全部 {count} 台在线相框。",
    lbl_pwd: "百度网盘提取码 (pwd)",
    ph_pwd: "4 位提取码（若链接含 ?pwd= 可留空）",
    sec_update: "应用自动更新与 APK 安装",
    lbl_update_url: "APK 更新下载链接（启动时及每 6 小时自动检测）",
    btn_check_update: "保存链接并检查更新",
    lbl_apk_upload: "或直接上传并安装本地 .apk 安装包",
    btn_upload_apk: "上传并安装 APK",
    msg_select_apk: "请先选择一个 .apk 安装包文件",
    sec_webframe: "网页端相框浏览器 (/frame)",
    webframe_desc: "在同一局域网内的平板、手机或电脑浏览器中免登录观看实时幻灯片。每台浏览器拥有独立的照片播放顺序。",
    lbl_webframe_switch: "开启或关闭网页端相框",
    lbl_webframe_profile: "画质与设备兼容模式",
    lbl_webframe_url: "网页相框访问地址",
    opt_wf_auto: "自动匹配 — 现代屏幕 1080p 高清，老款平板 768p 标准",
    opt_wf_hd: "1080p 高清 — 全高清画质与全部 9 种拼图布局",
    opt_wf_legacy: "768p 标准 — 老款平板省内存兼容模式",
    btn_wf_turn_on: "启用网页相框",
    btn_wf_turn_off: "关闭网页相框",
    btn_wf_open: "打开 /frame",
    btn_open_webframe: "打开网页相框 (/frame)",
    lbl_saved_albums: "已保存的历史连接相册与网盘目录（本地加密存储）",
    saved_albums_empty: "暂无已保存的历史链接。成功连接的相册或网盘分享将自动加密保存在此处。",
    btn_use_saved_album: "切换",
    badge_saved_active: "当前使用",
    broadcast_settings_note: "群控广播模式：当前仅显示可同步广播的通用设置。如需修改相册链接、主机名、屏幕旋转或 APK 更新，请选择单个相框。",
    lbl_dark_theme_switch: "深色主题",
    btn_theme_light: "浅色",
    btn_theme_dark: "深色",
    title_toggle_theme: "切换深色主题"
  },
  ja: {
    skip_to_main: "メインコンテンツへスキップ",
    login_title: "フォトフレーム ログイン",
    default_pw_hint: "初期パスワードは <b>admin</b> です。ログイン後に変更できます。",
    lbl_password: "パスワード",
    ph_password: "パスワードを入力",
    btn_signin: "ログイン",
    portal_title: "フォトフレーム管理",
    btn_signout: "ログアウト",
    default_pw_banner: "現在初期パスワード (<b>admin</b>) を使用しています。<b>システム</b>タブから変更してください。",
    nav_sections_aria: "セクション切り替え",
    nav_control: "操作",
    nav_display: "アルバム・表示",
    nav_system: "システム",
    sec_remote: "クイックリモコン操作",
    stat_album: "アルバムメディア",
    stat_layout: "レイアウトモード",
    stat_night: "ナイトモード予定",
    stat_display: "ディスプレイ",
    btn_wake_sleep: "起動 / スリープ",
    btn_wake_now: "画面をオンにする",
    btn_sleep_now: "画面をスリープ",
    btn_prev: "前へ",
    btn_pause: "一時停止 / 再開",
    btn_next: "次のスライド",
    btn_resync: "アルバム同期",
    btn_demo: "デモショーケース再生",
    btn_test_vid: "動画タイルをテスト",
    sec_settings: "アルバム・コラージュ・予定設定",
    sec_advanced_options: "高度な表示・ネットワーク設定",
    lbl_album: "共有アルバム URL（空欄または 'demo' で内蔵デモ再生）",
    ph_album: "https://photos.app.goo.gl/...（空欄でデモ再生）",
    lbl_device_name: "デバイスホスト名 (.local — 重複時は自動採番)",
    ph_device_name: "photoframe（例: living-room）",
    lbl_language: "表示言語",
    lbl_layout: "レイアウト / コラージュ",
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
    opt_trans_fade: "クロスフェード",
    opt_trans_fade_black: "ブラックフェード",
    opt_trans_slide_left: "左へスライド",
    opt_trans_slide_right: "右へスライド",
    opt_trans_slide_up: "上へスライド",
    opt_trans_wipe: "水平ワイプ切り替え",
    opt_trans_random: "ランダム切り替えミックス",
    lbl_videos: "動画再生モード",
    opt_vid_play: "タイル内でライブ動画再生",
    opt_vid_skip: "動画をスキップ",
    opt_vid_still: "静止画サムネイルのみ表示",
    lbl_max_vid: "最大動画再生時間（秒）",
    lbl_fit: "シングル写真のフィットモード",
    opt_fit_blur: "ダークボケ背景 + 全体表示",
    opt_fit_contain: "黒帯付き全体表示",
    opt_fit_cover: "中央クロップ全画面",
    lbl_rotate: "画面の回転",
    opt_rot_270: "270°（縦向き）",
    opt_rot_90: "90°（縦向き反転）",
    opt_rot_0: "0°（横向き）",
    opt_rot_180: "180°（横向き反転）",
    lbl_refresh: "アルバム自動同期間隔（時間）",
    lbl_shuffle: "写真と動画をランダムにシャッフル",
    lbl_night_enabled: "スケジュールされたナイトモードを有効化",
    lbl_night_start: "ナイトモード開始（スリープ）",
    lbl_night_end: "ナイトモード終了（ウェイク）",
    btn_save_settings: "設定を保存して適用",
    btn_saving_settings: "保存して適用中...",
    btn_saved_settings: "保存・適用完了！",
    sec_security: "セキュリティとパスワード変更",
    lbl_pw_current: "現在のパスワード",
    lbl_pw_new: "新しいパスワード",
    ph_pw_new: "4文字以上",
    lbl_pw_confirm: "新しいパスワード（確認）",
    btn_save_pw: "パスワードを更新",
    sec_system: "システム管理 & RAMログ",
    btn_refresh_log: "ログ更新",
    btn_exit_app: "フォトフレームを終了",
    badge_night: "ナイトモード（スリープ中）",
    badge_paused: "一時停止中",
    badge_active: "再生中",
    stat_items_fmt: "{prefix}{photos} 件 ({videos} 動画)",
    stat_night_disabled: "無効（24時間オン）",
    msg_signed_in: "ログインしました",
    msg_invalid_pw: "パスワードが正しくありません",
    msg_settings_saved_banner: "{time} に設定を保存・即時適用しました！ソース: {source} • レイアウト: {layout} ({duration}秒)",
    msg_source_album: "共有アルバム ({album})",
    msg_source_demo: "内蔵デモショーケースパック",
    msg_pw_mismatch: "新しいパスワードが一致しません",
    msg_pw_updated: "パスワードを更新しました！セッションは維持されます。",
    confirm_exit_app: "フォトフレームアプリを今すぐ終了して閉じますか？アプリを再起動するまでWeb管理画面はオフラインになります。",
    confirm_exit_app_all: "接続されているすべてのフレームでフォトフレームアプリを終了しますか？再起動するまでWeb管理画面はオフラインになります。",
    btn_cancel: "キャンセル",
    badge_offline: "オフライン（終了済）",
    msg_exited_offline: "フォトフレームを終了しました。アプリを再起動するまでWeb管理画面はオフラインになります。",
    sec_fleet: "接続済みフォトフレーム (LAN管理)",
    btn_scan_fleet: "LANスキャン",
    btn_add_peer: "IP追加",
    ph_add_peer: "192.168.86.x または photoframe-2.local:8080",
    btn_connect_peer: "接続",
    fleet_all_frames: "すべてのフレーム ({count})",
    fleet_all_sub: "オンラインの全フレームに一括適用",
    fleet_this_frame: "このフレーム",
    chip_fleet_single: "LAN デバイス",
    chip_fleet_multi: "{count} 台",
    target_controlling_self: "操作中: {host} ({ip} — このフレーム)",
    target_controlling_peer: "リモート操作中: {host} ({ip}) — 再ログイン不要",
    target_controlling_all: "一括配信モード: 操作と共通設定が全 {count} 台に同時適用されます。",
    lbl_pwd: "Baidu Pan 抽出コード (pwd)",
    ph_pwd: "4文字コード（URLに ?pwd= がある場合は省略可）",
    sec_update: "アプリ自動更新 & APK インストーラー",
    lbl_update_url: "APK 更新ダウンロードリンク",
    btn_check_update: "リンクを保存して更新確認",
    lbl_apk_upload: "または .apk ファイルを直接アップロードしてインストール",
    btn_upload_apk: "APK をアップロードしてインストール",
    msg_select_apk: "先に .apk ファイルを選択してください",
    sec_webframe: "Web スライドショービューア (/frame)",
    webframe_desc: "同じ Wi-Fi 上のブラウザでログイン不要でスライドショーを表示します。ブラウザごとに独立した再生順でアルバムを共有します。",
    lbl_webframe_switch: "Web スライドショービューアの切り替え",
    lbl_webframe_profile: "画質・デバイスプロファイル",
    lbl_webframe_url: "Web ビューアのアドレス",
    opt_wf_auto: "自動 — スマートマッチ（最新は 1080p HD、旧型タブレットは 768p）",
    opt_wf_hd: "1080p HD — フル解像度・全9種コラージュ",
    opt_wf_legacy: "768p 標準 — 旧型タブレット向け省メモリモード",
    btn_wf_turn_on: "Web ビューアを有効化",
    btn_wf_turn_off: "Web ビューアを無効化",
    btn_wf_open: "/frame を開く",
    btn_open_webframe: "Web ビューアを開く (/frame)",
    lbl_saved_albums: "保存済みの接続アルバム・フォルダ（ローカル暗号化保存）",
    saved_albums_empty: "保存されたリンクはまだありません。接続に成功したアルバムやフォルダは自動的に暗号化されて保存されます。",
    btn_use_saved_album: "切替",
    badge_saved_active: "使用中",
    broadcast_settings_note: "一括配信モード：すべてのフレームに適用できる共通設定のみ表示しています。個別の設定変更は上でフレームを選択してください。",
    lbl_dark_theme_switch: "ダークテーマ",
    btn_theme_light: "ライト",
    btn_theme_dark: "ダーク",
    title_toggle_theme: "ダークテーマを切り替え"
  },
  ko: {
    skip_to_main: "본문으로 바로가기",
    login_title: "디지털 액자 로그인",
    default_pw_hint: "기본 비밀번호는 <b>admin</b>입니다. 로그인 후 변경할 수 있습니다.",
    lbl_password: "비밀번호",
    ph_password: "비밀번호 입력",
    btn_signin: "로그인",
    portal_title: "디지털 액자 제어 포털",
    btn_signout: "로그아웃",
    default_pw_banner: "현재 기본 비밀번호(<b>admin</b>)를 사용 중입니다. <b>시스템</b> 탭에서 새 비밀번호를 설정하세요.",
    nav_sections_aria: "섹션 탐색",
    nav_control: "제어",
    nav_display: "앨범 및 화면",
    nav_system: "시스템",
    sec_remote: "빠른 원격 제어",
    stat_album: "앨범 미디어",
    stat_layout: "레이아웃 모드",
    stat_night: "야간 모드 일정",
    stat_display: "디스플레이",
    btn_wake_sleep: "화면 켜기 / 절전",
    btn_wake_now: "지금 화면 깨우기",
    btn_sleep_now: "절전 모드로 전환",
    btn_prev: "이전",
    btn_pause: "일시정지 / 재생",
    btn_next: "다음 슬라이드",
    btn_resync: "앨범 동기화",
    btn_demo: "데모 쇼케이스 재생",
    btn_test_vid: "비디오 타일 테스트",
    sec_settings: "앨범, 콜라주 및 일정 설정",
    sec_advanced_options: "고급 화면 및 네트워크 옵션",
    lbl_album: "공유 앨범 URL (비워두거나 'demo' 입력 시 데모 팩 재생)",
    ph_album: "https://photos.app.goo.gl/... (비워두면 데모 재생)",
    lbl_device_name: "기기 호스트 이름 (.local — 충돌 시 자동 번호 부여)",
    ph_device_name: "photoframe (예: living-room)",
    lbl_language: "언어",
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
    opt_trans_fade: "크로스페이드",
    opt_trans_fade_black: "블랙 페이드 전환",
    opt_trans_slide_left: "왼쪽으로 슬라이드",
    opt_trans_slide_right: "오른쪽으로 슬라이드",
    opt_trans_slide_up: "위로 슬라이드",
    opt_trans_wipe: "수평 와이프 전환",
    opt_trans_random: "랜덤 전환 효과 믹스",
    lbl_videos: "동영상 재생 모드",
    opt_vid_play: "타일 내 실시간 동영상 재생",
    opt_vid_skip: "동영상 건너뛰기",
    opt_vid_still: "정지 썸네일만 표시",
    lbl_max_vid: "최대 동영상 재생 시간 (초)",
    lbl_fit: "단일 사진 맞춤 모드",
    opt_fit_blur: "다크 보케 배경 위 전체 표시",
    opt_fit_contain: "검은 여백과 함께 전체 표시",
    opt_fit_cover: "중앙 크롭 전체화면",
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
    btn_save_settings: "설정 저장 및 즉시 적용",
    btn_saving_settings: "저장 및 적용 중...",
    btn_saved_settings: "저장 및 적용 완료!",
    sec_security: "보안 및 비밀번호 변경",
    lbl_pw_current: "현재 비밀번호",
    lbl_pw_new: "새 비밀번호",
    ph_pw_new: "최소 4자 이상",
    lbl_pw_confirm: "새 비밀번호 확인",
    btn_save_pw: "비밀번호 업데이트",
    sec_system: "시스템 제어 및 RAM 로그",
    btn_refresh_log: "로그 새로고침",
    btn_exit_app: "포토 프레임 종료",
    badge_night: "야간 모드 (절전 중)",
    badge_paused: "일시정지됨",
    badge_active: "재생 중",
    stat_items_fmt: "{prefix}{photos}개 항목 (동영상 {videos}개)",
    stat_night_disabled: "비활성화됨 (24시간 켜짐)",
    msg_signed_in: "로그인되었습니다",
    msg_invalid_pw: "비밀번호가 올바르지 않습니다",
    msg_settings_saved_banner: "{time}에 설정이 저장 및 적용되었습니다! 소스: {source} • 레이아웃: {layout} ({duration}초)",
    msg_source_album: "공유 앨범 ({album})",
    msg_source_demo: "내장 데모 쇼케이스 팩",
    msg_pw_mismatch: "새 비밀번호가 일치하지 않습니다",
    msg_pw_updated: "비밀번호가 성공적으로 변경되었습니다!",
    confirm_exit_app: "지금 포토 프레임 앱을 종료하고 닫으시겠습니까? 앱을 다시 시작할 때까지 웹 제어 포털이 오프라인 상태가 됩니다.",
    confirm_exit_app_all: "연결된 모든 액자에서 포토 프레임 앱을 종료하시겠습니까? 다시 시작할 때까지 웹 제어 포털이 오프라인 상태가 됩니다.",
    btn_cancel: "취소",
    badge_offline: "오프라인 (종료됨)",
    msg_exited_offline: "포토 프레임 앱이 종료되었습니다. 앱을 다시 시작할 때까지 웹 제어 포털이 오프라인 상태로 유지됩니다.",
    sec_fleet: "연결된 디지털 액자 (LAN 관리)",
    btn_scan_fleet: "LAN 스캔",
    btn_add_peer: "IP 추가",
    ph_add_peer: "192.168.86.x 또는 photoframe-2.local:8080",
    btn_connect_peer: "연결",
    fleet_all_frames: "모든 액자 ({count})",
    fleet_all_sub: "온라인 액자 전체에 동시 적용",
    fleet_this_frame: "현재 액자",
    chip_fleet_single: "LAN 액자",
    chip_fleet_multi: "{count}대 액자",
    target_controlling_self: "제어 중: {host} ({ip} — 현재 액자)",
    target_controlling_peer: "원격 액자 제어 중: {host} ({ip}) — 추가 로그인 불필요",
    target_controlling_all: "전체 브로드캐스트 모드: 원격 제어 및 공통 설정이 {count}대 액자 전체에 동시에 적용됩니다.",
    lbl_pwd: "Baidu Pan 추출 코드 (pwd)",
    ph_pwd: "4자리 코드 (URL에 ?pwd= 포함 시 생략 가능)",
    sec_update: "앱 자동 업데이트 및 APK 설치",
    lbl_update_url: "APK 업데이트 다운로드 링크",
    btn_check_update: "링크 저장 및 업데이트 확인",
    lbl_apk_upload: "또는 브라우저에서 .apk 파일을 직접 업로드하여 설치",
    btn_upload_apk: "APK 업로드 및 설치",
    msg_select_apk: "먼저 .apk 파일을 선택하세요",
    sec_webframe: "웹 슬라이드쇼 뷰어 (/frame)",
    webframe_desc: "동일한 Wi-Fi의 브라우저에서 로그인 없이 실시간 슬라이드쇼를 감상하세요. 브라우저별로 독립된 사진 순서를 사용합니다.",
    lbl_webframe_switch: "웹 슬라이드쇼 뷰어 켜기/끄기",
    lbl_webframe_profile: "화질 및 기기 프로필",
    lbl_webframe_url: "웹 뷰어 주소",
    opt_wf_auto: "자동 — 스마트 매치 (최신 화면 1080p HD, 구형 태블릿 768p)",
    opt_wf_hd: "1080p HD — 전체 해상도 및 9가지 콜라주",
    opt_wf_legacy: "768p 표준 — 구형 태블릿용 메모리 절약 모드",
    btn_wf_turn_on: "웹 뷰어 켜기",
    btn_wf_turn_off: "웹 뷰어 끄기",
    btn_wf_open: "/frame 열기",
    btn_open_webframe: "웹 뷰어 열기 (/frame)",
    lbl_saved_albums: "저장된 연결 앨범 및 폴더 (로컬 암호화 저장)",
    saved_albums_empty: "저장된 링크가 없습니다. 성공적으로 연결된 앨범 및 폴더가 자동으로 암호화되어 저장됩니다.",
    btn_use_saved_album: "전환",
    badge_saved_active: "사용 중",
    broadcast_settings_note: "전체 브로드캐스트 모드: 모든 액자에 적용 가능한 공통 설정만 표시됩니다. 개별 설정은 위에서 액자를 선택하세요.",
    lbl_dark_theme_switch: "다크 테마",
    btn_theme_light: "라이트",
    btn_theme_dark: "다크",
    title_toggle_theme: "다크 테마 전환"
  },
  es: {
    skip_to_main: "Ir al contenido principal",
    login_title: "Acceso al Marco Digital",
    default_pw_hint: "La contraseña predeterminada es <b>admin</b>. Puedes cambiarla después de iniciar sesión.",
    lbl_password: "Contraseña",
    ph_password: "Ingresa la contraseña",
    btn_signin: "Iniciar Sesión",
    portal_title: "Portal del Marco Digital",
    btn_signout: "Cerrar Sesión",
    default_pw_banner: "Usas la contraseña predeterminada (<b>admin</b>). Ve a la pestaña <b>Sistema</b> para cambiarla.",
    nav_sections_aria: "Secciones de control",
    nav_control: "Control",
    nav_display: "Álbum y Pantalla",
    nav_system: "Sistema",
    sec_remote: "Controles Remotos Rápidos",
    stat_album: "Multimedia del Álbum",
    stat_layout: "Modo de Diseño",
    stat_night: "Horario Nocturno",
    stat_display: "Pantalla",
    btn_wake_sleep: "Despertar / Dormir",
    btn_wake_now: "Despertar Pantalla",
    btn_sleep_now: "Suspender Pantalla",
    btn_prev: "Anterior",
    btn_pause: "Pausar / Reanudar",
    btn_next: "Siguiente",
    btn_resync: "Sincronizar Álbum",
    btn_demo: "Reproducir Demo",
    btn_test_vid: "Probar Videos",
    sec_settings: "Álbum, Collage y Horario",
    sec_advanced_options: "Opciones Avanzadas de Pantalla y Red",
    lbl_album: "URL del Álbum Compartido (dejar vacío o 'demo' para el paquete Demo)",
    ph_album: "https://photos.app.goo.gl/... (o vacío para Demo)",
    lbl_device_name: "Nombre de Host (.local — numeración automática si hay conflicto)",
    ph_device_name: "photoframe (ej. sala-estar)",
    lbl_language: "Idioma",
    lbl_layout: "Estilo de Diseño / Collage",
    opt_layout_collage: "Mezcla de Collages (9 Cuadrículas + Verticales)",
    opt_layout_single: "Solo Pantalla Completa Individual",
    opt_layout_trio: "Grid Trio (1 Horizontal + 2 Verticales)",
    opt_layout_classic5: "Grid Classic 5 (1 Principal + 2V + 2H)",
    opt_layout_mosaic7: "Grid Mosaic 7 (Divisiones Alternas + Principal)",
    opt_layout_sidebar9: "Grid Sidebar 9 (Columna Izquierda + Derecha)",
    opt_layout_mondrian8: "Grid Mondrian 8 (Mosaico Simétrico)",
    opt_layout_323: "Grid 3-2-3 (3V + 2H + 3V)",
    opt_layout_stack: "Stack 2 (2 Horizontales)",
    opt_layout_triple: "Stack 3 (3 Horizontales)",
    opt_layout_bento: "Bento 4 (4 Horizontales)",
    lbl_duration: "Duración de Diapositiva (segundos)",
    lbl_transition: "Transición de Diapositiva",
    opt_trans_none: "Ninguna (Corte Instantáneo — Predeterminado)",
    opt_trans_fade: "Fundido Cruzado",
    opt_trans_fade_black: "Fundido a Negro",
    opt_trans_slide_left: "Deslizar a la Izquierda",
    opt_trans_slide_right: "Deslizar a la Derecha",
    opt_trans_slide_up: "Deslizar hacia Arriba",
    opt_trans_wipe: "Barrido Horizontal",
    opt_trans_random: "Mezcla Aleatoria",
    lbl_videos: "Modo de Video",
    opt_vid_play: "Reproducir Video en Vivo en Mosaicos",
    opt_vid_skip: "Omitir Videos",
    opt_vid_still: "Mostrar Miniatura Estática",
    lbl_max_vid: "Duración Máxima de Video (segundos)",
    lbl_fit: "Ajuste de Foto Individual",
    opt_fit_blur: "Contener sobre Fondo Bokeh Oscuro",
    opt_fit_contain: "Contener con Barras Negras",
    opt_fit_cover: "Recorte Central Pantalla Completa",
    lbl_rotate: "Rotación de Pantalla",
    opt_rot_270: "270° (Vertical)",
    opt_rot_90: "90° (Vertical Invertido)",
    opt_rot_0: "0° (Horizontal)",
    opt_rot_180: "180° (Horizontal Invertido)",
    lbl_refresh: "Intervalo de Sincronización (horas)",
    lbl_shuffle: "Mezclar Fotos y Videos Aleatoriamente",
    lbl_night_enabled: "Activar Modo Nocturno Programado",
    lbl_night_start: "Inicio del Modo Nocturno (Dormir)",
    lbl_night_end: "Fin del Modo Nocturno (Despertar)",
    btn_save_settings: "Guardar y Aplicar Configuración",
    btn_saving_settings: "Guardando y Aplicando...",
    btn_saved_settings: "¡Guardado y Aplicado!",
    sec_security: "Seguridad y Contraseña",
    lbl_pw_current: "Contraseña Actual",
    lbl_pw_new: "Nueva Contraseña",
    ph_pw_new: "Al menos 4 caracteres",
    lbl_pw_confirm: "Confirmar Nueva Contraseña",
    btn_save_pw: "Actualizar Contraseña",
    sec_system: "Control del Sistema y Registro RAM",
    btn_refresh_log: "Actualizar Registro",
    btn_exit_app: "Salir del Marco de Fotos",
    badge_night: "Modo Nocturno",
    badge_paused: "En Pausa",
    badge_active: "Activo",
    stat_items_fmt: "{prefix}{photos} elementos ({videos} videos)",
    stat_night_disabled: "Desactivado (24/7 Encendido)",
    msg_signed_in: "Sesión iniciada correctamente",
    msg_invalid_pw: "Contraseña incorrecta",
    msg_settings_saved_banner: "Configuración guardada a las {time}. Fuente: {source} • Diseño: {layout} ({duration}s)",
    msg_source_album: "Álbum ({album})",
    msg_source_demo: "Paquete Demo Integrado",
    msg_pw_mismatch: "Las contraseñas nuevas no coinciden",
    msg_pw_updated: "Contraseña actualizada correctamente. Tu sesión sigue activa.",
    confirm_exit_app: "¿Salir y cerrar la aplicación del marco de fotos ahora? El portal web quedará sin conexión hasta volver a iniciarla.",
    confirm_exit_app_all: "¿Salir y cerrar la aplicación en TODOS los marcos conectados ahora? Sus portales web quedarán sin conexión hasta volver a iniciarlos.",
    btn_cancel: "Cancelar",
    badge_offline: "Sin Conexión",
    msg_exited_offline: "Marco de fotos cerrado. El portal web está ahora sin conexión hasta que se vuelva a iniciar la aplicación.",
    sec_fleet: "Marcos Conectados (Flota LAN)",
    btn_scan_fleet: "Escanear LAN",
    btn_add_peer: "Añadir IP",
    ph_add_peer: "192.168.86.x o photoframe-2.local:8080",
    btn_connect_peer: "Conectar",
    fleet_all_frames: "Todos los Marcos ({count})",
    fleet_all_sub: "Difusión a todos los marcos activos",
    fleet_this_frame: "Este Marco",
    chip_fleet_single: "Red LAN",
    chip_fleet_multi: "{count} Marcos",
    target_controlling_self: "Controlando: {host} ({ip} — Este Marco)",
    target_controlling_peer: "Controlando Marco Remoto: {host} ({ip})",
    target_controlling_all: "Modo Difusión: Las acciones y ajustes se aplicarán a los {count} marcos activos.",
    lbl_pwd: "Código de Extracción Baidu Pan (pwd)",
    ph_pwd: "Código de 4 caracteres (opcional si ?pwd= está en la URL)",
    sec_update: "Actualización Automática e Instalador APK",
    lbl_update_url: "Enlace de Descarga de Actualización APK",
    btn_check_update: "Guardar y Buscar Actualización",
    lbl_apk_upload: "O Subir e Instalar Archivo .apk Directamente",
    btn_upload_apk: "Subir e Instalar APK",
    msg_select_apk: "Por favor selecciona un archivo .apk primero",
    sec_webframe: "Visor Web (/frame)",
    webframe_desc: "Ve la presentación en vivo desde cualquier navegador en tu Wi-Fi sin iniciar sesión. Cada navegador tiene su propio orden independiente.",
    lbl_webframe_switch: "Activar o desactivar Visor Web",
    lbl_webframe_profile: "Calidad de Imagen y Perfil",
    lbl_webframe_url: "Dirección del Visor Web",
    opt_wf_auto: "Auto — Ajuste Inteligente (1080p HD / 768p Tablets Antiguas)",
    opt_wf_hd: "1080p HD — Resolución Completa y 9 Collages",
    opt_wf_legacy: "768p Estándar — Modo Ligero para Tablets Antiguas",
    btn_wf_turn_on: "Activar Visor Web",
    btn_wf_turn_off: "Desactivar Visor Web",
    btn_wf_open: "Abrir /frame",
    btn_open_webframe: "Abrir Visor Web (/frame)",
    lbl_saved_albums: "Álbumes y Carpetas Guardados (Cifrados Localmente)",
    saved_albums_empty: "Aún no hay enlaces guardados. Los álbumes y carpetas conectados con éxito se cifran y guardan aquí.",
    btn_use_saved_album: "Cambiar",
    badge_saved_active: "Activo",
    broadcast_settings_note: "Modo Difusión: Solo se muestran los ajustes compartidos. Selecciona un marco individual para cambiar su URL, Hostname, Rotación o APK.",
    lbl_dark_theme_switch: "Tema oscuro",
    btn_theme_light: "Claro",
    btn_theme_dark: "Oscuro",
    title_toggle_theme: "Alternar tema oscuro"
  },
  de: {
    skip_to_main: "Zum Hauptinhalt springen",
    login_title: "Bilderrahmen-Anmeldung",
    default_pw_hint: "Das Standardpasswort lautet <b>admin</b>. Sie können es nach der Anmeldung ändern.",
    lbl_password: "Passwort",
    ph_password: "Passwort eingeben",
    btn_signin: "Anmelden",
    portal_title: "Bilderrahmen-Portal",
    btn_signout: "Abmelden",
    default_pw_banner: "Sie verwenden das Standardpasswort (<b>admin</b>). Öffnen Sie den Tab <b>System</b>, um es zu ändern.",
    nav_sections_aria: "Bereiche",
    nav_control: "Steuerung",
    nav_display: "Album & Anzeige",
    nav_system: "System",
    sec_remote: "Schnellsteuerung",
    stat_album: "Album-Medien",
    stat_layout: "Layout-Modus",
    stat_night: "Nachtmodus-Zeitplan",
    stat_display: "Anzeige",
    btn_wake_sleep: "Wach / Ruhemodus",
    btn_wake_now: "Bildschirm aufwecken",
    btn_sleep_now: "Ruhemodus aktivieren",
    btn_prev: "Zurück",
    btn_pause: "Pause / Fortsetzen",
    btn_next: "Weiter",
    btn_resync: "Album synchronisieren",
    btn_demo: "Demo abspielen",
    btn_test_vid: "Videos testen",
    sec_settings: "Album, Collagen & Zeitplan",
    sec_advanced_options: "Erweiterte Anzeige- & Netzwerkoptionen",
    lbl_album: "Geteilte Album-URL (leer oder 'demo' für Demo-Paket)",
    ph_album: "https://photos.app.goo.gl/... (oder leer für Demo)",
    lbl_device_name: "Geräte-Hostname (.local — automatische Nummerierung)",
    ph_device_name: "photoframe (z. B. wohnzimmer)",
    lbl_language: "Sprache",
    lbl_layout: "Layout / Collagen-Stil",
    opt_layout_collage: "Collagen-Mix (Alle 9 Raster + Hochformat)",
    opt_layout_single: "Nur Einzelbild-Vollbild",
    opt_layout_trio: "Grid Trio (1 Quer + 2 Hoch)",
    opt_layout_classic5: "Grid Classic 5 (1 Hauptbild + 2H + 2Q)",
    opt_layout_mosaic7: "Grid Mosaic 7 (Wechselnde Teilung)",
    opt_layout_sidebar9: "Grid Sidebar 9 (Links gestapelt + Rechts Hauptbild)",
    opt_layout_mondrian8: "Grid Mondrian 8 (Symmetrisches Mosaik)",
    opt_layout_323: "Grid 3-2-3 (3H + 2Q + 3H)",
    opt_layout_stack: "Stack 2 (2 Querformat)",
    opt_layout_triple: "Stack 3 (3 Querformat)",
    opt_layout_bento: "Bento 4 (4 Querformat)",
    lbl_duration: "Anzeigedauer (Sekunden)",
    lbl_transition: "Folienübergang",
    opt_trans_none: "Keiner (Direkter Schnitt — Standard)",
    opt_trans_fade: "Überblenden (Crossfade)",
    opt_trans_fade_black: "Schwarzblende",
    opt_trans_slide_left: "Nach links schieben",
    opt_trans_slide_right: "Nach rechts schieben",
    opt_trans_slide_up: "Nach oben schieben",
    opt_trans_wipe: "Horizontaler Wischübergang",
    opt_trans_random: "Zufälliger Übergangs-Mix",
    lbl_videos: "Videomodus",
    opt_vid_play: "Live-Video in Kacheln abspielen",
    opt_vid_skip: "Videos überspringen",
    opt_vid_still: "Statisches Vorschaubild anzeigen",
    lbl_max_vid: "Maximale Videodauer (Sekunden)",
    lbl_fit: "Einzelbild-Anpassung",
    opt_fit_blur: "Einpassen über Bokeh-Hintergrund",
    opt_fit_contain: "Einpassen mit schwarzen Balken",
    opt_fit_cover: "Mittig zuschneiden (Cover)",
    lbl_rotate: "Bildschirmdrehung",
    opt_rot_270: "270° (Hochformat)",
    opt_rot_90: "90° (Hochformat gedreht)",
    opt_rot_0: "0° (Querformat)",
    opt_rot_180: "180° (Querformat gedreht)",
    lbl_refresh: "Auto-Sync-Intervall (Stunden)",
    lbl_shuffle: "Fotos & Videos zufällig mischen",
    lbl_night_enabled: "Geplanten Nachtmodus aktivieren",
    lbl_night_start: "Nachtmodus Start (Ruhemodus)",
    lbl_night_end: "Nachtmodus Ende (Aufwachen)",
    btn_save_settings: "Einstellungen speichern & anwenden",
    btn_saving_settings: "Speichern & Anwenden...",
    btn_saved_settings: "Gespeichert & Angewendet!",
    sec_security: "Sicherheit & Passwort ändern",
    lbl_pw_current: "Aktuelles Passwort",
    lbl_pw_new: "Neues Passwort",
    ph_pw_new: "Mindestens 4 Zeichen",
    lbl_pw_confirm: "Neues Passwort bestätigen",
    btn_save_pw: "Passwort aktualisieren",
    sec_system: "Systemsteuerung & RAM-Protokoll",
    btn_refresh_log: "Protokoll aktualisieren",
    btn_exit_app: "Bilderrahmen beenden",
    badge_night: "Nachtmodus",
    badge_paused: "Pausiert",
    badge_active: "Aktiv",
    stat_items_fmt: "{prefix}{photos} Elemente ({videos} Videos)",
    stat_night_disabled: "Deaktiviert (24/7 An)",
    msg_signed_in: "Erfolgreich angemeldet",
    msg_invalid_pw: "Ungültiges Passwort",
    msg_settings_saved_banner: "Einstellungen um {time} gespeichert! Quelle: {source} • Layout: {layout} ({duration}s)",
    msg_source_album: "Geteiltes Album ({album})",
    msg_source_demo: "Demo-Showcase-Paket",
    msg_pw_mismatch: "Neue Passwörter stimmen nicht überein",
    msg_pw_updated: "Passwort erfolgreich aktualisiert. Ihre Sitzung bleibt aktiv.",
    confirm_exit_app: "Bilderrahmen-App jetzt beenden und schließen? Das Web-Steuerportal bleibt offline, bis die App wieder gestartet wird.",
    confirm_exit_app_all: "Bilderrahmen-App auf ALLEN verbundenen Rahmen jetzt beenden? Die Web-Steuerportale bleiben bis zum Neustart offline.",
    btn_cancel: "Abbrechen",
    badge_offline: "Offline (Beendet)",
    msg_exited_offline: "Bilderrahmen beendet. Das Web-Steuerportal ist jetzt offline, bis die App wieder gestartet wird.",
    sec_fleet: "Verbundene Rahmen (LAN-Flotte)",
    btn_scan_fleet: "LAN scannen",
    btn_add_peer: "IP hinzufügen",
    ph_add_peer: "192.168.86.x oder photoframe-2.local:8080",
    btn_connect_peer: "Verbinden",
    fleet_all_frames: "Alle Rahmen ({count})",
    fleet_all_sub: "Smart-Broadcast an alle aktiven Rahmen",
    fleet_this_frame: "Dieser Rahmen",
    chip_fleet_single: "LAN-Geräte",
    chip_fleet_multi: "{count} Rahmen",
    target_controlling_self: "Steuerung: {host} ({ip} — Dieser Rahmen)",
    target_controlling_peer: "Fernsteuerung: {host} ({ip})",
    target_controlling_all: "Broadcast-Modus: Aktionen und gemeinsame Einstellungen gelten gleichzeitig für alle {count} Rahmen.",
    lbl_pwd: "Baidu Pan Extraktionscode (pwd)",
    ph_pwd: "4-stelliger Code (optional wenn ?pwd= in URL)",
    sec_update: "App-Auto-Update & APK-Installation",
    lbl_update_url: "APK-Update-Download-Link",
    btn_check_update: "Speichern & Auf Update prüfen",
    lbl_apk_upload: "Oder .apk-Datei direkt hochladen & installieren",
    btn_upload_apk: "APK hochladen & installieren",
    msg_select_apk: "Bitte wählen Sie zuerst eine .apk-Datei aus",
    sec_webframe: "Web-Diashow-Viewer (/frame)",
    webframe_desc: "Zeigen Sie die Live-Diashow ohne Anmeldung in jedem Browser im WLAN an. Jeder Browser erhält eine eigene Reihenfolge.",
    lbl_webframe_switch: "Web-Viewer ein-/ausschalten",
    lbl_webframe_profile: "Bildqualität & Geräteprofil",
    lbl_webframe_url: "Web-Viewer-Adresse",
    opt_wf_auto: "Auto — Smart Match (1080p HD / 768p ältere Tablets)",
    opt_wf_hd: "1080p HD — Volle Auflösung & alle 9 Collagen",
    opt_wf_legacy: "768p Standard — Speicherschonend für ältere Tablets",
    btn_wf_turn_on: "Web-Viewer aktivieren",
    btn_wf_turn_off: "Web-Viewer deaktivieren",
    btn_wf_open: "/frame öffnen",
    btn_open_webframe: "Web-Viewer öffnen (/frame)",
    lbl_saved_albums: "Gespeicherte Alben & Ordner (Lokal verschlüsselt)",
    saved_albums_empty: "Noch keine Links gespeichert. Erfolgreich verbundene Alben und Ordner werden hier verschlüsselt abgelegt.",
    btn_use_saved_album: "Wechseln",
    badge_saved_active: "Aktiv",
    broadcast_settings_note: "Broadcast-Modus: Nur gemeinsame Einstellungen werden angezeigt. Wählen Sie oben einen einzelnen Rahmen für Album-URL, Hostname oder Drehung.",
    lbl_dark_theme_switch: "Dunkles Design",
    btn_theme_light: "Hell",
    btn_theme_dark: "Dunkel",
    title_toggle_theme: "Dunkles Design umschalten"
  }
};

const PORTAL_TABS = ['control', 'display', 'system'];
let currentLang = localStorage.getItem('frame_lang') || 'en';
let currentTheme = (document.documentElement.getAttribute('data-theme') === 'light') ? 'light' : 'dark';
let activeTab = (function() {
  try {
    const s = sessionStorage.getItem('frame_active_tab');
    return PORTAL_TABS.includes(s) ? s : 'control';
  } catch (e) {
    return 'control';
  }
})();
let formPopulated = false;
let lastStatusSnapshot = null;
let selectedTarget = 'self';
let fleetNodes = [];
let fleetDrawerExpanded = false;
let activeTargetEdition = 'gphotos';
let activeTargetPlatform = 'linux';
let savedAlbumsList = [];
let lastActiveAlbumUrl = '';
let lastActiveAlbumTitle = '';
let portalExitedOffline = false;
let consecutiveStatusFailures = 0;
let lastModalTriggerEl = null;

function setBtnLabel(elOrId, text) {
  const el = typeof elOrId === 'string' ? document.getElementById(elOrId) : elOrId;
  if (!el) return;
  const lbl = el.querySelector('.btn-label');
  if (lbl) {
    lbl.textContent = text;
  } else {
    el.textContent = text;
  }
}

function setBadgeContent(elOrId, cls, text) {
  const el = typeof elOrId === 'string' ? document.getElementById(elOrId) : elOrId;
  if (!el) return;
  if (cls) el.className = cls;
  const txtEl = el.querySelector('.badge-text');
  if (txtEl) {
    txtEl.textContent = text;
  } else {
    el.textContent = text;
  }
}

function escapeHtml(s) {
  return String(s || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function t(key, vars = {}) {
  const dict = I18N[currentLang] || I18N.en;
  let str = dict[key] || I18N.en[key] || key;
  for (const [k, v] of Object.entries(vars)) {
    str = str.replace(`{${k}}`, v);
  }
  return str;
}

/* -------- THEME SWITCHING (M3 HORIZONTAL SWITCH + OS PREFERENCE) -------- */
function updateThemeSwitches() {
  const isDark = (currentTheme === 'dark');
  const ariaLbl = t('lbl_dark_theme_switch');
  const tip = t('title_toggle_theme');
  ['login-theme-switch', 'header-theme-switch'].forEach(id => {
    const sw = document.getElementById(id);
    if (!sw) return;
    sw.selected = isDark;
    if (isDark) sw.setAttribute('selected', '');
    else sw.removeAttribute('selected');
    sw.setAttribute('aria-checked', String(isDark));
    sw.setAttribute('aria-label', ariaLbl);
    const wrap = sw.closest('.theme-switch-wrap');
    if (wrap) wrap.setAttribute('title', tip);
  });
}

function applyTheme(theme, persist = true) {
  currentTheme = (theme === 'light') ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', currentTheme);
  if (persist) {
    try { localStorage.setItem('frame_theme', currentTheme); } catch (e) {}
  }
  updateThemeSwitches();
}

function toggleTheme() {
  applyTheme(currentTheme === 'dark' ? 'light' : 'dark', true);
}

function onThemeSwitchChange(el) {
  applyTheme(Boolean(el && el.selected) ? 'dark' : 'light', true);
}

function onThemeSwitchFallbackClick(ev, el) {
  if (window.customElements && window.customElements.get('md-switch')) return;
  if (ev) ev.preventDefault();
  const next = !(el && el.getAttribute('aria-checked') === 'true');
  applyTheme(next ? 'dark' : 'light', true);
}

function onThemeSwitchFallbackKey(ev, el) {
  if (window.customElements && window.customElements.get('md-switch')) return;
  if (ev.key === ' ' || ev.key === 'Enter') {
    ev.preventDefault();
    onThemeSwitchFallbackClick(ev, el);
  }
}

try {
  if (window.matchMedia) {
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    const onOsChange = function(ev) {
      try {
        const saved = localStorage.getItem('frame_theme');
        if (saved === 'light' || saved === 'dark') return;
      } catch (e) {}
      applyTheme(ev.matches ? 'dark' : 'light', false);
    };
    if (mq.addEventListener) mq.addEventListener('change', onOsChange);
    else if (mq.addListener) mq.addListener(onOsChange);
  }
} catch (e) {}

/* -------- 3-TAB NAVIGATION (M3 TABS + MOBILE BOTTOM NAV BAR) -------- */
function switchPortalTab(tabName, focusMobTab = false) {
  if (!PORTAL_TABS.includes(tabName)) tabName = 'control';
  activeTab = tabName;
  try { sessionStorage.setItem('frame_active_tab', tabName); } catch (e) {}

  PORTAL_TABS.forEach((name, idx) => {
    const isSel = (name === tabName);
    const panel = document.getElementById(`panel-${name}`);
    if (panel) panel.classList.toggle('hidden', !isSel);

    const dTab = document.getElementById(`tab-${name}`);
    if (dTab) {
      if (isSel) dTab.setAttribute('active', '');
      else dTab.removeAttribute('active');
    }

    const mTab = document.getElementById(`mob-tab-${name}`);
    if (mTab) {
      mTab.setAttribute('aria-selected', String(isSel));
      mTab.setAttribute('tabindex', isSel ? '0' : '-1');
      if (isSel && focusMobTab) mTab.focus();
    }
  });

  const desktopTabs = document.getElementById('desktop-m3-tabs');
  const activeIdx = PORTAL_TABS.indexOf(tabName);
  if (desktopTabs && typeof desktopTabs.activeTabIndex === 'number' && desktopTabs.activeTabIndex !== activeIdx) {
    desktopTabs.activeTabIndex = activeIdx;
  }
}

function onDesktopTabChange(tabsEl) {
  const idx = Number(tabsEl && tabsEl.activeTabIndex);
  if (idx >= 0 && idx < PORTAL_TABS.length) {
    switchPortalTab(PORTAL_TABS[idx]);
  }
}

function onDesktopTabFallback(tabName, idx) {
  if (window.customElements && window.customElements.get('md-tabs')) return;
  switchPortalTab(tabName);
}

function onTabListKeyDown(ev, curTab) {
  const idx = PORTAL_TABS.indexOf(curTab);
  if (idx < 0) return;
  let nextIdx = -1;
  if (ev.key === 'ArrowRight') nextIdx = (idx + 1) % PORTAL_TABS.length;
  else if (ev.key === 'ArrowLeft') nextIdx = (idx - 1 + PORTAL_TABS.length) % PORTAL_TABS.length;
  else if (ev.key === 'Home') nextIdx = 0;
  else if (ev.key === 'End') nextIdx = PORTAL_TABS.length - 1;
  if (nextIdx >= 0) {
    ev.preventDefault();
    switchPortalTab(PORTAL_TABS[nextIdx], true);
  }
}

function openPasswordTab() {
  switchPortalTab('system');
  setTimeout(() => {
    const pwEl = document.getElementById('pw-current');
    if (pwEl) pwEl.focus();
  }, 60);
}

/* -------- FLEET DRAWER & PROGRESSIVE DISCLOSURE -------- */
function toggleFleetDrawer() {
  fleetDrawerExpanded = !fleetDrawerExpanded;
  updateFleetCardVisibility();
}

function updateFleetCardVisibility() {
  const card = document.getElementById('fleet-card');
  const chip = document.getElementById('header-fleet-chip');
  const chipLabel = document.getElementById('header-fleet-chip-label');
  const multiFrame = fleetNodes.length > 1;
  const shouldShow = multiFrame || fleetDrawerExpanded;
  if (card) card.classList.toggle('hidden', !shouldShow);
  if (chip) chip.setAttribute('aria-expanded', String(shouldShow));
  if (chipLabel) {
    const selfNode = fleetNodes.find(n => n.is_self) || fleetNodes[0] || {};
    if (multiFrame) {
      chipLabel.textContent = selectedTarget === 'all'
        ? t('fleet_all_frames', {count: fleetNodes.filter(n => n.online !== false).length})
        : t('chip_fleet_multi', {count: fleetNodes.length});
    } else {
      chipLabel.textContent = selfNode.mdns_hostname || t('chip_fleet_single');
    }
  }
}

function syncNightFieldsVisibility() {
  const enabled = Boolean(document.getElementById('cfg-night-enabled')?.checked);
  ['group-night-start', 'group-night-end'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.style.opacity = enabled ? '1' : '0.55';
  });
}

/* -------- SAVED ALBUMS -------- */
function renderSavedAlbums(list, currentAlbum, currentTitle) {
  savedAlbumsList = Array.isArray(list) ? list : [];
  if (typeof currentAlbum === 'string') lastActiveAlbumUrl = currentAlbum;
  if (typeof currentTitle === 'string') lastActiveAlbumTitle = currentTitle;

  const badgeEl = document.getElementById('active-album-title-badge');
  if (badgeEl) {
    if (lastActiveAlbumTitle && lastActiveAlbumUrl && lastActiveAlbumUrl !== 'demo') {
      badgeEl.textContent = lastActiveAlbumTitle;
      badgeEl.classList.remove('hidden');
    } else {
      badgeEl.classList.add('hidden');
    }
  }

  const container = document.getElementById('saved-albums-list');
  if (!container) return;
  if (!savedAlbumsList.length) {
    container.innerHTML = `<div style="font-size:0.82rem; color:var(--md-sys-color-on-surface-variant); grid-column:1/-1;">${escapeHtml(t('saved_albums_empty'))}</div>`;
    return;
  }

  const normCur = String(lastActiveAlbumUrl || '').trim();
  const folderSvg = `<svg class="m3-icon-sm" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path fill="currentColor" d="M10 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-8l-2-2z"/></svg>`;
  const cloudSvg = `<svg class="m3-icon-sm" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path fill="currentColor" d="M19.35 10.04C18.67 6.59 15.64 4 12 4 9.11 4 6.6 5.64 5.35 8.04 2.34 8.36 0 10.91 0 14c0 3.31 2.69 6 6 6h13c2.76 0 5-2.24 5-5 0-2.64-2.05-4.78-4.65-4.96z"/></svg>`;
  const closeSvg = `<svg class="m3-icon-sm" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path fill="currentColor" d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg>`;

  container.innerHTML = savedAlbumsList.map(item => {
    const id = escapeHtml(item.id || item.album || '');
    const url = String(item.album || '');
    const pwd = String(item.pwd || '');
    const isBaidu = item.edition === 'baidupan' || url.toLowerCase().includes('pan.baidu.com');
    const iconSvg = isBaidu ? cloudSvg : folderSvg;
    const rawName = String(item.name || '').trim();
    const shortUrl = url.replace(/^https?:\\/\\//i, '');
    const displayTitle = rawName || shortUrl;
    const isCurrent = normCur && normCur !== 'demo' && (url === normCur || shortUrl === normCur.replace(/^https?:\\/\\//i, ''));
    const pwdTag = pwd ? ` • pwd: ${escapeHtml(pwd)}` : '';
    const subLine = rawName ? `${escapeHtml(shortUrl)}${pwdTag}` : (pwd ? `pwd: ${escapeHtml(pwd)}` : (isBaidu ? 'Baidu Pan' : 'Google Photos'));
    const activeBadge = isCurrent
      ? `<span class="badge active" style="font-size:0.72rem; padding:3px 9px;"><span class="status-dot" aria-hidden="true"></span>${escapeHtml(t('badge_saved_active'))}</span>`
      : `<button type="button" class="tonal saved-use-btn" onclick="applySavedAlbum(event, '${id}')">${escapeHtml(t('btn_use_saved_album'))}</button>`;
    return `<div class="saved-album-pill ${isCurrent ? 'active' : ''}" role="button" tabindex="0" onclick="applySavedAlbum(event, '${id}')" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();applySavedAlbum(event, '${id}');}">
      <div class="saved-album-info">
        <div class="saved-album-title" title="${escapeHtml(displayTitle)}">${iconSvg}<span class="saved-album-title-text">${escapeHtml(displayTitle)}</span></div>
        <div class="saved-album-meta" title="${escapeHtml(url)}">${subLine}</div>
      </div>
      <div class="saved-album-actions">
        ${activeBadge}
        <button type="button" class="icon-remove-btn" aria-label="Remove saved link" title="Remove" onclick="deleteSavedAlbum(event, '${id}')">${closeSvg}</button>
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

function autoExtractBaiduPwd() {
  const albumEl = document.getElementById('cfg-album');
  const pwdEl = document.getElementById('cfg-pwd');
  if (!albumEl || !pwdEl) return;
  const raw = albumEl.value || '';
  const m = raw.match(/[?&]pwd=([a-zA-Z0-9]{4})/) || raw.match(/(?:提取码|密码|pwd)\\s*[:：=]?\\s*([a-zA-Z0-9]{4})/i);
  if (m && !pwdEl.value.trim()) {
    pwdEl.value = m[1];
  }
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
  const desktopTabs = document.getElementById('desktop-m3-tabs');
  if (desktopTabs) desktopTabs.setAttribute('aria-label', t('nav_sections_aria'));
  const mobNav = document.getElementById('mobile-bottom-nav');
  if (mobNav) mobNav.setAttribute('aria-label', t('nav_sections_aria'));
  const wfSw = document.getElementById('sw-webframe-power');
  if (wfSw) wfSw.setAttribute('aria-label', t('lbl_webframe_switch'));

  updateThemeSwitches();
  if (lastStatusSnapshot) {
    renderStatusSnapshot(lastStatusSnapshot);
  }
  if (fleetNodes && fleetNodes.length > 0) {
    renderFleetBar(fleetNodes);
  } else {
    updateFleetCardVisibility();
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

  const closeSvg = `<svg class="m3-icon-sm" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path fill="currentColor" d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg>`;
  let html = '';
  if (fleetNodes.length > 1) {
    const isAll = (selectedTarget === 'all');
    html += `<div class="fleet-pill ${isAll ? 'selected' : ''}" role="button" tabindex="0" onclick="selectFleetTarget('all')" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();selectFleetTarget('all');}">
      <div class="fleet-pill-top">
        <span>${escapeHtml(t('fleet_all_frames', {count: onlineNodes.length}))}</span>
      </div>
      <div class="fleet-pill-meta">Broadcast Mode</div>
      <div class="fleet-pill-sub">${escapeHtml(t('fleet_all_sub'))}</div>
    </div>`;
  }

  fleetNodes.forEach(node => {
    const targetKey = node.is_self ? 'self' : node.id;
    const isSelected = (selectedTarget === targetKey) || (node.is_self && selectedTarget === node.id);
    const statusDotColor = node.online === false ? '#D9534F' : (node.is_night_mode ? '#D49B38' : (node.is_paused ? '#8E8E93' : '#30A46C'));
    const platLabel = node.platform === 'android_tv' ? 'Android TV' : 'Linux';
    const edLabel = node.edition === 'baidupan' ? 'Baidu' : 'Google';
    const selfTag = node.is_self ? ` <span style="font-size:0.74rem; color:var(--md-sys-color-accent-text);">(${escapeHtml(t('fleet_this_frame'))})</span>` : '';
    const removeBtn = (!node.is_self && (node.is_manual || node.online === false))
      ? `<button type="button" class="icon-remove-btn" aria-label="Remove frame" title="Remove" onclick="removePeer(event, '${escapeHtml(node.id)}')">${closeSvg}</button>`
      : '';
    const hostTitle = escapeHtml(node.mdns_hostname || node.ip || node.id);
    const mediaSummary = node.online === false
      ? 'Offline'
      : `${node.is_demo_mode ? 'Demo • ' : ''}${node.photo_count ?? 0} items • ${escapeHtml(node.landscape_mode || 'collage')}`;
    html += `<div class="fleet-pill ${isSelected ? 'selected' : ''}" role="button" tabindex="0" onclick="selectFleetTarget('${escapeHtml(targetKey)}')" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();selectFleetTarget('${escapeHtml(targetKey)}');}">
      <div class="fleet-pill-top">
        <span style="display:inline-flex; align-items:center; gap:7px;"><span class="status-dot" aria-hidden="true" style="color:${statusDotColor}"></span>${hostTitle}${selfTag}</span>
        ${removeBtn}
      </div>
      <div class="fleet-pill-meta">${escapeHtml(node.ip)}:${node.port || 8080} • ${platLabel} (${edLabel}) • v${escapeHtml(node.app_version || '1.0')}</div>
      <div class="fleet-pill-sub">${mediaSummary}</div>
    </div>`;
  });
  grid.innerHTML = html;

  if (banner) {
    if (selectedTarget === 'all') {
      banner.textContent = t('target_controlling_all', {count: onlineNodes.length});
    } else if (selectedTarget === 'self' || selectedTarget === selfNode.id) {
      const selfPort = selfNode.port || 8080;
      const selfPortSuffix = selfPort !== 8080 ? `:${selfPort}` : '';
      banner.textContent = t('target_controlling_self', {
        host: (selfNode.mdns_hostname || 'photoframe.local') + selfPortSuffix,
        ip: (selfNode.ip || '127.0.0.1') + `:${selfPort}`
      });
    } else {
      const peer = fleetNodes.find(n => n.id === selectedTarget) || {};
      const peerPort = peer.port || 8080;
      const peerPortSuffix = peerPort !== 8080 ? `:${peerPort}` : '';
      banner.textContent = t('target_controlling_peer', {
        host: (peer.mdns_hostname || selectedTarget) + peerPortSuffix,
        ip: peer.ip ? `${peer.ip}:${peerPort}` : selectedTarget
      });
    }
  }

  updateFleetCardVisibility();
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

  const wfUrlGroup = document.getElementById('group-webframe-url');
  if (wfUrlGroup) wfUrlGroup.classList.toggle('hidden', isAll);
  const btnOpenWf = document.getElementById('btn-open-webframe');
  if (btnOpenWf) btnOpenWf.classList.toggle('hidden', isAll);

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
  if (st.is_night_mode) {
    setBadgeContent('state-badge', 'badge night', t('badge_night'));
    setBtnLabel('btn-wake-sleep', t('btn_wake_now'));
  } else if (st.is_paused) {
    setBadgeContent('state-badge', 'badge', t('badge_paused'));
    setBtnLabel('btn-wake-sleep', t('btn_sleep_now'));
  } else {
    setBadgeContent('state-badge', 'badge active', t('badge_active'));
    setBtnLabel('btn-wake-sleep', t('btn_sleep_now'));
  }

  const titlePrefix = (!st.is_demo_mode && st.album_title) ? `${st.album_title} • ` : '';
  const modePrefix = st.is_demo_mode ? 'Demo: ' : titlePrefix;
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
    hdrHostBadge.textContent = st.mdns_hostname;
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

  const wfEnabled = Boolean(st.web_frame_enabled);
  const wfClients = st.web_frame_clients || 0;
  if (wfEnabled) {
    setBadgeContent('webframe-state-badge', 'badge active', `ON (/frame • ${wfClients})`);
  } else {
    setBadgeContent('webframe-state-badge', 'badge', 'OFF');
  }
  const wfSw = document.getElementById('sw-webframe-power');
  if (wfSw) {
    wfSw.selected = wfEnabled;
    if (wfEnabled) wfSw.setAttribute('selected', '');
    else wfSw.removeAttribute('selected');
    wfSw.setAttribute('aria-checked', String(wfEnabled));
  }
  const wfBtn = document.getElementById('btn-webframe-toggle');
  if (wfBtn) {
    setBtnLabel(wfBtn, wfEnabled ? t('btn_wf_turn_off') : t('btn_wf_turn_on'));
    wfBtn.className = wfEnabled ? 'danger' : 'primary';
  }

  const wfHost = st.lan_ip && st.lan_ip !== '127.0.0.1' ? st.lan_ip : (window.location.hostname || 'localhost');
  const activeNode = (selectedTarget === 'self' ? fleetNodes.find(n => n.is_self) : fleetNodes.find(n => n.id === selectedTarget)) || {};
  const wfPort = st.web_port || activeNode.port || window.location.port || 8080;
  const wfPathLink = document.getElementById('webframe-path-link');
  if (wfPathLink) {
    wfPathLink.href = `http://${wfHost}:${wfPort}/frame`;
    wfPathLink.textContent = `http://${wfHost}:${wfPort}/frame`;
  }
  const wfProfSel = document.getElementById('cfg-webframe-profile');
  if (wfProfSel && st.web_frame_profile) {
    wfProfSel.value = st.web_frame_profile;
  }
}

async function toggleWebFramePower() {
  const wfEnabled = Boolean(lastStatusSnapshot && lastStatusSnapshot.web_frame_enabled);
  const action = wfEnabled ? 'web_frame_off' : 'web_frame_on';
  await sendAction(action);
}

function onWebFrameSwitchChange(el) {
  const wantOn = Boolean(el && el.selected);
  sendAction(wantOn ? 'web_frame_on' : 'web_frame_off');
}

function onWebFrameSwitchFallbackClick(ev, el) {
  if (window.customElements && window.customElements.get('md-switch')) return;
  if (ev) ev.preventDefault();
  toggleWebFramePower();
}

function onWebFrameSwitchFallbackKey(ev, el) {
  if (window.customElements && window.customElements.get('md-switch')) return;
  if (ev.key === ' ' || ev.key === 'Enter') {
    ev.preventDefault();
    toggleWebFramePower();
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
      showToast(`Web Viewer picture quality set to ${labelMap[profileVal] || profileVal}`);
      fetchStatus(false);
    }
  } catch (e) {}
}

function openWebFrameViewer() {
  const wfEnabled = Boolean(lastStatusSnapshot && lastStatusSnapshot.web_frame_enabled);
  if (!wfEnabled) {
    showToast('Enable the Web Slideshow Viewer first to launch /frame', true);
    return;
  }
  const wfHost = (lastStatusSnapshot && lastStatusSnapshot.lan_ip && lastStatusSnapshot.lan_ip !== '127.0.0.1')
    ? lastStatusSnapshot.lan_ip
    : (window.location.hostname || 'localhost');
  const activeNode = (selectedTarget === 'self' ? fleetNodes.find(n => n.is_self) : fleetNodes.find(n => n.id === selectedTarget)) || {};
  const wfPort = (lastStatusSnapshot && lastStatusSnapshot.web_port) || activeNode.port || window.location.port || 8080;
  window.open(`http://${wfHost}:${wfPort}/frame`, '_blank');
}

function showToast(msg, isError = false) {
  const tEl = document.getElementById('toast');
  if (!tEl) return;
  tEl.textContent = msg;
  tEl.style.borderColor = isError ? 'var(--md-sys-color-error)' : 'var(--md-sys-color-success)';
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
  setBadgeContent('state-badge', 'badge offline', t('badge_offline'));
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
    switchPortalTab(activeTab);

    if (selectedTarget && selectedTarget !== 'self' && selectedTarget !== 'all' && data.target === 'self') {
      selectedTarget = 'self';
    }
    activeTargetEdition = data.target_edition || data.status?.edition || 'gphotos';
    activeTargetPlatform = data.target_platform || data.status?.platform || 'linux';
    if (data.fleet) {
      renderFleetBar(data.fleet);
    } else {
      updateFleetCardVisibility();
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
      syncNightFieldsVisibility();
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
        showInlineBanner('system-confirm-banner', errMsg, true);
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
        showInlineBanner('system-confirm-banner', errMsg, true);
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
  lastModalTriggerEl = document.activeElement;
  const modal = document.getElementById('exit-confirm-modal');
  const msgEl = document.getElementById('exit-modal-msg');
  const titleEl = document.getElementById('exit-modal-title');
  const cancelBtn = document.getElementById('btn-exit-cancel');
  const confirmBtn = document.getElementById('btn-exit-confirm');
  const promptKey = selectedTarget === 'all' ? 'confirm_exit_app_all' : 'confirm_exit_app';
  if (titleEl) setBtnLabel(titleEl, t('btn_exit_app'));
  if (msgEl) msgEl.textContent = t(promptKey);
  if (cancelBtn) setBtnLabel(cancelBtn, t('btn_cancel'));
  if (confirmBtn) {
    setBtnLabel(confirmBtn, t('btn_exit_app'));
    confirmBtn.disabled = false;
  }
  if (modal) {
    modal.classList.remove('hidden');
    if (cancelBtn) cancelBtn.focus();
  }
}

function closeExitModal() {
  const modal = document.getElementById('exit-confirm-modal');
  if (modal) {
    modal.classList.add('hidden');
  }
  if (lastModalTriggerEl && typeof lastModalTriggerEl.focus === 'function') {
    try { lastModalTriggerEl.focus(); } catch (e) {}
  }
}

async function executeConfirmedExit() {
  const confirmBtn = document.getElementById('btn-exit-confirm');
  if (confirmBtn) confirmBtn.disabled = true;
  closeExitModal();
  await sendAction('exit_app');
}

document.addEventListener('keydown', function(ev) {
  const modal = document.getElementById('exit-confirm-modal');
  if (!modal || modal.classList.contains('hidden')) return;
  if (ev.key === 'Escape') {
    closeExitModal();
    return;
  }
  if (ev.key === 'Tab') {
    const cancelBtn = document.getElementById('btn-exit-cancel');
    const confirmBtn = document.getElementById('btn-exit-confirm');
    const focusables = [cancelBtn, confirmBtn].filter(el => el && !el.disabled);
    if (focusables.length === 2) {
      if (ev.shiftKey && document.activeElement === focusables[0]) {
        ev.preventDefault();
        focusables[1].focus();
      } else if (!ev.shiftKey && document.activeElement === focusables[1]) {
        ev.preventDefault();
        focusables[0].focus();
      }
    }
  }
});

const settingsFormEl = document.getElementById('settings-form');
if (settingsFormEl) {
  settingsFormEl.addEventListener('invalid', function() {
    const disc = document.getElementById('advanced-settings-disclosure');
    if (disc) disc.open = true;
  }, true);
}

async function saveSettings(e) {
  e.preventDefault();
  autoExtractBaiduPwd();
  const btn = document.getElementById('btn-save-settings');
  if (btn) {
    btn.disabled = true;
    setBtnLabel(btn, t('btn_saving_settings'));
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
        setBtnLabel(btn, t('btn_saved_settings'));
        setTimeout(() => { setBtnLabel(btn, t('btn_save_settings')); btn.disabled = false; }, 2500);
      }
      setTimeout(() => fetchStatus(true), 600);
    } else {
      const errMsg = data.error || 'Failed to save settings';
      showToast(errMsg, true);
      showInlineBanner('settings-confirm-banner', errMsg, true);
      if (btn) { setBtnLabel(btn, t('btn_save_settings')); btn.disabled = false; }
    }
  } catch (err) {
    showToast('Network error while saving settings', true);
    showInlineBanner('settings-confirm-banner', 'Network error while saving settings', true);
    if (btn) { setBtnLabel(btn, t('btn_save_settings')); btn.disabled = false; }
  }
}

async function changePassword(e) {
  e.preventDefault();
  const current_password = document.getElementById('pw-current').value;
  const new_password = document.getElementById('pw-new').value;
  const confirm_password = document.getElementById('pw-confirm').value;
  if (new_password !== confirm_password) {
    showToast(t('msg_pw_mismatch'), true);
    showInlineBanner('pw-confirm-banner', t('msg_pw_mismatch'), true);
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
    showInlineBanner('pw-confirm-banner', errMsg, true);
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
    showInlineBanner('update-confirm-banner', data.message || 'Checking for updates...', false);
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
    showInlineBanner('update-confirm-banner', t('msg_select_apk'), true);
    return;
  }
  const file = fileInput.files[0];
  if (btn) {
    btn.disabled = true;
    setBtnLabel(btn, 'Uploading 0%...');
  }
  const xhr = new XMLHttpRequest();
  xhr.open('POST', `/api/update_upload?target=${encodeURIComponent(selectedTarget)}`, true);
  xhr.setRequestHeader('Content-Type', 'application/vnd.android.package-archive');
  xhr.upload.onprogress = function(ev) {
    if (ev.lengthComputable && btn) {
      const pct = Math.round((ev.loaded * 100) / ev.total);
      setBtnLabel(btn, `Uploading ${pct}%...`);
    }
  };
  xhr.onload = function() {
    if (btn) {
      btn.disabled = false;
      setBtnLabel(btn, t('btn_upload_apk'));
    }
    try {
      const data = JSON.parse(xhr.responseText || '{}');
      if (xhr.status >= 200 && xhr.status < 300 && data.ok) {
        showToast(data.message || 'APK uploaded & installing');
        showInlineBanner('update-confirm-banner', data.message || 'APK uploaded & installing', false);
        fileInput.value = '';
        setTimeout(() => fetchStatus(false), 1000);
      } else {
        const err = data.error || ('Upload failed (HTTP ' + xhr.status + ')');
        showToast(err, true);
        showInlineBanner('update-confirm-banner', err, true);
      }
    } catch (e) {
      showToast('Invalid response from server', true);
    }
  };
  xhr.onerror = function() {
    if (btn) {
      btn.disabled = false;
      setBtnLabel(btn, t('btn_upload_apk'));
    }
    showToast('Network error during APK upload', true);
    showInlineBanner('update-confirm-banner', 'Network error during APK upload', true);
  };
  xhr.send(file);
}

applyTheme(currentTheme, false);
setLanguage(currentLang);
switchPortalTab(activeTab);
fetchStatus(true);
setInterval(() => fetchStatus(false), 5000);
</script>
</body>
</html>"""


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

            if parsed.path.startswith("/assets/m3/"):
                asset_name = parsed.path[len("/assets/m3/"):]
                accept_enc = (self.headers.get("Accept-Encoding", "") or "").lower()
                asset = get_m3_asset(asset_name, accept_gzip=("gzip" in accept_enc))
                if asset is not None:
                    body, is_gz = asset
                    self.send_response(200)
                    self.send_header("Content-Type", "application/javascript; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    if is_gz:
                        self.send_header("Content-Encoding", "gzip")
                    self.send_header("Cache-Control", "public, max-age=604800, immutable")
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self._send_json(404, {"error": "Not found"})
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
