"""RTP-inspired teaching format adapted from RtpPacket.py, with overlay metadata.

Fixed RTP v2 header, dynamic PT 96; private payload, NOT RFC 2435 RTP/JPEG.
The extension below is our payload prefix, not an RTP extension header.
"""

from dataclasses import dataclass, replace
import hashlib
import struct

RTP = struct.Struct("!BBHII")
META = struct.Struct("!4s16s16sQQHHB")
MAX_DATAGRAM = 1200
PAYLOAD_SIZE = MAX_DATAGRAM - RTP.size - META.size
MAX_FRAME = 99999  # Five ASCII length digits in the supplied movie format.
MAX_FRAGMENTS = (MAX_FRAME + PAYLOAD_SIZE - 1) // PAYLOAD_SIZE


def controller_token(controller_id):
    return hashlib.blake2b(controller_id.encode("utf-8"), digest_size=16).digest()


@dataclass(frozen=True)
class VideoPacket:
    controller: bytes
    session: bytes
    epoch: int
    frame: int
    fragment: int
    fragments: int
    sequence: int
    timestamp: int
    ssrc: int
    payload: bytes
    ttl: int = 64

    def encode(self):
        marker = int(self.fragment == self.fragments - 1)
        return (RTP.pack(0x80, (marker << 7) | 96, self.sequence, self.timestamp, self.ssrc) +
                META.pack(b"OTT1", self.controller, self.session, self.epoch, self.frame,
                          self.fragment, self.fragments, self.ttl) + self.payload)

    @classmethod
    def decode(cls, data):
        if not RTP.size + META.size < len(data) <= MAX_DATAGRAM:
            raise ValueError("Tamanho de datagrama inválido")
        first, second, sequence, timestamp, ssrc = RTP.unpack_from(data)
        magic, controller, session, epoch, frame, fragment, fragments, ttl = META.unpack_from(data, RTP.size)
        payload = data[RTP.size + META.size:]
        if (first != 0x80 or second & 127 != 96 or magic != b"OTT1" or frame < 1 or
                not 1 <= fragments <= MAX_FRAGMENTS or not 0 <= fragment < fragments or
                not 1 <= ttl <= 64 or second >> 7 != int(fragment == fragments - 1) or
                (fragment < fragments - 1 and len(payload) != PAYLOAD_SIZE) or
                (fragment == fragments - 1 and (fragments - 1) * PAYLOAD_SIZE + len(payload) > MAX_FRAME)):
            raise ValueError("Cabeçalho de vídeo inválido")
        return cls(controller, session, epoch, frame, fragment, fragments, sequence,
                   timestamp, ssrc, payload, ttl)

    def forwarded(self):
        return replace(self, ttl=self.ttl - 1)


def packetize(jpeg, controller_id, session, epoch, frame, sequence, timestamp):
    if not 1 <= len(jpeg) <= MAX_FRAME:
        raise ValueError("Tamanho de JPEG inválido")
    count = (len(jpeg) + PAYLOAD_SIZE - 1) // PAYLOAD_SIZE
    token = controller_token(controller_id)
    ssrc = int.from_bytes(session[:4], "big")
    return [VideoPacket(token, session, epoch, frame, index, count,
                        (sequence + index) & 65535, timestamp & 0xffffffff, ssrc,
                        jpeg[index * PAYLOAD_SIZE:(index + 1) * PAYLOAD_SIZE]).encode()
            for index in range(count)]
