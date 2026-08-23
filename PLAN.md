# PIANO — DiscordLedBridge: stato audio Discord → LED side-keyboard

Stato: **COMPLETATO e collaudato dall'utente il 23/08/2026**. Tutte le milestone (B, A, C, D)
implementate e verificate end-to-end: OAuth funzionante, LED speculari ai pulsanti (keycap 1 =
mic, keycap 2 = deafen), riconnessione Discord e riapertura tastiera. Unico passo eventuale
futuro: autostart (Task Scheduler) se l'avvio manuale non basta.

## 1. Contesto e obiettivo

Daemon minimale in background che riflette lo stato mute/deafen di Discord sui LED di
3 tasti della side-keyboard SDINNOVATION, con spia opzionale a schermo. Vedere a colpo
d'occhio: mic live, mic muto, audio mutato.

**Device**: SIDE-KEYBOARD (`USB\VID_0816&PID_246E`, famiglia SDCX/HCY-K006, 6 tasti +
rotella, LED per-key). Accesso LED via interfaccia vendor HID (usage page `0xFF00`,
report 64 byte), protocollo già reverse-engineered (MIT, `parsaj-dev/sdcx-keypad`).

## 2. Decisioni concordate

| Aspetto | Decisione |
|---|---|
| Rilevamento mute | **Strada (a): RPC locale di Discord con OAuth** (`discord-ipc-0`) |
| Credenziali | File locale `credentials.json` con **permessi ristretti al solo utente** (mode 600/ACL), mai nel repo |
| Tasti LED | **Stato globale sul gruppo keycap 1+2** (indici `0,1`, stesso colore): blu ok / giallo mic muto / rosso deafen; keycap 3-4 spenti |
| Colori | `#00F0FF` (blu) tutto ok · `#FFFF00` (giallo) solo mic muto · `#FF0000` (rosso) audio mutato |
| Caso "solo cuffie mute" | **Ignorato** (nessun aggiornamento LED) |
| Fallback | Strada (c): tastiera come controllo mute se l'OAuth si rompe |

## 3. Architettura

```
DiscordLedBridge (daemon Python, autostart con Windows)
│
├─ DiscordMonitor.py   → named pipe \\.\pipe\discord-ipc-0 (pywin32)
│    handshake {v:1, client_id} → AUTHENTICATE → GET_VOICE_SETTINGS
│    subscribe VOICE_SETTINGS_UPDATE (eventi mute/deafen)
│    refresh token auto, riconnessione con backoff (watchdog psutil)
│    output: { mute: bool, deaf: bool }
│
├─ KeyboardLed.py      → hidapi (interfaccia vendor, VID 0816 / PID 246E)
│    light mode 5 (Custom), colore per-key via [0x06,0x14,...]
│    rilevamento plug/unplug, ripristino modalità luce alla chiusura
│
├─ TrayIcon.py         → pystray: spia che cambia colore (blu/giallo/rosso)
│
└─ app.py              → coordinatore: stati → mapping colori → LED + tray
     config.json + credentials.json in %LOCALAPPDATA%\DiscordLedBridge\
```

**Mapping** (soggetto a conferma semantica "deafen = cuffie mute"):
- `mute=false, deaf=false` → `#00F0FF` (blu)
- `mute=true, deaf=false` → `#FFFF00` (giallo)
- `mute=true, deaf=true` → `#FF0000` (rosso)
- `mute=false, deaf=true` → nessun aggiornamento

## 4. Stack

- **Python 3.12** (presente) · **pywin32** (presente) · **psutil** (presente)
- **`hid`** (hidapi, da installare) · **`pystray`** (da installare)
- Nessun servizio esterno; tutto locale

## 5. Setup OAuth (una tantum, richiede l'utente)

1. Creare app su discord.com/developers → Application ID + Client Secret
2. Redirect URI `http://localhost` nella pagina OAuth2
3. Autorizzazione una tantum via comando setup (finestra di consenso dentro Discord)
4. Credenziali → `%LOCALAPPDATA%\DiscordLedBridge\credentials.json` (permessi ristretti), scopes `rpc.voice.read`

## 6. Sicurezza

- `client_secret`/token **mai** hardcodati, mai committati, mai nel repo del progetto
- File credenziali con ACL ristrette al solo utente
- Rispetto dei vincoli globali macchina (nessuna lettura di config altrui)

## 7. Rischi e fallback

1. **RPC locale non documentata** (voce settings = reverse-engineering): update di
   Discord potrebbe romperla → **fallback strada (c)**: tastiera come controllo mute
   (inoltra `Ctrl+Shift+M`/`Ctrl+Shift+D`, stato tenuto dal tool); solo il modulo
   DiscordMonitor cambia, LED invariati.
