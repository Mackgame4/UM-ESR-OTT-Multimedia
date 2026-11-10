"""Isolated Linux CORE namespace smoke test using every link in topology.xml.

Run as root on the CORE host: python3 tests/core_smoke.py
Requires vnoded, vcmd and ip. Uses static underlay routes instead of OSPF.
Never attaches to or modifies an existing CORE session.
"""

import collections
import ipaddress
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def run(*args):
    return subprocess.run(list(map(str, args)), check=True, capture_output=True, text=True)


def wait_for(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    raise RuntimeError("CORE test timed out")


def main():
    if sys.platform != "linux" or os.geteuid() != 0:
        raise SystemExit("Run as root on a Linux CORE host")
    for tool in ("ip", "vnoded", "vcmd"):
        if not shutil.which(tool):
            raise SystemExit("Missing: " + tool)
    xml = ET.parse(ROOT / "topology/topology.xml").getroot()
    devices = {d.get("id"): d.get("name") for d in xml.findall("devices/device")}
    switches = {n.get("id") for n in xml.findall("networks/network")}
    session = Path(tempfile.mkdtemp(prefix="ott-core-"))
    prefix = "ot" + session.name[-4:]
    bridges, interfaces, processes, logs, pids = [], [], [], [], []
    graph = collections.defaultdict(list)
    ports = {}
    segments = collections.defaultdict(list)

    def cmd(node, *args):
        return run("vcmd", "-c", session / devices[node], "--", *args)

    def app(name, *args):
        log = open(session / (name + "-app.log"), "w+")
        logs.append(log)
        process = subprocess.Popen(
            ["vcmd", "-c", str(session / name), "--", sys.executable,
             "-m", "src.main", *args], cwd=ROOT, stdin=subprocess.PIPE,
            stdout=log, stderr=subprocess.STDOUT, text=True)
        processes.append(process)
        return process, log

    def contents(log):
        return Path(log.name).read_text()

    try:
        for node, name in devices.items():
            pidfile = session / (name + ".pid")
            run("vnoded", "-c", session / name, "-p", pidfile,
                "-l", session / (name + ".log"), "-C", ROOT)
            wait_for(lambda: (session / name).is_socket() and pidfile.exists())
            pids.append(int(pidfile.read_text()))
            cmd(node, "ip", "link", "set", "lo", "up")
        for switch in switches:
            bridge = prefix + "s" + switch
            run("ip", "link", "add", bridge, "type", "bridge")
            bridges.append(bridge)
            run("ip", "link", "set", bridge, "up")
        for index, link in enumerate(xml.findall("links/link")):
            a, b = link.get("node1"), link.get("node2")
            graph[a].append(b)
            graph[b].append(a)
            switch = a if a in switches else b if b in switches else None
            bridge = prefix + "s" + switch if switch else prefix + "p" + str(index)
            if not switch:
                run("ip", "link", "add", bridge, "type", "bridge")
                bridges.append(bridge)
                run("ip", "link", "set", bridge, "up")
            for side, node, other in ((1, a, b), (2, b, a)):
                if node not in devices:
                    continue
                iface = link.find("iface" + str(side))
                host_end = prefix + "h" + str(index) + str(side)
                peer_end = prefix + "n" + str(index) + str(side)
                run("ip", "link", "add", host_end, "type", "veth", "peer", "name", peer_end)
                interfaces.append(host_end)
                pid = (session / (devices[node] + ".pid")).read_text().strip()
                run("ip", "link", "set", peer_end, "netns", pid)
                run("ip", "link", "set", host_end, "master", bridge)
                run("ip", "link", "set", host_end, "up")
                cmd(node, "ip", "link", "set", peer_end, "name", iface.get("name"))
                cmd(node, "ip", "addr", "add", iface.get("ip4") + "/" + iface.get("ip4_mask"),
                    "dev", iface.get("name"))
                cmd(node, "ip", "link", "set", iface.get("name"), "up")
                cmd(node, "sysctl", "-qw", "net.ipv4.ip_forward=1")
                ports[node, other] = iface.get("ip4")
                subnet = str(ipaddress.ip_network(iface.get("ip4") + "/" + iface.get("ip4_mask"), strict=False))
                segments[subnet].append(node)
        # Shortest paths through the exact XML links; switches count as L2 hops.
        for source in devices:
            paths = {source: [source]}
            todo = collections.deque([source])
            while todo:
                current = todo.popleft()
                for neighbour in graph[current]:
                    if neighbour not in paths:
                        paths[neighbour] = paths[current] + [neighbour]
                        todo.append(neighbour)
            for subnet, owners in segments.items():
                if source in owners:
                    continue
                path = min((paths[owner] for owner in owners), key=len)
                next_device = path[1] if path[1] in devices else path[2]
                back = source if path[1] in devices else path[1]
                cmd(source, "ip", "route", "add", subnet, "via", ports[next_device, back])
        c1_id = next(n for n, name in devices.items() if name == "C1")
        cmd(c1_id, "ping", "-c", "1", "-W", "3", "10.0.4.20")
        server, server_log = app("Server1", "--id", "Server1", "--controller",
                                 "--core-session", str(session))
        wait_for(lambda: "Bootstrapper TCP" in contents(server_log))
        assert not (session / "R3-ott.log").exists(), "R3 started before C1"
        client, client_log = app("C1", "10.0.4.20", "--id", "C1")
        wait_for(lambda: "RX HELLO de R3" in contents(client_log))
        wait_for(lambda: "RX HELLO de R3" in contents(server_log))
        second, second_log = app("C2", "10.0.4.20", "--id", "C2")
        wait_for(lambda: "RX HELLO de R3" in contents(second_log))
        for router in ("R9", "R7", "R4"):
            assert not (session / (router + "-ott.log")).exists(), "Unneeded router activated"
        server.stdin.write("CORE multicast test\n")
        server.stdin.flush()
        for log in (client_log, second_log):
            wait_for(lambda log=log: "RX MESSAGE de Server1: CORE multicast test" in contents(log))
        client.stdin.write("CORE bootstrap test\n")
        client.stdin.flush()
        wait_for(lambda: "RX MESSAGE de C1: CORE bootstrap test" in contents(server_log))
        assert "CORE bootstrap test" not in contents(second_log), "Client uplink leaked to C2"
        client.stdin.write("/quit\n")
        client.stdin.flush()
        client.wait(timeout=10)
        wait_for(lambda: "LEAVE C1" in contents(server_log))
        second.stdin.write("/quit\n")
        second.stdin.flush()
        second.wait(timeout=10)
        server.stdin.write("/quit\n")
        server.stdin.flush()
        server.wait(timeout=15)
        if client.returncode or second.returncode or server.returncode:
            raise RuntimeError("Application exited unsuccessfully")
        print("PASS: full XML underlay; only R3 activated; multicast to C1/C2; C1 uplink only to Server1; LEAVE")
    finally:
        print("CORE test logs:", session)
        for process in processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        for pid in reversed(pids):
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        for name in reversed(interfaces + bridges):
            subprocess.run(["ip", "link", "del", name], capture_output=True)
        for log in logs:
            log.close()


if __name__ == "__main__":
    main()
