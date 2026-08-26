# DiscordLedBridge

Mirrors your Discord voice state (mic muted / headphones muted) onto the per-key
LEDs of a **SIDE-KEYBOARD** macropad (SDINNOVATION, VID `0816` / PID `246E`,
4 keycaps with LEDs + a rotary knob without LEDs).

## How it works

A minimal background daemon reads `mute`/`deafen` from Discord's local RPC
(named pipe `discord-ipc-0`, OAuth scopes `rpc rpc.voice.read`) and colors the
keycap group 1+2:

| State | Keycap 1 | Keycap 2 |
|---|---|---|
| All good (mic live) | blue | blue |
| Mic muted | yellow | yellow |
| Audio muted (deafen) | red | red |

Keycaps 3-4 stay off. Everything is configurable via `config.json`.

## Requirements

- **Windows** (uses pywin32 named pipes and HID)
- Python 3.12 with: `hidapi`, `pywin32`, `pystray`, `Pillow`
- Discord desktop running

```powershell
python -m pip install -r requirements.txt
```

## OAuth setup (one-time)

1. Create an app at <https://discord.com/developers> (New Application).
2. In **OAuth2** → **Redirects**, add `http://localhost:53123`.
3. Run the one-time authorization:

```powershell
python src\discord_test.py --setup --client-id <ApplicationID> --client-secret <ClientSecret>
```

   A consent window opens inside Discord (click Authorize). Credentials are saved
   to `%LOCALAPPDATA%\DiscordLedBridge\credentials.json` with ACLs restricted to
   the current user only. The secret is never stored in the repo.

4. Verify the voice state can be read:

```powershell
python src\discord_test.py
```

   It prints `VOICE_SETTINGS -> {mute, deaf}` and stays listening for changes.

## Running the bridge

```powershell
python src\app.py
```

The bridge switches the pad to Custom lighting, colors the keycaps and keeps
listening. Optional tray icon (disable it in `config.json` or with `--no-tray`).
On exit (tray menu "Quit" or Ctrl+C) it restores the previous lighting mode.

## Autostart with Windows

A scheduled task starts the bridge at logon (no console window, `pythonw.exe`),
with automatic retry on failure:

```powershell
# create (done on this machine)
Register-ScheduledTask -TaskName "DiscordLedBridge" `
  -Action (New-ScheduledTaskAction -Execute "C:\Python312\pythonw.exe" `
    -Argument "D:\projects\side-keyboard-project\src\app.py" `
    -WorkingDirectory "D:\projects\side-keyboard-project") `
  -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME) `
  -Settings (New-ScheduledTaskSettingsSet -RestartCount 5 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero))

# disable autostart
Unregister-ScheduledTask -TaskName "DiscordLedBridge"
```

When launched this way, logs go to
`%LOCALAPPDATA%\DiscordLedBridge\app.log` (no console window).

## Configuration

`config.json` lives in the project folder (auto-created on first run):

```json
{
  "tray_icon": true,
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

- `group_keys`: keycap indices that show the global status.
- `colors`: color for each state (`ok`, `mic_muted`, `deafened`).
- `idle_keys`: indices kept off.
- `tray_icon`: show a tray icon that mirrors the LED state.

All LEDs are written together with a single **bulk RGB report**, so the keycaps
switch state at the same time instead of one after another.

### Hot reload

`config.json` is watched while the app runs: save it and `colors`, `group_keys`,
`idle_keys` and `mode` are re-applied automatically (check the
`[config] reloaded` log). Invalid JSON is ignored (the previous settings stay
active) and the app keeps running. Toggling `tray_icon` still requires a restart.

## Testing tools

```powershell
# read-only dump of the pad state (no writes)
python src\keyboard_test.py --probe-only

# color keycaps 0,1,2 and leave them on
python src\keyboard_test.py --keep --color "#00F0FF"

# write keycaps 0-3 with a single bulk report and leave them on
python src\keyboard_test.py --bulk --color "#00F0FF"

# restore the previous lighting mode (from led_backup.json)
python src\keyboard_test.py --restore
```

## Technical notes

- The pad has 4 keycaps with LEDs (indices 0-3) plus a knob without LEDs
  (indices 16=mute, 17=vol+, 18=vol-). Indices 4-5 are writable but have no
  physical LEDs.
- Protocol reverse-engineered from `parsaj-dev/sdcx-keypad` (MIT): vendor
  interface, usage page `0xFF00`, 64-byte reports.
- On Windows, HID writes need the report-ID 0 prefix (`[0x00] + 64 bytes`).
- The device drops isolated per-key writes, so the bridge uses the **bulk RGB
  command (`[18]`)**: one report carries every key's color and all LEDs change
  together, then the group is re-affirmed ~1s later.
- The per-key color read-back (`[19]`) is unreliable on odd indices: the correct
  verification is visual.

## Security

- `client_secret` and tokens live only in `credentials.json` (user-restricted
  ACLs), never in the repo.
- The bridge only writes to the vendor interface of the local device.

## License

MIT. The device protocol is reverse-engineered from
[`parsaj-dev/sdcx-keypad`](https://github.com/parsaj-dev/sdcx-keypad) (MIT); no
code is copied, only its published protocol and layout references are used.
