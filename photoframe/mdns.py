"""
Lightweight zero-config mDNS responder (RFC 6762) on UDP 224.0.0.251:5353
with automatic LAN hostname conflict detection & numbering (`photoframe.local`,
`photoframe-2.local`, `photoframe-3.local`, ...) and custom device hostname support.
"""

import re
import socket
import struct
import threading
import time
from typing import Callable, List, Optional, Tuple

from .hardware import logger

MDNS_HOSTNAME = "photoframe.local"
MDNS_GROUP = "224.0.0.251"
MDNS_PORT = 5353

_STATE_LOCK = threading.Lock()
_DESIRED_BASE_NAME = "photoframe"
_ACTIVE_MDNS_HOSTNAME = MDNS_HOSTNAME
_CONFIG_GENERATION = 0


def normalize_mdns_base_name(name: Optional[str]) -> str:
    """
    Sanitizes a user-supplied device name into a valid lowercase DNS label
    (without `.local`), defaulting to `'photoframe'` when blank.
    """
    if not name:
        return "photoframe"
    s = str(name).strip().lower()
    if s.endswith(".local."):
        s = s[:-7]
    elif s.endswith(".local"):
        s = s[:-6]
    s = s.strip(".")
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"[^a-z0-9-]", "", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    if not s:
        return "photoframe"
    return s[:48]


def format_mdns_hostname(base_name: str, index: int = 1) -> str:
    """
    Formats `<base>.local` for index 1, or `<base>-<index>.local` for index >= 2.
    """
    clean_base = normalize_mdns_base_name(base_name)
    if index <= 1:
        return f"{clean_base}.local"
    return f"{clean_base}-{index}.local"


def get_active_mdns_hostname() -> str:
    with _STATE_LOCK:
        return _ACTIVE_MDNS_HOSTNAME


def set_mdns_base_name(base_name: Optional[str]) -> str:
    """
    Updates the desired base hostname (e.g. `'photoframe'` or `'living-room'`)
    and signals the running mDNS responder to re-probe and announce the new name.
    Returns the initial candidate `<base>.local`.
    """
    global _DESIRED_BASE_NAME, _ACTIVE_MDNS_HOSTNAME, _CONFIG_GENERATION
    clean_base = normalize_mdns_base_name(base_name)
    candidate = format_mdns_hostname(clean_base, 1)
    with _STATE_LOCK:
        if clean_base != _DESIRED_BASE_NAME:
            _DESIRED_BASE_NAME = clean_base
            _ACTIVE_MDNS_HOSTNAME = candidate
            _CONFIG_GENERATION += 1
        return _ACTIVE_MDNS_HOSTNAME


def _set_active_mdns_hostname(hostname: str) -> None:
    global _ACTIVE_MDNS_HOSTNAME
    with _STATE_LOCK:
        _ACTIVE_MDNS_HOSTNAME = hostname


def _ip_to_int(ip_str: str) -> int:
    try:
        return struct.unpack("!I", socket.inet_aton(ip_str))[0]
    except Exception:
        return 0


def _read_dns_name(data: bytes, start_offset: int) -> Tuple[str, int]:
    pos = start_offset
    next_offset = -1
    labels = []
    jumps = 0
    n = len(data)
    while pos < n and jumps < 10:
        length = data[pos]
        if length == 0:
            pos += 1
            break
        if (length & 0xC0) == 0xC0:
            if pos + 1 >= n:
                return "", -1
            if next_offset < 0:
                next_offset = pos + 2
            pos = ((length & 0x3F) << 8) | data[pos + 1]
            jumps += 1
            continue
        pos += 1
        if pos + length > n:
            return "", -1
        try:
            labels.append(data[pos : pos + length].decode("utf-8", errors="ignore"))
        except Exception:
            return "", -1
        pos += length
    return ".".join(labels), (next_offset if next_offset >= 0 else pos)


def _encode_dns_name(hostname: str) -> bytes:
    qname = bytearray()
    for part in hostname.strip(".").split("."):
        encoded = part.encode("utf-8")
        qname.append(len(encoded))
        qname.extend(encoded)
    qname.append(0)
    return bytes(qname)


def build_mdns_query(tx_id: int, hostname: str = MDNS_HOSTNAME) -> bytes:
    """
    Builds an mDNS A-record query packet (QR=0, QTYPE=A, QCLASS=IN multicast response)
    used to probe whether `hostname` is already claimed by another device on the LAN.
    """
    header = struct.pack("!HHHHHH", tx_id & 0xFFFF, 0x0000, 1, 0, 0, 0)
    qname = _encode_dns_name(hostname)
    qtail = struct.pack("!HH", 1, 0x0001)
    return header + qname + qtail


