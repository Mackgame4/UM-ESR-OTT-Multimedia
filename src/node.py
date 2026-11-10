"""Overlay node acting simultaneously as a TCP server and client."""

import logging
import socket
import threading
import uuid
from collections import OrderedDict

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
        self._routing = None
        self._seen = OrderedDict()

    def configure_routing(self, role, server_id, parent_id, children):
        if role not in ("server", "router", "client"):
            raise ValueError("Papel de encaminhamento inválido")
        with self._lock:
            self._routing = dict(role=role, server_id=server_id,
                                 parent_id=parent_id, children=set(children))

    def _remember(self, message_id):
        with self._lock:
            if message_id in self._seen:
                return False
            self._seen[message_id] = None
            if len(self._seen) > 4096:
                self._seen.popitem(last=False)
            return True

    def _forward(self, message, peer_ids):
        with self._lock:
            peers = [peer for peer in self._connections if peer.node_id in peer_ids]
        count = 0
        fields = {key: message[key] for key in ("delivery", "message_id", "ttl")}
        for peer in peers:
            try:
                peer.send("MESSAGE", message["node_id"], message["text"], **fields)
                count += 1
            except OSError:
                peer.close()
        return count

    def _route_received(self, connection, message):
        routing = self._routing
        mode = message.get("delivery")
        if mode == "MULTICAST":
            if (connection.node_id != routing["parent_id"] or
                    message["node_id"] != routing["server_id"]):
                raise ValueError("Multicast deve chegar do servidor através do pai")
        elif mode == "UPSTREAM":
            if connection.node_id not in routing["children"]:
                raise ValueError("Mensagem upstream deve chegar de um filho")
        else:
            raise ValueError("MESSAGE sem direção de encaminhamento")
        if not self._remember(message["message_id"]):
            return
        destination = ((mode == "MULTICAST" and routing["role"] == "client") or
                       (mode == "UPSTREAM" and routing["role"] == "server"))
        if destination:
            LOGGER.info("[%s] RX MESSAGE de %s: %s", self.node_id, message["node_id"], message["text"])
            if self.on_message:
                self.on_message(connection, message)
        elif routing["role"] == "router" and message["ttl"] > 0:
            forwarded = dict(message, ttl=message["ttl"] - 1)
            targets = routing["children"] if mode == "MULTICAST" else {routing["parent_id"]}
            count = self._forward(forwarded, targets)
            LOGGER.info("[%s] FORWARD %s de %s para %s ligação(ões): %s",
                        self.node_id, mode, message["node_id"], count, message["text"])

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

    def ensure_neighbour(self, node_id, host, port):
        """The bootstrap worker is the sole initiator of configured links."""
        with self._lock:
            if any(p.node_id == node_id or p.address == (host, port)
                   for p in self._connections):
                return
        self.connect(host, port)

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
                # node_id on a forwarded MESSAGE is its original source, not
                # the neighbour owning this TCP connection.
                if message["type"] == "HELLO" or connection.node_id is None:
                    if connection.node_id is not None and connection.node_id != message["node_id"]:
                        raise ValueError("Vizinho alterou a identidade")
                    connection.node_id = message["node_id"]
                if message["type"] == "MESSAGE" and self._routing is not None:
                    self._route_received(connection, message)
                    continue
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
        routing = self._routing
        if message_type == "MESSAGE" and routing is not None:
            if routing["role"] == "router":
                raise ValueError("Routers apenas reencaminham mensagens")
            message = dict(node_id=self.node_id, text=text, message_id=uuid.uuid4().hex,
                           ttl=64, delivery="MULTICAST" if routing["role"] == "server" else "UPSTREAM")
            self._remember(message["message_id"])
            targets = routing["children"] if routing["role"] == "server" else {routing["parent_id"]}
            return self._forward(message, targets)
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
