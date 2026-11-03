"""Integration tests using real local TCP sockets and ephemeral ports."""

import concurrent.futures
import queue
import socket
import unittest

from src.node import OverlayNode
from src.config import MAX_MESSAGE_BYTES
from src.protocol.messages import decode_message, encode_message


class NodeTests(unittest.TestCase):
    def make_node(self, name):
        inbox = queue.Queue()
        node = OverlayNode(name, "127.0.0.1", 0,
                           on_message=lambda peer, message: inbox.put(message))
        node.start()
        self.addCleanup(node.stop)
        return node, inbox

    def test_hello_and_full_duplex(self):
        a, inbox_a = self.make_node("A")
        b, inbox_b = self.make_node("B")
        b.connect("127.0.0.1", a.port)
        self.assertEqual(inbox_a.get(timeout=3),
                         {"type": "HELLO", "node_id": "B", "text": "HELLO"})
        self.assertEqual(inbox_b.get(timeout=3),
                         {"type": "HELLO", "node_id": "A", "text": "HELLO"})
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            sends = [pool.submit(a.broadcast, "Olá de A", "MESSAGE"),
                     pool.submit(b.broadcast, "Olá de B", "MESSAGE")]
            self.assertEqual([send.result(timeout=3) for send in sends], [1, 1])
        self.assertEqual(inbox_a.get(timeout=3)["text"], "Olá de B")
        self.assertEqual(inbox_b.get(timeout=3)["text"], "Olá de A")
        self.assertEqual(inbox_a.qsize(), 0)
        self.assertEqual(inbox_b.qsize(), 0)

    def test_accepts_another_peer_while_connected(self):
        a, inbox_a = self.make_node("A")
        b, inbox_b = self.make_node("B")
        c, inbox_c = self.make_node("C")
        b.connect("127.0.0.1", a.port)
        c.connect("127.0.0.1", a.port)
        self.assertEqual({inbox_a.get(timeout=3)["node_id"] for _ in range(2)}, {"B", "C"})
        inbox_b.get(timeout=3)
        inbox_c.get(timeout=3)
        self.assertEqual(a.broadcast(), 2)
        self.assertEqual(inbox_b.get(timeout=3)["type"], "HELLO")
        self.assertEqual(inbox_c.get(timeout=3)["type"], "HELLO")

    def test_tcp_fragmentation_and_coalescing(self):
        node, inbox = self.make_node("A")
        with socket.create_connection(("127.0.0.1", node.port), timeout=3) as sock:
            with sock.makefile("rb") as reader:
                self.assertEqual(decode_message(reader.readline())["type"], "HELLO")
                first = encode_message("MESSAGE", "raw", "Olá\nsegunda linha")
                second = encode_message("HELLO", "raw")
                sock.sendall(first[:4])
                sock.sendall(first[4:] + second)
                self.assertEqual(inbox.get(timeout=3)["text"], "Olá\nsegunda linha")
                self.assertEqual(inbox.get(timeout=3)["type"], "HELLO")

    def test_bad_peer_is_closed_and_listener_survives(self):
        node, inbox = self.make_node("A")
        with socket.create_connection(("127.0.0.1", node.port), timeout=3) as sock:
            with sock.makefile("rb") as reader:
                reader.readline()
                sock.sendall(b"not-json\n")
                self.assertEqual(reader.read(1), b"")
        peer, peer_inbox = self.make_node("B")
        peer.connect("127.0.0.1", node.port)
        self.assertEqual(inbox.get(timeout=3)["node_id"], "B")
        self.assertEqual(peer_inbox.get(timeout=3)["node_id"], "A")

    def test_stop_closes_active_connections_and_workers(self):
        a, inbox_a = self.make_node("A")
        b, inbox_b = self.make_node("B")
        b.connect("127.0.0.1", a.port)
        inbox_a.get(timeout=3)
        inbox_b.get(timeout=3)
        a.stop()
        b.stop()
        self.assertEqual(a.peers(), [])
        self.assertEqual(b.peers(), [])
        self.assertFalse(a._threads)
        self.assertFalse(b._threads)

    def test_oversized_message_closes_connection(self):
        node, _ = self.make_node("A")
        with socket.create_connection(("127.0.0.1", node.port), timeout=3) as sock:
            with sock.makefile("rb") as reader:
                reader.readline()
                sock.sendall(b"x" * (MAX_MESSAGE_BYTES + 1))
                self.assertEqual(reader.read(1), b"")

    def test_outgoing_limit_counts_utf8_bytes(self):
        with self.assertRaises(ValueError):
            encode_message("MESSAGE", "A", "á" * (MAX_MESSAGE_BYTES // 2))
