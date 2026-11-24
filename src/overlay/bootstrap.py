"""Centralised, on-demand registration and renewable membership leases."""

import logging
import socket
import socketserver
import threading
import time

from src.config import CONNECT_TIMEOUT
from src.network.tcp import TCPConnection
from src.routing.links import sample

LOGGER = logging.getLogger(__name__)
BOOTSTRAP_PORT = 5001


def request(address, node_id, kind="REGISTER", **fields):
    sock = socket.create_connection(address, timeout=CONNECT_TIMEOUT)
    connection = TCPConnection(sock, address)
    try:
        connection.send(kind, node_id, "", **fields)
        response = next(connection.messages(), None)
        if response is None:
            raise OSError("Bootstrapper desligado sem resposta")
        if response["type"] == "ERROR":
            raise ValueError(response["text"])
        expected = "NEIGHBOURS" if kind in ("REGISTER", "ROUTE_REQUEST", "ROUTE_WITHDRAW") else "ACK"
        if response["type"] != expected:
            raise ValueError("Resposta inesperada do bootstrapper")
        return response
    finally:
        connection.close()


class BootstrapController:
    def __init__(self, node_id, config, host="0.0.0.0", port=BOOTSTRAP_PORT,
                 lease_seconds=15, activate=None, routes=None, node=None, interval=2, probe=sample):
        if node_id not in config["nodes"] or lease_seconds <= 0:
            raise ValueError("Controlador ou lease inválido")
        self.node_id = node_id
        self.config = config
        self.lease_seconds = lease_seconds
        self.activate = activate
        self.routes = routes
        self.node = node
        self.interval = interval
        self.probe = probe
        self._stop = threading.Event()
        self.worker = None
        self._members = {}
        self._registered = set()
        self._requested_routers = set()
        self._lock = threading.RLock()
        controller = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                self.request.settimeout(CONNECT_TIMEOUT)
                connection = TCPConnection(self.request, self.client_address)
                try:
                    message = next(connection.messages(), None)
                    if message is None:
                        return
                    controller.handle(connection, message)
                except (OSError, ValueError) as exc:
                    try:
                        connection.send("ERROR", controller.node_id, str(exc))
                    except OSError:
                        pass
                finally:
                    connection.close()

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True
            block_on_close = False

        self.server = Server((host, port), Handler)
        self.port = self.server.server_address[1]
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        if self.routes:
            self.routes.register(self.node_id, {})
            self._apply_root()
            self.worker = threading.Thread(target=self._maintain, daemon=True)
            self.worker.start()
        LOGGER.info("[%s] Bootstrapper TCP em %s:%s", self.node_id, *self.server.server_address)

    def members(self):
        if self.routes:
            return sorted(self.routes.active() - {self.node_id})
        with self._lock:
            now = time.monotonic()
            self._members = {name: seen for name, seen in self._members.items()
                             if now - seen < self.lease_seconds}
            return sorted(self._members)

    def member_statuses(self):
        """Distinguish registered control agents from active data relays."""
        if self.routes:
            self.routes.recompute()
            active = self.routes.active()
            used = set(self.routes.parents) | set(self.routes.parents.values())
            return [(self.node_id, "CONTROLLER / STREAM SERVER")] + [
                (name, ("OVERLAY ROUTER" if name in used else "CONTROL ONLY") if entry.get("router")
                 and name in active else "OVERLAY CLIENT" if name in active else "OFFLINE")
                for name, entry in sorted(self.config["nodes"].items())
                if name != self.node_id and (entry.get("router") or name in active)]
        with self._lock:
            active = set(self.members())
            rows = [(self.node_id, "CONTROLLER")]
            for name, entry in sorted(self.config["nodes"].items()):
                if name == self.node_id:
                    continue
                if entry.get("router"):
                    if name in active:
                        status = "OVERLAY ROUTER"
                    elif name in self._registered:
                        status = "OVERLAY INACTIVE"
                    elif name in self._requested_routers:
                        status = "OVERLAY NOT REGISTERED"
                    else:
                        status = "UNDERLAY ONLY"
                    rows.append((name, status))
                elif name in active:
                    rows.append((name, "OVERLAY CLIENT"))
            return rows

    def handle(self, connection, message):
        name = message["node_id"]
        entry = self.config["nodes"].get(name)
        if entry is None or name == self.node_id:
            raise ValueError("Identificador não autorizado: " + name)
        if self.routes:
            self._handle_live(connection, message)
            return
        if message["type"] == "LEAVE":
            with self._lock:
                self._members.pop(name, None)
            LOGGER.info("[%s] LEAVE %s", self.node_id, name)
            connection.send("ACK", self.node_id, "")
            return
        if message["type"] != "REGISTER":
            raise ValueError("Esperado REGISTER ou LEAVE")
        targets = entry.get("activate", [])
        if targets:
            if self.activate is None:
                raise ValueError("Ativação remota requer --core-session")
            for target in targets:
                self.activate(target)
                with self._lock:
                    self._requested_routers.add(target)
        with self._lock:
            first = name not in self._members
            self._members[name] = time.monotonic()
            self._registered.add(name)
        if first:
            LOGGER.info("[%s] REGISTER %s; vizinhos: %s", self.node_id, name, entry["neighbours"])
            if "path" in entry:
                LOGGER.info("[%s] Caminho overlay: %s", self.node_id, " -> ".join(entry["path"]))
        # Lists are directional: only the named node initiates each full-duplex link.
        neighbours = [dict(node_id=peer, host=self.config["nodes"][peer]["host"],
                           port=self.config["nodes"][peer]["port"])
                      for peer in entry["neighbours"]]
        connection.send("NEIGHBOURS", self.node_id, "", neighbours=neighbours,
                        routing=routing_for(self.config, name, self.node_id))

    def stop(self):
        self._stop.set()
        if self.worker:
            self.worker.join()
        if self.thread:
            self.server.shutdown()
            self.thread.join()
        self.server.server_close()

    def _apply_root(self):
        if self.node:
            self.node.configure_routing(**self.routes.routing(self.node_id))

    def _maintain(self):
        while not self._stop.is_set():
            if self.activate:
                for name, entry in self.config["nodes"].items():
                    if entry.get("router"):
                        try:
                            self.activate(name)
                        except (OSError, ValueError) as exc:
                            LOGGER.warning("Agente %s indisponível: %s", name, exc)
            try:
                reports = self.probe(self.routes.targets(self.node_id))
                self.routes.register(self.node_id, reports)
                self._apply_root()
            except OSError as exc:
                LOGGER.error("Monitorização indisponível: %s", exc)
            if self._stop.wait(self.interval):
                break

    def _handle_live(self, connection, message):
        name, kind = message["node_id"], message["type"]
        if kind == "REGISTER":
            first = name not in self.routes.active()
            self.routes.register(name, message.get("links", {}))
            if first:
                LOGGER.info("[%s] REGISTER %s", self.node_id, name)
        elif kind in ("ROUTE_REQUEST", "ROUTE_WITHDRAW"):
            if message.get("stream_id", "default") != "default":
                raise ValueError("Stream desconhecida")
            self.routes.subscribe(name, kind == "ROUTE_REQUEST")
        elif kind == "LEAVE":
            self.routes.leave(name)
            self._apply_root()
            LOGGER.info("[%s] LEAVE %s", self.node_id, name)
            connection.send("ACK", self.node_id, "")
            return
        else:
            raise ValueError("Mensagem de controlo inesperada")
        self._apply_root()
        connection.send("NEIGHBOURS", self.node_id, "", **self.routes.snapshot(name))


