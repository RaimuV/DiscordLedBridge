"""Client for Discord local RPC (named pipe discord-ipc-0).

Local protocol framing (little-endian on all OSes):
    4 byte opcode | 4 byte length | JSON payload (UTF-8)

Ops: HANDSHAKE=0, FRAME=1, CLOSE=2, PING=3, PONG=4.

Typical usage:
    rpc = DiscordIPC()
    rpc.connect()
    rpc.handshake(client_id)
    rpc.authenticate(token)
    settings = rpc.get_voice_settings()
    rpc.subscribe("VOICE_SETTINGS_UPDATE", on_update)
    rpc.listen()
"""

import json
import struct
import threading
import time

import win32file
import win32pipe

PIPE_NAME = r"\\.\pipe\discord-ipc-0"

OP_HANDSHAKE = 0
OP_FRAME = 1
OP_CLOSE = 2
OP_PING = 3
OP_PONG = 4

ERROR_CODES = {
    4000: "Unknown error",
    4001: "Invalid command",
    4002: "Malformed args",
    4003: "No such event",
    4004: "Invalid token",
    4005: "Bad OAuth2 token",
    4006: "Needs updated",
    4007: "Need more authorization",
    4008: "Command failed",
    4009: "No such command",
    4010: "Feature not enabled",
    4011: "The RPC connection was not authenticated",
}


class DiscordRPCError(Exception):
    pass


class DiscordIPC:
    def __init__(self, pipe=PIPE_NAME):
        self.pipe = pipe
        self.handle = None
        self._lock = threading.Lock()
        self._buffer = b""

    # -- trasporto ---------------------------------------------------------

    def connect(self):
        try:
            self.handle = win32file.CreateFile(
                self.pipe,
                win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                0,
                None,
                win32file.OPEN_EXISTING,
                0,
                None,
            )
        except win32file.error as exc:
            raise DiscordRPCError(f"unable to open {self.pipe}: {exc}") from exc

    def close(self):
        if self.handle is not None:
            try:
                self.handle.Close()
            except Exception:
                pass
            self.handle = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()

    def _send_frame(self, op, payload):
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        header = struct.pack("<II", op, len(data))
        with self._lock:
            win32file.WriteFile(self.handle, header + data)

    def _recv_frame(self):
        # header: 8 bytes
        while len(self._buffer) < 8:
            got, self._buffer = self._read_more(self._buffer)
            if got == 0:
                raise DiscordRPCError("pipe closed by Discord")
        op, length = struct.unpack("<II", self._buffer[:8])
        self._buffer = self._buffer[8:]
        while len(self._buffer) < length:
            got, self._buffer = self._read_more(self._buffer)
            if got == 0:
                raise DiscordRPCError("pipe closed while receiving")
        data, self._buffer = self._buffer[:length], self._buffer[length:]
        return op, json.loads(data.decode("utf-8"))

    def recv_frame(self, timeout=None):
        """Like _recv_frame but with timeout (seconds): TimeoutError if nothing arrives."""
        deadline = time.monotonic() + timeout if timeout is not None else None
        while True:
            try:
                _, available, _ = win32pipe.PeekNamedPipe(self.handle, 0)
            except win32file.error as exc:
                # any error on PeekNamedPipe (e.g. ERROR_BROKEN_PIPE 109
                # when Discord exits) means the pipe is unusable: the monitor
                # must reconnect, not keep waiting.
                raise DiscordRPCError(
                    f"pipe unavailable ({exc.winerror}): {exc}") from exc
            if available:
                return self._recv_frame()
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("no frame within the timeout")
            time.sleep(0.05)

    def _read_more(self, buffer):
        try:
            result, data = win32file.ReadFile(self.handle, 4096)
        except win32file.error as exc:
            if exc.winerror == 109:  # ERROR_BROKEN_PIPE
                return 0, buffer
            raise DiscordRPCError(f"pipe read error: {exc}") from exc
        if result:
            raise DiscordRPCError(f"ReadFile failed: {result}")
        return len(data), buffer + bytes(data)

    # -- protocollo --------------------------------------------------------

    def handshake(self, client_id):
        self._send_frame(OP_HANDSHAKE, {"v": 1, "client_id": str(client_id)})
        op, payload = self._recv_frame()
        if op == OP_FRAME:
            if payload.get("cmd") == "READY":
                return payload
        if op == OP_CLOSE:
            code = payload.get("code")
            raise DiscordRPCError(
                f"handshake rejected (code {code}): {payload.get('message', ERROR_CODES.get(code, '?'))}"
            )
        return payload

    def _command(self, cmd, args=None, evt=None):
        nonce = f"{id(self)}.{cmd}"
        payload = {"cmd": cmd, "args": args or {}, "evt": evt, "nonce": nonce}
        self._send_frame(OP_FRAME, payload)
        while True:
            op, frame = self._recv_frame()
            if op == OP_FRAME and frame.get("nonce") == nonce:
                if frame.get("evt") == "ERROR":
                    code = frame.get("data", {}).get("code")
                    raise DiscordRPCError(
                        f"{cmd} failed (code {code}): "
                        f"{frame.get('data', {}).get('message', ERROR_CODES.get(code, '?'))}"
                    )
                return frame.get("data")
            if op == OP_CLOSE:
                code = frame.get("code")
                raise DiscordRPCError(f"connection closed (code {code}): {frame.get('message')}")
            # unrelated frame: dispatch event, passes it to the listener

    def authenticate(self, access_token):
        return self._command("AUTHENTICATE", {"access_token": access_token})

    def get_voice_settings(self):
        data = self._command("GET_VOICE_SETTINGS")
        return data or {}

    def subscribe(self, event):
        return self._command("SUBSCRIBE", {}, evt=event)

    def listen(self, handler, stop=None):
        """Reads frames until an event handled by handler arrives.

        `handler(frame)` returns True to stop (or None to continue).
        `stop` is an optional threading.Event, checked every ~200ms.
        """
        while stop is None or not stop.is_set():
            try:
                op, frame = self.recv_frame(timeout=0.2)
            except TimeoutError:
                continue
            if op == OP_CLOSE:
                raise DiscordRPCError(
                    f"closed by Discord (code {frame.get('code')}): {frame.get('message')}"
                )
            if op == OP_FRAME:
                if frame.get("evt") and handler:
                    if handler(frame):
                        return


def voice_state(data):
    """Extracts {mute, deaf} from the voice settings payload, with field aliases."""
    if not isinstance(data, dict):
        return None
    mute = data.get("mute")
    deaf = data.get("deaf", data.get("deafened", data.get("deafness")))
    if mute is None or deaf is None:
        return None
    return {"mute": bool(mute), "deaf": bool(deaf)}