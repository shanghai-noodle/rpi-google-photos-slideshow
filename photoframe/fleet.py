"""
Peer-to-Peer Multi-Frame Fleet Discovery & Management for Photo Frame Control Portal.
Allows any single frame's Web Control Portal to automatically discover and manage all
running photo frame instances (Linux/Raspberry Pi & Android TV, Google Photos & Baidu Pan)
on the same LAN without logging into each server individually.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import hmac
import http.client
import json
import socket
import ssl
import threading
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from .config import FrameConfigStore
from .hardware import logger
from .mdns import get_active_mdns_hostname, get_discovered_lan_ips

if TYPE_CHECKING:
    from .app import SlideshowApp

PEER_AUTH_HEADER = "X-PhotoFrame-Peer"
PEER_PW_HEADER = "X-PhotoFrame-Peer-Auth"
_CLUSTER_SECRET = b"PhotoFrame-LAN-Fleet-Trust-v1-2026"
PEER_TOKEN = hmac.new(_CLUSTER_SECRET, b"photoframe-lan-peer-v1", hashlib.sha256).hexdigest()
CANDIDATE_WEB_PORTS = (8080, 8081, 8082, 8088)

SHARED_SETTINGS_KEYS = (
    "language",
    "landscape_mode",
    "duration",
    "transition",
    "videos",
    "max_video_seconds",
    "fit",
    "refresh_hours",
    "shuffle",
    "night_mode_enabled",
    "night_start",
    "night_end",
    "timezone",
    "web_frame_enabled",
    "web_frame_profile",
    "web_frame_port",
)


def is_private_lan_ip(ip_str: str) -> bool:
    if not ip_str:
        return False
    s = ip_str.strip().lower()
    if s in ("localhost", "::1", "127.0.0.1"):
        return True
    if s.startswith("::ffff:"):
        s = s[7:]
    parts = s.split(".")
    if len(parts) != 4:
        return False
    try:
        a, b, _, _ = [int(x) for x in parts]
    except ValueError:
        return False
    if a in (10, 127):
        return True
    if a == 192 and b == 168:
        return True
    if a == 172 and 16 <= b <= 31:
        return True
    if a == 169 and b == 254:
        return True
    return False


def verify_peer_request(headers: Any, client_ip: str) -> bool:
    if not is_private_lan_ip(client_ip):
        return False
    token = ""
    if hasattr(headers, "get"):
        token = str(headers.get(PEER_AUTH_HEADER) or headers.get(PEER_AUTH_HEADER.lower()) or "").strip()
    if not token:
        return False
    return hmac.compare_digest(token, PEER_TOKEN)


def verify_peer_password_auth(headers: Any, client_ip: str, config_store: FrameConfigStore) -> bool:
    if not verify_peer_request(headers, client_ip):
        return False
    expected = config_store.get_peer_pw_token()
    if not expected:
        return False
    raw = ""
    if hasattr(headers, "get"):
        raw = str(headers.get(PEER_PW_HEADER) or headers.get(PEER_PW_HEADER.lower()) or "")
    if not raw:
        return False
    for part in raw.split(","):
        cand = part.strip()
        if cand and hmac.compare_digest(cand, expected):
            return True
    return False


def normalize_peer_target(raw_host: str, default_port: int = 8080) -> Optional[Tuple[str, int, str]]:
    if not raw_host:
        return None
    s = str(raw_host).strip()
    if s.startswith("http://"):
        s = s[7:]
    elif s.startswith("https://"):
        s = s[8:]
    s = s.split("/")[0].strip()
    if not s:
        return None
    host = s
    port = default_port
    if ":" in s:
        h_part, p_part = s.rsplit(":", 1)
        host = h_part.strip()
        try:
            port = int(p_part.strip())
        except ValueError:
            port = default_port
    if not host or port <= 0 or port > 65535:
        return None
    try:
        resolved_ip = socket.gethostbyname(host)
    except Exception:
        resolved_ip = host
    return resolved_ip, port, f"{resolved_ip}:{port}"


class FleetManager:
    """
    Coordinates automatic LAN discovery (mDNS + background /24 port-8080 sweep + peer gossip)
    and authenticated peer-to-peer proxy requests across all Photo Frame instances on the LAN.
    """

    def __init__(
        self,
        app: "SlideshowApp",
        config_store: FrameConfigStore,
        port: int = 8080,
        edition: str = "gphotos",
        platform: str = "linux",
        enable_background_scan: bool = True,
    ):
        self.app = app
        self.config_store = config_store
        self.port = int(port) if port > 0 else 8080
        self.edition = edition
        self.platform = platform
        self._lock = threading.Lock()
        self._peers: Dict[str, Dict[str, Any]] = {}
        self._scanning = False
        self._last_scan_ts = 0.0
        self._last_peer_refresh_ts = 0.0
        self._dead_mdns_ips: Dict[str, float] = {}
        self._enable_background_scan = enable_background_scan

        if enable_background_scan:
            t = threading.Thread(target=self._background_discovery_loop, name="FleetDiscoveryThread", daemon=True)
            t.start()

    def _get_my_ip(self) -> str:
        try:
            ip = self.app._get_local_ip()
            if ip and ip != "0.0.0.0":
                return ip
        except Exception:
            pass
        return "127.0.0.1"

    def get_self_target(self) -> str:
        return f"{self._get_my_ip()}:{self.port}"

    def is_self_target(self, target: str) -> bool:
        if not target or target in ("self", "local"):
            return True
        my_ip = self._get_my_ip()
        norm = normalize_peer_target(target, self.port)
        if not norm:
            return False
        ip, port, _ = norm
        return port == self.port and ip in (my_ip, "127.0.0.1", "localhost")

    def get_known_peer_targets(self) -> List[str]:
        with self._lock:
            return sorted(self._peers.keys())

    def _get_manual_peers(self) -> List[str]:
        s = self.config_store.get_settings()
        raw = s.get("manual_peers", [])
        if isinstance(raw, list):
            return [str(x).strip() for x in raw if str(x).strip()]
        return []

    def _save_manual_peers(self, peers: List[str]) -> None:
        clean = sorted(set(str(x).strip() for x in peers if str(x).strip()))
        self.config_store.save_settings({"manual_peers": clean})

    def build_ping_payload(self, peer_authorized: Optional[bool] = None) -> Dict[str, Any]:
        my_ip = self._get_my_ip()
        st = self.app.get_status_snapshot()
        cfg = self.app.get_current_settings_dict()
        hostname = st.get("mdns_hostname") or get_active_mdns_hostname()
        payload = {
            "photoframe": True,
            "service": "photoframe",
            "id": f"{my_ip}:{self.port}",
            "ip": my_ip,
            "port": self.port,
            "mdns_hostname": hostname,
            "device_name": cfg.get("device_name") or "photoframe",
            "edition": st.get("edition") or self.edition,
            "platform": st.get("platform") or self.platform,
            "profile": st.get("profile", ""),
            "app_version": st.get("app_version", "1.0.0"),
            "app_version_code": st.get("app_version_code", 1),
            "is_night_mode": bool(st.get("is_night_mode", False)),
            "night_mode_enabled": bool(st.get("night_mode_enabled", True)),
            "is_paused": bool(st.get("is_paused", False)),
            "is_demo_mode": bool(st.get("is_demo_mode", False)),
            "photo_count": int(st.get("photo_count", 0)),
            "video_count": int(st.get("video_count", 0)),
            "landscape_mode": st.get("landscape_mode", "collage"),
            "slide_duration": st.get("slide_duration", 15),
            "night_start": st.get("night_start", "22:00"),
            "night_end": st.get("night_end", "06:00"),
            "rotation": int(st.get("rotation", 0)),
            "canvas_w": int(st.get("canvas_w", 1920)),
            "canvas_h": int(st.get("canvas_h", 1080)),
            "web_frame_enabled": bool(st.get("web_frame_enabled", False)),
            "web_frame_running": bool(st.get("web_frame_running", False)),
            "web_frame_port": int(st.get("web_frame_port", 8081)),
            "web_frame_profile": str(st.get("web_frame_profile", "auto")),
            "web_frame_clients": int(st.get("web_frame_clients", 0)),
            "known_peers": self.get_known_peer_targets(),
        }
        if peer_authorized is not None:
            payload["peer_authorized"] = bool(peer_authorized)
        return payload

    def _build_peer_auth_header(self, target_id: str) -> str:
        tokens: List[str] = []
        stored = self.config_store.get_peer_auth_tokens().get(target_id, "")
        local = self.config_store.get_peer_pw_token()
        for tok in (stored, local):
            clean = str(tok or "").strip()
            if clean and clean not in tokens:
                tokens.append(clean)
        return ",".join(tokens)

    def _mark_peer_locked(self, target_id: str) -> None:
        with self._lock:
            if target_id in self._peers:
                self._peers[target_id]["locked"] = True
        self.config_store.remove_peer_auth_token(target_id)

    def _http_request_peer(
        self,
        ip: str,
        port: int,
        method: str,
        path: str,
        body_bytes: Optional[bytes] = None,
        content_type: str = "application/json",
        timeout: float = 2.0,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> Tuple[int, Dict[str, Any]]:
        headers = {
            PEER_AUTH_HEADER: PEER_TOKEN,
            "Accept": "application/json",
            "Connection": "close",
        }
        auth_val = self._build_peer_auth_header(f"{ip}:{port}")
        if auth_val:
            headers[PEER_PW_HEADER] = auth_val
        if extra_headers:
            headers.update(extra_headers)
        if body_bytes is not None:
            headers["Content-Type"] = content_type
            headers["Content-Length"] = str(len(body_bytes))

        for use_https in (False, True):
            conn = None
            try:
                if use_https:
                    ctx = ssl._create_unverified_context()
                    conn = http.client.HTTPSConnection(ip, port, timeout=timeout, context=ctx)
                else:
                    conn = http.client.HTTPConnection(ip, port, timeout=timeout)
                conn.request(method, path, body=body_bytes, headers=headers)
                resp = conn.getresponse()
                raw = resp.read(131072)
                data = json.loads(raw.decode("utf-8")) if raw else {}
                return resp.status, data
            except Exception:
                continue
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
        return 0, {}

    def probe_peer(self, raw_target: str, timeout: float = 0.85, follow_gossip: bool = True) -> Optional[Dict[str, Any]]:
        norm = normalize_peer_target(raw_target, self.port)
        if not norm:
            return None
        ip, port, target_id = norm
        if self.is_self_target(target_id):
            node = self.build_ping_payload()
            node["locked"] = False
            return node

        status, data = self._http_request_peer(ip, port, "GET", "/api/fleet/ping", timeout=timeout)
        if status == 200 and isinstance(data, dict) and data.get("photoframe") is True:
            node = dict(data)
            peer_auth = data.get("peer_authorized", True)
            locked = not bool(peer_auth)
            node["id"] = target_id
            node["ip"] = ip
            node["port"] = port
            node["is_self"] = False
            node["online"] = True
            node["locked"] = locked
            node["last_seen"] = time.time()
            with self._lock:
                self._peers[target_id] = node
            if locked:
                self.config_store.remove_peer_auth_token(target_id)

            if follow_gossip:
                gossip_peers = data.get("known_peers")
                if isinstance(gossip_peers, list):
                    for gp in gossip_peers[:16]:
                        gp_norm = normalize_peer_target(str(gp), self.port)
                        if gp_norm and not self.is_self_target(gp_norm[2]):
                            with self._lock:
                                already = gp_norm[2] in self._peers
                            if not already:
                                self.probe_peer(gp_norm[2], timeout=0.6, follow_gossip=False)
            return node

        with self._lock:
            if target_id in self._peers:
                self._peers[target_id]["online"] = False
        return None

    def refresh_known_peers(self, include_mdns: bool = False, force: bool = False) -> None:
        manual_targets = set()
        for mp in self._get_manual_peers():
            norm = normalize_peer_target(mp, self.port)
            if norm and not self.is_self_target(norm[2]):
                manual_targets.add(norm[2])

        now = time.time()
        if not include_mdns and not force:
            with self._lock:
                all_manual_known = all(mt in self._peers for mt in manual_targets)
            if all_manual_known and (now - self._last_peer_refresh_ts) < 2.5:
                return

        candidates = set(self.get_known_peer_targets()) | manual_targets
        scan_ports = [self.port] + [p for p in CANDIDATE_WEB_PORTS if p != self.port]
        probed_new_mdns: List[str] = []
        if include_mdns:
            for mdns_ip in get_discovered_lan_ips():
                has_known = any(k.startswith(f"{mdns_ip}:") for k in candidates)
                if not has_known:
                    dead_ts = self._dead_mdns_ips.get(mdns_ip, 0.0)
                    if (now - dead_ts) < 120.0:
                        continue
                    probed_new_mdns.append(mdns_ip)
                    for p in scan_ports:
                        candidates.add(f"{mdns_ip}:{p}")
                else:
                    candidates.add(f"{mdns_ip}:{self.port}")

        my_target = self.get_self_target()
        my_ip = self._get_my_ip()
        for p in scan_ports:
            candidates.discard(f"127.0.0.1:{p}")
            if p == self.port:
                candidates.discard(f"{my_ip}:{p}")
        candidates.discard(my_target)
        if not candidates:
            self._last_peer_refresh_ts = time.time()
            return

        with ThreadPoolExecutor(max_workers=min(16, len(candidates))) as pool:
            futures = [pool.submit(self.probe_peer, c, 0.75, True) for c in candidates]
            for f in as_completed(futures):
                try:
                    f.result()
                except Exception:
                    pass

        if probed_new_mdns:
            done_ts = time.time()
            with self._lock:
                known_keys = list(self._peers.keys())
            for mip in probed_new_mdns:
                if any(k.startswith(f"{mip}:") for k in known_keys):
                    self._dead_mdns_ips.pop(mip, None)
                else:
                    self._dead_mdns_ips[mip] = done_ts
        self._last_peer_refresh_ts = time.time()

    def scan_lan_subnet(self, blocking: bool = False) -> None:
        with self._lock:
            if self._scanning:
                return
            self._scanning = True

        def _run_scan():
            try:
                self.refresh_known_peers(include_mdns=True, force=True)
                my_ip = self._get_my_ip()
                parts = my_ip.split(".")
                if len(parts) == 4 and is_private_lan_ip(my_ip) and not my_ip.startswith("127."):
                    prefix = f"{parts[0]}.{parts[1]}.{parts[2]}."
                    my_last = int(parts[3])
                    scan_ports = [self.port] + [p for p in CANDIDATE_WEB_PORTS if p != self.port]

                    def _check_host(last_octet: int):
                        if last_octet == my_last:
                            return
                        ip = f"{prefix}{last_octet}"
                        for cand_port in scan_ports:
                            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                            sock.settimeout(0.20)
                            connected = False
                            try:
                                connected = (sock.connect_ex((ip, cand_port)) == 0)
                            except Exception:
                                pass
                            finally:
                                try:
                                    sock.close()
                                except Exception:
                                    pass
                            if connected:
                                node = self.probe_peer(f"{ip}:{cand_port}", timeout=0.75)
                                if node is not None:
                                    break

                    with ThreadPoolExecutor(max_workers=32) as pool:
                        list(pool.map(_check_host, range(1, 255)))
                self._last_scan_ts = time.time()
            finally:
                with self._lock:
                    self._scanning = False

        if blocking:
            _run_scan()
        else:
            threading.Thread(target=_run_scan, name="FleetSubnetScan", daemon=True).start()

    def _background_discovery_loop(self) -> None:
        time.sleep(1.5)
        self.scan_lan_subnet(blocking=True)
        while True:
            time.sleep(25.0)
            try:
                self.refresh_known_peers(include_mdns=True, force=True)
                if time.time() - self._last_scan_ts > 90.0:
                    self.scan_lan_subnet(blocking=True)
            except Exception:
                pass

    def get_fleet_nodes(self, trigger_scan: bool = False) -> List[Dict[str, Any]]:
        if trigger_scan:
            self.scan_lan_subnet(blocking=True)
        else:
            self.refresh_known_peers(include_mdns=False, force=False)

        self_node = self.build_ping_payload()
        self_node["is_self"] = True
        self_node["online"] = True
        self_node["locked"] = False

        manual_set = set()
        for mp in self._get_manual_peers():
            norm = normalize_peer_target(mp, self.port)
            if norm:
                manual_set.add(norm[2])

        peers_list: List[Dict[str, Any]] = []
        now = time.time()
        with self._lock:
            for pid, pnode in list(self._peers.items()):
                if self.is_self_target(pid):
                    continue
                # Expire non-manual offline peers after 5 minutes
                if not pnode.get("online") and pid not in manual_set and (now - pnode.get("last_seen", 0)) > 300:
                    del self._peers[pid]
                    continue
                copy_node = dict(pnode)
                copy_node["is_self"] = False
                copy_node["is_manual"] = pid in manual_set
                copy_node["locked"] = bool(pnode.get("locked", False))
                peers_list.append(copy_node)

        peers_list.sort(key=lambda x: (str(x.get("mdns_hostname", "")), str(x.get("ip", ""))))
        return [self_node] + peers_list

    def add_manual_peer(self, host_input: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        norm = normalize_peer_target(host_input, self.port)
        if not norm:
            return False, "Invalid IP address or hostname", None
        ip, initial_port, target_id = norm
        if self.is_self_target(target_id):
            node = self.build_ping_payload()
            node["locked"] = False
            return True, "This frame is already in the fleet", node

        node = self.probe_peer(target_id, timeout=1.8)
        if not node:
            for alt_port in CANDIDATE_WEB_PORTS:
                if alt_port == initial_port:
                    continue
                alt_target = f"{ip}:{alt_port}"
                if self.is_self_target(alt_target):
                    continue
                node = self.probe_peer(alt_target, timeout=1.2)
                if node:
                    target_id = alt_target
                    break
        if not node:
            return False, f"Could not reach a Photo Frame portal at {target_id}", None

        cur_manual = self._get_manual_peers()
        if target_id not in cur_manual:
            cur_manual.append(target_id)
            self._save_manual_peers(cur_manual)
        return True, f"Added {node.get('mdns_hostname', target_id)} ({target_id})", node

    def unlock_peer(self, target_input: str, password: str) -> Tuple[int, Dict[str, Any]]:
        norm = normalize_peer_target(target_input, self.port)
        if not norm:
            return 400, {"ok": False, "error": "Invalid target"}
        ip, port, target_id = norm
        if self.is_self_target(target_id):
            return 200, {"ok": True, "target": target_id, "fleet": self.get_fleet_nodes(trigger_scan=False)}

        body = json.dumps({"password": password or ""}).encode("utf-8")
        code, resp = self._http_request_peer(ip, port, "POST", "/api/fleet/verify_peer", body_bytes=body, timeout=3.5)
        if code == 200 and resp.get("ok"):
            tok = str(resp.get("peer_pw_token") or FrameConfigStore.compute_peer_pw_token(password or "")).strip()
            self.config_store.save_peer_auth_token(target_id, tok)
            node = self.probe_peer(target_id, timeout=1.5, follow_gossip=False)
            return 200, {
                "ok": True,
                "target": target_id,
                "node": node,
                "fleet": self.get_fleet_nodes(trigger_scan=False),
            }
        if code == 401:
            return 401, {"ok": False, "error": resp.get("error", "Invalid password")}
        return 502, {"ok": False, "error": resp.get("error", f"Could not verify password on {target_id}")}

    def remove_peer(self, target_input: str) -> bool:
        norm = normalize_peer_target(target_input, self.port)
        target_id = norm[2] if norm else target_input.strip()
        cur_manual = [p for p in self._get_manual_peers() if p != target_id and p != target_input.strip()]
        self._save_manual_peers(cur_manual)
        self.config_store.remove_peer_auth_token(target_id)
        with self._lock:
            self._peers.pop(target_id, None)
        return True

    def get_target_status(self, target: str) -> Dict[str, Any]:
        if not target or target == "all" or self.is_self_target(target):
            cur_settings = self.app.get_current_settings_dict()
            st = self.app.get_status_snapshot()
            return {
                "ok": True,
                "target": self.get_self_target(),
                "is_self": True,
                "edition": st.get("edition") or self.edition,
                "platform": st.get("platform") or self.platform,
                "status": st,
                "settings": cur_settings,
                "log_tail": self.app.get_ram_log_tail(35),
            }

        norm = normalize_peer_target(target, self.port)
        if not norm:
            return {"ok": False, "error": f"Invalid target: {target}"}
        ip, port, target_id = norm
        code, data = self._http_request_peer(ip, port, "GET", "/api/status", timeout=3.5)
        if code == 403 or (isinstance(data, dict) and data.get("locked")):
            self._mark_peer_locked(target_id)
            return {"ok": False, "locked": True, "target": target_id, "error": "Password required for this frame"}
        if code == 200 and isinstance(data, dict) and data.get("status"):
            st = data["status"]
            cfg = data.get("settings", {}) or {}
            ed = st.get("edition") or ("baidupan" if "pwd" in cfg else "gphotos")
            plat = st.get("platform") or ("android_tv" if "update_url" in cfg else "linux")
            with self._lock:
                prev = dict(self._peers.get(target_id, {}))
                upserted = {
                    **prev,
                    "photoframe": True,
                    "service": "photoframe",
                    "id": target_id,
                    "ip": ip,
                    "port": port,
                    "is_self": False,
                    "online": True,
                    "locked": False,
                    "last_seen": time.time(),
                    "mdns_hostname": st.get("mdns_hostname") or prev.get("mdns_hostname") or target_id,
                    "device_name": cfg.get("device_name") or prev.get("device_name") or "photoframe",
                    "edition": ed,
                    "platform": plat,
                    "profile": st.get("profile", prev.get("profile", "")),
                    "app_version": st.get("app_version", prev.get("app_version", "1.0.0")),
                    "app_version_code": st.get("app_version_code", prev.get("app_version_code", 1)),
                    "is_night_mode": bool(st.get("is_night_mode", False)),
                    "night_mode_enabled": bool(st.get("night_mode_enabled", True)),
                    "is_paused": bool(st.get("is_paused", False)),
                    "is_demo_mode": bool(st.get("is_demo_mode", False)),
                    "photo_count": int(st.get("photo_count", 0)),
                    "video_count": int(st.get("video_count", 0)),
                    "landscape_mode": st.get("landscape_mode", "collage"),
                    "slide_duration": st.get("slide_duration", 15),
                    "night_start": st.get("night_start", "22:00"),
                    "night_end": st.get("night_end", "06:00"),
                    "rotation": int(st.get("rotation", 0)),
                    "canvas_w": int(st.get("canvas_w", 1920)),
                    "canvas_h": int(st.get("canvas_h", 1080)),
                    "web_frame_enabled": bool(st.get("web_frame_enabled", False)),
                    "web_frame_running": bool(st.get("web_frame_running", False)),
                    "web_frame_port": int(st.get("web_frame_port", 8081)),
                    "web_frame_profile": str(st.get("web_frame_profile", "auto")),
                    "web_frame_clients": int(st.get("web_frame_clients", 0)),
                }
                self._peers[target_id] = upserted
            return {
                "ok": True,
                "target": target_id,
                "is_self": False,
                "edition": ed,
                "platform": plat,
                "status": st,
                "settings": cfg,
                "log_tail": data.get("log_tail", ""),
            }
        with self._lock:
            if target_id in self._peers:
                self._peers[target_id]["online"] = False
        return {
            "ok": False,
            "unreachable": True,
            "target": target_id,
            "error": f"Peer {target_id} is temporarily unreachable",
        }

    def _dispatch_action_local(self, action: str, album_arg: str, pwd_arg: str) -> str:
        try:
            return self.app.queue_web_action(action, album_arg=album_arg, pwd_arg=pwd_arg)
        except TypeError:
            return self.app.queue_web_action(action, album_arg=album_arg)

    def execute_action(self, target: str, action: str, album_arg: str = "", pwd_arg: str = "") -> Tuple[int, Dict[str, Any]]:
        if not target or self.is_self_target(target):
            msg = self._dispatch_action_local(action, album_arg, pwd_arg)
            if msg and msg.startswith("Unknown action"):
                return 400, {"ok": False, "error": f"{msg} — please update this Photo Frame to the latest version first."}
            return 200, {"ok": True, "message": msg}

        if target == "all":
            nodes = self.get_fleet_nodes(trigger_scan=False)
            unlocked_nodes = [n for n in nodes if n.get("online", True) and not n.get("locked", False)]
            remote_nodes = [n for n in unlocked_nodes if not n.get("is_self")]
            has_self = any(n.get("is_self") for n in unlocked_nodes)
            success_count = 0
            last_msg = "Action executed"

            def _run_on_node(node: Dict[str, Any]) -> Tuple[bool, str]:
                ip = node["ip"]
                port = int(node.get("port", self.port))
                node_edition = node.get("edition", "")
                payload: Dict[str, Any] = {"action": action}
                if action == "resync":
                    if album_arg and node_edition == self.edition:
                        payload["album"] = album_arg
                        if pwd_arg:
                            payload["pwd"] = pwd_arg
                elif action in ("check_update", "delete_saved_album"):
                    if album_arg and (action == "delete_saved_album" or node_edition == self.edition):
                        payload["album"] = album_arg
                body = json.dumps(payload).encode("utf-8")
                code, resp = self._http_request_peer(ip, port, "POST", "/api/action", body_bytes=body, timeout=3.5)
                if code == 200 and resp.get("ok"):
                    return True, str(resp.get("message", "OK"))
                return False, str(resp.get("error", "Failed"))

            if remote_nodes:
                with ThreadPoolExecutor(max_workers=min(16, len(remote_nodes))) as pool:
                    for ok, m in pool.map(_run_on_node, remote_nodes):
                        if ok:
                            success_count += 1
                            last_msg = m

            if has_self:
                m = self._dispatch_action_local(action, album_arg, pwd_arg)
                if m and not m.startswith("Unknown action"):
                    success_count += 1
                    last_msg = m

            return 200, {
                "ok": success_count > 0,
                "message": f"🌐 Broadcast ({success_count}/{len(unlocked_nodes)} frames): {last_msg}",
            }

        norm = normalize_peer_target(target, self.port)
        if not norm:
            return 400, {"ok": False, "error": f"Invalid target: {target}"}
        ip, port, target_id = norm
        payload = {"action": action, "album": album_arg, "pwd": pwd_arg}
        body = json.dumps(payload).encode("utf-8")
        code, resp = self._http_request_peer(ip, port, "POST", "/api/action", body_bytes=body, timeout=3.5)
        if code == 200 and resp.get("ok"):
            return 200, resp
        if code == 403 or resp.get("locked"):
            self._mark_peer_locked(target_id)
            return 403, {"ok": False, "locked": True, "error": resp.get("error", "Password required for this frame")}
        return (code if code >= 400 else 502), {"ok": False, "error": resp.get("error", f"Failed to reach {target_id}")}

    @staticmethod
    def _filter_settings_for_node(
        raw_settings: Dict[str, Any],
        source_edition: str,
        node_edition: str,
        is_broadcast_all: bool,
    ) -> Dict[str, Any]:
        if not is_broadcast_all:
            return dict(raw_settings)

        # In "all" broadcast mode, only broadcast shared slideshow/collage/schedule
        # settings. Per-frame settings (album, pwd, device_name, rotate, update_url)
        # are never overwritten across the fleet.
        filtered: Dict[str, Any] = {}
        for k in SHARED_SETTINGS_KEYS:
            if k in raw_settings:
                filtered[k] = raw_settings[k]

        return filtered

    def execute_settings(self, target: str, settings_data: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        if not target or self.is_self_target(target):
            msg = self.app.update_and_persist_settings(settings_data)
            return 200, {"ok": True, "message": msg}

        if target == "all":
            nodes = self.get_fleet_nodes(trigger_scan=False)
            unlocked_nodes = [n for n in nodes if n.get("online", True) and not n.get("locked", False)]
            success_count = 0
            last_msg = "Settings saved and applied live!"

            def _save_on_node(node: Dict[str, Any]) -> Tuple[bool, str]:
                node_edition = str(node.get("edition") or self.edition)
                filtered = self._filter_settings_for_node(
                    settings_data,
                    source_edition=self.edition,
                    node_edition=node_edition,
                    is_broadcast_all=True,
                )
                if node.get("is_self"):
                    m = self.app.update_and_persist_settings(filtered)
                    return True, m
                ip = node["ip"]
                port = int(node.get("port", self.port))
                body = json.dumps(filtered).encode("utf-8")
                code, resp = self._http_request_peer(ip, port, "POST", "/api/settings", body_bytes=body, timeout=4.0)
                if code == 200 and resp.get("ok"):
                    return True, str(resp.get("message", "OK"))
                return False, str(resp.get("error", "Failed"))

            with ThreadPoolExecutor(max_workers=min(16, max(1, len(unlocked_nodes)))) as pool:
                for ok, m in pool.map(_save_on_node, unlocked_nodes):
                    if ok:
                        success_count += 1
                        last_msg = m

            return 200, {
                "ok": success_count > 0,
                "message": f"🌐 Applied settings across {success_count}/{len(unlocked_nodes)} frames!",
            }

        norm = normalize_peer_target(target, self.port)
        if not norm:
            return 400, {"ok": False, "error": f"Invalid target: {target}"}
        ip, port, target_id = norm
        body = json.dumps(settings_data).encode("utf-8")
        code, resp = self._http_request_peer(ip, port, "POST", "/api/settings", body_bytes=body, timeout=4.0)
        if code == 200 and resp.get("ok"):
            return 200, resp
        if code == 403 or resp.get("locked"):
            self._mark_peer_locked(target_id)
            return 403, {"ok": False, "locked": True, "error": resp.get("error", "Password required for this frame")}
        return (code if code >= 400 else 502), {"ok": False, "error": resp.get("error", f"Failed to save settings on {target_id}")}

    def execute_password(
        self,
        target: str,
        current_pw: str,
        new_pw: str,
    ) -> Tuple[int, Dict[str, Any], Optional[str]]:
        if (target or "").strip().lower() == "all":
            return 400, {"ok": False, "error": "Password changes are not allowed in Broadcast Mode. Select an individual frame."}, None

        if len(new_pw) < 4:
            return 400, {"ok": False, "error": "New password must be at least 4 characters"}, None

        if not target or self.is_self_target(target):
            if not self.config_store.verify_password(current_pw):
                return 400, {"ok": False, "error": "Current password is incorrect"}, None
            self.config_store.change_password(new_pw)
            token = self.config_store.create_session_token()
            cookie = f"frame_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000"
            logger.info("🔐 Web portal password updated by user.")
            return 200, {"ok": True}, cookie

        norm = normalize_peer_target(target, self.port)
        if not norm:
            return 400, {"ok": False, "error": f"Invalid target: {target}"}, None
        ip, port, target_id = norm
        v_body = json.dumps({"password": current_pw or ""}).encode("utf-8")
        v_code, v_resp = self._http_request_peer(ip, port, "POST", "/api/fleet/verify_peer", body_bytes=v_body, timeout=3.5)
        if v_code != 200 or not v_resp.get("ok"):
            if v_code == 401:
                return 400, {"ok": False, "error": "Current password is incorrect"}, None
            return (v_code if v_code >= 400 else 502), {"ok": False, "error": v_resp.get("error", f"Could not verify password on {target_id}")}, None

        cur_tok = str(v_resp.get("peer_pw_token") or FrameConfigStore.compute_peer_pw_token(current_pw or "")).strip()
        if cur_tok:
            self.config_store.save_peer_auth_token(target_id, cur_tok)
        body = json.dumps({"new_password": new_pw}).encode("utf-8")
        code, resp = self._http_request_peer(ip, port, "POST", "/api/fleet/sync_password", body_bytes=body, timeout=3.0)
        if code == 200 and resp.get("ok"):
            self.config_store.save_peer_auth_token(target_id, FrameConfigStore.compute_peer_pw_token(new_pw))
            self.probe_peer(target_id, timeout=1.5, follow_gossip=False)
            return 200, {"ok": True}, None
        if code == 403 or resp.get("locked"):
            self._mark_peer_locked(target_id)
            return 403, {"ok": False, "locked": True, "error": resp.get("error", "Password required for this frame")}, None
        return 502, {"ok": False, "error": resp.get("error", f"Failed to update password on {target_id}")}, None

    def proxy_apk_upload(self, target: str, apk_bytes: bytes) -> Tuple[int, Dict[str, Any]]:
        if target == "all":
            nodes = self.get_fleet_nodes(trigger_scan=False)
            tv_peers = [
                n for n in nodes
                if not n.get("is_self") and n.get("online", True) and not n.get("locked", False) and n.get("platform") == "android_tv"
            ]
            if not tv_peers:
                return 400, {"ok": False, "error": "No unlocked online Android TV frames found on LAN"}
            ok_count = 0
            last_msg = ""
            for p in tv_peers:
                code, resp = self._http_request_peer(
                    p["ip"],
                    int(p.get("port", self.port)),
                    "POST",
                    "/api/update_upload",
                    body_bytes=apk_bytes,
                    content_type="application/vnd.android.package-archive",
                    timeout=60.0,
                )
                if code == 200 and resp.get("ok"):
                    ok_count += 1
                    last_msg = str(resp.get("message", "Uploaded"))
            return 200, {"ok": ok_count > 0, "message": f"Uploaded APK to {ok_count}/{len(tv_peers)} Android TV frames. {last_msg}"}

        norm = normalize_peer_target(target, self.port)
        if not norm:
            return 400, {"ok": False, "error": f"Invalid target: {target}"}
        ip, port, target_id = norm
        code, resp = self._http_request_peer(
            ip,
            port,
            "POST",
            "/api/update_upload",
            body_bytes=apk_bytes,
            content_type="application/vnd.android.package-archive",
            timeout=60.0,
        )
        if code == 200 and resp.get("ok"):
            return 200, resp
        if code == 403 or resp.get("locked"):
            self._mark_peer_locked(target_id)
            return 403, {"ok": False, "locked": True, "error": resp.get("error", "Password required for this frame")}
        return (code if code >= 400 else 502), {"ok": False, "error": resp.get("error", f"APK upload to {target_id} failed")}
