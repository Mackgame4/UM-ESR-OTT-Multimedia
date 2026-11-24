"""Stage 3: path accumulation, reverse replies, timeout and stale tree handling."""

from concurrent.futures import ThreadPoolExecutor
import threading
import time
import unittest
from unittest.mock import patch

from src.node import OverlayNode
from src.protocol.messages import encode_message


class RoutePingTests(unittest.TestCase):
    def setUp(self):
        self.nodes = {}
        parents = dict(Server1=None, R1="Server1", R2="Server1", C1="R1", C2="R1", C3="R2")
        self.paths = dict(C1=["Server1", "R1", "C1"], C2=["Server1", "R1", "C2"],
                          C3=["Server1", "R2", "C3"])
        for name, parent in parents.items():
            node = OverlayNode(name, "127.0.0.1", 0)
            node.configure_routing("server" if name == "Server1" else "router" if name.startswith("R") else "client",
                                   "Server1", parent, [child for child, p in parents.items() if p == name],
                                   epoch=1, controller_id="controller")
            node.start()
            self.addCleanup(node.stop)
            self.nodes[name] = node
        for name, parent in parents.items():
            if parent:
                self.nodes[name].connect("127.0.0.1", self.nodes[parent].port)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if all(parent in {peer for peer, _ in self.nodes[child].peers()} and
                   child in {peer for peer, _ in self.nodes[parent].peers()}
                   for child, parent in parents.items() if parent):
                break
            time.sleep(0.01)
        else:
            self.fail("HELLO timed out")
        self.server = self.nodes["Server1"]

    def test_multicast_ping_accumulates_independent_paths_and_returns_in_reverse(self):
        with self.assertLogs("src.routing.diagnostics", level="INFO") as logs:
            rows = self.server.diagnostics.ping(self.paths, timeout=1)
        self.assertEqual([row["client"] for row in rows], ["C1", "C2", "C3"])
        for row in rows:
            self.assertEqual(row["status"], "OK")
            self.assertEqual(row["path"], self.paths[row["client"]])
            self.assertEqual(row["return_path"], list(reversed(row["path"])))
            self.assertEqual(row["hops"], 2)
            self.assertGreaterEqual(row["rtt_ms"], 0)
        self.assertEqual(len({row["test_id"] for row in rows}), 1)
        self.assertTrue(any("[R1] ROUTE_PING" in line for line in logs.output))
        self.assertTrue(any("[R1] ROUTE_PONG" in line for line in logs.output))
        self.assertEqual({peer for peer, _ in self.nodes["C1"].peers()}, {"R1"})

    def test_missing_reply_times_out_only_its_client_and_late_replies_are_ignored(self):
        client = self.nodes["C2"]
        real_forward = client._forward
        held = []

        def hold_reply(message, targets):
            held.append((message, targets))
            return 0

        with patch.object(client, "_forward", side_effect=hold_reply):
            rows = self.server.diagnostics.ping(self.paths, timeout=0.1)
        self.assertEqual({row["client"]: row["status"] for row in rows},
                         dict(C1="OK", C2="TIMEOUT", C3="OK"))
        self.assertEqual(self.server.diagnostics._pending, {})
        for message, targets in held:
            real_forward(message, targets)
        self.assertTrue(all(row["status"] == "OK" for row in self.server.diagnostics.ping(self.paths, timeout=1)))

    def test_route_change_wakes_pending_test_and_marks_all_results_stale(self):
        reached = threading.Event()
        with patch.object(self.nodes["C2"], "_forward", side_effect=lambda *_: reached.set() or 0):
            with ThreadPoolExecutor(1) as pool:
                future = pool.submit(self.server.diagnostics.ping, self.paths, 3)
                self.assertTrue(reached.wait(1))
                self.server.configure_routing("server", "Server1", None, ["R1", "R2"],
                                              epoch=2, controller_id="controller")
                rows = future.result(timeout=1)
        self.assertTrue(all(row["status"] == "STALE" for row in rows))

    def test_duplicate_replies_produce_one_result(self):
        client = self.nodes["C2"]
        real_forward = client._forward

        def twice(message, targets):
            return real_forward(message, targets) + real_forward(message, targets)

        with patch.object(client, "_forward", side_effect=twice):
            rows = self.server.diagnostics.ping(self.paths, timeout=1)
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row["status"] == "OK" for row in rows))

    def test_unreachable_clients_and_empty_tree_do_not_wait(self):
        self.assertEqual(self.server.diagnostics.ping({}), [])
        row = self.server.diagnostics.ping(dict(C9=[]), timeout=1)[0]
        self.assertEqual(row["status"], "UNREACHABLE")
        with self.assertRaises(ValueError):
            self.nodes["C1"].diagnostics.ping(self.paths)

    def test_ttl_zero_is_not_forwarded(self):
        real_forward = self.server._forward
        with patch.object(self.server, "_forward", side_effect=lambda message, targets:
                          real_forward(dict(message, ttl=0), targets)):
            rows = self.server.diagnostics.ping(self.paths, timeout=0.1)
        self.assertTrue(all(row["status"] == "TIMEOUT" for row in rows))

    def test_observed_path_is_checked_against_controller_plan(self):
        paths = dict(self.paths, C1=["Server1", "R2", "C1"])
        rows = self.server.diagnostics.ping(paths, timeout=1)
        self.assertEqual(rows[0]["status"], "PATH_MISMATCH")
        self.assertEqual(rows[0]["path"], self.paths["C1"])

    def test_invalid_protocol_paths_are_rejected(self):
        packet = dict(test_id="test", ttl=64, epoch=1, controller_id="controller", path=["Server1"])
        for path in ([], ["Server1", "Server1"], ["Server1", 3]):
            with self.assertRaises(ValueError):
                encode_message("ROUTE_PING", "Server1", "", **dict(packet, path=path))
        with self.assertRaises(ValueError):
            encode_message("ROUTE_PONG", "C1", "", **packet)
