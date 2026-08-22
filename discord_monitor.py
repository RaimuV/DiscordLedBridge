"""Monitor dello stato voce di Discord via RPC locale.

Thread che tiene aperta la connessione alla pipe discord-ipc-0, si autentica,
legge lo stato iniziale e resta in ascolto degli eventi VOICE_SETTINGS_UPDATE.
Alla disconnessione (es. riavvio di Discord) si riconnette con backoff
esponenziale. Ogni cambio di stato chiama on_state({'mute': bool, 'deaf': bool}).
"""

import threading
import time

from discord_rpc import DiscordIPC, DiscordRPCError, voice_state
from discord_test import load_credentials, refresh_access_token

BACKOFF_START = 2.0
BACKOFF_MAX = 30.0

_TOKEN_ERRORS = ("4004", "4005", "4010")


class DiscordMonitor:
    def __init__(self, on_state, on_log=None):
        self.on_state = on_state
        self.on_log = on_log or (lambda msg: None)
        self._running = False
        self._thread = None

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=3)

    def _log(self, msg):
        self.on_log(msg)

    # -- loop ---------------------------------------------------------------

    def _run(self):
        backoff = BACKOFF_START
        while self._running:
            try:
                cred = load_credentials()
                with DiscordIPC() as rpc:
                    rpc.handshake(cred["client_id"])
                    self._authenticate(rpc, cred)
                    self._log("collegato e autenticato a Discord")
                    settings = rpc.get_voice_settings()
                    self._dispatch(settings)
                    rpc.subscribe("VOICE_SETTINGS_UPDATE")
                    self._listen(rpc)
                backoff = BACKOFF_START
            except DiscordRPCError as exc:
                self._log(f"RPC: {exc}")
                backoff = self._sleep_backoff(backoff)
            except SystemExit:
                self._log("credenziali non configurate (esegui: python discord_test.py --setup)")
                backoff = self._sleep_backoff(backoff)
            except Exception as exc:
                self._log(f"errore monitor: {exc}")
                backoff = self._sleep_backoff(backoff)

    def _sleep_backoff(self, backoff):
        if not self._running:
            return backoff
        self._log(f"riconnessione tra {backoff:.0f}s")
        waited = 0.0
        while waited < backoff and self._running:
            time.sleep(0.2)
            waited += 0.2
        return min(backoff * 2, BACKOFF_MAX)

    # -- autenticazione ------------------------------------------------------

    def _authenticate(self, rpc, cred):
        try:
            rpc.authenticate(cred["access_token"])
        except DiscordRPCError as exc:
            if any(code in str(exc) for code in _TOKEN_ERRORS):
                self._log("token scaduto, aggiorno con refresh token")
                cred = refresh_access_token(cred)
                rpc.authenticate(cred["access_token"])
            else:
                raise

    # -- eventi ---------------------------------------------------------------

    def _dispatch(self, data):
        state = voice_state(data)
        if state and self._running:
            self.on_state(state)

    def _listen(self, rpc):
        stop = threading.Event()

        def handler(frame):
            if frame.get("evt") == "VOICE_SETTINGS_UPDATE":
                self._dispatch(frame.get("data", {}))
            return False

        # l'evento di stop e' gestito dal loop chiamante via stop()
        while self._running:
            try:
                op, frame = rpc.recv_frame(timeout=0.2)
            except TimeoutError:
                continue
            if op == 2:  # OP_CLOSE
                raise DiscordRPCError(
                    f"chiusa da Discord (code {frame.get('code')}): "
                    f"{frame.get('message')}")
            if op == 1 and frame.get("evt"):
                handler(frame)