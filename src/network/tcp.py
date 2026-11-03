"""A persistent TCP connection with independent reading and writing."""

import socket
import threading

from src.config import MAX_MESSAGE_BYTES
from src.protocol.messages import decode_message, encode_message


class TCPConnection:
    def __init__(self, sock, address):
        self.socket = sock
        self.address = address
        self.node_id = None
        self._send_lock = threading.Lock()

    def send(self, message_type, node_id, text="HELLO"):
        data = encode_message(message_type, node_id, text)
        # Prevent messages from different sending threads from interleaving.
        with self._send_lock:
            self.socket.sendall(data)

    def messages(self):
        # TCP is a byte stream: reads may split or combine application messages.
        with self.socket.makefile("rb") as reader:
            while True:
                line = reader.readline(MAX_MESSAGE_BYTES + 1)
                if not line:
                    return
                if len(line) > MAX_MESSAGE_BYTES or not line.endswith(b"\n"):
                    raise ValueError("Mensagem demasiado grande ou incompleta")
                yield decode_message(line)

    def close(self):
        # shutdown also wakes a reader blocked in readline.
        try:
            self.socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.socket.close()
