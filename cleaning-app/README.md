# Chore Points

A small self-hosted chore tracker for a household. People earn points for chores and spend them on rewards. It runs on your own Linux machine or VM and is used from Android phones as an installed web app.

- **Server:** a single `server.py`, standard-library Python 3.9+ only (nothing to `pip install`).
- **Database:** one SQLite file on the VM.
- **App:** a web app (PWA) the server hosts. Chrome on Android installs it like a normal app.

## Features

- Chores with point values and an optional "every N days" schedule. Due and overdue chores sort to the top.
- **I am …** picker at the top, remembered per phone, so **Done** credits the right person in one tap. 👥 credits someone else.
- **Scores:** a last-7-days leaderboard plus each person's spendable balance.
- **Rewards:** redeem points ("skip dishes once = 30"). The server rejects a redemption you can't afford.
- **History** with undo, plus an Undo button on every toast.
- Phones refresh every 30 s and whenever the app is reopened, so everyone sees the same numbers.
- **Manage** tab to add, edit and remove people, chores and rewards. Removing something keeps its history.
- One-tap **database backup** download.

## Try it locally

```bash
python3 server.py
# open http://localhost:8080
```

The database is created at `./data/chores.db`. You can change it with `DB_PATH=/some/file.db`, and the port with `PORT=9000`.

## Install on the Linux VM (systemd)

On the VM (Debian/Ubuntu shown; any distro with Python 3.9+ and systemd works):

```bash
sudo apt install -y python3 git
git clone <this repo> ~/nocode
sudo mkdir -p /opt/chore-points
sudo cp -r ~/nocode/cleaning-app/server.py ~/nocode/cleaning-app/public /opt/chore-points/
sudo useradd --system --no-create-home --shell /usr/sbin/nologin chores
sudo cp ~/nocode/cleaning-app/deploy/chore-points.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now chore-points
systemctl status chore-points        # should say "active (running)"
```

- The database lives at `/var/lib/chore-points/chores.db`.
- If the VM has a firewall: `sudo ufw allow 8080/tcp`.
- Find the VM's IP with `ip -4 addr` and open `http://<vm-ip>:8080` on your phone (same Wi-Fi).
- **Update** later: `git pull`, copy `server.py` and `public/` into `/opt/chore-points` again, then `sudo systemctl restart chore-points`. The phones pick up the new version on their next open.

### Or with Docker

```bash
cd cleaning-app
docker compose up -d --build         # data is stored in ./data/chores.db
```

## Installing on Android

Chrome only fully installs a web app (its own icon, full-screen, listed in the app drawer) from an **HTTPS** address. Over plain `http://192.168.x.x` it still works, but only as a home-screen shortcut. Pick one of these:

**Option A: Tailscale (recommended; also works away from home)**
1. Install [Tailscale](https://tailscale.com) on the VM and on each phone, signed in to the same account.
2. In the Tailscale admin console, turn on **MagicDNS** and **HTTPS certificates** (DNS page).
3. On the VM, run `sudo tailscale serve --bg 8080`.
4. On the phone, open the `https://<vm-name>.<tailnet>.ts.net` address it prints, then Chrome menu ⋮ → **Install app**.

**Option B: LAN only, no extra software**
1. On each phone, open `chrome://flags/#unsafely-treat-insecure-origin-as-secure` in Chrome.
2. Enable it, enter `http://<vm-ip>:8080`, and tap **Relaunch**.
3. Open `http://<vm-ip>:8080`, then ⋮ → **Install app**.

(Give the VM a fixed IP or a DHCP reservation in your router so the address doesn't change.)

**Option C: just a shortcut.** Open `http://<vm-ip>:8080`, then ⋮ → **Add to home screen**. It opens in a Chrome tab.

## Backups

- In the app: **Manage → Download backup** saves a `.db` file to the phone.
- On the VM: `sqlite3 /var/lib/chore-points/chores.db ".backup /path/to/backup.db"`. Put it in a daily cron job if you like.
- **Restore:** `sudo systemctl stop chore-points`, copy the backup over `/var/lib/chore-points/chores.db` (owned by `chores`), then start the service again.

## Security note

There are no logins. Anyone who can reach the address can use the app, which is fine on a home network or a Tailscale tailnet. **Don't port-forward it to the open internet.**
