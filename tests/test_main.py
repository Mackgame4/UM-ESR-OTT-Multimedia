"""Tests for the application entry point."""

import subprocess
import sys
import unittest
from pathlib import Path


class MainTests(unittest.TestCase):
    """Check the application's command-line output."""

    def test_hello_world(self):
        """The entry point exits successfully and prints a greeting."""
        project_root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, "-m", "src.main"],
            cwd=project_root,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(result.stdout, "Hello, world!\n")
        self.assertEqual(result.stderr, "")
