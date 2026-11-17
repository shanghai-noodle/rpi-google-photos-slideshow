"""
Modular Web Slideshow Viewer (/frame) Controller, Composer & Weather Resolver
=============================================================================
Hosts a lightweight browser-based photo frame on the same HTTP server (`:8080/frame`)
that can be enabled/disabled live from the Admin Portal, sharing the synced photo
album while advancing slides in each browser's own independent order and screen
orientation (videos are rendered as high-resolution static poster frames inside
single slides and collage tiles).

Includes an iPad-style Clock & Weather ambient overlay (Digital / Analog / Hidden,
Bottom Banner & Corner positions, 6-language localization, and a Smart Dual-Region
Weather Resolver supporting Tencent Weather + CMA Sojson for Mainland China and
Open-Meteo for global cities with auto-IP location detection).

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
import ssl
import threading
import time
import urllib.parse
import urllib.request
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
VALID_WEATHER_UNITS = ("auto", "C", "F")
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


def normalize_weather_unit(unit: Optional[str]) -> str:
    """Normalizes a weather temperature unit string into `'auto'`, `'C'`, or `'F'`."""
    if not unit:
        return "auto"
    val = str(unit).strip().upper()
    if val in ("C", "CELSIUS", "°C"):
        return "C"
    if val in ("F", "FAHRENHEIT", "°F"):
        return "F"
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


# ==============================================================================
# Smart Dual-Region Weather Resolver (Tencent Weather + CMA Sojson + Open-Meteo)
# ==============================================================================

_WEATHER_CACHE: Dict[str, Tuple[Dict[str, Any], float]] = {}
_WEATHER_CACHE_LOCK = threading.Lock()
_WEATHER_CACHE_TTL = 900.0  # 15 minutes

_IP_GEO_CACHE: Dict[str, Any] = {"data": None, "ts": 0.0}
_IP_GEO_TTL = 3600.0  # 1 hour

_WMO_I18N: Dict[str, Dict[str, str]] = {
    "clear": {
        "en": "Clear", "zh": "晴", "ja": "快晴", "ko": "맑음", "es": "Despejado", "de": "Klar",
    },
    "mostly_clear": {
        "en": "Mostly Clear", "zh": "晴间多云", "ja": "晴れ", "ko": "대체로 맑음", "es": "Mayormente despejado", "de": "Überwiegend klar",
    },
    "partly_cloudy": {
        "en": "Partly Cloudy", "zh": "多云", "ja": "曇り時々晴れ", "ko": "구름 조금", "es": "Parcialmente nublado", "de": "Teilweise bewölkt",
    },
    "overcast": {
        "en": "Overcast", "zh": "阴", "ja": "曇り", "ko": "흐림", "es": "Nublado", "de": "Bedeckt",
    },
    "fog": {
        "en": "Fog", "zh": "雾", "ja": "霧", "ko": "안개", "es": "Niebla", "de": "Nebel",
    },
    "drizzle": {
        "en": "Drizzle", "zh": "毛毛雨", "ja": "霧雨", "ko": "이슬비", "es": "Llovizna", "de": "Nieselregen",
    },
    "light_rain": {
        "en": "Light Rain", "zh": "小雨", "ja": "小雨", "ko": "약한 비", "es": "Lluvia ligera", "de": "Leichter Regen",
    },
    "rain": {
        "en": "Rain", "zh": "中雨", "ja": "雨", "ko": "비", "es": "Lluvia", "de": "Regen",
    },
    "heavy_rain": {
        "en": "Heavy Rain", "zh": "大雨", "ja": "大雨", "ko": "폭우", "es": "Lluvia fuerte", "de": "Starkregen",
    },
    "showers": {
        "en": "Showers", "zh": "阵雨", "ja": "にわか雨", "ko": "소나기", "es": "Chubascos", "de": "Regenschauer",
    },
    "snow": {
        "en": "Snow", "zh": "雪", "ja": "雪", "ko": "눈", "es": "Nieve", "de": "Schnee",
    },
    "thunderstorm": {
        "en": "Thunderstorm", "zh": "雷阵雨", "ja": "雷雨", "ko": "뇌우", "es": "Tormenta", "de": "Gewitter",
    },
}

# Pre-mapped Mainland China cities & Shanghai districts -> (province, city, county, display_zh, cma_code)
_CHINA_CITY_DIRECTORY: List[Tuple[Tuple[str, ...], Tuple[str, str, str, str, str]]] = [
    (("闵行", "minhang"), ("上海", "上海", "闵行", "上海 闵行", "101020200")),
    (("浦东", "pudong"), ("上海", "上海", "浦东", "上海 浦东", "101020600")),
    (("徐汇", "xuhui"), ("上海", "上海", "徐汇", "上海 徐汇", "101021200")),
    (("静安", "jingan", "jing'an"), ("上海", "上海", "静安", "上海 静安", "101021000")),
    (("长宁", "changning"), ("上海", "上海", "长宁", "上海 长宁", "101021100")),
    (("黄浦", "huangpu"), ("上海", "上海", "黄浦", "上海 黄浦", "101020100")),
    (("普陀", "putuo"), ("上海", "上海", "普陀", "上海 普陀", "101021800")),
    (("虹口", "hongkou"), ("上海", "上海", "虹口", "上海 虹口", "101021900")),
    (("杨浦", "yangpu"), ("上海", "上海", "杨浦", "上海 杨浦", "101022000")),
    (("松江", "songjiang"), ("上海", "上海", "松江", "上海 松江", "101021300")),
    (("嘉定", "jiading"), ("上海", "上海", "嘉定", "上海 嘉定", "101020500")),
    (("宝山", "baoshan"), ("上海", "上海", "宝山", "上海 宝山", "101020300")),
    (("青浦", "qingpu"), ("上海", "上海", "青浦", "上海 青浦", "101021400")),
    (("奉贤", "fengxian"), ("上海", "上海", "奉贤", "上海 奉贤", "101021500")),
    (("金山", "jinshan"), ("上海", "上海", "金山", "上海 金山", "101021600")),
    (("崇明", "chongming"), ("上海", "上海", "崇明", "上海 崇明", "101021700")),
    (("上海", "shanghai"), ("上海", "上海", "闵行", "上海 闵行", "101020200")),
    (("北京", "beijing"), ("北京", "北京", "海淀", "北京", "101010100")),
    (("杭州", "hangzhou"), ("浙江", "杭州", "西湖", "杭州", "101210101")),
    (("深圳", "shenzhen"), ("广东", "深圳", "南山", "深圳", "101280601")),
    (("广州", "guangzhou"), ("广东", "广州", "天河", "广州", "101280101")),
    (("南京", "nanjing"), ("江苏", "南京", "玄武", "南京", "101190101")),
    (("苏州", "suzhou"), ("江苏", "苏州", "姑苏", "苏州", "101190401")),
    (("无锡", "wuxi"), ("江苏", "无锡", "梁溪", "无锡", "101190201")),
    (("成都", "chengdu"), ("四川", "成都", "武侯", "成都", "101270101")),
    (("武汉", "wuhan"), ("湖北", "武汉", "武昌", "武汉", "101200101")),
    (("西安", "xian", "xi'an"), ("陕西", "西安", "雁塔", "西安", "101110101")),
    (("重庆", "chongqing"), ("重庆", "重庆", "渝中", "重庆", "101040100")),
    (("天津", "tianjin"), ("天津", "天津", "和平", "天津", "101030100")),
    (("青岛", "qingdao"), ("山东", "青岛", "市南", "青岛", "101120201")),
    (("济南", "jinan"), ("山东", "济南", "历下", "济南", "101120101")),
    (("厦门", "xiamen"), ("福建", "厦门", "思明", "厦门", "101230201")),
    (("福州", "fuzhou"), ("福建", "福州", "鼓楼", "福州", "101230101")),
    (("长沙", "changsha"), ("湖南", "长沙", "岳麓", "长沙", "101250101")),
    (("郑州", "zhengzhou"), ("河南", "郑州", "金水", "郑州", "101180101")),
    (("昆明", "kunming"), ("云南", "昆明", "五华", "昆明", "101290101")),
    (("大连", "dalian"), ("辽宁", "大连", "中山", "大连", "101070201")),
    (("沈阳", "shenyang"), ("辽宁", "沈阳", "和平", "沈阳", "101070101")),
    (("哈尔滨", "harbin"), ("黑龙江", "哈尔滨", "南岗", "哈尔滨", "101050101")),
    (("宁波", "ningbo"), ("浙江", "宁波", "海曙", "宁波", "101210401")),
    (("合肥", "hefei"), ("安徽", "合肥", "蜀山", "合肥", "101220101")),
    (("三亚", "sanya"), ("海南", "三亚", "", "三亚", "101310201")),
    (("海口", "haikou"), ("海南", "海口", "", "海口", "101310101")),
    (("珠海", "zhuhai"), ("广东", "珠海", "香洲", "珠海", "101280701")),
    (("佛山", "foshan"), ("广东", "佛山", "禅城", "佛山", "101280800")),
    (("东莞", "dongguan"), ("广东", "东莞", "", "东莞", "101281601")),
]


def _get_unverified_ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def localize_wmo_weather(code: int, is_day: bool = True, lang: str = "en") -> Tuple[str, str]:
    """Maps a WMO weather code (`0..99`) to `(localized_description, emoji_icon)`."""
    lg = (lang or "en").strip().lower()[:2]
    if lg not in ("en", "zh", "ja", "ko", "es", "de"):
        lg = "en"

    if code == 0:
        key, icon = "clear", ("☀️" if is_day else "🌙")
    elif code == 1:
        key, icon = "mostly_clear", ("🌤️" if is_day else "🌙")
    elif code == 2:
        key, icon = "partly_cloudy", "⛅"
    elif code == 3:
        key, icon = "overcast", "☁️"
    elif code in (45, 48):
        key, icon = "fog", "🌫️"
    elif code in (51, 53, 55, 56, 57):
        key, icon = "drizzle", "🌦️"
    elif code in (61, 66):
        key, icon = "light_rain", "🌦️"
    elif code in (63,):
        key, icon = "rain", "🌧️"
    elif code in (65, 67):
        key, icon = "heavy_rain", "⛈️"
    elif code in (71, 73, 75, 77, 85, 86):
        key, icon = "snow", "❄️"
    elif code in (80, 81, 82):
        key, icon = "showers", "🌦️"
    elif code in (95, 96, 99):
        key, icon = "thunderstorm", "⚡"
    else:
        key, icon = "clear", ("☀️" if is_day else "🌙")

    desc = _WMO_I18N.get(key, _WMO_I18N["clear"]).get(lg, _WMO_I18N["clear"]["en"])
    return desc, icon


def localize_zh_weather_desc(raw_desc: str, lang: str = "en") -> Tuple[str, str]:
    """Maps a Chinese weather description (e.g. `'晴'`, `'多云'`, `'小雨'`) to `(localized_desc, icon)`."""
    d = (raw_desc or "晴").strip()
    lg = (lang or "en").strip().lower()[:2]
    if lg not in ("en", "zh", "ja", "ko", "es", "de"):
        lg = "en"

    if "雷" in d:
        key, icon = "thunderstorm", "⚡"
    elif "雪" in d:
        key, icon = "snow", "❄️"
    elif "大雨" in d or "暴雨" in d:
        key, icon = "heavy_rain", "⛈️"
    elif "阵雨" in d:
        key, icon = "showers", "🌦️"
    elif "小雨" in d:
        key, icon = "light_rain", "🌦️"
    elif "雨" in d:
        key, icon = "rain", "🌧️"
    elif "雾" in d or "霾" in d:
        key, icon = "fog", "🌫️"
    elif "多云" in d:
        key, icon = "partly_cloudy", "⛅"
    elif "阴" in d:
        key, icon = "overcast", "☁️"
    else:
        key, icon = "clear", "☀️"

    if lg == "zh":
        return d, icon
    return _WMO_I18N[key].get(lg, _WMO_I18N[key]["en"]), icon


def match_china_city(query: str) -> Optional[Tuple[str, str, str, str, str]]:
    """
    Checks if `query` matches a Mainland China city (by Chinese characters or known Pinyin).
    Returns `(province, city, county, display_name, cma_code)` or `None`.
    """
    q = (query or "").strip()
    if not q:
        return None
    q_low = q.lower()
    for keys, info in _CHINA_CITY_DIRECTORY:
        if any(k in q_low for k in keys):
            return info

    # If the user entered Chinese characters like "浙江 温州" or "广东 汕头", parse parts
    if re.search(r"[\u4e00-\u9fff]", q):
        parts = [p.strip() for p in re.split(r"[\s,，/]+", q) if p.strip()]
        if len(parts) >= 3:
            return (parts[0], parts[1], parts[2], f"{parts[1]} {parts[2]}", "101020200")
        if len(parts) == 2:
            return (parts[0], parts[1], "", f"{parts[0]} {parts[1]}", "101020200")
        return (parts[0], parts[0], "", parts[0], "101020200")
    return None


def _detect_ip_location() -> Optional[Dict[str, Any]]:
    """Detects approximate `(city, countryCode, regionName, lat, lon)` from public IP (cached 1h)."""
    now = time.time()
    if _IP_GEO_CACHE["data"] is not None and (now - _IP_GEO_CACHE["ts"]) < _IP_GEO_TTL:
        return _IP_GEO_CACHE["data"]

    ctx = _get_unverified_ssl_ctx()
    # 1. Try ip-api.com (fast JSON)
    try:
        req = urllib.request.Request(
            "http://ip-api.com/json/?fields=status,countryCode,regionName,city,lat,lon",
            headers={"User-Agent": "Mozilla/5.0 (PhotoFrame/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=3.5) as r:
            d = json.loads(r.read().decode("utf-8"))
            if d.get("status") == "success" and d.get("city"):
                res = {
                    "city": str(d.get("city", "")).strip(),
                    "region": str(d.get("regionName", "")).strip(),
                    "country": str(d.get("countryCode", "")).strip().upper(),
                    "lat": float(d.get("lat", 0.0)),
                    "lon": float(d.get("lon", 0.0)),
                }
                _IP_GEO_CACHE["data"] = res
                _IP_GEO_CACHE["ts"] = now
                return res
    except Exception:
        pass

    # 2. Fallback: ipwho.is
    try:
        req = urllib.request.Request(
            "https://ipwho.is/",
            headers={"User-Agent": "Mozilla/5.0 (PhotoFrame/1.0)"},
        )
        with urllib.request.urlopen(req, context=ctx, timeout=3.5) as r:
            d = json.loads(r.read().decode("utf-8"))
            if d.get("success") is not False and d.get("city"):
                res = {
                    "city": str(d.get("city", "")).strip(),
                    "region": str(d.get("region", "")).strip(),
                    "country": str(d.get("country_code", "")).strip().upper(),
                    "lat": float(d.get("latitude", 0.0)),
                    "lon": float(d.get("longitude", 0.0)),
                }
                _IP_GEO_CACHE["data"] = res
                _IP_GEO_CACHE["ts"] = now
                return res
    except Exception:
        pass

    return None


def _fetch_china_weather_endpoints(
    prov: str,
    cty: str,
    cty_code: str,
    disp: str,
    cma_code: str,
    lang: str = "en",
) -> Optional[Dict[str, Any]]:
    """Queries Tencent Weather (`wis.qq.com`) and falls back to CMA Sojson (`sojson.com`)."""
    ctx = _get_unverified_ssl_ctx()
    # 1. Primary China Endpoint: Tencent Weather (wis.qq.com)
    try:
        qs = urllib.parse.urlencode({
            "source": "pc",
            "weather_type": "observe|forecast_24h",
            "province": prov,
            "city": cty,
            "county": cty_code,
        })
        req = urllib.request.Request(
            f"https://wis.qq.com/weather/common?{qs}",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        )
        with urllib.request.urlopen(req, context=ctx, timeout=5.0) as r:
            wdata = json.loads(r.read().decode("utf-8"))
            obs = (wdata.get("data") or {}).get("observe") or {}
            if obs and "degree" in obs and str(obs["degree"]).strip() != "":
                temp_c = int(round(float(obs["degree"])))
                temp_f = int(round(temp_c * 9.0 / 5.0 + 32.0))
                raw_desc = str(obs.get("weather", "晴") or "晴")
                desc, icon = localize_zh_weather_desc(raw_desc, lang=lang)
                return {
                    "ok": True,
                    "city": disp,
                    "tempC": temp_c,
                    "tempF": temp_f,
                    "desc": desc,
                    "icon": icon,
                    "humidity": f"{obs.get('humidity', '')}%",
                    "source": "Tencent Weather",
                }
    except Exception:
        pass

    # 2. Secondary China Endpoint: CMA Sojson
    if cma_code:
        try:
            req = urllib.request.Request(
                f"http://t.weather.sojson.com/api/weather/city/{cma_code}",
                headers={"User-Agent": "Mozilla/5.0"},
            )
            with urllib.request.urlopen(req, timeout=5.0) as r:
                sdata = json.loads(r.read().decode("utf-8"))
                d = sdata.get("data") or {}
                if "wendu" in d:
                    temp_c = int(round(float(d.get("wendu", 24))))
                    temp_f = int(round(temp_c * 9.0 / 5.0 + 32.0))
                    forecast_list = d.get("forecast") or [{}]
                    raw_desc = str((forecast_list[0] if forecast_list else {}).get("type", "晴") or "晴")
                    desc, icon = localize_zh_weather_desc(raw_desc, lang=lang)
                    return {
                        "ok": True,
                        "city": disp,
                        "tempC": temp_c,
                        "tempF": temp_f,
                        "desc": desc,
                        "icon": icon,
                        "humidity": str(d.get("shidu", "")),
                        "source": "CMA Weather",
                    }
        except Exception:
            pass

    return None


def localize_en_weather_desc(raw_desc: str, lang: str = "en") -> Tuple[str, str]:
    """Maps an English weather description (e.g. from wttr.in) to `(localized_desc, icon)`."""
    d = (raw_desc or "Clear").strip()
    low = d.lower()
    lg = (lang or "en").strip().lower()[:2]
    if lg not in ("en", "zh", "ja", "ko", "es", "de"):
        lg = "en"

    if "thunder" in low or "storm" in low:
        key, icon = "thunderstorm", "⚡"
    elif "snow" in low or "blizzard" in low or "sleet" in low or "ice" in low:
        key, icon = "snow", "❄️"
    elif "heavy rain" in low or "torrential" in low:
        key, icon = "heavy_rain", "⛈️"
    elif "shower" in low:
        key, icon = "showers", "🌦️"
    elif "light rain" in low or "patchy rain" in low:
        key, icon = "light_rain", "🌦️"
    elif "drizzle" in low:
        key, icon = "drizzle", "🌦️"
    elif "rain" in low:
        key, icon = "rain", "🌧️"
    elif "fog" in low or "mist" in low or "haze" in low:
        key, icon = "fog", "🌫️"
    elif "partly cloudy" in low:
        key, icon = "partly_cloudy", "⛅"
    elif "cloudy" in low or "overcast" in low:
        key, icon = "overcast", "☁️"
    elif "clear" in low or "sunny" in low:
        key, icon = "clear", ("🌙" if "clear" in low and "sunny" not in low else "☀️")
    else:
        key, icon = "clear", "☀️"

    return _WMO_I18N[key].get(lg, _WMO_I18N[key]["en"]), icon


def _fetch_wttr_weather(city_query: str, lang: str = "en") -> Optional[Dict[str, Any]]:
    """Fetches current weather from `wttr.in/{city}?format=j1` (IPv4-friendly global endpoint)."""
    ctx = _get_unverified_ssl_ctx()
    q = (city_query or "").strip()
    if not q:
        return None
    try:
        encoded_city = urllib.parse.quote(q)
        req = urllib.request.Request(
            f"https://wttr.in/{encoded_city}?format=j1",
            headers={"User-Agent": "Mozilla/5.0 (PhotoFrame/1.0)"},
        )
        with urllib.request.urlopen(req, context=ctx, timeout=4.5) as r:
            d = json.loads(r.read().decode("utf-8"))
            cond_list = d.get("current_condition") or []
            if cond_list:
                cur = cond_list[0]
                temp_c = int(round(float(cur.get("temp_C", 22))))
                temp_f = int(round(float(cur.get("temp_F", round(temp_c * 9.0 / 5.0 + 32.0)))))
                w_desc_list = cur.get("weatherDesc") or [{}]
                raw_desc = str((w_desc_list[0] if w_desc_list else {}).get("value", "Clear") or "Clear")
                desc, icon = localize_en_weather_desc(raw_desc, lang=lang)
                hum = cur.get("humidity", "")
                return {
                    "ok": True,
                    "city": q,
                    "tempC": temp_c,
                    "tempF": temp_f,
                    "desc": desc,
                    "icon": icon,
                    "humidity": f"{hum}%" if hum != "" else "",
                    "source": "wttr.in",
                }
    except Exception:
        pass
    return None


def _fetch_open_meteo_by_coords(
    lat: float,
    lon: float,
    city_label: str,
    lang: str = "en",
) -> Optional[Dict[str, Any]]:
    """Fetches current weather from Open-Meteo for `(lat, lon)`."""
    ctx = _get_unverified_ssl_ctx()
    try:
        qs = urllib.parse.urlencode({
            "latitude": f"{lat:.4f}",
            "longitude": f"{lon:.4f}",
            "current": "temperature_2m,relative_humidity_2m,is_day,weather_code",
            "temperature_unit": "celsius",
        })
        req = urllib.request.Request(
            f"https://api.open-meteo.com/v1/forecast?{qs}",
            headers={"User-Agent": "Mozilla/5.0 (PhotoFrame/1.0)"},
        )
        with urllib.request.urlopen(req, context=ctx, timeout=5.0) as r:
            d = json.loads(r.read().decode("utf-8"))
            cur = d.get("current") or {}
            if "temperature_2m" in cur:
                temp_c = int(round(float(cur["temperature_2m"])))
                temp_f = int(round(temp_c * 9.0 / 5.0 + 32.0))
                wmo_code = int(cur.get("weather_code", 0))
                is_day = bool(cur.get("is_day", 1))
                desc, icon = localize_wmo_weather(wmo_code, is_day=is_day, lang=lang)
                hum = cur.get("relative_humidity_2m")
                return {
                    "ok": True,
                    "city": city_label,
                    "tempC": temp_c,
                    "tempF": temp_f,
                    "desc": desc,
                    "icon": icon,
                    "humidity": f"{int(hum)}%" if hum is not None else "",
                    "source": "Open-Meteo",
                }
    except Exception:
        pass
    return None


def _fetch_open_meteo_by_city(city_query: str, lang: str = "en") -> Optional[Dict[str, Any]]:
    """Geocodes `city_query` via Open-Meteo Geocoding API and fetches current weather."""
    ctx = _get_unverified_ssl_ctx()
    lg = (lang or "en").strip().lower()[:2]
    try:
        qs = urllib.parse.urlencode({
            "name": city_query.strip(),
            "count": 1,
            "language": lg,
            "format": "json",
        })
        req = urllib.request.Request(
            f"https://geocoding-api.open-meteo.com/v1/search?{qs}",
            headers={"User-Agent": "Mozilla/5.0 (PhotoFrame/1.0)"},
        )
        with urllib.request.urlopen(req, context=ctx, timeout=5.0) as r:
            d = json.loads(r.read().decode("utf-8"))
            results = d.get("results") or []
            if results:
                first = results[0]
                lat = float(first.get("latitude", 0.0))
                lon = float(first.get("longitude", 0.0))
                name = str(first.get("name") or city_query).strip()
                return _fetch_open_meteo_by_coords(lat, lon, name, lang=lang)
    except Exception:
        pass
    return None


def fetch_weather_data(
    city_query: str = "",
    lang: str = "en",
    edition: str = "baidupan",
) -> Dict[str, Any]:
    """
    Smart Dual-Region Weather Resolver with 15-minute RAM caching:
    - When `city_query` is blank or `'auto'`, detects location from IP (falling back to
      `'上海 闵行'` on `baidupan` / Chinese or `'San Francisco'` on `gphotos`).
    - For Mainland China cities, queries Tencent Weather (`wis.qq.com`) + CMA Sojson fallback.
    - For global/US cities, queries Open-Meteo Geocoding + Forecast API (with China fallback).
    """
    clean_q = (city_query or "").strip()
    if clean_q.lower() in ("auto", "default", "none"):
        clean_q = ""
    lg = (lang or "en").strip().lower()[:2]
    if lg not in ("en", "zh", "ja", "ko", "es", "de"):
        lg = "en"

    cache_key = f"{clean_q.lower() or '__auto__'}:{lg}:{edition}"
    now = time.time()
    with _WEATHER_CACHE_LOCK:
        cached = _WEATHER_CACHE.get(cache_key)
        if cached is not None and (now - cached[1]) < _WEATHER_CACHE_TTL:
            return dict(cached[0])

    result: Optional[Dict[str, Any]] = None

    # Case 1: Explicit city provided
    if clean_q:
        cn_match = match_china_city(clean_q)
        if cn_match is not None:
            prov, cty, cty_code, disp, cma_code = cn_match
            result = _fetch_china_weather_endpoints(prov, cty, cty_code, disp, cma_code, lang=lg)
            if result is None:
                result = _fetch_wttr_weather(cty or clean_q, lang=lg) or _fetch_open_meteo_by_city(cty or clean_q, lang=lg)
                if result is not None:
                    result["city"] = disp
        else:
            result = _fetch_wttr_weather(clean_q, lang=lg) or _fetch_open_meteo_by_city(clean_q, lang=lg)
            if result is None:
                # Fallback if global endpoints are blocked in China
                result = _fetch_china_weather_endpoints("上海", "上海", "闵行", clean_q, "101020200", lang=lg)
    else:
        # Case 2: Auto-detect from IP geolocation
        geo = _detect_ip_location()
        if geo is not None:
            country = geo.get("country", "")
            city_name = geo.get("city", "")
            region_name = geo.get("region", "")
            lat = float(geo.get("lat", 0.0))
            lon = float(geo.get("lon", 0.0))
            if country == "CN":
                cn_match = match_china_city(f"{region_name} {city_name}")
                if cn_match is not None:
                    prov, cty, cty_code, disp, cma_code = cn_match
                    result = _fetch_china_weather_endpoints(prov, cty, cty_code, disp, cma_code, lang=lg)
                else:
                    result = _fetch_china_weather_endpoints(
                        region_name or city_name or "上海",
                        city_name or "上海",
                        "",
                        city_name or "上海",
                        "101020200",
                        lang=lg,
                    )
            if result is None and city_name:
                result = _fetch_wttr_weather(city_name, lang=lg)
            if result is None and (lat != 0.0 or lon != 0.0):
                result = _fetch_open_meteo_by_coords(lat, lon, city_name or "Local", lang=lg)

        if result is None:
            if edition == "baidupan" or lg == "zh":
                result = _fetch_china_weather_endpoints("上海", "上海", "闵行", "上海 闵行", "101020200", lang=lg)
            else:
                result = _fetch_wttr_weather("San Francisco", lang=lg) or _fetch_open_meteo_by_coords(37.7749, -122.4194, "San Francisco", lang=lg)

    if result is None:
        default_city = clean_q or ("上海 闵行" if (edition == "baidupan" or lg == "zh") else "San Francisco")
        desc, icon = localize_wmo_weather(0, is_day=True, lang=lg)
        result = {
            "ok": True,
            "city": default_city,
            "tempC": 24,
            "tempF": 75,
            "desc": desc,
            "icon": icon,
            "humidity": "55%",
            "source": "Fallback",
        }

    with _WEATHER_CACHE_LOCK:
        _WEATHER_CACHE[cache_key] = (dict(result), now)
    return result


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


WEB_FRAME_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="mobile-web-app-capable" content="yes">
<title>Web Slideshow Viewer</title>
<style type="text/css">
  * { box-sizing: border-box; margin: 0; padding: 0; -webkit-tap-highlight-color: transparent; }
  html, body {
    width: 100%; height: 100%; overflow: hidden;
    background-color: #000000; color: #f8fafc;
    font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Helvetica Neue", Helvetica, Arial, sans-serif;
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
  .vignette-overlay {
    position: fixed; top: 0; left: 0; right: 0; bottom: 0;
    background: linear-gradient(to top, rgba(0, 0, 0, 0.62) 0%, rgba(0, 0, 0, 0.18) 18%, transparent 38%),
                linear-gradient(to bottom, rgba(0, 0, 0, 0.25) 0%, transparent 20%);
    pointer-events: none; z-index: 15;
  }

  /* ==========================================================================
     iPad-Style Clock & Weather Ambient Overlay (ES5 / iOS 9 Compatible)
     ========================================================================== */
  .clock-widget {
    position: fixed; z-index: 30;
    display: -webkit-flex; display: flex;
    -webkit-flex-direction: column; flex-direction: column;
    padding: 16px 22px; border-radius: 22px;
    background: rgba(10, 14, 24, 0.52);
    -webkit-backdrop-filter: blur(20px); backdrop-filter: blur(20px);
    border: 1px solid rgba(255, 255, 255, 0.14);
    box-shadow: 0 10px 32px rgba(0, 0, 0, 0.45);
    color: #ffffff; pointer-events: none; max-width: 92vw;
  }
  .clock-widget.hidden { display: none !important; }
  .hidden { display: none !important; }

  /* Corner & Center Positions */
  .clock-widget.pos-bottom-left { bottom: 26px; left: 28px; }
  .clock-widget.pos-bottom-right {
    bottom: 26px; right: 28px;
    -webkit-align-items: flex-end; align-items: flex-end; text-align: right;
  }
  .clock-widget.pos-top-left { top: 26px; left: 28px; }
  .clock-widget.pos-top-right {
    top: 26px; right: 28px;
    -webkit-align-items: flex-end; align-items: flex-end; text-align: right;
  }
  .clock-widget.pos-center-bottom {
    bottom: 28px; left: 50%;
    -webkit-transform: translateX(-50%); transform: translateX(-50%);
    -webkit-align-items: center; align-items: center; text-align: center;
  }

  /* Adaptive Bottom Banner (Default — 100% Transparent, 2x Clock Left, 2-Line Weather Right) */
  .clock-widget.pos-bottom-banner {
    top: auto !important; bottom: 0 !important; left: 0 !important; right: 0 !important;
    width: 100% !important; max-width: 100% !important;
    background: transparent !important; border: none !important;
    border-radius: 0 !important; box-shadow: none !important;
    -webkit-backdrop-filter: none !important; backdrop-filter: none !important;
    padding: 0 28px 16px 28px !important;
    display: -webkit-flex !important; display: flex !important;
    -webkit-flex-direction: row !important; flex-direction: row !important;
    -webkit-align-items: flex-end !important; align-items: flex-end !important;
    -webkit-justify-content: space-between !important; justify-content: space-between !important;
  }

  .digital-clock-face {
    display: -webkit-flex; display: flex;
    -webkit-flex-direction: column; flex-direction: column;
  }
  .time-row {
    display: -webkit-flex; display: flex;
    -webkit-align-items: baseline; align-items: baseline;
    font-weight: 700; line-height: 1; letter-spacing: -0.02em;
    text-shadow: 0 2px 18px rgba(0, 0, 0, 0.92), 0 0 4px rgba(0, 0, 0, 0.75);
  }
  .time-main {
    font-size: 62px; font-size: clamp(3.0rem, 7.2vw, 6.2rem);
    font-variant-numeric: tabular-nums;
  }
  .time-colon {
    font-size: 54px; font-size: clamp(2.5rem, 6.2vw, 5.4rem);
    opacity: 0.9; margin: 0 3px;
  }
  .time-ampm {
    font-size: 20px; font-size: clamp(1.1rem, 2.2vw, 2.0rem);
    font-weight: 600; margin-left: 10px;
    color: rgba(255, 255, 255, 0.88);
    text-shadow: 0 2px 14px rgba(0, 0, 0, 0.92);
  }
  .date-row {
    font-size: 22px; font-size: clamp(1.2rem, 2.4vw, 2.0rem);
    font-weight: 500; color: rgba(255, 255, 255, 0.92);
    margin-top: 4px; line-height: 1.2; white-space: nowrap;
    text-shadow: 0 2px 14px rgba(0, 0, 0, 0.92);
  }
  .date-day { font-weight: 600; color: #ffffff; }

  /* Analog Clock Face */
  .analog-clock-face {
    display: -webkit-flex; display: flex;
    -webkit-flex-direction: column; flex-direction: column;
    -webkit-align-items: center; align-items: center;
    padding: 4px 8px;
  }
  .analog-dial {
    width: 120px; height: 120px; border-radius: 50%;
    border: 3px solid rgba(255, 255, 255, 0.55);
    position: relative; background: rgba(0, 0, 0, 0.38);
    box-shadow: 0 4px 18px rgba(0, 0, 0, 0.65), inset 0 0 14px rgba(0, 0, 0, 0.45);
  }
  .analog-marks .mark { position: absolute; background: rgba(255, 255, 255, 0.85); }
  .mark.m12, .mark.m6 {
    width: 3px; height: 9px; left: 50%; margin-left: -1.5px;
  }
  .mark.m12 { top: 5px; }
  .mark.m6 { bottom: 5px; }
  .mark.m3, .mark.m9 {
    width: 9px; height: 3px; top: 50%; margin-top: -1.5px;
  }
  .mark.m3 { right: 5px; }
  .mark.m9 { left: 5px; }
  .analog-hand {
    position: absolute; bottom: 50%; left: 50%;
    -webkit-transform-origin: bottom center; transform-origin: bottom center;
    border-radius: 4px;
  }
  .hand-hour { width: 5px; height: 31%; margin-left: -2.5px; background: #ffffff; z-index: 2; }
  .hand-minute { width: 3px; height: 43%; margin-left: -1.5px; background: rgba(255, 255, 255, 0.92); z-index: 3; }
  .analog-center-pin {
    position: absolute; width: 9px; height: 9px; border-radius: 50%;
    background: #38bdf8; top: 50%; left: 50%; margin-left: -4.5px; margin-top: -4.5px; z-index: 5;
  }
  .analog-date-label {
    font-size: 15px; font-weight: 600; color: rgba(255, 255, 255, 0.9);
    margin-top: 6px; text-shadow: 0 2px 10px rgba(0, 0, 0, 0.9);
  }

  /* Weather Widget Badge */
  .weather-widget {
    display: -webkit-flex; display: flex;
    -webkit-flex-direction: row; flex-direction: row;
    -webkit-align-items: center; align-items: center;
    margin-top: 8px; padding-top: 8px;
    border-top: 1px solid rgba(255, 255, 255, 0.14);
  }
  .clock-widget.pos-bottom-banner .weather-widget {
    margin-top: 0 !important; padding-top: 0 !important;
    border-top: none !important;
    -webkit-justify-content: flex-end; justify-content: flex-end;
  }
  .weather-icon {
    font-size: 32px; font-size: clamp(1.7rem, 3.2vw, 2.7rem);
    line-height: 1; margin-right: 12px;
    text-shadow: 0 2px 12px rgba(0, 0, 0, 0.9);
  }
  .weather-text-group {
    display: -webkit-flex; display: flex;
    -webkit-flex-direction: column; flex-direction: column;
  }
  .clock-widget.pos-bottom-banner .weather-text-group,
  .clock-widget.pos-bottom-right .weather-text-group,
  .clock-widget.pos-top-right .weather-text-group {
    -webkit-align-items: flex-end; align-items: flex-end; text-align: right;
  }
  .weather-primary {
    display: -webkit-flex; display: flex;
    -webkit-align-items: baseline; align-items: baseline;
    line-height: 1.1;
  }
  .weather-temp {
    font-size: 26px; font-size: clamp(1.35rem, 2.6vw, 2.1rem);
    font-weight: 700; color: #ffffff; margin-right: 8px;
    text-shadow: 0 2px 14px rgba(0, 0, 0, 0.95);
  }
  .weather-desc {
    font-size: 18px; font-size: clamp(1.0rem, 1.8vw, 1.4rem);
    font-weight: 500; color: rgba(255, 255, 255, 0.92);
    text-shadow: 0 2px 12px rgba(0, 0, 0, 0.95);
  }
  .weather-location {
    font-size: 14px; font-size: clamp(0.82rem, 1.4vw, 1.1rem);
    font-weight: 500; color: rgba(255, 255, 255, 0.78);
    margin-top: 3px; text-shadow: 0 2px 10px rgba(0, 0, 0, 0.95);
  }

  #clock-pill { display: none; }

  #night-overlay {
    position: fixed; top: 0; left: 0; right: 0; bottom: 0;
    background-color: #000000; color: #475569; z-index: 50;
    display: none; text-align: center; padding-top: 42vh; font-size: 15px;
  }
  #hud {
    position: fixed; left: 0; right: 0; bottom: 0; z-index: 60;
    background: rgba(11, 15, 25, 0.92); border-top: 1px solid rgba(255, 255, 255, 0.18);
    padding: 10px 14px; display: none;
  }
  .hud-row {
    max-width: 920px; margin: 0 auto;
    display: table; width: 100%;
  }
  .hud-row-sub {
    max-width: 920px; margin: 8px auto 0 auto;
    padding-top: 8px; border-top: 1px solid rgba(255, 255, 255, 0.1);
    text-align: center;
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
  .hud-btn, .hud-select, .hud-input {
    background: #1e293b; color: #f8fafc; border: 1px solid #334155;
    border-radius: 7px; padding: 6px 10px; font-size: 12px; font-weight: bold;
    margin: 2px 3px; cursor: pointer; vertical-align: middle;
  }
  .hud-input { width: 135px; font-weight: normal; cursor: text; }
  .hud-btn:active { background: #38bdf8; color: #082f49; }
</style>
</head>
<body>
  <div id="stage" onclick="toggleHud()">
    <img id="slide-a" class="slide-img" alt="" src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7">
    <img id="slide-b" class="slide-img" alt="" style="opacity:0; display:none;" src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7">
    <div id="vignette-overlay" class="vignette-overlay"></div>
  </div>

  <!-- iPad-Style Clock & Weather Ambient Overlay -->
  <div id="clock-widget" class="clock-widget pos-bottom-banner style-digital">
    <div id="digital-clock" class="digital-clock-face">
      <div class="time-row">
        <span id="clock-hours" class="time-main">12</span>
        <span class="time-colon">:</span>
        <span id="clock-minutes" class="time-main">00</span>
        <span id="clock-ampm" class="time-ampm">PM</span>
      </div>
      <div id="clock-date-row" class="date-row">
        <span id="clock-day" class="date-day">Sunday</span><span id="clock-date-sep">, </span><span id="clock-date" class="date-full">Nov 15</span>
      </div>
    </div>

    <div id="analog-clock" class="analog-clock-face hidden">
      <div class="analog-dial">
        <div class="analog-marks">
          <span class="mark m12"></span><span class="mark m3"></span>
          <span class="mark m6"></span><span class="mark m9"></span>
        </div>
        <div id="hand-hour" class="analog-hand hand-hour"></div>
        <div id="hand-minute" class="analog-hand hand-minute"></div>
        <div class="analog-center-pin"></div>
      </div>
      <div class="analog-date-label">
        <span id="analog-date-text">Nov 15</span>
      </div>
    </div>

    <div id="weather-widget" class="weather-widget">
      <span id="weather-icon" class="weather-icon">☀️</span>
      <div class="weather-text-group">
        <div class="weather-primary">
          <span id="weather-temp" class="weather-temp">--°</span>
          <span id="weather-desc" class="weather-desc"></span>
        </div>
        <span id="weather-location" class="weather-location"></span>
      </div>
    </div>
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
        <select id="hud-lang" class="hud-select" onchange="changeClientLang(this.value)">
          <option value="auto" id="opt-lang-auto">🌐 Lang: Auto (Admin)</option>
          <option value="en">English</option>
          <option value="zh">中文</option>
          <option value="ja">日本語</option>
          <option value="ko">한국어</option>
          <option value="es">Español</option>
          <option value="de">Deutsch</option>
        </select>
        <select id="hud-profile" class="hud-select" onchange="changeClientProfile(this.value)">
          <option value="auto" id="opt-qual-auto">Quality: Auto</option>
          <option value="hd" id="opt-qual-hd">Quality: 1080p HD</option>
          <option value="legacy" id="opt-qual-legacy">Quality: 768p Standard</option>
        </select>
        <button type="button" class="hud-btn" onclick="loadSlide('prev')">⏮</button>
        <button type="button" class="hud-btn" id="btn-pause" onclick="toggleLocalPause()">⏸</button>
        <button type="button" class="hud-btn" onclick="loadSlide('next')">⏭</button>
        <button type="button" class="hud-btn" onclick="toggleFullscreen()">⛶</button>
      </div>
    </div>
    <div class="hud-row-sub">
      <select id="hud-clock-style" class="hud-select" onchange="changeClockStyle(this.value)">
        <option value="digital" id="opt-clk-digital">⏰ Digital Clock</option>
        <option value="analog" id="opt-clk-analog">🕰️ Analog Clock</option>
        <option value="hidden" id="opt-clk-hidden">🚫 Hide Clock</option>
      </select>
      <select id="hud-clock-pos" class="hud-select" onchange="changeClockPosition(this.value)">
        <option value="pos-bottom-banner" id="opt-pos-banner">Banner (Bottom)</option>
        <option value="pos-bottom-left" id="opt-pos-bl">Bottom Left</option>
        <option value="pos-bottom-right" id="opt-pos-br">Bottom Right</option>
        <option value="pos-top-left" id="opt-pos-tl">Top Left</option>
        <option value="pos-top-right" id="opt-pos-tr">Top Right</option>
        <option value="pos-center-bottom" id="opt-pos-bc">Bottom Center</option>
      </select>
      <button type="button" class="hud-btn" id="btn-clock-24h" onclick="toggleClock24h()">12h</button>
      <select id="hud-weather-unit" class="hud-select" onchange="changeWeatherUnit(this.value)">
        <option value="auto" id="opt-wu-auto">🌤️ Unit: Auto</option>
        <option value="C">🌤️ °C</option>
        <option value="F">🌤️ °F</option>
        <option value="off" id="opt-wu-off">🚫 Weather Off</option>
      </select>
      <input type="text" id="hud-weather-city" class="hud-input" placeholder="City (e.g. 上海 闵行)">
      <button type="button" class="hud-btn" id="btn-set-city" onclick="saveClientWeatherCity()">Set City</button>
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
  var cleanupTimer = null;
  var hudTimer = null;

  /* Clock & Weather State */
  var serverLang = "en";
  var clientLang = "auto";
  var frameLang = "en";
  var frameEdition = "gphotos";
  var serverWeatherCity = "";
  var serverWeatherUnit = "auto";
  var clockStyle = "digital";
  var clockPos = "pos-bottom-banner";
  var clock24h = false;
  var clientWeatherUnit = "auto";
  var clientWeatherCity = "";
  var lastWeatherData = null;
  var lastStateData = null;

  var I18N_DATE = {
    en: {
      days: ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"],
      months: ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
      am: "AM", pm: "PM"
    },
    zh: {
      days: ["星期日", "星期一", "星期二", "星期三", "星期四", "星期五", "星期六"],
      months: ["1月", "2月", "3月", "4月", "5月", "6月", "7月", "8月", "9月", "10月", "11月", "12月"],
      am: "上午", pm: "下午"
    },
    ja: {
      days: ["日曜日", "月曜日", "火曜日", "水曜日", "木曜日", "金曜日", "土曜日"],
      months: ["1月", "2月", "3月", "4月", "5月", "6月", "7月", "8月", "9月", "10月", "11月", "12月"],
      am: "午前", pm: "午後"
    },
    ko: {
      days: ["일요일", "월요일", "화요일", "수요일", "목요일", "금요일", "토요일"],
      months: ["1월", "2월", "3월", "4월", "5월", "6월", "7월", "8월", "9월", "10월", "11월", "12월"],
      am: "오전", pm: "오후"
    },
    es: {
      days: ["domingo", "lunes", "martes", "miércoles", "jueves", "viernes", "sábado"],
      months: ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"],
      am: "AM", pm: "PM"
    },
    de: {
      days: ["Sonntag", "Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag"],
      months: ["Jan.", "Feb.", "März", "Apr.", "Mai", "Juni", "Juli", "Aug.", "Sep.", "Okt.", "Nov.", "Dez."],
      am: "AM", pm: "PM"
    }
  };

  var I18N_UI = {
    en: {
      lang_auto: "🌐 Lang: Auto (Admin)",
      qual_auto: "Quality: Auto", qual_hd: "Quality: 1080p HD", qual_legacy: "Quality: 768p Standard",
      clk_digital: "⏰ Digital Clock", clk_analog: "🕰️ Analog Clock", clk_hidden: "🚫 Hide Clock",
      pos_banner: "Banner (Bottom)", pos_bl: "Bottom Left", pos_br: "Bottom Right",
      pos_tl: "Top Left", pos_tr: "Top Right", pos_bc: "Bottom Center",
      wu_auto: "🌤️ Unit: Auto", wu_off: "🚫 Weather Off",
      ph_city_auto: "City (Auto IP)", ph_city_def: "Default: ", btn_set_city: "Set City",
      items_unit: "items", p_hd: "1080p HD", p_leg: "768p Standard",
      disabled_msg: "🖼️ Web Slideshow Viewer is Disabled by Admin",
      night_msg: "🌙 Night Mode Sleep"
    },
    zh: {
      lang_auto: "🌐 语言: 自动 (跟随管理端)",
      qual_auto: "画质: 自动", qual_hd: "画质: 1080p 高清", qual_legacy: "画质: 768p 标准",
      clk_digital: "⏰ 数字时钟", clk_analog: "🕰️ 指针时钟", clk_hidden: "🚫 隐藏时钟",
      pos_banner: "底部通栏", pos_bl: "左下角", pos_br: "右下角",
      pos_tl: "左上角", pos_tr: "右上角", pos_bc: "底部居中",
      wu_auto: "🌤️ 温度: 自动", wu_off: "🚫 关闭天气",
      ph_city_auto: "城市 (自动定位)", ph_city_def: "默认: ", btn_set_city: "设置城市",
      items_unit: "项", p_hd: "1080p 高清", p_leg: "768p 标准",
      disabled_msg: "🖼️ 网页幻灯片已被管理员关闭",
      night_msg: "🌙 夜间休眠模式"
    },
    ja: {
      lang_auto: "🌐 言語: 自動 (管理画面に従う)",
      qual_auto: "画質: 自動", qual_hd: "画質: 1080p HD", qual_legacy: "画質: 768p 標準",
      clk_digital: "⏰ デジタル時計", clk_analog: "🕰️ アナログ時計", clk_hidden: "🚫 時計を隠す",
      pos_banner: "下部バナー", pos_bl: "左下", pos_br: "右下",
      pos_tl: "左上", pos_tr: "右上", pos_bc: "下部中央",
      wu_auto: "🌤️ 単位: 自動", wu_off: "🚫 天気オフ",
      ph_city_auto: "都市 (自動IP)", ph_city_def: "既定: ", btn_set_city: "都市設定",
      items_unit: "件", p_hd: "1080p HD", p_leg: "768p 標準",
      disabled_msg: "🖼️ Webスライドショーは管理者により無効化されています",
      night_msg: "🌙 ナイトモード休止中"
    },
    ko: {
      lang_auto: "🌐 언어: 자동 (관리자 설정)",
      qual_auto: "화질: 자동", qual_hd: "화질: 1080p HD", qual_legacy: "화질: 768p 표준",
      clk_digital: "⏰ 디지털 시계", clk_analog: "🕰️ 아날로그 시계", clk_hidden: "🚫 시계 숨기기",
      pos_banner: "하단 배너", pos_bl: "왼쪽 하단", pos_br: "오른쪽 하단",
      pos_tl: "왼쪽 상단", pos_tr: "오른쪽 상단", pos_bc: "하단 중앙",
      wu_auto: "🌤️ 단위: 자동", wu_off: "🚫 날씨 끄기",
      ph_city_auto: "도시 (자동 IP)", ph_city_def: "기본: ", btn_set_city: "도시 설정",
      items_unit: "개 항목", p_hd: "1080p HD", p_leg: "768p 표준",
      disabled_msg: "🖼️ 웹 슬라이드쇼가 관리자에 의해 비활성화되었습니다",
      night_msg: "🌙 야간 절전 모드"
    },
    es: {
      lang_auto: "🌐 Idioma: Auto (Admin)",
      qual_auto: "Calidad: Auto", qual_hd: "Calidad: 1080p HD", qual_legacy: "Calidad: 768p Estándar",
      clk_digital: "⏰ Reloj Digital", clk_analog: "🕰️ Reloj Analógico", clk_hidden: "🚫 Ocultar Reloj",
      pos_banner: "Banner (Abajo)", pos_bl: "Abajo Izq.", pos_br: "Abajo Der.",
      pos_tl: "Arriba Izq.", pos_tr: "Arriba Der.", pos_bc: "Abajo Centro",
      wu_auto: "🌤️ Unidad: Auto", wu_off: "🚫 Clima Apagado",
      ph_city_auto: "Ciudad (IP Auto)", ph_city_def: "Predet.: ", btn_set_city: "Fijar Ciudad",
      items_unit: "elem.", p_hd: "1080p HD", p_leg: "768p Estándar",
      disabled_msg: "🖼️ Visor Web desactivado por el administrador",
      night_msg: "🌙 Modo Noche Activo"
    },
    de: {
      lang_auto: "🌐 Sprache: Auto (Admin)",
      qual_auto: "Qualität: Auto", qual_hd: "Qualität: 1080p HD", qual_legacy: "Qualität: 768p Standard",
      clk_digital: "⏰ Digitaluhr", clk_analog: "🕰️ Analoguhr", clk_hidden: "🚫 Uhr ausblenden",
      pos_banner: "Banner (Unten)", pos_bl: "Unten Links", pos_br: "Unten Rechts",
      pos_tl: "Oben Links", pos_tr: "Oben Rechts", pos_bc: "Unten Mitte",
      wu_auto: "🌤️ Einheit: Auto", wu_off: "🚫 Wetter Aus",
      ph_city_auto: "Stadt (Auto-IP)", ph_city_def: "Standard: ", btn_set_city: "Stadt setzen",
      items_unit: "Elem.", p_hd: "1080p HD", p_leg: "768p Standard",
      disabled_msg: "🖼️ Web-Diashow vom Administrator deaktiviert",
      night_msg: "🌙 Nachtmodus Aktiv"
    }
  };

  function resolveEffectiveLang() {
    if (clientLang && clientLang !== "auto" && I18N_DATE[clientLang]) return clientLang;
    if (serverLang && I18N_DATE[serverLang]) return serverLang;
    return "en";
  }

  function applyFrameI18n() {
    frameLang = resolveEffectiveLang();
    var u = I18N_UI[frameLang] || I18N_UI.en;
    var mapIds = {
      "opt-lang-auto": u.lang_auto,
      "opt-qual-auto": u.qual_auto,
      "opt-qual-hd": u.qual_hd,
      "opt-qual-legacy": u.qual_legacy,
      "opt-clk-digital": u.clk_digital,
      "opt-clk-analog": u.clk_analog,
      "opt-clk-hidden": u.clk_hidden,
      "opt-pos-banner": u.pos_banner,
      "opt-pos-bl": u.pos_bl,
      "opt-pos-br": u.pos_br,
      "opt-pos-tl": u.pos_tl,
      "opt-pos-tr": u.pos_tr,
      "opt-pos-bc": u.pos_bc,
      "opt-wu-auto": u.wu_auto,
      "opt-wu-off": u.wu_off,
      "btn-set-city": u.btn_set_city
    };
    for (var id in mapIds) {
      if (mapIds.hasOwnProperty(id)) {
        var el = document.getElementById(id);
        if (el) el.innerHTML = mapIds[id];
      }
    }
    var elCity = document.getElementById("hud-weather-city");
    if (elCity && !clientWeatherCity) {
      elCity.placeholder = serverWeatherCity ? (u.ph_city_def + serverWeatherCity) : u.ph_city_auto;
    }
    updateClock();
    renderWeatherUI();
    renderHudStatusInfo();
  }

  function renderHudStatusInfo() {
    var u = I18N_UI[frameLang] || I18N_UI.en;
    var vp = getViewportSize();
    var infoEl = document.getElementById("hud-info");
    if (infoEl && lastStateData) {
      var pLabel = (effectiveProfile === "legacy") ? u.p_leg : u.p_hd;
      infoEl.innerHTML = (lastStateData.photo_count || 0) + " " + u.items_unit + " · " + pLabel + " (" + (lastStateData.target_w || vp.w) + "×" + (lastStateData.target_h || vp.h) + ")";
    }
    var nightMsg = document.getElementById("night-msg");
    if (nightMsg) {
      if (!isEnabled) {
        nightMsg.innerHTML = u.disabled_msg;
      } else if (isNightMode) {
        var ns = (lastStateData && lastStateData.night_start) ? lastStateData.night_start : "";
        var ne = (lastStateData && lastStateData.night_end) ? lastStateData.night_end : "";
        nightMsg.innerHTML = u.night_msg + (ns ? (" (" + ns + " - " + ne + ")") : "");
      }
    }
  }

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

    var ql = getQueryParam("lang");
    try {
      var sl = localStorage.getItem("webframe_lang") || "auto";
      if (ql && (ql === "auto" || I18N_DATE[ql])) {
        clientLang = ql;
      } else if (sl === "auto" || I18N_DATE[sl]) {
        clientLang = sl;
      }
      var cs = localStorage.getItem("webframe_clock_style");
      if (cs === "digital" || cs === "analog" || cs === "hidden") clockStyle = cs;
      var cp = localStorage.getItem("webframe_clock_pos");
      if (cp) clockPos = cp;
      var c24 = localStorage.getItem("webframe_clock_24h");
      if (c24 === "1" || c24 === "0") clock24h = (c24 === "1");
      var wu = localStorage.getItem("webframe_weather_unit");
      if (wu === "auto" || wu === "C" || wu === "F" || wu === "off") clientWeatherUnit = wu;
      var wc = localStorage.getItem("webframe_weather_city");
      if (wc !== null) clientWeatherCity = wc;
    } catch (e) {}

    var elLang = document.getElementById("hud-lang");
    if (elLang) elLang.value = clientLang;
    var elStyle = document.getElementById("hud-clock-style");
    if (elStyle) elStyle.value = clockStyle;
    var elPos = document.getElementById("hud-clock-pos");
    if (elPos) elPos.value = clockPos;
    var el24 = document.getElementById("btn-clock-24h");
    if (el24) el24.innerHTML = clock24h ? "24h" : "12h";
    var elUnit = document.getElementById("hud-weather-unit");
    if (elUnit) elUnit.value = clientWeatherUnit;
    var elCity = document.getElementById("hud-weather-city");
    if (elCity) elCity.value = clientWeatherCity;

    applyFrameI18n();
    applyClockAppearance();
  }

  window.changeClientLang = function(val) {
    clientLang = (val && (val === "auto" || I18N_DATE[val])) ? val : "auto";
    try { localStorage.setItem("webframe_lang", clientLang); } catch (e) {}
    applyFrameI18n();
    fetchWeather();
    resetHudTimer();
  };

  function applyClockAppearance() {
    var cw = document.getElementById("clock-widget");
    var dig = document.getElementById("digital-clock");
    var ana = document.getElementById("analog-clock");
    var wEl = document.getElementById("weather-widget");
    var vig = document.getElementById("vignette-overlay");
    if (!cw) return;

    cw.className = "clock-widget " + clockPos + " style-" + clockStyle + (clockStyle === "hidden" ? " hidden" : "");
    if (vig) vig.style.display = (clockStyle === "hidden") ? "none" : "block";
    if (dig) dig.className = (clockStyle === "analog") ? "digital-clock-face hidden" : "digital-clock-face";
    if (ana) ana.className = (clockStyle === "analog") ? "analog-clock-face" : "analog-clock-face hidden";
    if (wEl) wEl.style.display = (clientWeatherUnit === "off") ? "none" : "";
    updateClock();
    renderWeatherUI();
  }

  window.changeClockStyle = function(val) {
    clockStyle = val || "digital";
    try { localStorage.setItem("webframe_clock_style", clockStyle); } catch (e) {}
    applyClockAppearance();
    resetHudTimer();
  };

  window.changeClockPosition = function(val) {
    clockPos = val || "pos-bottom-banner";
    try { localStorage.setItem("webframe_clock_pos", clockPos); } catch (e) {}
    applyClockAppearance();
    resetHudTimer();
  };

  window.toggleClock24h = function() {
    clock24h = !clock24h;
    try { localStorage.setItem("webframe_clock_24h", clock24h ? "1" : "0"); } catch (e) {}
    var el24 = document.getElementById("btn-clock-24h");
    if (el24) el24.innerHTML = clock24h ? "24h" : "12h";
    updateClock();
    resetHudTimer();
  };

  window.changeWeatherUnit = function(val) {
    clientWeatherUnit = val || "auto";
    try { localStorage.setItem("webframe_weather_unit", clientWeatherUnit); } catch (e) {}
    applyClockAppearance();
    if (clientWeatherUnit !== "off" && !lastWeatherData) {
      fetchWeather();
    }
    resetHudTimer();
  };

  window.saveClientWeatherCity = function() {
    var inp = document.getElementById("hud-weather-city");
    clientWeatherCity = inp ? inp.value.replace(/^\s+|\s+$/g, "") : "";
    try { localStorage.setItem("webframe_weather_city", clientWeatherCity); } catch (e) {}
    fetchWeather();
    resetHudTimer();
  };

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
    var now = new Date();
    var hh24 = now.getHours();
    var mm = now.getMinutes();
    var mStr = (mm < 10 ? "0" : "") + mm;

    var loc = I18N_DATE[frameLang] || I18N_DATE.en;
    var dispH = hh24;
    var ampmStr = "";
    if (!clock24h) {
      ampmStr = (hh24 >= 12) ? loc.pm : loc.am;
      dispH = hh24 % 12;
      if (dispH === 0) dispH = 12;
    }
    var hStr = clock24h ? ((dispH < 10 ? "0" : "") + dispH) : ("" + dispH);

    var elH = document.getElementById("clock-hours");
    var elM = document.getElementById("clock-minutes");
    var elAmpm = document.getElementById("clock-ampm");
    if (elH) elH.innerHTML = hStr;
    if (elM) elM.innerHTML = mStr;
    if (elAmpm) {
      elAmpm.innerHTML = ampmStr;
      elAmpm.style.display = clock24h ? "none" : "";
    }

    var dayName = loc.days[now.getDay()];
    var monthName = loc.months[now.getMonth()];
    var dateNum = now.getDate();
    var fullDateStr = "";
    if (frameLang === "zh" || frameLang === "ja") {
      fullDateStr = monthName + dateNum + "日";
    } else if (frameLang === "ko") {
      fullDateStr = monthName + " " + dateNum + "일";
    } else if (frameLang === "de") {
      fullDateStr = dateNum + ". " + monthName;
    } else if (frameLang === "es") {
      fullDateStr = dateNum + " de " + monthName;
    } else {
      fullDateStr = monthName + " " + dateNum;
    }

    var elDay = document.getElementById("clock-day");
    var elSep = document.getElementById("clock-date-sep");
    var elDate = document.getElementById("clock-date");
    var elAnaDate = document.getElementById("analog-date-text");
    if (elDay) elDay.innerHTML = dayName;
    if (elSep) elSep.innerHTML = (frameLang === "zh" || frameLang === "ja" || frameLang === "ko") ? " · " : ", ";
    if (elDate) elDate.innerHTML = fullDateStr;
    if (elAnaDate) elAnaDate.innerHTML = dayName + " · " + fullDateStr;

    var minAngle = mm * 6;
    var hrAngle = ((hh24 % 12) + mm / 60.0) * 30;
    var handMin = document.getElementById("hand-minute");
    var handHr = document.getElementById("hand-hour");
    if (handMin) {
      handMin.style.webkitTransform = "rotate(" + minAngle + "deg)";
      handMin.style.transform = "rotate(" + minAngle + "deg)";
    }
    if (handHr) {
      handHr.style.webkitTransform = "rotate(" + hrAngle + "deg)";
      handHr.style.transform = "rotate(" + hrAngle + "deg)";
    }

    var pill = document.getElementById("clock-pill");
    if (pill) pill.innerHTML = hStr + ":" + mStr;
  }

  function effectiveTemperatureUnit() {
    if (clientWeatherUnit === "C" || clientWeatherUnit === "F") return clientWeatherUnit;
    if (serverWeatherUnit === "C" || serverWeatherUnit === "F") return serverWeatherUnit;
    if (frameEdition === "baidupan" || frameLang !== "en") return "C";
    return "F";
  }

  function renderWeatherUI() {
    if (!lastWeatherData) return;
    var unit = effectiveTemperatureUnit();
    var tempVal = (unit === "F")
      ? (lastWeatherData.tempF !== undefined ? lastWeatherData.tempF : 75)
      : (lastWeatherData.tempC !== undefined ? lastWeatherData.tempC : 24);
    var elIcon = document.getElementById("weather-icon");
    var elTemp = document.getElementById("weather-temp");
    var elDesc = document.getElementById("weather-desc");
    var elLoc = document.getElementById("weather-location");
    if (elIcon) elIcon.innerHTML = lastWeatherData.icon || "☀️";
    if (elTemp) elTemp.innerHTML = tempVal + "°" + unit;
    if (elDesc) elDesc.innerHTML = lastWeatherData.desc || "";
    if (elLoc) elLoc.innerHTML = lastWeatherData.city || "";
  }

  function fetchWeather() {
    if (clientWeatherUnit === "off") return;
    var cityToUse = clientWeatherCity || serverWeatherCity || "";
    var url = "/api/webframe/weather?city=" + encodeURIComponent(cityToUse)
      + "&lang=" + encodeURIComponent(frameLang)
      + "&t=" + (new Date().getTime());
    var xhr = new XMLHttpRequest();
    xhr.open("GET", url, true);
    xhr.onreadystatechange = function() {
      if (xhr.readyState !== 4) return;
      if (xhr.status === 200) {
        try {
          var d = JSON.parse(xhr.responseText || "{}");
          if (d && d.city !== undefined) {
            lastWeatherData = d;
            renderWeatherUI();
          }
        } catch (e) {}
      }
    };
    xhr.send(null);
  }

  window.toggleHud = function() {
    var hud = document.getElementById("hud");
    localPaused = !localPaused;
    var btn = document.getElementById("btn-pause");
    if (btn) btn.innerHTML = localPaused ? "▶" : "⏸";
    if (localPaused) {
      if (slideTimer) { clearTimeout(slideTimer); slideTimer = null; }
      if (hud) hud.style.display = "block";
      if (hudTimer) { clearTimeout(hudTimer); hudTimer = null; }
    } else {
      if (hud) hud.style.display = "none";
      if (hudTimer) { clearTimeout(hudTimer); hudTimer = null; }
      scheduleNextSlide();
    }
  };

  function resetHudTimer() {
    if (hudTimer) clearTimeout(hudTimer);
    if (localPaused) return;
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
    oldEl.onload = null;
    oldEl.onerror = null;
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

  function loadSlide(dir) {
    if (isFetchingSlide || !isEnabled || isNightMode) return;
    var imgA = document.getElementById("slide-a");
    var imgB = document.getElementById("slide-b");
    if (!imgA) return;

    isFetchingSlide = true;
    if (slideTimer) {
      clearTimeout(slideTimer);
      slideTimer = null;
    }
    if (cleanupTimer) {
      clearTimeout(cleanupTimer);
      cleanupTimer = null;
    }

    var vp = getViewportSize();
    var direction = dir || "next";
    var url = "/api/webframe/slide.jpg?cid=" + encodeURIComponent(clientId)
      + "&w=" + vp.w
      + "&h=" + vp.h
      + "&profile=" + encodeURIComponent(profileOverride)
      + "&dir=" + encodeURIComponent(direction)
      + "&t=" + (new Date().getTime());

    if (effectiveProfile !== "hd" || !imgB) {
      if (imgB) {
        imgB.onload = null;
        imgB.onerror = null;
        imgB.style.opacity = "0";
        imgB.style.display = "none";
        imgB.src = BLANK_GIF;
      }
      if (slideCount > 0 && slideCount % 40 === 0) {
        imgA = resetImgElement("slide-a", true);
      }
      imgA.className = "slide-img";
      imgA.style.display = "block";
      imgA.onload = function() {
        imgA.onload = null;
        imgA.onerror = null;
        isFetchingSlide = false;
        slideCount += 1;
        imgA.style.opacity = "1";
        activeSlot = "a";
        scheduleNextSlide();
      };
      imgA.onerror = function() {
        imgA.onload = null;
        imgA.onerror = null;
        isFetchingSlide = false;
        scheduleNextSlide();
      };
      imgA.src = url;
      return;
    }

    var useFade = (transitionMode !== "none");
    var cls = useFade ? "slide-img fade-transition" : "slide-img";
    imgA.className = cls;
    imgB.className = cls;

    var targetImg = (activeSlot === "a") ? imgB : imgA;
    var currentImg = (activeSlot === "a") ? imgA : imgB;
    var nextSlot = (activeSlot === "a") ? "b" : "a";

    targetImg.onload = null;
    targetImg.onerror = null;
    targetImg.style.opacity = "0";
    targetImg.style.display = "block";
    targetImg.onload = function() {
      targetImg.onload = null;
      targetImg.onerror = null;
      isFetchingSlide = false;
      slideCount += 1;
      targetImg.style.opacity = "1";
      currentImg.style.opacity = "0";
      activeSlot = nextSlot;
      cleanupTimer = setTimeout(function() {
        cleanupTimer = null;
        if (activeSlot === nextSlot) {
          currentImg.onload = null;
          currentImg.onerror = null;
          currentImg.src = BLANK_GIF;
        }
      }, useFade ? 750 : 60);
      scheduleNextSlide();
    };
    targetImg.onerror = function() {
      targetImg.onload = null;
      targetImg.onerror = null;
      isFetchingSlide = false;
      scheduleNextSlide();
    };
    targetImg.src = url;
  }
  window.loadSlide = loadSlide;

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
          lastStateData = data;
          var wasNightOrDisabled = (isNightMode || !isEnabled);
          isEnabled = !!data.enabled;
          isNightMode = !!data.is_night_mode;
          serverPaused = !!data.is_paused;
          if (data.effective_profile) effectiveProfile = data.effective_profile;
          if (data.slide_duration && data.slide_duration >= 3) {
            slideDurationMs = Math.round(parseFloat(data.slide_duration) * 1000);
          }
          if (data.transition) transitionMode = data.transition;

          var prevEffectiveLang = frameLang;
          var langOrCityChanged = false;
          if (data.language && data.language !== serverLang) {
            serverLang = data.language;
          }
          if (data.edition) frameEdition = data.edition;
          if (data.weather_city !== undefined && data.weather_city !== serverWeatherCity) {
            serverWeatherCity = data.weather_city || "";
            langOrCityChanged = true;
          }
          if (data.weather_unit && data.weather_unit !== serverWeatherUnit) {
            serverWeatherUnit = data.weather_unit;
          }
          applyFrameI18n();
          if (frameLang !== prevEffectiveLang) {
            langOrCityChanged = true;
          }
          if (langOrCityChanged || !lastWeatherData) {
            fetchWeather();
          }

          var hostEl = document.getElementById("hud-host");
          if (hostEl && data.mdns_hostname) {
            hostEl.innerHTML = "🖼️ " + data.mdns_hostname;
          }
          renderHudStatusInfo();

          var overlay = document.getElementById("night-overlay");
          if (!isEnabled) {
            if (overlay) overlay.style.display = "block";
            var imgA = document.getElementById("slide-a");
            var imgB = document.getElementById("slide-b");
            if (imgA) { imgA.onload = null; imgA.onerror = null; imgA.src = BLANK_GIF; }
            if (imgB) { imgB.onload = null; imgB.onerror = null; imgB.src = BLANK_GIF; }
          } else if (isNightMode) {
            if (overlay) overlay.style.display = "block";
            var imgA2 = document.getElementById("slide-a");
            var imgB2 = document.getElementById("slide-b");
            if (imgA2) { imgA2.onload = null; imgA2.onerror = null; imgA2.src = BLANK_GIF; }
            if (imgB2) { imgB2.onload = null; imgB2.onerror = null; imgB2.src = BLANK_GIF; }
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
  setInterval(fetchWeather, 900000);
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
    LRU caching, orientation-matched server-side JPEG slide composition, and the
    Smart Dual-Region Weather endpoint (`/api/webframe/weather`).
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
        args_obj = getattr(self.app, "args", None)
        lang = str(getattr(args_obj, "language", "en") or "en") if args_obj else "en"
        w_city = str(getattr(args_obj, "weather_city", "") or "") if args_obj else ""
        w_unit = normalize_weather_unit(getattr(args_obj, "weather_unit", "auto") if args_obj else "auto")
        edition = str(st.get("edition") or ("baidupan" if hasattr(args_obj, "pwd") else "gphotos"))
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
            "language": lang,
            "edition": edition,
            "weather_city": w_city,
            "weather_unit": w_unit,
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

        if path in ("/api/webframe/weather", "/api/weather"):
            args_obj = getattr(self.app, "args", None)
            default_city = str(getattr(args_obj, "weather_city", "") or "") if args_obj else ""
            default_lang = str(getattr(args_obj, "language", "en") or "en") if args_obj else "en"
            edition = "baidupan" if hasattr(args_obj, "pwd") else "gphotos"
            q_city = (qs.get("city", [default_city])[0] or default_city).strip()
            q_lang = (qs.get("lang", [default_lang])[0] or default_lang).strip()
            wdata = fetch_weather_data(city_query=q_city, lang=q_lang, edition=edition)
            body = json.dumps(wdata, ensure_ascii=False).encode("utf-8")
            handler.send_response(200)
            handler.send_header("Content-Type", "application/json; charset=utf-8")
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
