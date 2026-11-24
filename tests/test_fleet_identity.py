"""
Regression tests for fleet (multi-frame admin) problems:

* Frames reached over Tailscale could never authenticate (peer trust only allowed RFC1918 sources).
* One physical frame reachable on several addresses (LAN + Tailscale + ...) showed up several times.
* A frame did not recognise itself under its own non-LAN addresses.
* Removed frames came straight back through discovery/gossip, and online auto-discovered frames
  had no Remove button at all.
"""

import os
import re
import tempfile
import unittest
from unittest import mock

from photoframe.config import FrameConfigStore
from photoframe import fleet as F
from photoframe.web_portal import WEB_PORTAL_HTML

MY_LAN_IP = "192.168.86.44"
MY_TAILSCALE_IP = "100.74.85.54"

# Remote frame "collage": one physical frame reachable on its LAN and Tailscale addresses.
COLLAGE_LAN = "192.168.86.53"
COLLAGE_TS = "100.117.141.25"
# Remote frame "nest": a different frame that gossips about the others.
NEST_LAN = "192.168.86.247"


class StubApp:
    def __init__(self, ip=MY_LAN_IP):
        self.ip = ip

    def _get_local_ip(self):
        return self.ip

    def get_status_snapshot(self):
        return {"mdns_hostname": "photoframe", "edition": "baidupan", "platform": "linux"}

    def get_current_settings_dict(self):
        return {"device_name": "pihome"}


def ping_payload(ip, device_id=None, hostname="photoframe-2", known_peers=None):
    payload = {
        "photoframe": True,
        "service": "photoframe",
        "id": f"{ip}:8080",
        "ip": ip,
        "port": 8080,
        "mdns_hostname": hostname,
        "device_name": hostname,
        "edition": "gphotos",
        "platform": "linux",
        "known_peers": list(known_peers or []),
    }
    if device_id is not None:
        payload["device_id"] = device_id
    return payload


class TestPeerTrust(unittest.TestCase):
    def test_trusted_sources(self):
        for ip in (
            "127.0.0.1", "localhost", "::1",
            "10.14.14.1", "10.144.144.44", "172.16.5.4", "192.168.86.44", "169.254.1.2",
            "100.74.85.54", "100.64.0.1", "100.127.255.254",          # Tailscale IPv4 (100.64.0.0/10)
            "fd7a:115c:a1e0::1", "::ffff:100.74.85.54", "fe80::1%eth0",  # Tailscale IPv6, mapped, link-local
        ):
            self.assertTrue(F.is_trusted_peer_ip(ip), ip)

    def test_untrusted_sources(self):
        for ip in ("", "garbage", "8.8.8.8", "100.63.255.255", "100.128.0.1", "172.32.0.1", "2001:db8::1"):
            self.assertFalse(F.is_trusted_peer_ip(ip), ip)

    def test_peer_request_over_tailscale_is_accepted_with_valid_token(self):
        good = {F.PEER_AUTH_HEADER: F.PEER_TOKEN}
        self.assertTrue(F.verify_peer_request(good, MY_TAILSCALE_IP))
        self.assertFalse(F.verify_peer_request({F.PEER_AUTH_HEADER: "wrong"}, MY_TAILSCALE_IP))
        self.assertFalse(F.verify_peer_request({}, MY_TAILSCALE_IP))
        self.assertFalse(F.verify_peer_request(good, "8.8.8.8"))

    def test_lan_scan_helper_is_unchanged(self):
        # The /24 sweep must still only run on genuine private LAN addresses.
        self.assertTrue(F.is_private_lan_ip("192.168.86.44"))
        self.assertFalse(F.is_private_lan_ip("100.74.85.54"))


class FleetTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = FrameConfigStore(path=os.path.join(self.tmp.name, "frame_config.json"))
        self.remote = {}   # ip -> ping payload served by the fake network

        def fake_http(mgr, ip, port, method, path, **kw):
            if path == "/api/fleet/ping" and ip in self.remote:
                return 200, dict(self.remote[ip])
            return 0, {}

        for patcher in (
            mock.patch.object(F.FleetManager, "_http_request_peer", fake_http),
            mock.patch.object(F.FleetManager, "_local_ipv4s", return_value=frozenset()),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.mgr = F.FleetManager(StubApp(), self.store, enable_background_scan=False)

    def peers(self):
        return [n for n in self.mgr.get_fleet_nodes() if not n["is_self"]]


class TestDeviceId(FleetTestBase):
    def test_device_id_is_stable_and_persisted(self):
        first = self.store.get_device_id()
        self.assertRegex(first, r"^[0-9a-f]{32}$")
        self.assertEqual(self.store.get_device_id(), first)
        reloaded = FrameConfigStore(path=self.store.path)
        self.assertEqual(reloaded.get_device_id(), first)

    def test_ping_payload_advertises_device_id(self):
        self.assertEqual(self.mgr.build_ping_payload()["device_id"], self.store.get_device_id())


class TestAliasCollapse(FleetTestBase):
    def test_one_frame_on_two_addresses_is_one_entry(self):
        self.remote[COLLAGE_LAN] = ping_payload(COLLAGE_LAN, device_id="dev-collage")
        self.remote[COLLAGE_TS] = ping_payload(COLLAGE_TS, device_id="dev-collage")
        self.mgr.probe_peer(f"{COLLAGE_LAN}:8080")
        ok, _, _ = self.mgr.add_manual_peer(f"{COLLAGE_TS}:8080")   # user adds the tailnet address
        self.assertTrue(ok)

        peers = self.peers()
        self.assertEqual(len(peers), 1, [p["id"] for p in peers])
        self.assertEqual(peers[0]["id"], f"{COLLAGE_LAN}:8080")      # LAN alias preferred
        self.assertEqual(peers[0]["aliases"], [f"{COLLAGE_TS}:8080"])
        self.assertTrue(peers[0]["is_manual"])                        # manual flag kept across aliases

    def test_frames_without_device_id_are_never_merged(self):
        self.remote[COLLAGE_LAN] = ping_payload(COLLAGE_LAN)
        self.remote[NEST_LAN] = ping_payload(NEST_LAN)
        self.mgr.probe_peer(f"{COLLAGE_LAN}:8080")
        self.mgr.probe_peer(f"{NEST_LAN}:8080")
        self.assertEqual(len(self.peers()), 2)

    def test_distinct_device_ids_stay_distinct(self):
        self.remote[COLLAGE_LAN] = ping_payload(COLLAGE_LAN, device_id="dev-a")
        self.remote[NEST_LAN] = ping_payload(NEST_LAN, device_id="dev-b")
        self.mgr.probe_peer(f"{COLLAGE_LAN}:8080")
        self.mgr.probe_peer(f"{NEST_LAN}:8080")
        self.assertEqual(len(self.peers()), 2)


class TestSelfRecognition(FleetTestBase):
    def test_own_other_address_is_recognised_by_local_interfaces(self):
        self.assertFalse(self.mgr.is_self_target(f"{MY_TAILSCALE_IP}:8080"))
        with mock.patch.object(F.FleetManager, "_local_ipv4s", return_value=frozenset({MY_TAILSCALE_IP, "10.14.14.1"})):
            self.assertTrue(self.mgr.is_self_target(f"{MY_TAILSCALE_IP}:8080"))
            self.assertTrue(self.mgr.is_self_target("10.14.14.1:8080"))
            self.assertFalse(self.mgr.is_self_target(f"{MY_TAILSCALE_IP}:9999"))   # different port is not us

    def test_reaching_ourselves_via_device_id_never_creates_a_peer(self):
        my_id = self.store.get_device_id()
        self.remote[MY_TAILSCALE_IP] = ping_payload(MY_TAILSCALE_IP, device_id=my_id, hostname="photoframe")
        node = self.mgr.probe_peer(f"{MY_TAILSCALE_IP}:8080")
        self.assertTrue(node["is_self"])
        self.assertEqual(self.peers(), [])

        ok, msg, _ = self.mgr.add_manual_peer(f"{MY_TAILSCALE_IP}:8080")
        self.assertTrue(ok)
        self.assertIn("already in the fleet", msg)
        self.assertEqual(self.store.get_settings().get("manual_peers", []), [])
        self.assertEqual(self.peers(), [])


class TestRemovalSticks(FleetTestBase):
    def setUp(self):
        super().setUp()
        self.remote[COLLAGE_LAN] = ping_payload(COLLAGE_LAN, device_id="dev-collage")
        self.remote[COLLAGE_TS] = ping_payload(COLLAGE_TS, device_id="dev-collage")
        # "nest" gossips about the collage frame, like real peers do.
        self.remote[NEST_LAN] = ping_payload(
            NEST_LAN, device_id="dev-nest", hostname="nest",
            known_peers=[f"{COLLAGE_LAN}:8080", f"{COLLAGE_TS}:8080"],
        )
        self.mgr.probe_peer(f"{COLLAGE_LAN}:8080")
        self.mgr.add_manual_peer(f"{COLLAGE_TS}:8080")

    def test_remove_drops_every_alias_and_manual_entry(self):
        self.assertEqual(len(self.peers()), 1)
        self.assertTrue(self.mgr.remove_peer(f"{COLLAGE_LAN}:8080"))
        self.assertEqual(self.peers(), [])
        self.assertEqual(self.store.get_settings().get("manual_peers", []), [])

    def test_removed_frame_is_not_readded_by_gossip_or_refresh(self):
        self.mgr.remove_peer(f"{COLLAGE_LAN}:8080")
        self.mgr.probe_peer(f"{NEST_LAN}:8080")          # nest gossips both collage addresses
        self.mgr.refresh_known_peers(include_mdns=False, force=True)
        ids = [p["id"] for p in self.peers()]
        self.assertEqual(ids, [f"{NEST_LAN}:8080"])

    def test_removal_survives_restart(self):
        self.mgr.remove_peer(f"{COLLAGE_LAN}:8080")
        store2 = FrameConfigStore(path=self.store.path)
        mgr2 = F.FleetManager(StubApp(), store2, enable_background_scan=False)
        mgr2.probe_peer(f"{COLLAGE_TS}:8080")
        self.assertEqual([n for n in mgr2.get_fleet_nodes() if not n["is_self"]], [])

    def test_adding_manually_brings_a_removed_frame_back(self):
        self.mgr.remove_peer(f"{COLLAGE_LAN}:8080")
        ok, _, node = self.mgr.add_manual_peer(f"{COLLAGE_LAN}:8080")
        self.assertTrue(ok)
        self.assertIsNotNone(node)
        self.assertEqual([p["id"] for p in self.peers()], [f"{COLLAGE_LAN}:8080"])
        self.assertNotIn("dev-collage", self.store.get_settings().get("removed_peers", []))


class TestBroadcastAllowedActions(unittest.TestCase):
    def test_recent_stream_actions_allowed_for_broadcast(self):
        for act in ("recent_5", "recent_10", "recent_stop"):
            self.assertIn(act, F.BROADCAST_ALLOWED_ACTIONS)


class TestPortalRemoveButton(unittest.TestCase):
    def test_remove_button_is_offered_for_every_non_self_frame(self):
        self.assertIn("const removeBtn = (!node.is_self)", WEB_PORTAL_HTML)
        self.assertNotIn("node.is_manual || isOffline", WEB_PORTAL_HTML)


if __name__ == "__main__":
    unittest.main()
