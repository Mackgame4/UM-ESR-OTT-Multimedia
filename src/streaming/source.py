"""One paced MJPEG source for the entire multicast tree, adapted from Servidor.py."""

import logging
import threading
import time
import uuid
import math

from src.streaming.packet import packetize
from src.streaming.video import VideoStream


class VideoSource:
    def __init__(self, transport, filename, fps=20, loop=True):
        if not math.isfinite(fps) or not 0 < fps <= 120:
            raise ValueError("FPS inválido")
        self.transport = transport
        self.reader = VideoStream(filename)
        self.interval = 1 / fps
        self.loop = loop
        self.session = uuid.uuid4().bytes
        self.frame = 0
        self.sequence = 0
        self._stop = threading.Event()
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        deadline = time.monotonic()
        try:
            while not self._stop.is_set():
                plan = self.transport.snapshot()
                if not plan or not plan["children"]:
                    self._stop.wait(0.05)
                    deadline = time.monotonic()
                    continue
                if self._stop.wait(max(0, deadline - time.monotonic())):
                    break
                jpeg = self.reader.next_frame()
                if jpeg is None and self.loop:
                    self.reader.rewind()
                    jpeg = self.reader.next_frame()
                if jpeg is None:
                    logging.info("Vídeo: fim do ficheiro")
                    break
                self.frame += 1
                datagrams = packetize(jpeg, plan["controller_id"], self.session, plan["epoch"],
                                      self.frame, self.sequence, int(time.monotonic() * 90000))
                self.sequence = (self.sequence + len(datagrams)) & 65535
                self.transport.publish(datagrams, plan)
                deadline += self.interval
                # Never burst a backlog of old frames after a scheduling delay.
                if deadline < time.monotonic():
                    deadline = time.monotonic() + self.interval
        except (OSError, ValueError) as exc:
            logging.error("Fonte de vídeo terminada: %s", exc)

    def stop(self):
        self._stop.set()
        if self.thread:
            self.thread.join()
        self.reader.close()