class BootstrapClient:
    def __init__(self, node, address, interval=2, probe=sample):
        self.node = node
        self.address = address
        self.interval = interval
        self._stop = threading.Event()
        self.thread = None
        self.probe = probe
        self.targets = []
        self.dynamic = False
        self.subscribed = False
        self._refresh_lock = threading.Lock()

    def start(self):
        # Fail clearly on an unknown ID or unavailable controller at startup.
        self.refresh()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def refresh(self):
        with self._refresh_lock:
            response = request(self.address, self.node.node_id, links=self.probe(self.targets))
            first = not self.dynamic and response.get("dynamic", False)
            self.dynamic = response.get("dynamic", False)
            self.targets = response.get("probe_targets", [])
            if first and response["routing"]["role"] == "client":
                self.subscribed = True
            if self.dynamic and self.subscribed:
                response = request(self.address, self.node.node_id, "ROUTE_REQUEST", stream_id="default")
            self._install(response)

    def _install(self, response):
        if "routing" in response:
            self.node.configure_routing(**response["routing"])
        for peer in response["neighbours"]:
            try:
                self.node.ensure_neighbour(peer["node_id"], peer["host"], peer["port"])
            except OSError as exc:
                LOGGER.warning("[%s] Vizinho %s indisponível; nova tentativa: %s",
                               self.node.node_id, peer["node_id"], exc)

    def subscribe(self, enabled=True):
        if not self.dynamic:
            raise ValueError("Controlador sem rotas dinâmicas")
        with self._refresh_lock:
            response = request(self.address, self.node.node_id,
                               "ROUTE_REQUEST" if enabled else "ROUTE_WITHDRAW", stream_id="default")
            self.subscribed = enabled
            self._install(response)

    def _run(self):
        while not self._stop.wait(self.interval):
            try:
                self.refresh()
            except (OSError, ValueError) as exc:
                LOGGER.warning("[%s] Falha de renovação: %s", self.node.node_id, exc)

    def stop(self):
        self._stop.set()
        if self.thread:
            self.thread.join()
        try:
            request(self.address, self.node.node_id, "LEAVE")
        except (OSError, ValueError):
            pass  # Lease expiry covers abrupt departures and controller failure.


def routing_for(config, name, server_id):
    entry = config["nodes"][name]
    return dict(role="server" if name == server_id else "router" if entry.get("router") else "client",
                server_id=server_id,
                parent_id=next(iter(entry["neighbours"]), None),
                children=[peer for peer, data in config["nodes"].items()
                          if name in data["neighbours"]])
