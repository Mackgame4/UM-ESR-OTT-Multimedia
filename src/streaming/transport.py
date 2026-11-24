"""UDP application multicast: one datagram per child branch, no retransmissions."""

from collections import OrderedDict
import logging
from pathlib import Path
import re
import select
import socket
import threading

from src.streaming.packet import MAX_DATAGRAM, VideoPacket, controller_token
from src.streaming.receiver import LiveReceiver


class VideoTransport:
    def __init__(self, node, host="0.0.0.0", port=6000, loss_log=None, on_frame=None):
        self.node = node
        self.host, self.port = host, port
        self.on_frame = on_frame
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._plan = None
        self._socket = None
        self.thread = None
        self._seen = OrderedDict()
        self.stats = dict(sent=0, received=0, forwarded=0, frames=0, lost_frames=0, rejected=0)
        safe_name = re.sub(r"[^A-Za-z0-9_-]", "_", node.node_id)
        self.log_path = Path(loss_log or (Path("logs") / (safe_name + "-video.log")))
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log = logging.Logger("video." + node.node_id)
        self.handler = logging.FileHandler(self.log_path, encoding="utf-8")
        self.handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        self.log.addHandler(self.handler)
        self.receiver = LiveReceiver(self._loss)

    def _loss(self, reason, first, last, received, total):
        self.stats["lost_frames"] += last - first + 1
        self.log.warning("FRAME_LOSS reason=%s frames=%s..%s fragments=%s/%s",
                         reason, first, last, received, total)

    def start(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
            sock.bind((self.host, self.port))
            sock.setblocking(False)
        except OSError:
            sock.close()
            raise
        self._socket = sock
        self.port = sock.getsockname()[1]
        self.thread = threading.Thread(target=self._receive, daemon=True)
        self.thread.start()
        logging.info("[%s] Vídeo UDP em %s:%s; log: %s", self.node.node_id, self.host, self.port, self.log_path)

    def configure(self, plan):
        routing = self.node._routing
        if not routing or (routing.get("epoch"), routing.get("controller_id")) != (plan["epoch"], plan["controller_id"]):
            return
        with self._lock:
            previous = self._plan
            if previous and (previous["epoch"], previous["controller_id"]) != (plan["epoch"], plan["controller_id"]):
                self.receiver.discard("route_change")
                self._seen.clear()
            self._plan = dict(plan, role=routing["role"])

    def snapshot(self):
        with self._lock:
            plan = self._plan
            routing = self.node._routing
            if not plan or not routing or (plan["epoch"], plan["controller_id"]) != (routing.get("epoch"), routing.get("controller_id")):
                return None
            return dict(plan)

    def _send(self, data, targets):
        count = 0
        for target in targets:
            try:
                self._socket.sendto(data, (target["host"], target["port"]))
                count += 1
            except OSError as exc:
                if not self._stop.is_set():
                    self.log.warning("UDP_SEND_DROP target=%s:%s error=%s", target["host"], target["port"], exc)
        with self._lock:
            self.stats["sent"] += count
        return count

    def publish(self, datagrams, plan):
        current = self.snapshot()
        if (not current or current["role"] != "server" or
                (current["epoch"], current["controller_id"]) != (plan["epoch"], plan["controller_id"])):
            return 0
        return sum(self._send(data, current["children"]) for data in datagrams)

    def _receive(self):
        while not self._stop.is_set():
            try:
                ready, _, _ = select.select([self._socket], [], [], 0.02)
                if ready:
                    data, address = self._socket.recvfrom(MAX_DATAGRAM + 1)
                    self._handle(data, address)
                with self._lock:
                    self.receiver.expire()
            except (OSError, ValueError) as exc:
                if not self._stop.is_set():
                    self.log.warning("UDP_RECEIVE_DROP error=%s", exc)

    def _handle(self, data, address):
        packet = VideoPacket.decode(data)
        plan = self.snapshot()
        parent = plan.get("parent") if plan else None
        if (not parent or address != (parent["host"], parent["port"]) or
                packet.controller != controller_token(plan["controller_id"]) or packet.epoch != plan["epoch"]):
            with self._lock:
                self.stats["rejected"] += 1
            return
        key = (packet.session, packet.epoch, packet.frame, packet.fragment)
        with self._lock:
            if key in self._seen:
                return
            self._seen[key] = None
            if len(self._seen) > 8192:
                self._seen.popitem(last=False)
            self.stats["received"] += 1
            if plan["role"] == "client":
                result = self.receiver.push(packet)
                if result:
                    self.stats["frames"] += 1
                    if self.stats["frames"] == 1:
                        self.log.info("VIDEO_START frame=%s bytes=%s", result[0], len(result[1]))
            else:
                result = None
        if plan["role"] == "router" and packet.ttl > 1:
            count = self._send(packet.forwarded().encode(), plan["children"])
            with self._lock:
                self.stats["forwarded"] += count
        elif result and self.on_frame:
            try:
                self.on_frame(*result)
            except Exception as exc:
                self.log.warning("FRAME_CALLBACK_DROP frame=%s error=%s", result[0], exc)

    def stop(self):
        self._stop.set()
        if self.thread:
            self.thread.join()
        with self._lock:
            self.receiver.discard("shutdown")
        if self._socket:
            self._socket.close()
        self.handler.close()
        self.log.removeHandler(self.handler)
