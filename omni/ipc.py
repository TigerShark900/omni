"""Talks to the Discord desktop app over its local socket (the same way games do)."""
import json
import os
import socket
import struct
import uuid

OP_HANDSHAKE, OP_FRAME, OP_CLOSE = 0, 1, 2


def _socket_dirs():
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    # Normal install, Flatpak Discord, Snap Discord, Vesktop flatpak
    return [
        base,
        os.path.join(base, "app", "com.discordapp.Discord"),
        os.path.join(base, "app", "dev.vencord.Vesktop"),
        os.path.join(base, "snap.discord"),
        "/tmp",
    ]


class DiscordError(Exception):
    pass


class DiscordIPC:
    def __init__(self, client_id):
        self.client_id = str(client_id)
        self.sock = None
        self.user = None

    @property
    def connected(self):
        return self.sock is not None

    def connect(self):
        last = None
        for d in _socket_dirs():
            for i in range(10):
                path = os.path.join(d, f"discord-ipc-{i}")
                if not os.path.exists(path):
                    continue
                try:
                    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    s.settimeout(5)
                    s.connect(path)
                    self.sock = s
                    self._send(OP_HANDSHAKE, {"v": 1, "client_id": self.client_id})
                    op, data = self._recv()
                    if op == OP_CLOSE or data.get("evt") == "ERROR":
                        raise DiscordError(data.get("message") or data.get("data", {}).get("message") or str(data))
                    self.user = (data.get("data") or {}).get("user")
                    return
                except (OSError, DiscordError) as e:
                    last = e
                    self.close()
        raise DiscordError(f"can't reach Discord ({last or 'no socket found - is Discord running?'})")

    def _send(self, op, payload):
        body = json.dumps(payload).encode()
        self.sock.sendall(struct.pack("<II", op, len(body)) + body)

    def _recv_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise OSError("Discord closed the connection")
            buf += chunk
        return buf

    def _recv(self):
        op, length = struct.unpack("<II", self._recv_exact(8))
        return op, json.loads(self._recv_exact(length) or b"{}")

    def set_activity(self, activity):
        """activity=None clears the status."""
        if not self.sock:
            self.connect()
        nonce = str(uuid.uuid4())
        try:
            self._send(OP_FRAME, {"cmd": "SET_ACTIVITY", "args": {"pid": os.getpid(), "activity": activity}, "nonce": nonce})
            while True:
                op, data = self._recv()
                if op == OP_CLOSE:
                    raise OSError(data.get("message", "closed"))
                if data.get("nonce") == nonce:
                    break
        except OSError:
            self.close()
            raise
        if data.get("evt") == "ERROR":
            raise DiscordError(data.get("data", {}).get("message", "unknown error"))
        return data

    def close(self):
        if self.sock:
            try:
                self._send(OP_CLOSE, {})
            except OSError:
                pass
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None
