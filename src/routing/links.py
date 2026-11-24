"""One-hop interface-bound probes: an alternate OSPF path cannot hide a cut."""

import subprocess
from concurrent.futures import ThreadPoolExecutor


def probe(target):
    try:
        result = subprocess.run(
            ["ping", "-n", "-I", target["interface"], "-t", "1", "-c", "1", "-W", "1", target["host"]],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False
    # A missing ping executable is a configuration error, not a link failure.


def sample(targets):
    if not targets:
        return {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(probe, targets))
    return {target["id"]: up for target, up in zip(targets, results)}
