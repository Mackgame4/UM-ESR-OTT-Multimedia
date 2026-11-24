"""End-to-end tests over the installed multicast tree, independent of ICMP probes."""

import logging
import threading
import time
import uuid

LOGGER = logging.getLogger(__name__)


def version(routing):
    return routing.get("controller_id"), routing.get("epoch")


class RouteDiagnostics:
    def __init__(self, node):
        self.node = node
        self._lock = threading.Lock()
        self._pending = {}

    def route_changed(self, routing):
        with self._lock:
            for test in self._pending.values():
                if version(routing) != test["version"]:
                    test["stale"] = True
                    test["done"].set()

    def cancel(self):
        with self._lock:
            for test in self._pending.values():
                test["cancelled"] = True
                test["done"].set()

    def ping(self, expected_paths, timeout=3, routing=None):
        routing = routing or self.node._routing
        if (not routing or routing["role"] != "server" or routing.get("epoch") is None
                or not routing.get("controller_id")):
            raise ValueError("/ping requer um servidor com rotas dinâmicas")
        if timeout <= 0:
            raise ValueError("O timeout deve ser positivo")
        paths = {name: list(path) for name, path in expected_paths.items()}
        expected = {name: path for name, path in paths.items() if path}
        test_id = uuid.uuid4().hex
        started = time.monotonic()
        test = dict(version=version(routing), started=started, deadline=started + timeout,
                    expected=expected, results={}, done=threading.Event(), stale=False, cancelled=False)
        with self._lock:
            self._pending[test_id] = test
        try:
            current = self.node._routing
            if current is None or version(current) != test["version"]:
                test["stale"] = True
            elif expected:
                packet = dict(type="ROUTE_PING", node_id=self.node.node_id, text="",
                              test_id=test_id, ttl=64, path=[self.node.node_id],
                              epoch=routing["epoch"], controller_id=routing["controller_id"])
                LOGGER.info("[%s] ROUTE_PING %s v%s: %s", self.node.node_id, test_id,
                            routing["epoch"], self.node.node_id)
                self.node._forward(packet, routing["children"])
                test["done"].wait(max(0, test["deadline"] - time.monotonic()))
            with self._lock:
                current = self.node._routing
                stale = test["stale"] or current is None or version(current) != test["version"]
                rows = []
                for name, expected_path in sorted(paths.items()):
                    result = test["results"].get(name, {})
                    status = ("CANCELLED" if test["cancelled"] else "STALE" if stale else
                              "UNREACHABLE" if not expected_path else result.get("status", "TIMEOUT"))
                    path = result.get("path", [])
                    rows.append(dict(client=name, status=status, test_id=test_id,
                                     epoch=routing["epoch"], expected_path=expected_path, path=path,
                                     return_path=result.get("return_path", []),
                                     hops=len(path) - 1 if path else None, rtt_ms=result.get("rtt_ms")))
                return rows
        finally:
            with self._lock:
                self._pending.pop(test_id, None)

    def receive(self, connection, message):
        routing = self.node._routing
        if routing is None or version(message) != version(routing) or message["ttl"] <= 0:
            return
        if message["type"] == "ROUTE_PING":
            self._ping_received(connection, message, routing)
        else:
            self._pong_received(connection, message, routing)

    def _ping_received(self, connection, message, routing):
        path = message["path"]
        if (connection.node_id != routing["parent_id"] or path[-1] != connection.node_id
                or path[0] != routing["server_id"] or message["node_id"] != routing["server_id"]
                or self.node.node_id in path):
            raise ValueError("Percurso ROUTE_PING inválido")
        if not self.node._remember("route-ping:" + message["test_id"]):
            return
        path = path + [self.node.node_id]
        LOGGER.info("[%s] ROUTE_PING %s v%s: %s", self.node.node_id, message["test_id"],
                    message["epoch"], " -> ".join(path))
        if routing["role"] == "client":
            reply = dict(type="ROUTE_PONG", node_id=self.node.node_id, text="",
                         test_id=message["test_id"], epoch=message["epoch"],
                         controller_id=message["controller_id"], ttl=64, path=path,
                         return_path=[self.node.node_id])
            LOGGER.info("[%s] ROUTE_PONG %s: %s", self.node.node_id, message["test_id"], self.node.node_id)
            self.node._forward(reply, {routing["parent_id"]})
        elif routing["role"] == "router":
            self.node._forward(dict(message, path=path, ttl=message["ttl"] - 1), routing["children"])

    def _pong_received(self, connection, message, routing):
        path, back = message["path"], message["return_path"]
        reversed_path = list(reversed(path))
        if (connection.node_id not in routing["children"] or back[-1] != connection.node_id
                or path[0] != routing["server_id"] or path[-1] != message["node_id"]
                or len(back) >= len(path) or reversed_path[:len(back)] != back
                or reversed_path[len(back)] != self.node.node_id):
            raise ValueError("Percurso ROUTE_PONG inválido")
        if not self.node._remember("route-pong:" + message["test_id"] + ":" + message["node_id"]):
            return
        back = back + [self.node.node_id]
        LOGGER.info("[%s] ROUTE_PONG %s v%s: %s", self.node.node_id, message["test_id"],
                    message["epoch"], " -> ".join(back))
        if routing["role"] == "server":
            with self._lock:
                test = self._pending.get(message["test_id"])
                client = message["node_id"]
                now = time.monotonic()
                if (test is None or client not in test["expected"] or version(message) != test["version"]
                        or now > test["deadline"]):
                    return
                if client not in test["results"]:
                    test["results"][client] = dict(
                        status="OK" if path == test["expected"][client] else "PATH_MISMATCH",
                        path=path, return_path=back, rtt_ms=(now - test["started"]) * 1000)
                if len(test["results"]) == len(test["expected"]):
                    test["done"].set()
        elif routing["role"] == "router":
            self.node._forward(dict(message, return_path=back, ttl=message["ttl"] - 1), {routing["parent_id"]})
