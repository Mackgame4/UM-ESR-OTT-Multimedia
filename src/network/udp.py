"""Nonblocking UDP datagrams, independent of video formats and overlay routing."""

import select
import socket


class UDPEndpoint:
    def __init__(self, host="0.0.0.0", port=0, receive_buffer=1024 * 1024):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, receive_buffer)
            self.socket.bind((host, port))
            self.socket.setblocking(False)
        except OSError:
            self.socket.close()
            raise
        self.address = self.socket.getsockname()
        self.port = self.address[1]

    def send(self, data, address):
        """Send one datagram; socket errors propagate without retries."""
        return self.socket.sendto(data, address)

    def receive(self, max_bytes=65535, timeout=0.02):
        """Return (data, sender), or None when no datagram is available."""
        ready, _, _ = select.select([self.socket], [], [], timeout)
        if ready:
            try:
                return self.socket.recvfrom(max_bytes)
            except BlockingIOError:
                pass
        return None

    def close(self):
        self.socket.close()