def build_mdns_a_response(tx_id: int, ip_str: str, hostname: str = MDNS_HOSTNAME) -> Optional[bytes]:
    if not ip_str or ip_str in ("127.0.0.1", "localhost", "0.0.0.0"):
        return None
    try:
        ip_bytes = socket.inet_aton(ip_str)
    except OSError:
        return None

    # DNS Header: ID, Flags=0x8400 (QR=1, AA=1), QDCOUNT=0, ANCOUNT=1, NSCOUNT=0, ARCOUNT=0
    header = struct.pack("!HHHHHH", tx_id & 0xFFFF, 0x8400, 0, 1, 0, 0)
    qname = _encode_dns_name(hostname)

    # TYPE=1 (A), CLASS=0x8001 (IN + cache-flush), TTL=120s, RDLENGTH=4
    rr_tail = struct.pack("!HHIH", 1, 0x8001, 120, 4) + ip_bytes
    return header + qname + rr_tail


def parse_mdns_a_records(data: bytes) -> List[Tuple[str, str]]:
    """
    Extracts all (hostname_lower, ipv4_str) A-records from an mDNS response packet (QR=1).
    """
    results: List[Tuple[str, str]] = []
    if len(data) < 12:
        return results
    _, flags, qd_count, an_count, ns_count, ar_count = struct.unpack("!HHHHHH", data[:12])
    if (flags & 0x8000) == 0:
        return results

    offset = 12
    for _ in range(qd_count):
        _, offset = _read_dns_name(data, offset)
        if offset < 0 or offset + 4 > len(data):
            return results
        offset += 4

    total_rr = an_count + ns_count + ar_count
    for _ in range(total_rr):
        rr_name, offset = _read_dns_name(data, offset)
        if offset < 0 or offset + 10 > len(data):
            break
        rtype, _, _, rdlength = struct.unpack("!HHIH", data[offset : offset + 10])
        offset += 10
        if offset + rdlength > len(data):
            break
        if rtype == 1 and rdlength == 4:
            try:
                ip_str = socket.inet_ntoa(data[offset : offset + 4])
                results.append((rr_name.lower().strip("."), ip_str))
            except Exception:
                pass
        offset += rdlength
    return results


def _probe_hostname_conflict(
    sock: socket.socket,
    candidate_hostname: str,
    my_ip: str,
    probe_duration_sec: float = 0.28,
) -> bool:
    """
    Broadcasts mDNS A-record queries for `candidate_hostname` and listens for responses.
    Returns True if another device on the LAN (with IP != `my_ip`) responds as owning
    `candidate_hostname`.
    """
    if not my_ip or my_ip in ("127.0.0.1", "localhost", "0.0.0.0"):
        return False

    target_lower = candidate_hostname.lower().strip(".")
    query_pkt = build_mdns_query(0, hostname=candidate_hostname)
    deadline = time.monotonic() + probe_duration_sec
    sent_second = False

    for _ in range(2):
        try:
            sock.sendto(query_pkt, (MDNS_GROUP, MDNS_PORT))
            break
        except OSError:
            pass

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        if not sent_second and remaining <= (probe_duration_sec * 0.5):
            sent_second = True
            try:
                sock.sendto(query_pkt, (MDNS_GROUP, MDNS_PORT))
            except OSError:
                pass

        sock.settimeout(min(0.08, max(0.01, remaining)))
        try:
            data, _ = sock.recvfrom(1500)
        except socket.timeout:
            continue
        except OSError:
            break

        for rr_name, record_ip in parse_mdns_a_records(data):
            if rr_name == target_lower and record_ip not in (my_ip, "127.0.0.1", "0.0.0.0"):
                return True
    return False


def select_non_conflicting_hostname(
    sock: socket.socket,
    base_name: str,
    my_ip: str,
    start_index: int = 1,
    max_attempts: int = 25,
    probe_duration_sec: float = 0.28,
) -> Tuple[str, int]:
    """
    Probes `<base>.local`, `<base>-2.local`, `<base>-3.local`, ... on the mDNS socket
    and returns the first `(hostname, index)` that has no conflict on the LAN.
    """
    clean_base = normalize_mdns_base_name(base_name)
    idx = max(1, start_index)
    for _ in range(max_attempts):
        candidate = format_mdns_hostname(clean_base, idx)
        if not _probe_hostname_conflict(sock, candidate, my_ip, probe_duration_sec=probe_duration_sec):
            return candidate, idx
        logger.info(
            "⚠️ mDNS hostname '%s' is already in use on this LAN; trying '%s'...",
            candidate,
            format_mdns_hostname(clean_base, idx + 1),
        )
        idx += 1
    fallback = format_mdns_hostname(clean_base, idx)
    return fallback, idx


