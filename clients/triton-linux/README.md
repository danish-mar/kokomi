# Triton client (Linux)

Kokomi's remote hands on this machine. Pair it once with an 8-digit code, and
your Kokomi server can reach in to do allowlisted, read-only things — list a
folder, fetch a file. *Atlas plans, Triton acts.*

## Install & run

```bash
pip install requests          # default (poll) transport
python3 triton_client.py
```

On the same LAN as your Kokomi server, it auto-discovers the server and prints
an **8-digit pairing code**. Open **Kokomi → Settings → Triton**, find this
machine under *Discovered*, type the code, and hit **Pair**.

Point it at a specific server (required for a remote/hosted Kokomi):

```bash
python3 triton_client.py --server https://kokomi.example.com
# or:  export KOKOMI_SERVER=https://kokomi.example.com
```

## Transports

- **`poll` (default)** — plain HTTPS long-polling. Works through **nginx,
  Cloudflare, and any reverse proxy** with no special WebSocket config. Needs
  `pip install requests`. Use this for a hosted / proxied Kokomi.
- **`ws`** — a WebSocket; lowest latency, best on a LAN. Needs
  `pip install websockets`. Behind a proxy it requires `wss://` + WebSocket
  upgrade headers, so prefer `poll` unless you're on the local network.

```bash
python3 triton_client.py --transport ws --server ws://192.168.1.67:8780
```

## Restrict what it can touch

By default Triton may read anything under your home directory. Narrow it:

```bash
python3 triton_client.py --allow ~/Downloads --allow ~/Documents
```

Any request for a path outside the allowed folders is refused **on this machine**
— the server never gets to see it.

## Command execution (opt-in)

Reads are always on. **Running shell commands is off by default.** Turn it on with
`--allow-exec`, and — strongly recommended — restrict it to specific binaries:

```bash
# only these commands, only inside ~/projects
python3 triton_client.py --allow-exec \
  --allow-cmd git --allow-cmd ls --allow-cmd cat \
  --allow ~/projects
```

Rules enforced **on this machine** (the server can't override them):

- Without `--allow-exec`, any command request is refused.
- With `--allow-cmd` set, only those binaries run, and shell operators
  (`|`, `;`, `&`, `>`, `` ` ``, `$()`) are rejected so a command can't chain past
  the whitelist.
- Every command runs with its working directory pinned inside an `--allow` folder.
- Commands are capped at 300 s and their output at 100 KB per stream.

Passing `--allow-exec` **without** any `--allow-cmd` lets Kokomi run *any* command
as your user — convenient, but only do this on a machine you trust that trust to.
Env equivalents: `KOKOMI_ALLOW_EXEC=1`, `KOKOMI_ALLOW_CMD="git ls cat"`.

## Writing files (opt-in)

Off by default. Enable with `--allow-write`; writes are confined to the same
`--allow` folders (a path outside them is refused), parent folders are created
as needed, and content is capped at 25 MB.

```bash
python3 triton_client.py --allow-write --allow ~/notes
```

## Desktop actions (opt-in)

Off by default. Enable with `--allow-gui` to let Kokomi open a URL in your
browser, take a screenshot, and read/set the clipboard.

```bash
python3 triton_client.py --allow-gui
```

These use system tools already on most desktops (no extra Python packages):

- **Screenshot** — `grim` (Wayland) or `scrot` / `maim` / `gnome-screenshot` /
  `spectacle` / ImageMagick's `import` (X11).
- **Clipboard** — `wl-clipboard` (Wayland) or `xclip` / `xsel` (X11).

If none are installed you'll get a clear error naming what to install.

## Watching processes & services

Always on and **read-only** — Triton can *list* processes (`ps`) and systemd
services (`systemctl`) but can never kill, start, or stop anything.

## What it can do

Always on:
- `list_dir` — list a permitted folder
- `read_file` — fetch a file (≤ 25 MB) back into the chat
- `process_list` — snapshot running processes (list only)
- `service_list` — snapshot systemd service states (list only)

Opt-in (per flag):
- `run_command` — run an allowlisted shell command (`--allow-exec`)
- `write_file` — write inside a shared folder (`--allow-write`)
- `open_url` / `screenshot` / `clipboard_read` / `clipboard_write` (`--allow-gui`)

Env equivalents: `KOKOMI_ALLOW_EXEC`, `KOKOMI_ALLOW_CMD`, `KOKOMI_ALLOW_WRITE`,
`KOKOMI_ALLOW_GUI`.

## Where things live

- Device id + pairing token: `~/.config/kokomi-triton/state.json` (chmod 600)
- Revoke from the server (Settings → Triton → Revoke); the client will notice
  and stop. Re-run it to pair again with a fresh code.

## Run it as a service (optional)

```ini
# ~/.config/systemd/user/kokomi-triton.service
[Unit]
Description=Kokomi Triton client
After=network-online.target

[Service]
ExecStart=%h/.local/bin/python3 %h/path/to/triton_client.py --allow %h/Downloads
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now kokomi-triton
```
