"""Real TCP delivery tests: server fan-out and isolated client uplinks."""

import queue
import time
import unittest

from src.node import OverlayNode
from src.overlay.bootstrap import BootstrapClient, BootstrapController, routing_for


class MulticastTests(unittest.TestCase):
    def setUp(self):
        self.nodes, self.inboxes = {}, {}
        for name in ("Server1", "R3", "R6", "C1", "C2", "C5"):
            inbox = queue.Queue()
            node = OverlayNode(name, "127.0.0.1", 0,
                               on_message=lambda _, message, q=inbox: q.put(message)
                               if message["type"] == "MESSAGE" else None)
            node.start()
            self.addCleanup(node.stop)
            self.nodes[name], self.inboxes[name] = node, inbox
        parents = dict(Server1=None, R3="Server1", R6="Server1", C1="R3", C2="R3", C5="R6")
        config = {"nodes": {name: dict(host="127.0.0.1", port=node.port,
                                      neighbours=[parents[name]] if parents[name] else [],
                                      router=name in ("R3", "R6"))
                            for name, node in self.nodes.items()}}
        self.nodes["Server1"].configure_routing(**routing_for(config, "Server1", "Server1"))
        controller = BootstrapController("Server1", config, "127.0.0.1", 0)
        controller.start()
        self.addCleanup(controller.stop)
        for name in ("R3", "R6", "C1", "C2", "C5"):
            worker = BootstrapClient(self.nodes[name], ("127.0.0.1", controller.port))
            worker.start()
            self.addCleanup(worker.stop)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if all({peer for peer, _ in self.nodes[parent].peers()} >= {child}
                   and {peer for peer, _ in self.nodes[child].peers()} >= {parent}
                   for child, parent in parents.items() if parent):
                break
            time.sleep(0.01)
        else:
            self.fail("Overlay HELLO handshake timed out")

    def test_server_multicasts_once_to_every_client_and_preserves_peer_identity(self):
        self.assertEqual(self.nodes["Server1"].broadcast("everyone", "MESSAGE"), 2)
        for name in ("C1", "C2", "C5"):
            message = self.inboxes[name].get(timeout=3)
            self.assertEqual((message["node_id"], message["text"]), ("Server1", "everyone"))
            self.assertTrue(self.inboxes[name].empty())
        self.assertEqual({peer for peer, _ in self.nodes["C1"].peers()}, {"R3"})
        self.assertEqual({peer for peer, _ in self.nodes["Server1"].peers()}, {"R3", "R6"})
        for name in ("Server1", "R3", "R6"):
            self.assertTrue(self.inboxes[name].empty())

    def test_each_client_sends_only_to_server_without_echo_or_sibling_delivery(self):
        for name in ("C1", "C2", "C5"):
            self.nodes[name].broadcast("from " + name, "MESSAGE")
            message = self.inboxes["Server1"].get(timeout=3)
            self.assertEqual((message["node_id"], message["text"]), (name, "from " + name))
        # Observe beyond server receipt, to catch accidental server rebroadcasts.
        for name in ("C1", "C2", "C5", "R3", "R6"):
            with self.assertRaises(queue.Empty):
                self.inboxes[name].get(timeout=0.05)

    def test_router_cannot_originate_application_messages(self):
        with self.assertRaisesRegex(ValueError, "apenas reencaminham"):
            self.nodes["R3"].broadcast("router text", "MESSAGE")

    def test_duplicate_upstream_packet_is_delivered_once(self):
        client = self.nodes["C1"]
        message = dict(node_id="C1", text="duplicate", delivery="UPSTREAM", message_id="same", ttl=64)
        client._forward(message, {"R3"})
        self.assertEqual(self.inboxes["Server1"].get(timeout=3)["text"], "duplicate")
        client._forward(message, {"R3"})
        with self.assertRaises(queue.Empty):
            self.inboxes["Server1"].get(timeout=0.1)
