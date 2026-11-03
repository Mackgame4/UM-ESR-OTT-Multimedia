"""Overlay node acting simultaneously as a TCP server and client."""

import logging
import socket
import threading

from src.config import CONNECT_TIMEOUT, DEFAULT_HOST, DEFAULT_PORT
from src.network.tcp import TCPConnection

LOGGER = logging.getLogger(__name__)


class OverlayNode:
    def __init__(self, node_id, host=DEFAULT_HOST, port=DEFAULT_PORT, on_message=None):
        if not isinstance(node_id, str) or not node_id.strip():
            raise ValueError("O identificador do nó não pode estar vazio")
        self.node_id = node_id
        self.host = host
        self.port = port
        self.on_message = on_message
        self._listener = None
        self._running = False
        self._connections = set()
        self._threads = set()
        self._lock = threading.RLock()

    def start(self):
        with self._lock:
            if self._running:
                raise RuntimeError("O nó já está em execução")
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listener.bind((self.host, self.port))
                listener.listen()
                listener.settimeout(0.5)
            except OSError:
                listener.close()
                raise
            self._listener = listener
            self.port = listener.getsockname()[1]
            self._running = True
            self._spawn(self._accept_connections, listener)
        LOGGER.info("[%s] A escutar em %s:%s (TCP)", self.node_id, self.host, self.port)

    def _spawn(self, target, *args):
        def run():
            try:
                target(*args)
            finally:
                with self._lock:
                    self._threads.discard(threading.current_thread())

        thread = threading.Thread(target=run, daemon=True)
        self._threads.add(thread)
        thread.start()

    def _accept_connections(self, listener):
        while self._running:
            try:
                sock, address = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            self._register(sock, address)

    def connect(self, host, port=DEFAULT_PORT):
        with self._lock:
            if not self._running:
                raise RuntimeError("Inicie o nó antes de ligar a um vizinho")
        sock = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT)
        sock.settimeout(None)
        self._register(sock, sock.getpeername())

    def _register(self, sock, address):
        connection = TCPConnection(sock, address)
        with self._lock:
            if not self._running:
                connection.close()
                return
            self._connections.add(connection)
            self._spawn(self._receive, connection)

    def _receive(self, connection):
        try:
            # Both endpoints send one HELLO on connection; no reply loop.
            connection.send("HELLO", self.node_id)
            for message in connection.messages():
                connection.node_id = message["node_id"]
                LOGGER.info("[%s] RX %s de %s: %s", self.node_id,
                            message["type"], message["node_id"], message["text"])
                if self.on_message is not None:
                    self.on_message(connection, message)
        except (OSError, ValueError) as exc:
            if self._running:
                LOGGER.warning("[%s] Ligação %s:%s terminada: %s",
                               self.node_id, *connection.address, exc)
        finally:
            connection.close()
            with self._lock:
                self._connections.discard(connection)
            LOGGER.info("[%s] Vizinho desligado: %s:%s", self.node_id, *connection.address)

    def peers(self):
        with self._lock:
            return [(peer.node_id, peer.address) for peer in self._connections]

    def broadcast(self, text="HELLO", message_type="HELLO"):
        with self._lock:
            connections = list(self._connections)
        sent = 0
        for connection in connections:
            try:
                connection.send(message_type, self.node_id, text)
                sent += 1
            except OSError:
                connection.close()
        return sent

    def stop(self):
        with self._lock:
            self._running = False
            if self._listener is not None:
                self._listener.close()
                self._listener = None
            connections = list(self._connections)
            threads = list(self._threads)
        for connection in connections:
            connection.close()
        for thread in threads:
            if thread is not threading.current_thread():
                thread.join(timeout=CONNECT_TIMEOUT)
