"""CLI smoke tests without leaving a server running."""

import subprocess
import sys
import unittest
from pathlib import Path


class MainTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "src.main", *args],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, timeout=5,
        )

    def test_help(self):
        result = self.run_cli("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("--peer", result.stdout)

    def test_invalid_peer(self):
        result = self.run_cli("--peer", "localhost:70000")
        self.assertEqual(result.returncode, 2)
        self.assertIn("65535", result.stderr)

    def test_empty_node_id(self):
        result = self.run_cli("--id", " ")
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("Traceback", result.stderr)
