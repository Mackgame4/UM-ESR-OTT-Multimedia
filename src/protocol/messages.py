"""Control messages: UTF-8 JSON objects delimited by a newline over TCP."""

import json

from src.config import MAX_MESSAGE_BYTES


def encode_message(message_type, node_id, text="HELLO", **fields):
    message = {"type": message_type, "node_id": node_id, "text": text}
    message.update(fields)
    validate_message(message)
    data = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
    if len(data) > MAX_MESSAGE_BYTES:
        raise ValueError("Mensagem demasiado grande")
    return data


def decode_message(data):
    try:
        message = json.loads(data.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ValueError("Mensagem JSON/UTF-8 inválida") from exc
    validate_message(message)
    return message


def validate_message(message):
    if not isinstance(message, dict):
        raise ValueError("A mensagem deve ser um objeto JSON")
    if message.get("type") not in ("HELLO", "MESSAGE", "REGISTER", "NEIGHBOURS", "LEAVE", "ACK", "ERROR"):
        raise ValueError("Tipo de mensagem desconhecido")
    if not isinstance(message.get("node_id"), str) or not message["node_id"].strip():
        raise ValueError("node_id deve ser uma string não vazia")
    if not isinstance(message.get("text"), str):
        raise ValueError("text deve ser uma string")
    if "delivery" in message:
        if (message["type"] != "MESSAGE" or message["delivery"] not in ("MULTICAST", "UPSTREAM") or
                not isinstance(message.get("message_id"), str) or not message["message_id"] or
                type(message.get("ttl")) is not int or not 0 <= message["ttl"] <= 64):
            raise ValueError("Metadados de encaminhamento inválidos")
    if message["type"] == "NEIGHBOURS":
        if "routing" in message:
            routing = message["routing"]
            if (not isinstance(routing, dict) or
                    set(routing) != {"role", "server_id", "parent_id", "children"} or
                    routing["role"] not in ("server", "router", "client") or
                    not isinstance(routing["server_id"], str) or not routing["server_id"].strip() or
                    (routing["parent_id"] is not None and
                     (not isinstance(routing["parent_id"], str) or not routing["parent_id"].strip())) or
                    not isinstance(routing["children"], list) or
                    any(not isinstance(child, str) or not child.strip() for child in routing["children"])):
                raise ValueError("Configuração de encaminhamento inválida")
        neighbours = message.get("neighbours")
        if not isinstance(neighbours, list):
            raise ValueError("neighbours deve ser uma lista")
        for peer in neighbours:
            if (not isinstance(peer, dict) or
                    not isinstance(peer.get("node_id"), str) or not peer["node_id"].strip() or
                    not isinstance(peer.get("host"), str) or not peer["host"].strip() or
                    type(peer.get("port")) is not int or not 1 <= peer["port"] <= 65535):
                raise ValueError("Vizinho inválido")
