"""Tk/Pillow player adapted from ClienteGUI.py; all GUI work stays on the main thread."""

import io
import queue


class VideoPlayer:
    def __init__(self, transport, stopped):
        try:
            import tkinter as tk
            from PIL import Image, ImageTk
            self.root = tk.Tk()
        except Exception as exc:
            raise ValueError("Janela indisponível: instalar Pillow/python3-tk e verificar DISPLAY na VM: " + str(exc)) from exc
        self.Image, self.ImageTk = Image, ImageTk
        self.transport, self.stopped = transport, stopped
        self.frames = queue.Queue(maxsize=1)
        self.root.title("OTT Live — " + transport.node.node_id)
        self.label = tk.Label(self.root, text="À espera de vídeo live...")
        self.label.pack()
        self.status = tk.Label(self.root, text="UDP • sem retransmissões")
        self.status.pack()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        transport.on_frame = self.offer

    def offer(self, frame, jpeg):
        try:
            self.frames.put_nowait((frame, jpeg))
        except queue.Full:
            try:
                discarded, _ = self.frames.get_nowait()
                self.transport.log.warning("DISPLAY_DROP frame=%s reason=newer_complete_frame", discarded)
            except queue.Empty:
                pass
            self.frames.put_nowait((frame, jpeg))

    def _tick(self):
        if self.stopped.is_set():
            self.close()
            return
        try:
            frame, jpeg = self.frames.get_nowait()
            try:
                with self.Image.open(io.BytesIO(jpeg)) as image:
                    photo = self.ImageTk.PhotoImage(image)
                self.label.configure(image=photo, text="")
                self.label.image = photo
                self.status.configure(text="Frame {} • perdas: {}".format(frame, self.transport.stats["lost_frames"]))
            except Exception as exc:
                self.transport.log.warning("DECODE_DROP frame=%s error=%s", frame, exc)
        except queue.Empty:
            pass
        self.root.after(15, self._tick)

    def run(self):
        self.root.after(0, self._tick)
        self.root.mainloop()

    def close(self):
        self.stopped.set()
        self.transport.on_frame = None
        try:
            self.root.destroy()
        except Exception:
            pass
