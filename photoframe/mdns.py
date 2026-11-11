"""
Lightweight zero-config mDNS responder (RFC 6762) on UDP 224.0.0.251:5353
that resolves `photoframe.local` to the device's current LAN IPv4 address.
"""

import socket
import struct
import threading
from typing import Callable, Optional, Tuple

from .hardware import logger

MDNS_HOSTNAME = "photoframe.local"
MDNS_GROUP = "224.0.0.251"
MDNS_PORT = 5353


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


def build_mdns_a_response(tx_id: int, ip_str: str, hostname: str = MDNS_HOSTNAME) -> Optional[bytes]:
    if not ip_str or ip_str in ("127.0.0.1", "localhost", "0.0.0.0"):
        return None
    try:
        ip_bytes = socket.inet_aton(ip_str)
    except OSError:
        return None

    # DNS Header: ID, Flags=0x8400 (QR=1, AA=1), QDCOUNT=0, ANCOUNT=1, NSCOUNT=0, ARCOUNT=0
    header = struct.pack("!HHHHHH", tx_id & 0xFFFF, 0x8400, 0, 1, 0, 0)
    qname = bytearray()
    for part in hostname.strip(".").split("."):
        encoded = part.encode("utf-8")
        qname.append(len(encoded))
        qname.extend(encoded)
    qname.append(0)

    # TYPE=1 (A), CLASS=0x8001 (IN + cache-flush), TTL=120s, RDLENGTH=4
    rr_tail = struct.pack("!HHIH", 1, 0x8001, 120, 4) + ip_bytes
    return header + bytes(qname) + rr_tail


def start_mdns_responder(ip_provider: Callable[[], str], hostname: str = MDNS_HOSTNAME) -> None:
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

            # Send initial unsolicited mDNS announcement
            ann = build_mdns_a_response(0, ip_provider(), hostname=hostname)
            if ann:
                try:
                    sock.sendto(ann, (MDNS_GROUP, MDNS_PORT))
                except OSError:
                    pass

            target_lower = hostname.lower().strip(".")
            while True:
                try:
                    data, addr = sock.recvfrom(1500)
                except OSError:
                    break
                if len(data) < 12:
                    continue
                tx_id, flags, qd_count, _, _, _ = struct.unpack("!HHHHHH", data[:12])
                if (flags & 0x8000) != 0 or qd_count <= 0:
                    continue
                offset = 12
                for _ in range(qd_count):
                    qname, offset = _read_dns_name(data, offset)
                    if offset < 0 or offset + 4 > len(data):
                        break
                    qtype, qclass = struct.unpack("!HH", data[offset : offset + 4])
                    offset += 4
                    if qname.lower().strip(".") == target_lower and qtype in (1, 255):
                        resp = build_mdns_a_response(tx_id, ip_provider(), hostname=hostname)
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