def start_mdns_responder(
    ip_provider: Callable[[], str],
    hostname: str = MDNS_HOSTNAME,
) -> None:
    set_mdns_base_name(hostname)

    def _worker():
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, "SO_REUSEPORT"):
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                except OSError:
                    pass
            sock.bind(("", MDNS_PORT))
            mreq = struct.pack("4s4s", socket.inet_aton(MDNS_GROUP), socket.inet_aton("0.0.0.0"))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)

            with _STATE_LOCK:
                cur_base = _DESIRED_BASE_NAME
                seen_gen = _CONFIG_GENERATION

            my_ip = ip_provider()
            active_host, cur_idx = select_non_conflicting_hostname(
                sock, cur_base, my_ip, start_index=1
            )
            _set_active_mdns_hostname(active_host)
            logger.info("🌐 mDNS responder active: http://%s:8080 -> %s", active_host, my_ip)

            def _broadcast_announcement(host_to_announce: str, ip_to_announce: str):
                ann = build_mdns_a_response(0, ip_to_announce, hostname=host_to_announce)
                if ann:
                    try:
                        sock.sendto(ann, (MDNS_GROUP, MDNS_PORT))
                    except OSError:
                        pass

            _broadcast_announcement(active_host, my_ip)
            last_defend_time = 0.0

            while True:
                with _STATE_LOCK:
                    latest_gen = _CONFIG_GENERATION
                    latest_base = _DESIRED_BASE_NAME

                if latest_gen != seen_gen:
                    seen_gen = latest_gen
                    cur_base = latest_base
                    my_ip = ip_provider()
                    active_host, cur_idx = select_non_conflicting_hostname(
                        sock, cur_base, my_ip, start_index=1
                    )
                    _set_active_mdns_hostname(active_host)
                    last_defend_time = 0.0
                    _broadcast_announcement(active_host, my_ip)
                    logger.info("🌐 mDNS hostname updated live: http://%s:8080 -> %s", active_host, my_ip)

                sock.settimeout(0.5)
                try:
                    data, addr = sock.recvfrom(1500)
                except socket.timeout:
                    continue
                except OSError:
                    break

                if len(data) < 12:
                    continue

                tx_id, flags, qd_count, _, _, _ = struct.unpack("!HHHHHH", data[:12])
                target_lower = active_host.lower().strip(".")
                my_ip = ip_provider()

                # 1. Handle incoming mDNS responses (QR=1) -> continuous conflict defense
                if (flags & 0x8000) != 0:
                    for rr_name, record_ip in parse_mdns_a_records(data):
                        if rr_name == target_lower and record_ip not in (my_ip, "127.0.0.1", "0.0.0.0"):
                            now = time.monotonic()
                            # Tie-breaker: higher IP yields immediately, or yield if peer ignored our defense
                            if _ip_to_int(my_ip) > _ip_to_int(record_ip) or (now - last_defend_time) < 6.0:
                                active_host, cur_idx = select_non_conflicting_hostname(
                                    sock, cur_base, my_ip, start_index=cur_idx + 1
                                )
                                _set_active_mdns_hostname(active_host)
                                last_defend_time = 0.0
                                _broadcast_announcement(active_host, my_ip)
                                logger.info(
                                    "⚠️ mDNS conflict with %s resolved -> reassigned to http://%s:8080",
                                    record_ip,
                                    active_host,
                                )
                            else:
                                last_defend_time = now
                                _broadcast_announcement(active_host, my_ip)
                            break
                    continue

                # 2. Handle incoming mDNS queries (QR=0)
                if qd_count <= 0:
                    continue
                offset = 12
                for _ in range(qd_count):
                    qname, offset = _read_dns_name(data, offset)
                    if offset < 0 or offset + 4 > len(data):
                        break
                    qtype, qclass = struct.unpack("!HH", data[offset : offset + 4])
                    offset += 4
                    if qname.lower().strip(".") == target_lower and qtype in (1, 255):
                        resp = build_mdns_a_response(tx_id, my_ip, hostname=active_host)
                        if resp:
                            dest = addr if (qclass & 0x8000) else (MDNS_GROUP, MDNS_PORT)
                            try:
                                sock.sendto(resp, dest)
                            except OSError:
                                pass
                        break
        except Exception as e:
            logger.debug("mDNS responder unavailable: %s", e)
        finally:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass

    threading.Thread(target=_worker, name="MdnsResponderThread", daemon=True).start()
