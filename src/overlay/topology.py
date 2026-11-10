"""Derive an on-demand overlay tree from the CORE underlay, without overlay config."""

from collections import deque
from pathlib import Path
import xml.etree.ElementTree as ET

from src.config import DEFAULT_PORT


def load_topology(path, server_id="Server1", port=DEFAULT_PORT):
    try:
        root = ET.parse(Path(path)).getroot()
    except ET.ParseError as exc:
        raise ValueError("Topologia CORE XML inválida") from exc
    devices = {device.get("id"): device for device in root.findall("devices/device")}
    names = {device.get("name"): node for node, device in devices.items()}
    if server_id not in names:
        raise ValueError("Servidor ausente da topologia: " + server_id)
    graph = {node: [] for node in devices}
    switches = {node.get("id") for node in root.findall("networks/network")}
    graph.update({node: [] for node in switches})
    addresses = {}
    for link in root.findall("links/link"):
        a, b = link.get("node1"), link.get("node2")
        if a not in graph or b not in graph:
            raise ValueError("Ligação com nó desconhecido")
        graph[a].append(b)
        graph[b].append(a)
        for side, node in ((1, a), (2, b)):
            iface = link.find("iface" + str(side))
            if node in devices and iface is not None and iface.get("ip4"):
                addresses.setdefault(node, iface.get("ip4"))
    source = names[server_id]
    paths = {source: [source]}
    pending = deque([source])
    while pending:
        current = pending.popleft()
        # Hosts cannot be transit nodes; routers and L2 switches can.
        if current != source and current in devices and devices[current].get("type") != "router":
            continue
        for neighbour in graph[current]:
            if neighbour not in paths:
                paths[neighbour] = paths[current] + [neighbour]
                pending.append(neighbour)
    nodes = {}
    for node, device in devices.items():
        if node not in paths or node not in addresses:
            continue
        path_devices = [item for item in paths[node] if item in devices]
        # Only the client's access router participates in the overlay. All
        # intermediate physical routers remain ordinary IP transit nodes.
        access = next((item for item in reversed(path_devices[1:-1])
                       if devices[item].get("type") == "router"), None)
        is_router = device.get("type") == "router"
        parent = (devices[access].get("name") if access and not is_router
                  else server_id if node != source else None)
        routers = [parent] if access and not is_router else []
        overlay_path = ([server_id, parent, device.get("name")] if routers else
                        [server_id, device.get("name")] if parent else [server_id])
        nodes[device.get("name")] = {
            "host": addresses[node], "port": port,
            "neighbours": [parent] if parent else [], "activate": routers,
            "path": overlay_path,
            "router": is_router,
        }
    if server_id not in nodes:
        raise ValueError("Servidor sem endereço IPv4")
    return {"nodes": nodes}
