# Triton client (Linux)

Kokomi's remote hands on this machine. Pair it once with an 8-digit code, and
your Kokomi server can reach in to do allowlisted, read-only things — list a
folder, fetch a file. *Atlas plans, Triton acts.*

## Install & run

```bash
pip install websockets
python3 triton_client.py
```

On the same LAN as your Kokomi server, it auto-discovers the server and prints
an **8-digit pairing code**. Open **Kokomi → Settings → Triton**, find this
machine under *Discovered clients*, type the code, and hit **Pair**.

Not on the same network (or discovery blocked)? Point it explicitly:

```bash
python3 triton_client.py --server ws://YOUR_SERVER_IP:8000/api/triton/agent
# or:  export KOKOMI_SERVER=ws://YOUR_SERVER_IP:8000/api/triton/agent
```

## Restrict what it can touch

By default Triton may read anything under your home directory. Narrow it:

```bash
python3 triton_client.py --allow ~/Downloads --allow ~/Documents
```

Any request for a path outside the allowed folders is refused **on this machine**
— the server never gets to see it.

## What it can do (Phase 1)

- `list_dir` — list a permitted folder
- `read_file` — fetch a file (≤ 25 MB) back into the chat

Read-only. No writes, no shell, no automation yet — those come later, behind
explicit approval gates.

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
