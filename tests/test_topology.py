"""Router selection from the physical XML and shared process reuse."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.overlay.bootstrap import BootstrapController, request
from src.overlay.core import CoreLauncher
from src.overlay.topology import load_topology


class TopologyTests(unittest.TestCase):
    def test_c1_and_c2_share_only_the_access_router(self):
        nodes = load_topology("topology/topology.xml")["nodes"]
        self.assertEqual(nodes["C1"]["path"], ["Server1", "R3", "C1"])
        self.assertEqual(nodes["C1"]["neighbours"], ["R3"])
        self.assertEqual(nodes["C1"]["activate"], ["R3"])
        self.assertEqual(nodes["C2"]["activate"], nodes["C1"]["activate"])
        self.assertEqual(nodes["R3"]["neighbours"], ["Server1"])
        self.assertEqual(nodes["R9"]["neighbours"], ["Server1"])
        self.assertEqual(nodes["Server1"]["activate"], [])
        for node in nodes.values():
            self.assertTrue(all(nodes[name]["router"] for name in node["activate"]))

    def test_registration_activates_only_selected_routers_and_reuses_processes(self):
        config = load_topology("topology/topology.xml")
        with tempfile.TemporaryDirectory() as session, \
                patch.object(Path, "is_socket", return_value=True), \
                patch("src.overlay.core.subprocess.Popen") as popen:
            popen.return_value.poll.return_value = None
            launcher = CoreLauncher(session, "10.0.4.20:5001", config)
            controller = BootstrapController("Server1", config, "127.0.0.1", 0, activate=launcher)
            controller.start()
            try:
                address = ("127.0.0.1", controller.port)
                self.assertEqual(popen.call_count, 0)
                self.assertEqual(dict(controller.member_statuses())["R9"], "UNDERLAY ONLY")
                self.assertEqual(request(address, "C1")["neighbours"][0]["node_id"], "R3")
                self.assertEqual(list(launcher.processes), ["R3"])
                request(address, "C2")
                request(address, "C1")
                self.assertEqual(popen.call_count, 1)
                self.assertEqual(controller.members(), ["C1", "C2"])
                statuses = dict(controller.member_statuses())
                self.assertEqual(statuses["Server1"], "CONTROLLER")
                self.assertEqual(statuses["C1"], "OVERLAY CLIENT")
                self.assertEqual(statuses["C2"], "OVERLAY CLIENT")
                self.assertEqual(statuses["R3"], "OVERLAY NOT REGISTERED")
                for router in ("R4", "R7", "R9"):
                    self.assertEqual(statuses[router], "UNDERLAY ONLY")
                request(address, "R3")
                self.assertEqual(dict(controller.member_statuses())["R3"], "OVERLAY ROUTER")
                request(address, "R3", "LEAVE")
                self.assertEqual(dict(controller.member_statuses())["R3"], "OVERLAY INACTIVE")
                request(address, "R3")
                with patch("src.overlay.bootstrap.time.monotonic", return_value=float("inf")):
                    statuses = dict(controller.member_statuses())
                self.assertEqual(statuses["R3"], "OVERLAY INACTIVE")
                self.assertEqual(statuses["R9"], "UNDERLAY ONLY")
                self.assertNotIn("C1", statuses)
            finally:
                controller.stop()
                launcher.stop()

    def test_unknown_server_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "ausente"):
            load_topology("topology/topology.xml", "missing")

    def test_other_server_can_be_the_root(self):
        nodes = load_topology("topology/topology.xml", "Server2")["nodes"]
        self.assertEqual(nodes["Server2"]["neighbours"], [])
        # C7 and Server2 share S4: no router process is needed for this client.
        self.assertEqual(nodes["C7"]["activate"], [])
        self.assertEqual(nodes["C7"]["neighbours"], ["Server2"])
        self.assertEqual(nodes["C1"]["activate"], ["R3"])