2. **PID 246e non verificato su hardware**: verifica iniziale (lettura keymap+lighting)
   prima di scrivere LED.
3. **Conflitto col configuratore del produttore**: il bridge forza modalità Custom
   quando attivo e ripristina la modalità precedente alla chiusura.

## 8. Piano di esecuzione

Ordine deciso il 23/08/2026: **prima la tastiera, poi Discord** (sgancia dal setup OAuth
e valida subito l'hardware).

1. **Verifica ambiente**: `pip install hidapi` (NON `hid`: il pacchetto `hid` è solo il
   wrapper ctypes e su Windows manca `hidapi.dll`; `hidapi` la include bundle) ✅
2. **Milestone B — LED** ✅: `keyboard_test.py` — verifica PID su hardware, legge
   keymap/modalità, prova colore su 1 tasto, modalità Custom, ripristino
3. **Milestone A — RPC Discord**: `discord_test.py` — pipe, handshake+auth OAuth,
   `GET_VOICE_SETTINGS`, stampa mute/deafen (richiede autorizzazione OAuth dell'utente)
4. **Milestone C — Bridge completo**: `app.py` che collega A→B (eventi → colori),
   tray icon opzionale, handling riconnessione
5. **Milestone D ridotto**: config JSON + README d'uso (avvio manuale, niente autostart)
6. **Collaudo finale**: verifica dei 3 stati + comportamento al riavvio di Discord e al
   plug/unplug della tastiera

## 9. Decisioni aperte da confermare al via

Confermate il 23/08/2026:

1. **Tray icon**: SÌ, spia a schermo opzionale via config ✅
2. **Auto-start**: avvio manuale all'inizio; poi **Task Scheduler configurato il 23/08/2026**
   (task `DiscordLedBridge`, logon, `pythonw.exe`, retry su crash, log in `%LOCALAPPDATA%\DiscordLedBridge\app.log`) ✅
3. **Semantica "cuffie mute"**: `deafen` = cuffie mute (disattiva l'audio in uscita),
   mute controllato da UI Discord o dal pulsante; mapping invariato
   (`mute+deafen` → rosso) ✅

## 10. Progresso e riscontri hardware (Milestone B, 23/08/2026)

**Ambiente**: Python 3.12.8 · pywin32, psutil, pystray 0.19.5 presenti · `hidapi` 0.15.0 installato.

**Hardware verificato** (rischio §7.2 risolto):
- PID `246E` presente e risponde: config OK (pid 0x246E, firmware 106, 6 profili, 1 layer).
- Interfaccia vendor: MI_02, usage page `0xFF00`, usage `0x02`, report 64 byte (report ID 0).
- Trasporto Windows hidapi: in scrittura serve `[0x00] + 64 byte` (il report ID 0 viene
  rimosso da hidapi); in lettura il report ID viene già rimosso.
- Tasti fisici: **4 keycap con LED (indici 0-3) + knob senza LED (16=mute, 17=vol+, 18=vol-)**
  (consumer, stessa famiglia HCY-K006). Mappa verificata visivamente dall'utente:
  indice 0,1,2,3 = keycap 1,2,3,4. Il PID 246E non ha layout trascritto nel driver di
  riferimento (solo 246F) ma risponde allo stesso protocollo.
- Scrittura LED: comando `[0x06,0x14,3,T(idx*3),0,0,0,r,g,b]` **funziona** (confermato
  visivamente su tutti i 4 keycap).
- **Quirk timing (importante)**: il device processa le scritture per-key **in asincrono**;
  scritture ravvicinate vengono **droppate**. Serve ~250ms tra una scrittura e la
  successiva (testato: scrittura burst [0,1,2] lasciava spento il keycap 2). Il bridge
  deve accodare e spaziare le scritture.
- **Read-back `[0x06,0x13,58,T(off)]` INAFFIDABILE**: restituisce dati spuri su indici
  dispari (valori che sembrano chiavi della keymap). Da NON usare come verifica; la
  conferma è visiva.
- Modalità luce corrente del device: **mode 3 (Press-lit)**, brightness 4, color 1, h=238 s=97 v=79.

**File**: `keyboard_test.py` (test/probe + backup/ripristino LED), `led_backup.json` (stato luce
originale per `--restore`).

**Uso**:
- `python keyboard_test.py --probe-only` — sola lettura
- `python keyboard_test.py --keep --color "#00F0FF"` — scrive e lascia accesi i 3 LED
- `python keyboard_test.py --restore` — ripristina modalità luce + colori dal backup

## 11. Progresso Milestone A (RPC Discord, 23/08/2026)

- **Trasporto pipe validato**: `\\.\pipe\discord-ipc-0` presente (Discord in esecuzione);
  handshake inviato e risposta strutturata ricevuta (con client_id finto → CLOSE "Invalid
  Client ID", atteso). Framing `[op le32 | len le32 | json]` corretto.
- **File**: `discord_rpc.py` (client pipe: connect/handshake/authenticate/voice settings/
  subscribe/listen, refresh token, gestione errori), `discord_test.py` (CLI).
- **OAuth**: flusso implementato e testato fino alla cattura del redirect (server locale
  su `http://localhost:53123`, capture del `code` verificata). Credenziali in
  `%LOCALAPPDATA%\DiscordLedBridge\credentials.json` con ACL ristrette (icacls).
- **BLOCCATO su input utente**: servono Application ID + Client Secret dell'app Discord
  (discord.com/developers, redirect URI `http://localhost:53123`, scopes `rpc rpc.voice.read`),
  poi `python discord_test.py --setup --client-id ... --client-secret ...` e il consenso
  una tantum dentro Discord.

## 12. Progresso Milestone C/D (bridge, 23/08/2026)

**Layout LED definitivo — rev. 2 (23/08/2026, decisione utente: approccio A)**:

| Stato | Keycap 1 (idx 0) | Keycap 2 (idx 1) | Keycap 3-4 (idx 2,3) |
|---|---|---|---|
| Tutto ok (mic vivo) | blu `#00F0FF` | blu `#00F0FF` | spenti |
| Mic muto | giallo `#FFFF00` | giallo `#FFFF00` | spenti |
| Audio mutato (deafen) | rosso `#FF0000` | rosso `#FF0000` | spenti |

Stato globale sul gruppo 1+2 (stesso colore su entrambi). La rev. 1 (per-funzione
acceso/spento: keycap 1 = mic, keycap 2 = deafen) e' stata sostituita perché il mute del
mic lasciava entrambi i keycap spenti (tastiera "morta") e il global stato non era leggibile.

**File**:
- `keyboard_led.py` — controller LED con **thread worker** che spazia le scritture
  (`led_gap_seconds`, default 0.25s) e applica solo i colori cambiati; salva la modalità
  luce all'avvio e la ripristina in `close()`. Riapertura automatica su plug/unplug.
- `discord_monitor.py` — thread che connette/autentica/legge stato/sottoscrive
  `VOICE_SETTINGS_UPDATE`, con refresh token su 4004/4005/4010 e riconnessione con
  backoff esponenziale (2s→30s). Senza credenziali: log chiaro + retry.
- `app.py` — coordinatore: config `%LOCALAPPDATA%\DiscordLedBridge\config.json`
  (auto-creato se assente), tray icon opzionale (pystray+Pillow), stato iniziale
  predefinito (mic vivo) applicato all'avvio, ripristino luce a fine sessione.
- `README.md` — istruzioni complete (setup OAuth, avvio, layout, note tecniche).

**Verifiche eseguite**:
- Stato default applicato e confermato visivamente (keycap 1 blu, 2-3-4 spenti) ✓
- Ripristino luce alla chiusura: mode 3 → Custom → mode 3 ✓
- Senza credenziali l'app parte, applica il default LED e ritenta Discord con backoff ✓
- `led_backup.json` riallineato allo stato originale (mode 3 + colori factory)

## 13. Milestone A completata (23/08/2026) — OAuth + end-to-end

- OAuth completato: app Discord creata, scopes `rpc rpc.voice.read`, consenso dato,
  credenziali in `%LOCALAPPDATA%\DiscordLedBridge\credentials.json` (ACL ristrette).
- Fix applicati durante il setup:
  - browser aperto con `os.startfile` (il `cmd /c start` spezzava l'URL sui `&`)
  - scope corretto `rpc rpc.voice.read` (senza `rpc` base → `invalid_scope`)
  - User-Agent esplicito sul token endpoint (urllib "Python-urllib" → HTTP 403)
  - ripristinato `import subprocess` per `_restrict_acl`
- RPC: aggiunto `recv_frame(timeout)` (poll `PeekNamedPipe`) per ascolto interrompibile.
- Test end-to-end OK: `app.py` si connette, autentica, legge `VOICE_SETTINGS`
  (`mute=True, deaf=False` reale) e applica i LED (keycap 1 spento = mic muto).
- Resta il **collaudo finale interattivo**: toggle live mute/deafen, riavvio Discord,
  plug/unplug della tastiera.