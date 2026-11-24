"""Physical adjacency probes must not follow an alternate routed path."""

import subprocess
import unittest
from unittest.mock import patch

from src.routing.links import probe, sample


class LinkMonitorTests(unittest.TestCase):
    def test_probe_is_interface_bound_and_one_hop(self):
        target = dict(id="link", interface="eth2", host="10.0.5.1")
        with patch("src.routing.links.subprocess.run") as run:
            run.return_value.returncode = 0
            self.assertTrue(probe(target))
            self.assertEqual(run.call_args.args[0],
                             ["ping", "-n", "-I", "eth2", "-t", "1", "-c", "1", "-W", "1", "10.0.5.1"])

    def test_failed_or_timed_out_probe_is_down(self):
        target = dict(id="link", interface="eth2", host="10.0.5.1")
        with patch("src.routing.links.subprocess.run") as run:
            run.return_value.returncode = 1
            self.assertFalse(probe(target))
            run.side_effect = subprocess.TimeoutExpired("ping", 2)
            self.assertFalse(probe(target))

    def test_empty_sample_needs_no_processes(self):
        with patch("src.routing.links.subprocess.run") as run:
            self.assertEqual(sample([]), {})
            run.assert_not_called()
