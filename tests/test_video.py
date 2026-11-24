"""UDP multicast, bounded live loss handling and textbook MJPEG packetization."""

from dataclasses import replace
from pathlib import Path
import queue
import tempfile
import unittest
from unittest.mock import patch

from src.node import OverlayNode
from src.streaming.packet import MAX_DATAGRAM, MAX_FRAME, VideoPacket, packetize
from src.streaming.receiver import LiveReceiver
from src.streaming.source import VideoSource
from src.streaming.transport import VideoTransport
from src.streaming.video import VideoStream

CONTROLLER = "controller"
SESSION = b"s" * 16


def packets(frame, data=b"j" * 4000):
    return packetize(data, CONTROLLER, SESSION, 1, frame, 65534, 90000)


class VideoFormatTests(unittest.TestCase):
    def test_fragmentation_limits_roundtrip_and_sequence_wrap(self):
        jpeg = b"j" * MAX_FRAME
        data = packets(1, jpeg)
        self.assertTrue(all(len(packet) <= MAX_DATAGRAM for packet in data))
        decoded = [VideoPacket.decode(packet) for packet in data]
        self.assertEqual(b"".join(packet.payload for packet in decoded), jpeg)
        self.assertEqual([packet.sequence for packet in decoded[:3]], [65534, 65535, 0])
        self.assertEqual(decoded[0].forwarded().ttl, 63)
        with self.assertRaises(ValueError):
            VideoPacket.decode(data[0][:-1])
        with self.assertRaises(ValueError):
            VideoPacket.decode(b"bad")
        with self.assertRaises(ValueError):
            packets(1, b"j" * (MAX_FRAME + 1))

    def test_new_frame_overtakes_lost_fragment_without_waiting(self):
        losses = []
        receiver = LiveReceiver(lambda *args: losses.append(args))
        first = [VideoPacket.decode(data) for data in packets(1)]
        for packet in first[1:]:
            self.assertIsNone(receiver.push(packet))
        result = None
        for data in packets(2):
            result = receiver.push(VideoPacket.decode(data)) or result
        self.assertEqual(result, (2, b"j" * 4000))
        self.assertEqual(losses[0][:3], ("newer_frame", 1, 1))
        self.assertIsNone(receiver.push(first[0]))

    def test_reordered_fragments_duplicates_deadline_and_frame_gaps(self):
        now, losses = [0.0], []
        receiver = LiveReceiver(lambda *args: losses.append(args), clock=lambda: now[0])
        first = [VideoPacket.decode(data) for data in packets(80)]
        for packet in reversed(first):
            result = receiver.push(packet)
        self.assertEqual(result[0], 80)
        self.assertEqual(losses, [])  # Joining a live source does not lose its past.
        self.assertIsNone(receiver.push(first[0]))
        receiver.push(VideoPacket.decode(packets(82)[0]))
        self.assertEqual(losses[0][:3], ("sequence_gap", 81, 81))
        now[0] = 1
        receiver.expire()
        self.assertEqual(losses[-1][:3], ("fragment_deadline", 82, 82))

    def test_teaching_file_reader_checks_lengths_and_rewinds(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "movie.Mjpeg"
            path.write_bytes(b"00003abc00002de")
            stream = VideoStream(path)
            try:
                self.assertEqual(stream.next_frame(), b"abc")
                self.assertEqual(stream.next_frame(), b"de")
                self.assertIsNone(stream.next_frame())
                stream.rewind()
                self.assertEqual(stream.next_frame(), b"abc")
            finally:
                stream.close()
            path.write_bytes(b"00005a")
            stream = VideoStream(path)
            try:
                with self.assertRaises(ValueError):
                    stream.next_frame()
            finally:
                stream.close()

    def test_late_packets_from_previous_source_cannot_rewind_live_playback(self):
        receiver = LiveReceiver(lambda *_: None)
        old = VideoPacket.decode(packets(20, b"old")[0])
        self.assertEqual(receiver.push(old), (20, b"old"))
        new = replace(old, session=b"n" * 16, frame=1, payload=b"new")
        self.assertEqual(receiver.push(new), (1, b"new"))
        self.assertIsNone(receiver.push(replace(old, frame=21)))


class VideoMulticastTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.nodes, self.inboxes = {}, {}
        parents = dict(Server1=None, R3="Server1", C1="R3", C2="R3")
        for name, parent in parents.items():
            inbox = queue.Queue()
            node = OverlayNode(name)
            node.configure_routing("server" if name == "Server1" else "router" if name == "R3" else "client",
                                   "Server1", parent, [child for child, p in parents.items() if p == name],
                                   epoch=1, controller_id=CONTROLLER)
            node.media = VideoTransport(node, "127.0.0.1", 0, self.folder / (name + ".log"),
                                        on_frame=lambda frame, jpeg, q=inbox: q.put((frame, jpeg)))
            node.media.start()
            self.addCleanup(node.stop)
            self.nodes[name], self.inboxes[name] = node, inbox
        for name, parent in parents.items():
            def endpoint(peer):
                return dict(host="127.0.0.1", port=self.nodes[peer].media.port)
            self.nodes[name].media.configure(dict(epoch=1, controller_id=CONTROLLER,
                                                 parent=endpoint(parent) if parent else None,
                                                 children=[endpoint(child) for child, p in parents.items() if p == name]))
        self.server = self.nodes["Server1"].media

    def send(self, frame):
        data = packets(frame)
        self.assertEqual(self.server.publish(data, self.server.snapshot()), len(data))
        return len(data)

    def test_server_sends_one_copy_router_replicates_and_rejects_wrong_epoch(self):
        count = self.send(1)
        for name in ("C1", "C2"):
            self.assertEqual(self.inboxes[name].get(timeout=2), (1, b"j" * 4000))
        self.assertEqual(self.server.stats["sent"], count)
        self.assertEqual(self.nodes["R3"].media.stats["forwarded"], 2 * count)
        old = [replace(VideoPacket.decode(data), epoch=0).encode() for data in packets(2)]
        self.server.publish(old, self.server.snapshot())
        with self.assertRaises(queue.Empty):
            self.inboxes["C1"].get(timeout=0.05)

    def test_one_client_loss_is_logged_without_delaying_next_frame_or_other_client(self):
        router = self.nodes["R3"].media
        send = router._send

        def drop_one(data, targets):
            packet = VideoPacket.decode(data)
            if packet.frame == 1 and packet.fragment == 1:
                targets = [target for target in targets if target["port"] != self.nodes["C1"].media.port]
            return send(data, targets)

        with patch.object(router, "_send", side_effect=drop_one):
            self.send(1)
            self.assertEqual(self.inboxes["C2"].get(timeout=2)[0], 1)
            self.send(2)
            self.assertEqual(self.inboxes["C1"].get(timeout=2)[0], 2)
            self.assertEqual(self.inboxes["C2"].get(timeout=2)[0], 2)
        self.assertEqual(self.nodes["C1"].media.stats["lost_frames"], 1)
        self.assertIn("FRAME_LOSS", (self.folder / "C1.log").read_text())
        self.assertEqual(self.server.stats["sent"], 2 * len(packets(1)))  # No retries.

    def test_one_looping_source_shares_same_live_frames_with_both_clients(self):
        path = self.folder / "movie.Mjpeg"
        jpeg = b"j" * 4000
        path.write_bytes(b"04000" + jpeg)
        source = VideoSource(self.server, path, fps=20)
        self.addCleanup(source.stop)
        source.start()
        for expected in (1, 2, 3):
            for name in ("C1", "C2"):
                self.assertEqual(self.inboxes[name].get(timeout=2), (expected, jpeg))
