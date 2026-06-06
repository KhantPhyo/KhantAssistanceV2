# Installation

This is a self-contained dev workflow. The `start.sh` / `start.bat` launchers do most of the
heavy lifting once the prerequisites are in place.

---

## Prerequisites

- **Python 3.11+** (3.12 also works)
- **Node.js 18+** (20 LTS recommended) with npm
- A **Telegram bot token** per bot you intend to bind (get from [@BotFather](https://t.me/BotFather))

---

## macOS

```bash
# 1. Tooling — Homebrew
brew install python@3.12 node

# 2. Verify
python3 --version    # >=3.11
node -v              # >=18

# 3. Run
cd /path/to/KhantAssistanceV2
./start.sh
```

---

## Linux

### Ubuntu / Debian
```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nodejs npm
./start.sh
```

### Fedora
```bash
sudo dnf install -y python3 python3-virtualenv nodejs npm
./start.sh
```

### Arch
```bash
sudo pacman -S --noconfirm python python-pip nodejs npm
./start.sh
```

If `npm` is too old (Vite 5 needs Node 18+), install via `nvm`:
```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
nvm install 20 && nvm use 20
```

---

## Windows

### PowerShell (native)

1. Install Python 3.12 from <https://www.python.org/downloads/windows/> — tick **"Add Python to PATH"**.
2. Install Node 20 from <https://nodejs.org/en/download/>.
3. Open a fresh PowerShell, `cd` to the repo, then double-click `start.bat` (or run it from the shell).
4. Two terminal windows open — one for backend (`uvicorn`), one for frontend (`vite`).

### WSL2 (recommended for Windows)

```powershell
wsl --install -d Ubuntu
# inside WSL:
sudo apt update && sudo apt install -y python3 python3-venv python3-pip nodejs npm
cd /mnt/c/path/to/KhantAssistanceV2
./start.sh
```

---

## First-run checklist

1. Open <http://localhost:5173> → log in with the seeded credentials
   `khantphyo.myanmar@gmail.com` / `Cisco@123`.
2. Go to **Admins → me → Edit** and set your **Telegram @username** (no `@`).
   This is required for admin-bot pairing.
3. Go to **Control Panel → Admin Bot tab** and paste your bot token from BotFather.
4. Click the bot link Telegram returns and send `/start` from the same Telegram account
   whose username you just registered. Pairing succeeds; bot prints the command list.
5. Go to **Assistants → Add Assistant**, fill in name + Telegram @username + bot token.
   Send `/start` from the assistant's account, press **Accept**.
6. Create a job from the Web UI or via the admin bot: `/newjob Test | hello | 2026-12-31 23:59 | @<assistant-username>`.

---

## Notion realtime sync (optional)

A Notion database **"Staff Job Reports (Live)"** has been auto-provisioned under
**Miin Admin - Report from Staff** at  
<https://www.notion.so/9298ed2e0fb841a1b72d0c0602a3d995>.

To start pushing live data:

1. Go to <https://www.notion.so/my-integrations> → **+ New integration** →
   *Internal* → workspace = your Miin workspace → copy the **secret token** (`secret_xxx…`).
2. Open the **Miin Admin - Report from Staff** Notion page → click **Share** at the top right →
   invite the integration you just created (with **Edit** access). The "Staff Job Reports (Live)"
   child database inherits permissions automatically.
3. Edit `backend/.env` and set:
   ```
   NOTION_TOKEN=secret_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   NOTION_JOBS_DATABASE_ID=9298ed2e0fb841a1b72d0c0602a3d995
   ```
4. Restart the backend (`Ctrl+C`, then `./start.sh`).
5. Open **Control Panel → Notion Sync** in the web UI — the badge should turn green
   (`● connected`). Press **Re-sync all jobs** to backfill any existing rows.
6. Every new event (create / accept / decline / finished / reassign / cancel / status change)
   pushes a row to the Notion database within ~1 second.

### Week filtering inside your existing month pages

The backend tags every row with `Month` (May, Jun, …) and `Week` (Week 1 – Week 5) based on the
job's **Created** date. To replicate your `May > Week 1, Week 2…` layout, in Notion:

1. Inside the **May** page, type `/linked` → choose **Linked view of database** →
   pick **Staff Job Reports (Live)**.
2. On that view: **Filter** → `Month is May` AND `Week is Week 1`. Save as view "Week 1".
3. **Duplicate** the view, change the Week filter to "Week 2", "Week 3", etc.
4. Optionally drag the views inside the existing `<details>` Week 1 / Week 2 toggles.

Total one-time setup ≈ 2 minutes per month. After that, rows flow into the right week automatically.

---

## Verification (acceptance)

| # | Test | Expected |
|---|------|----------|
| 1 | Login with seed credentials | Lands on `/` dashboard |
| 2 | Bind admin bot, `/start` from non-matching Telegram account | Bot replies "❌ ခွင့်မပြုပါ" |
| 3 | Bind admin bot, `/start` from matching Telegram account | Bot prints command list |
| 4 | Web admin creates job → admin bot inbox | "✅ Job <code> created..." |
| 5 | Admin bot `/newjob ... \| @alice` | Web dashboard updates via WebSocket |
| 6 | `/broadcast meeting 5pm` | All active assistants receive DM |
| 7 | Assistant taps ✅ Accept | Job moves to `in_progress`; admin bot is notified |
| 8 | `/finished JOB-xxxx` with **photo** when `report_type=video` | Bot rejects with "video လိုအပ်ပါသည်" |
| 9 | `/delete_admin @bob` from admin bot | Refused; audit log row `action=blocked_command` |
| 10 | `Ctrl+C` backend, restart with `./start.sh` | Both bots resume from DB |
| 11 | Disconnect Wi-Fi 2 s, reconnect | WebSocket reconnects within ~3 s |
| 12 | `sqlite3 backend/data/app.db "select token_enc from bots limit 1"` | Ciphertext, not raw token |
| 13 | `/pause` then assistant `/jobs` | Silent no-op + audit row |
| 14 | Admins page: create 2nd web admin, log in as them, attempt to delete the seeded admin | 403 (cannot delete the last web admin) |

---

## Troubleshooting

- **Port 8000/5173 already in use** — `start.sh` kills stale PIDs. On Windows, close the leftover terminal windows manually.
- **Bot token decryption error after deleting `.fernet_key`** — re-bind the bot in the UI; the old ciphertext is unrecoverable.
- **"Telegram username not set"** when pairing — visit `/admins` and edit your row.
- **WebSocket 4401** — token expired; just log out and back in.
- **`bcrypt` warning about version** — harmless, comes from `passlib` reading `bcrypt.__about__`.

---

## Production notes

- Replace `JWT_SECRET` and set a fixed `FERNET_KEY` in `.env`.
- Switch to Postgres via the bundled `docker-compose.yml` (point `DB_PATH` at it).
- Put the API behind a reverse proxy (nginx/Caddy) terminating TLS.
- Restrict `CORS_ORIGINS` to your production frontend origin.
- Back up `backend/data/` (DB + `.fernet_key` + `uploads/`).
