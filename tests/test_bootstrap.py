"""Bootstrap integration with real sockets, lease expiry and peer recovery."""

import queue
import socket
import time
import unittest

from src.node import OverlayNode
from src.overlay.bootstrap import BootstrapClient, BootstrapController, request
from src.overlay.topology import load_topology


class BootstrapTests(unittest.TestCase):
    def node(self, name):
        inbox = queue.Queue()
        node = OverlayNode(name, "127.0.0.1", 0,
                           on_message=lambda _, message: inbox.put(message))
        node.start()
        self.addCleanup(node.stop)
        return node, inbox

    def controller(self, config, **kwargs):
        controller = BootstrapController("Server1", config, "127.0.0.1", 0, **kwargs)
        controller.start()
        self.addCleanup(controller.stop)
        return controller, ("127.0.0.1", controller.port)

    def test_c1_activates_r3_and_establishes_full_duplex_links(self):
        server, server_inbox = self.node("Server1")
        router, router_inbox = self.node("R3")
        client, client_inbox = self.node("C1")
        config = {"nodes": {
            "Server1": dict(host="127.0.0.1", port=server.port, neighbours=[]),
            "R3": dict(host="127.0.0.1", port=router.port, neighbours=["Server1"], router=True),
            "C1": dict(host="127.0.0.1", port=client.port, neighbours=["R3"], activate=["R3"])}}
        activated = []
        controller, address = self.controller(config, activate=activated.append)
        r3 = BootstrapClient(router, address, interval=0.05)
        c1 = BootstrapClient(client, address, interval=0.05)
        self.addCleanup(r3.stop)
        self.addCleanup(c1.stop)
        r3.start()
        c1.start()
        self.assertEqual(server_inbox.get(timeout=3)["node_id"], "R3")
        self.assertEqual(client_inbox.get(timeout=3)["node_id"], "R3")
        self.assertEqual({router_inbox.get(timeout=3)["node_id"] for _ in range(2)},
                         {"C1", "Server1"})
        self.assertEqual(activated[0], "R3")
        self.assertEqual(controller.members(), ["C1", "R3"])
        c1.refresh()
        self.assertEqual(len(client.peers()), 1)
        client.broadcast("test", "MESSAGE")
        self.assertEqual(server_inbox.get(timeout=3)["text"], "test")
        c1.stop()
        self.assertEqual(controller.members(), ["R3"])

    def test_expiry_unknown_id_and_missing_launcher(self):
        config = load_topology("topology/topology.xml")
        controller, address = self.controller(config, lease_seconds=0.05)
        with self.assertRaisesRegex(ValueError, "autorizado"):
            request(address, "unknown")
        with self.assertRaisesRegex(ValueError, "core-session"):
            request(address, "C1")
        request(address, "R9")
        self.assertEqual(controller.members(), ["R9"])
        time.sleep(0.06)
        self.assertEqual(controller.members(), [])

    def test_retries_neighbour_that_starts_late(self):
        server, _ = self.node("Server1")
        client, inbox = self.node("C1")
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        config = {"nodes": {
            "Server1": dict(host="127.0.0.1", port=server.port, neighbours=[]),
            "R3": dict(host="127.0.0.1", port=port, neighbours=[]),
            "C1": dict(host="127.0.0.1", port=client.port, neighbours=["R3"])}}
        _, address = self.controller(config)
        worker = BootstrapClient(client, address, interval=0.05)
        self.addCleanup(worker.stop)
        worker.start()
        router = OverlayNode("R3", "127.0.0.1", port)
        router.start()
        self.addCleanup(router.stop)
        self.assertEqual(inbox.get(timeout=3)["node_id"], "R3")

    def test_controller_failure_preserves_existing_link(self):
        server, server_inbox = self.node("Server1")
        client, inbox = self.node("C1")
        config = {"nodes": {
            "Server1": dict(host="127.0.0.1", port=server.port, neighbours=[]),
            "C1": dict(host="127.0.0.1", port=client.port, neighbours=["Server1"])}}
        controller, address = self.controller(config)
        worker = BootstrapClient(client, address)
        worker.start()
        self.addCleanup(worker.stop)
        inbox.get(timeout=3)
        server_inbox.get(timeout=3)
        controller.stop()
        client.broadcast("still connected", "MESSAGE")
        self.assertEqual(server_inbox.get(timeout=3)["text"], "still connected")
