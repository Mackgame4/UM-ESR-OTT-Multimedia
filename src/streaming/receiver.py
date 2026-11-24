"""Bounded live reassembly: a newer frame always overtakes an incomplete one."""

import time


class LiveReceiver:
    def __init__(self, loss, max_age=0.1, clock=time.monotonic):
        self.loss = loss
        self.max_age = max_age
        self.clock = clock
        self.session = None
        self.retired_sessions = []
        self.highest = None
        self.parts = {}
        self.count = 0
        self.retired = True
        self.started = 0

    def discard(self, reason):
        if self.highest is not None and not self.retired:
            self.loss(reason, self.highest, self.highest, len(self.parts), self.count)
            self.parts.clear()
            self.retired = True

    def expire(self):
        if not self.retired and self.clock() - self.started >= self.max_age:
            self.discard("fragment_deadline")

    def push(self, packet):
        session = (packet.controller, packet.session)
        if session != self.session:
            if session in self.retired_sessions:
                return None
            self.discard("source_restart")
            if self.session is not None:
                self.retired_sessions = (self.retired_sessions + [self.session])[-8:]
            self.session, self.highest, self.retired = session, None, True
            self.parts.clear()
        if self.highest is not None and packet.frame < self.highest:
            return None
        if self.highest is None or packet.frame > self.highest:
            self.discard("newer_frame")
            if self.highest is not None and packet.frame > self.highest + 1:
                self.loss("sequence_gap", self.highest + 1, packet.frame - 1, 0, 0)
            self.highest, self.count = packet.frame, packet.fragments
            self.parts, self.retired, self.started = {}, False, self.clock()
        self.expire()
        if self.retired:
            return None
        if packet.fragments != self.count:
            self.discard("inconsistent_fragments")
            return None
        self.parts.setdefault(packet.fragment, packet.payload)
        if len(self.parts) == self.count:
            jpeg = b"".join(self.parts[index] for index in range(self.count))
            self.parts.clear()
            self.retired = True
            return packet.frame, jpeg
        return None
