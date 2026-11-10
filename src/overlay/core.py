"""Start on-demand overlay processes inside an existing CORE VM session via vcmd."""

import logging
import subprocess
import sys
import threading
from pathlib import Path


class CoreLauncher:
    def __init__(self, session, bootstrapper, config, server_id="Server1"):
        self.session = Path(session).resolve() if session else None
        self.server_id = server_id
        self.bootstrapper = bootstrapper
        self.nodes = config["nodes"]
        self.processes = {}
        self._logs = {}
        self._lock = threading.Lock()
        self._stopped = False

    def __call__(self, name):
        with self._lock:
            if self._stopped:
                raise ValueError("Controlador a terminar")
            if self.session is None:
                candidates = [path for path in Path("/tmp").glob("pycore.*")
                              if (path / self.server_id).is_socket() and (path / name).is_socket()]
                if len(candidates) != 1:
                    raise ValueError("Não foi possível identificar a sessão CORE; indique --core-session /tmp/pycore.ID")
                self.session = candidates[0]
            if not self.nodes[name].get("router"):
                raise ValueError("Só é permitido iniciar overlay nos routers")
            process = self.processes.get(name)
            if process is not None and process.poll() is None:
                return
            # Only local configuration supplies socket paths and process arguments.
            if Path(name).name != name or name in (".", ".."):
                raise ValueError("Nome de nó CORE inválido")
            channel = self.session / name
            if not channel.is_socket():
                raise ValueError("Canal CORE inexistente: " + str(channel))
            if name in self._logs:
                self._logs.pop(name).close()
            log = open(self.session / (name + "-ott.log"), "ab", buffering=0)
            self._logs[name] = log
            entry = self.nodes[name]
            root = str(Path(__file__).resolve().parents[2])
            command = ["vcmd", "-c", str(channel), "--", sys.executable,
                       "-c", "import os,sys; os.chdir(sys.argv.pop(1)); "
                       "sys.path.insert(0, os.getcwd()); from src.main import main; "
                       "raise SystemExit(main())", root, self.bootstrapper, "--id", name,
                       "--port", str(entry["port"]), "--headless"]
            self.processes[name] = subprocess.Popen(
                command, cwd=Path(__file__).resolve().parents[2],
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
            logging.info("Ativado overlay em %s; log: %s", name, log.name)

    def stop(self):
        with self._lock:
            self._stopped = True
            for process in self.processes.values():
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
            for log in self._logs.values():
                log.close()
