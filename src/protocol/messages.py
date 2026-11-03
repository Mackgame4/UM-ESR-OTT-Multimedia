"""Control messages: UTF-8 JSON objects delimited by a newline over TCP."""

import json

from src.config import MAX_MESSAGE_BYTES


def encode_message(message_type, node_id, text="HELLO"):
    message = {"type": message_type, "node_id": node_id, "text": text}
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
    if message.get("type") not in ("HELLO", "MESSAGE"):
        raise ValueError("Tipo de mensagem desconhecido")
    if not isinstance(message.get("node_id"), str) or not message["node_id"].strip():
        raise ValueError("node_id deve ser uma string não vazia")
    if not isinstance(message.get("text"), str):
        raise ValueError("text deve ser uma string")
