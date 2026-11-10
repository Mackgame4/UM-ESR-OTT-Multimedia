"""Command-line interface for manual peers and controller-based bootstrapping."""

import argparse
import logging
import socket
import signal
import threading

from src.config import DEFAULT_HOST, DEFAULT_PORT
from src.node import OverlayNode
from src.overlay.bootstrap import BOOTSTRAP_PORT, BootstrapClient, BootstrapController, routing_for
from src.overlay.core import CoreLauncher
from src.overlay.topology import load_topology


def port_number(value):
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("A porta deve ser um número") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("A porta deve estar entre 1 e 65535")
    return port


def peer_address(value):
    host, separator, port = value.rpartition(":")
    if not separator or not host.strip():
        raise argparse.ArgumentTypeError("Use HOST:PORT para identificar o vizinho")
    return host, port_number(port)


def bootstrap_address(value):
    return peer_address(value) if ":" in value else (value, BOOTSTRAP_PORT)


def run(argv=None):
    parser = argparse.ArgumentParser(description="OTT: nó overlay com controlo TCP")
    parser.add_argument("bootstrapper", nargs="?", type=bootstrap_address,
                        help="Contacto de arranque HOST[:PORT], porta 5001")
    parser.add_argument("--controller", action="store_true", help="Servidor/bootstrapper")
    parser.add_argument("--topology", default="topology/topology.xml", help="Topologia física CORE (XML)")
    parser.add_argument("--bootstrap-port", type=port_number, default=BOOTSTRAP_PORT)
    parser.add_argument("--core-session", help="Diretório /tmp/pycore.ID da sessão CORE")
    parser.add_argument("--headless", action="store_true", help="Executar sem stdin")
    parser.add_argument("--id", default=socket.gethostname(), help="Identificador do nó")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Endereço IPv4 de escuta")
    parser.add_argument("--port", type=port_number, default=DEFAULT_PORT,
                        help="Porta TCP de escuta (por omissão: 5000)")
    parser.add_argument("--peer", type=peer_address, action="append", default=[],
                        metavar="HOST:PORT", help="Vizinho inicial; pode repetir a opção")
    args = parser.parse_args(argv)
    if not args.id.strip():
        parser.error("O identificador do nó não pode estar vazio")
    if args.controller and args.bootstrapper:
        parser.error("O controlador não recebe um bootstrapper")
    if args.bootstrapper and args.peer:
        parser.error("Use bootstrapper ou --peer")
    if args.core_session and not args.controller:
        parser.error("--core-session requer --controller")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    node = OverlayNode(args.id, args.host, args.port)
    controller = client = launcher = None
    stopped = threading.Event()
    previous_handler = signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    try:
        node.start()
        if args.controller:
            config = load_topology(args.topology, args.id, args.port)
            node.configure_routing(**routing_for(config, args.id, args.id))
            entry = config["nodes"].get(args.id)
            if entry is None or entry["port"] != node.port:
                raise ValueError("ID/porta do controlador não corresponde à configuração")
            launcher = CoreLauncher(args.core_session,
                                    "{}:{}".format(entry["host"], args.bootstrap_port), config, args.id)
            controller = BootstrapController(args.id, config, args.host,
                                             args.bootstrap_port, activate=launcher)
            controller.start()
        elif args.bootstrapper:
            client = BootstrapClient(node, args.bootstrapper)
            client.start()
        for host, port in args.peer:
            try:
                node.connect(host, port)
            except OSError as exc:
                print("Não foi possível ligar a {}:{}: {}".format(host, port, exc), flush=True)
        if args.headless:
            stopped.wait()
            return 0
        print("Comandos: /hello, /peers, /members, /connect HOST:PORT, /quit. "
              "Texto: servidor envia a todos os clientes; cliente envia ao servidor.", flush=True)
        while not stopped.is_set():
            try:
                command = input().strip()
            except EOFError:
                break
            if command == "/quit":
                break
            if command == "/members":
                if controller:
                    for name, status in controller.member_statuses():
                        print("{} ({})".format(name, status), flush=True)
                else:
                    print("Disponível apenas no controlador.", flush=True)
            elif command == "/peers":
                peers = node.peers()
                for node_id, (host, port) in peers:
                    print("{} em {}:{}".format(node_id or "(a identificar)", host, port), flush=True)
                if not peers:
                    print("Sem vizinhos ligados.", flush=True)
            elif command.startswith("/connect "):
                try:
                    node.connect(*peer_address(command[len("/connect "):].strip()))
                except (argparse.ArgumentTypeError, OSError) as exc:
                    print("Não foi possível ligar: {}".format(exc), flush=True)
            elif command:
                if command.startswith("/") and command != "/hello":
                    print("Comando desconhecido.", flush=True)
                    continue
                try:
                    count = (node.broadcast() if command == "/hello"
                             else node.broadcast(command, "MESSAGE"))
                    if command == "/hello" or not (args.controller or args.bootstrapper):
                        status = "Enviado a {} vizinho(s)."
                    elif args.controller:
                        status = "Multicast para os clientes enviado por {} ligação(ões) overlay."
                    else:
                        status = "Mensagem para o servidor enviada por {} ligação(ões) overlay."
                    print(status.format(count), flush=True)
                except ValueError as exc:
                    print(str(exc), flush=True)
    except KeyboardInterrupt:
        pass
    except (OSError, ValueError) as exc:
        logging.error("Erro: %s", exc)
        return 1
    finally:
        if client:
            client.stop()
        if controller:
            controller.stop()
        if launcher:
            launcher.stop()
        node.stop()
        signal.signal(signal.SIGTERM, previous_handler)
    return 0
