"""Command-line interface for the stage-one overlay."""

import argparse
import logging
import socket

from src.config import DEFAULT_HOST, DEFAULT_PORT
from src.node import OverlayNode


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


def run(argv=None):
    parser = argparse.ArgumentParser(description="OTT: nó overlay com controlo TCP")
    parser.add_argument("--id", default=socket.gethostname(), help="Identificador do nó")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Endereço IPv4 de escuta")
    parser.add_argument("--port", type=port_number, default=DEFAULT_PORT,
                        help="Porta TCP de escuta (por omissão: 5000)")
    parser.add_argument("--peer", type=peer_address, action="append", default=[],
                        metavar="HOST:PORT", help="Vizinho inicial; pode repetir a opção")
    args = parser.parse_args(argv)
    if not args.id.strip():
        parser.error("O identificador do nó não pode estar vazio")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    node = OverlayNode(args.id, args.host, args.port)
    try:
        node.start()
        for host, port in args.peer:
            try:
                node.connect(host, port)
            except OSError as exc:
                print("Não foi possível ligar a {}:{}: {}".format(host, port, exc), flush=True)
        print("Comandos: /hello, /peers, /connect HOST:PORT, /quit. "
              "Texto livre envia MESSAGE a todos os vizinhos.", flush=True)
        while True:
            try:
                command = input().strip()
            except EOFError:
                break
            if command == "/quit":
                break
            if command == "/peers":
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
                    print("Enviado a {} vizinho(s).".format(count), flush=True)
                except ValueError as exc:
                    print(str(exc), flush=True)
    except KeyboardInterrupt:
        pass
    except (OSError, ValueError) as exc:
        logging.error("Erro: %s", exc)
        return 1
    finally:
        node.stop()
    return 0
