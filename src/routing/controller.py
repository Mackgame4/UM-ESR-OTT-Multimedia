"""Strategy 2: server-owned live graph and demand-driven multicast tree."""

from collections import deque
import threading
import time
import logging
import uuid


class LiveRoutes:
    def __init__(self, config, server_id, lease_seconds=15, clock=time.monotonic):
        self.config = config
        self.server_id = server_id
        self.lease_seconds = lease_seconds
        self.clock = clock
        self._lock = threading.RLock()
        self._seen = {}
        self._reports = {}
        self._media_ports = {}
        self._subscribers = set()
        self.parents = {}
        self.paths = {}
        self.version = 0
        self.controller_id = uuid.uuid4().hex
        self._signature = None

    def targets(self, name):
        result = []
        for link in self.config["links"]:
            for local, remote in ((link["a"], link["b"]), (link["b"], link["a"])):
                if local["node"] == name:
                    result.append(dict(id=link["id"], interface=local["interface"], host=remote["host"]))
        return result

    def register(self, name, reports, udp_port=None):
        allowed = {target["id"] for target in self.targets(name)}
        if not isinstance(reports, dict) or any(key not in allowed or type(value) is not bool
                                                for key, value in reports.items()):
            raise ValueError("Relatório de ligações inválido")
        if udp_port is not None and (type(udp_port) is not int or not 1 <= udp_port <= 65535):
            raise ValueError("Porta UDP inválida")
        with self._lock:
            self._seen[name] = self.clock()
            self._reports[name] = dict(reports)
            self._media_ports[name] = udp_port
            self.recompute()

    def subscribe(self, name, enabled=True):
        with self._lock:
            if name not in self.active() or self.config["nodes"][name].get("router") or name == self.server_id:
                raise ValueError("Só clientes registados podem pedir uma rota")
            if enabled:
                self._subscribers.add(name)
            else:
                self._subscribers.discard(name)
            self.recompute()

    def leave(self, name):
        with self._lock:
            self._seen.pop(name, None)
            self._reports.pop(name, None)
            self._media_ports.pop(name, None)
            self._subscribers.discard(name)
            self.recompute()

    def active(self):
        with self._lock:
            now = self.clock()
            return {name for name, seen in self._seen.items() if now - seen < self.lease_seconds}

    def recompute(self):
        with self._lock:
            active = self.active()
            self._subscribers.intersection_update(active)
            graph = {name: set() for name in active}
            for link in self.config["links"]:
                a, b = link["a"]["node"], link["b"]["node"]
                if (a in active and b in active and self._reports.get(a, {}).get(link["id"], False)
                        and self._reports.get(b, {}).get(link["id"], False)):
                    graph[a].add(b)
                    graph[b].add(a)
            paths = {self.server_id: [self.server_id]} if self.server_id in graph else {}
            pending = deque(paths)
            while pending:
                node = pending.popleft()
                if node != self.server_id and not self.config["nodes"][node].get("router"):
                    continue
                for neighbour in sorted(graph[node]):
                    if neighbour not in paths:
                        paths[neighbour] = paths[node] + [neighbour]
                        pending.append(neighbour)
            parents = {}
            selected = {name: paths.get(name) for name in sorted(self._subscribers)}
            for path in selected.values():
                if path:
                    for parent, child in zip(path, path[1:]):
                        parents[child] = parent
            signature = (tuple(sorted(parents.items())),
                         tuple((name, tuple(path) if path else None) for name, path in selected.items()),
                         tuple((name, self._media_ports.get(name)) for name in sorted(set(parents) | set(parents.values()))))
            if signature != self._signature:
                self.version += 1
                self._signature = signature
                logging.info("[%s] Rotas v%s: %s", self.server_id, self.version,
                             "; ".join(name + ": " + (" -> ".join(path) if path else "UNREACHABLE")
                                       for name, path in selected.items()) or "sem clientes")
            self.parents, self.paths = parents, selected

    def routing(self, name):
        with self._lock:
            self.recompute()
            return dict(role="server" if name == self.server_id else
                        "router" if self.config["nodes"][name].get("router") else "client",
                        server_id=self.server_id, parent_id=self.parents.get(name),
                        children=sorted(child for child, parent in self.parents.items() if parent == name),
                        epoch=self.version, controller_id=self.controller_id)

    def neighbour(self, name):
        with self._lock:
            parent = self.parents.get(name)
            if parent is None:
                return []
            for link in self.config["links"]:
                for local, remote in ((link["a"], link["b"]), (link["b"], link["a"])):
                    if local["node"] == name and remote["node"] == parent:
                        if not (self._reports.get(name, {}).get(link["id"], False) and
                                self._reports.get(parent, {}).get(link["id"], False)):
                            continue
                        return [dict(node_id=parent, host=remote["host"], port=self.config["nodes"][parent]["port"])]
            raise ValueError("Rota sem ligação candidata")

    def snapshot(self, name):
        with self._lock:
            routing = self.routing(name)
            return dict(routing=routing, neighbours=self.neighbour(name),
                        probe_targets=self.targets(name), dynamic=True, media=self._media_plan(name))

    def _media_plan(self, name):
        def endpoint(local, remote):
            port = self._media_ports.get(remote)
            if port is None:
                return None
            for link in self.config["links"]:
                for a, b in ((link["a"], link["b"]), (link["b"], link["a"])):
                    if (a["node"] == local and b["node"] == remote and
                            self._reports.get(local, {}).get(link["id"], False) and
                            self._reports.get(remote, {}).get(link["id"], False)):
                        return dict(host=b["host"], port=port)
            return None

        parent = endpoint(name, self.parents[name]) if name in self.parents else None
        children = [endpoint(name, child) for child, p in self.parents.items() if p == name]
        return dict(epoch=self.version, controller_id=self.controller_id, parent=parent,
                    children=[child for child in children if child is not None])

    def table(self):
        with self._lock:
            self.recompute()
            return [dict(client=name, stream="default", state="ACTIVE" if path else "UNREACHABLE",
                         path=path or [], hops=len(path) - 1 if path else None, epoch=self.version)
                    for name, path in self.paths.items()]

    def diagnostic_plan(self):
        with self._lock:
            routing = self.routing(self.server_id)
            return {name: list(path) if path else [] for name, path in self.paths.items()}, routing
