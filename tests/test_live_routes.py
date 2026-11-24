"""Strategy 2: live metrics, demand activation, expiry and real TCP failover."""

import queue
import threading
import time
import unittest

from src.node import OverlayNode
from src.overlay.bootstrap import BootstrapClient, BootstrapController
from src.routing.controller import LiveRoutes


def topology(ports=None):
    names = ("Server1", "R1", "R2", "R3", "C1", "C2")
    nodes = {name: dict(host="127.0.0.1", port=(ports or {}).get(name, 5000),
                        router=name.startswith("R"), neighbours=[]) for name in names}
    pairs = [("Server1", "R1"), ("Server1", "R2"), ("R1", "R3"),
             ("R2", "R3"), ("R3", "C1"), ("R3", "C2")]
    links = [dict(id=str(i), a=dict(node=a, host="127.0.0.1", interface="eth" + str(i)),
                  b=dict(node=b, host="127.0.0.1", interface="eth" + str(i)))
             for i, (a, b) in enumerate(pairs)]
    return dict(nodes=nodes, links=links)


class LiveRouteTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.config = topology()
        self.routes = LiveRoutes(self.config, "Server1", clock=lambda: self.now)
        for name in self.config["nodes"]:
            self.report(name)

    def report(self, name, down=()):
        self.routes.register(name, {target["id"]: target["id"] not in down
                                    for target in self.routes.targets(name)})

    def test_no_data_tree_until_client_requests_stream(self):
        self.assertEqual(self.routes.parents, {})
        self.assertEqual(self.routes.table(), [])
        self.routes.subscribe("C1")
        row = self.routes.table()[0]
        self.assertEqual(row["path"], ["Server1", "R1", "R3", "C1"])
        self.assertEqual(row["hops"], 3)
        self.routes.subscribe("C2")
        self.assertEqual(self.routes.routing("R3")["children"], ["C1", "C2"])
        self.routes.subscribe("C1", False)
        self.assertEqual(self.routes.routing("R3")["children"], ["C2"])
        self.routes.subscribe("C2", False)
        self.assertEqual(self.routes.parents, {})

    def test_single_endpoint_failure_is_enough_and_restoration_reconsiders_shortest_path(self):
        self.routes.subscribe("C1")
        previous = self.routes.version
        self.report("R1", down={"2"})
        self.assertEqual(self.routes.table()[0]["path"], ["Server1", "R2", "R3", "C1"])
        self.assertGreater(self.routes.version, previous)
        self.report("R1")
        self.assertEqual(self.routes.table()[0]["path"][1], "R1")

    def test_partition_is_unreachable_not_a_stale_route(self):
        self.routes.subscribe("C1")
        self.report("R3", down={"2", "3"})
        self.assertEqual(self.routes.table()[0]["state"], "UNREACHABLE")
        self.assertEqual(self.routes.parents, {})
        self.report("R3")
        self.assertEqual(self.routes.table()[0]["state"], "ACTIVE")

    def test_expired_router_cannot_be_used(self):
        self.routes.subscribe("C1")
        self.now = 16
        for name in self.config["nodes"]:
            if name != "R1":
                self.report(name)
        self.routes.subscribe("C1")
        self.assertEqual(self.routes.table()[0]["path"][1], "R2")

    def test_missing_reports_are_not_assumed_up_and_foreign_links_are_rejected(self):
        self.routes.subscribe("C1")
        self.routes.register("R1", {})
        self.assertEqual(self.routes.table()[0]["path"][1], "R2")
        with self.assertRaises(ValueError):
            self.routes.register("C1", {"0": True})
        with self.assertRaises(ValueError):
            self.routes.subscribe("R1")

    def test_new_controller_generation_can_install_a_lower_epoch(self):
        node = OverlayNode("C1")
        node.configure_routing("client", "Server1", "R1", [], epoch=20, controller_id="old")
        node.configure_routing("client", "Server1", "R2", [], epoch=19, controller_id="old")
        self.assertEqual(node._routing["parent_id"], "R1")
        node.configure_routing("client", "Server1", "R2", [], epoch=1, controller_id="new")
        self.assertEqual(node._routing["parent_id"], "R2")


class LiveDeliveryTests(unittest.TestCase):
    def test_multicast_and_upstream_recover_after_link_cut_and_restore(self):
        nodes, inboxes = {}, {}
        for name in topology()["nodes"]:
            inbox = queue.Queue()
            node = OverlayNode(name, "127.0.0.1", 0,
                               on_message=lambda _, msg, q=inbox: q.put(msg)
                               if msg["type"] == "MESSAGE" else None)
            node.start()
            self.addCleanup(node.stop)
            nodes[name], inboxes[name] = node, inbox
        config = topology({name: node.port for name, node in nodes.items()})
        routes = LiveRoutes(config, "Server1")
        down = threading.Event()

        def probe(targets):
            return {target["id"]: not (down.is_set() and target["id"] == "2") for target in targets}

        controller = BootstrapController("Server1", config, "127.0.0.1", 0,
                                         routes=routes, node=nodes["Server1"], interval=0.03, probe=probe)
        controller.start()
        self.addCleanup(controller.stop)
        for name in config["nodes"]:
            if name != "Server1":
                worker = BootstrapClient(nodes[name], ("127.0.0.1", controller.port), interval=0.03, probe=probe)
                worker.start()
                self.addCleanup(worker.stop)

        def settled(parent):
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                rows = routes.table()
                used = {"Server1", parent, "R3", "C1", "C2"}
                if (len(rows) == 2 and all(row["path"] and row["path"][1] == parent for row in rows)
                        and all(nodes[name]._routing and nodes[name]._routing["epoch"] == routes.version for name in used)
                        and all({peer for peer, _ in nodes[child].peers()} >= {p}
                                and {peer for peer, _ in nodes[p].peers()} >= {child}
                                for child, p in routes.parents.items())):
                    return
                time.sleep(0.02)
            self.fail("Dynamic tree did not converge")

        for parent, text in (("R1", "before cut"), ("R2", "after cut"), ("R1", "restored")):
            if text == "after cut":
                down.set()
            elif text == "restored":
                down.clear()
            settled(parent)
            paths, routing = routes.diagnostic_plan()
            results = nodes["Server1"].diagnostics.ping(paths, timeout=2, routing=routing)
            self.assertTrue(all(result["status"] == "OK" for result in results))
            for result in results:
                self.assertEqual(result["path"], paths[result["client"]])
                self.assertEqual(result["path"][1], parent)
            nodes["Server1"].broadcast(text, "MESSAGE")
            for client in ("C1", "C2"):
                message = inboxes[client].get(timeout=3)
                self.assertEqual((message["node_id"], message["text"]), ("Server1", text))
            nodes["C1"].broadcast(text, "MESSAGE")
            self.assertEqual(inboxes["Server1"].get(timeout=3)["node_id"], "C1")
            with self.assertRaises(queue.Empty):
                inboxes["C2"].get(timeout=0.05)
