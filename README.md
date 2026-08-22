# DiscordLedBridge

Riflette lo stato voce di Discord (mic muto / cuffie mute) sui LED dei keycap
della side-keyboard **SIDE-KEYBOARD** (SDINNOVATION, VID `0816` / PID `246E`,
4 keycap con LED + rotella senza LED).

## Stato del progetto

- Milestone B (LED): **fatta e verificata** su hardware.
- Milestone A (RPC Discord): trasporto validato; **in attesa del setup OAuth**
  (serve creare l'app Discord e dare il consenso una tantum).
- Milestone C (bridge `app.py`): **fatta**, testata la parte LED; la parte
  Discord si completa al setup OAuth.
- Milestone D (packaging): **avvio manuale** (niente autostart per ora).

## Requisiti

- Python 3.12 con: `hidapi`, `pywin32`, `psutil`, `pystray`, `Pillow`
- Discord desktop in esecuzione

```powershell
python -m pip install hidapi pystray Pillow   # pywin32 e psutil gia' presenti
```

## Setup OAuth (una tantum)

1. Crea un'app su <https://discord.com/developers> (New Application).
2. Tab **OAuth2** → **Redirects**: aggiungi `http://localhost:53123`.
3. Sotto **Scopes** aggiungi `rpc rpc.voice.read` (lo scope `rpc` e' obbligatorio).
4. Autorizzazione una tantum:

```powershell
python discord_test.py --setup --client-id <ApplicationID> --client-secret <ClientSecret>
```

   Si apre il consenso dentro Discord (Authorize). Le credenziali finiscono in
   `%LOCALAPPDATA%\DiscordLedBridge\credentials.json` con ACL ristrette al solo
   utente. Il secret non viene mai messo nel repo.

5. Prova la lettura dello stato:

```powershell
python discord_test.py
```

   Stampa `VOICE_SETTINGS -> {mute, deaf}` e resta in ascolto delle variazioni.

## Avvio del bridge

```powershell
python app.py
```

Il bridge imposta la modalita' luce Custom, colora i keycap e resta in ascolto.
Tray icon opzionale (disattivabile da `config.json` o con `--no-tray`). All'uscita
(menu tray "Esci" o Ctrl+C) ripristina la modalita' luce precedente.

## Layout LED (default)

**Stato globale** mostrato dal gruppo keycap 1+2 (stesso colore su entrambi),
keycap 3-4 spenti:

| Stato | Keycap 1 | Keycap 2 |
|---|---|---|
| Tutto ok (mic vivo) | blu | blu |
| Mic muto | giallo | giallo |
| Audio mutato (deafen) | rosso | rosso |

Configurabile in `config.json` (nella cartella del progetto, auto-creato al primo avvio):

```json
{
  "tray_icon": true,
  "led_gap_seconds": 0.25,
  "mode": "global",
  "group_keys": [0, 1],
  "colors": {
    "ok": "#00F0FF",
    "mic_muted": "#FFFF00",
    "deafened": "#FF0000"
  },
  "idle_keys": [2, 3]
}
```

- `group_keys`: indici dei keycap che mostrano lo stato globale.
- `colors`: colore per ogni stato (`ok`, `mic_muted`, `deafened`).
- `idle_keys`: indici tenuti spenti.

`led_gap_seconds` e' la pausa tra una scrittura LED e la successiva: il device
droppa le scritture ravvicinate, quindi non abbassarlo sotto ~0.2s.

## Strumenti di test

```powershell
# lettura sola dello stato della tastiera (non scrive)
python keyboard_test.py --probe-only

# colora i keycap 0,1,2 e li lascia accesi
python keyboard_test.py --keep --color "#00F0FF"

# ripristina la modalita' luce precedente (da led_backup.json)
python keyboard_test.py --restore
```

## Note tecniche

- Il device ha 4 keycap con LED (indici 0-3) + rotella senza LED (indici
  16=mute, 17=vol+, 18=vol-). Gli indici 4-5 sono scrivibili ma senza LED fisico.
- Protocollo SDCX/SDINNOVATION reverse-engineered da `parsaj-dev/sdcx-keypad`
  (MIT), interface vendor usage page `0xFF00`, report 64 byte.
- Su Windows le scritture HID richiedono il prefisso report-ID 0 (`[0x00] + 64B`).
- Il read-back della tabella colori (`[19]`) e' inaffidabile su indici dispari:
  la verifica corretta e' visiva.

## Sicurezza

- `client_secret` e token vivono solo in `credentials.json` (ACL utente),
  mai nel repo.
- Il bridge scrive solo sull'interfaccia vendor del device locale.

## Licenza

MIT. Il protocollo device e' reverse-engineered da
[`parsaj-dev/sdcx-keypad`](https://github.com/parsaj-dev/sdcx-keypad) (MIT), non copia
di codice: ne riusa i riferimenti pubblicati su protocollo e layout.