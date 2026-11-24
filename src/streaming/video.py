"""MJPEG reader adapted from the supplied teaching example's VideoStream.py."""

from pathlib import Path


class VideoStream:
    def __init__(self, filename):
        self.file = Path(filename).open("rb")

    def next_frame(self):
        # The example uses five ASCII bytes, not five bits, for JPEG length.
        prefix = self.file.read(5)
        if not prefix:
            return None
        if len(prefix) != 5 or not prefix.isdigit() or int(prefix) == 0:
            raise ValueError("Prefixo MJPEG inválido")
        frame = self.file.read(int(prefix))
        if len(frame) != int(prefix):
            raise ValueError("Frame MJPEG truncado")
        return frame

    def rewind(self):
        self.file.seek(0)

    def close(self):
        self.file.close()
