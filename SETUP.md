# Shuvo Hosting Bot — VPS Setup & Deployment Guide

## Ubuntu 24.04 Fresh VPS Setup

### Step 1 — System Update

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3.12 python3.12-venv python3-pip git unzip curl
```

### Step 2 — Create Dedicated User

```bash
sudo useradd -m -s /bin/bash shuvo
sudo usermod -aG sudo shuvo
```

### Step 3 — Create Directory Structure

```bash
sudo mkdir -p /opt/shuvo-hosting/{data,storage/bots}
sudo chown -R shuvo:shuvo /opt/shuvo-hosting
```

### Step 4 — Upload Project Files

```bash
# Option A: via git
sudo -u shuvo git clone <your-repo> /opt/shuvo-hosting

# Option B: via scp from local machine
scp -r ./shuvo-hosting/ user@your-vps-ip:/opt/shuvo-hosting/
sudo chown -R shuvo:shuvo /opt/shuvo-hosting
```

### Step 5 — Python Virtual Environment

```bash
sudo -u shuvo bash -c "
  cd /opt/shuvo-hosting
  python3.12 -m venv venv
  venv/bin/pip install --upgrade pip
  venv/bin/pip install -r requirements.txt
"
```

### Step 6 — Generate Fernet Key

```bash
sudo -u shuvo /opt/shuvo-hosting/venv/bin/python -c \
  "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Copy the output — this is your `MASTER_KEY`.

### Step 7 — Configure .env

```bash
sudo -u shuvo cp /opt/shuvo-hosting/.env.example /opt/shuvo-hosting/.env
sudo -u shuvo nano /opt/shuvo-hosting/.env
```

Fill in:
- `BOT_TOKEN` — your @BotFather token
- `ADMIN_IDS` — your Telegram user ID
- `MASTER_KEY` — from Step 6

To find your Telegram ID, message @userinfobot.

### Step 8 — Test Run

```bash
sudo -u shuvo /opt/shuvo-hosting/venv/bin/python /opt/shuvo-hosting/bot.py
```

Send `/start` to your bot. If the welcome message appears, press Ctrl+C and continue.

### Step 9 — Install systemd Service

```bash
sudo cp /opt/shuvo-hosting/shuvo-hosting.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable shuvo-hosting
sudo systemctl start shuvo-hosting
```

### Step 10 — Verify

```bash
sudo systemctl status shuvo-hosting
sudo journalctl -u shuvo-hosting -f
```

---

## Common Commands

```bash
# Start / Stop / Restart
sudo systemctl start   shuvo-hosting
sudo systemctl stop    shuvo-hosting
sudo systemctl restart shuvo-hosting

# View live logs
sudo journalctl -u shuvo-hosting -f

# View last 100 lines
sudo journalctl -u shuvo-hosting -n 100

# Check status
sudo systemctl status shuvo-hosting
```

---

## Directory Structure After Deployment

```
/opt/shuvo-hosting/
├── bot.py
├── config.py
├── database.py
├── requirements.txt
├── .env                  ← your secrets (never share)
├── .env.example
├── venv/                 ← Python virtual environment
├── data/
│   ├── hosting.db        ← SQLite database
│   └── manager.log       ← Manager logs
├── storage/
│   └── bots/
│       ├── my_bot_001/
│       │   ├── bot.py
│       │   ├── requirements.txt
│       │   ├── .env
│       │   ├── venv/
│       │   ├── logs/
│       │   │   ├── stdout.log
│       │   │   └── stderr.log
│       │   └── versions/
│       └── payment_bot/
│           └── ...
├── handlers/
├── services/
├── keyboards/
└── utils/
```

---

## Troubleshooting

### Bot doesn't respond
```bash
# Check if service is running
sudo systemctl status shuvo-hosting

# Check for Python errors
sudo journalctl -u shuvo-hosting -n 50
```

### Permission errors
```bash
sudo chown -R shuvo:shuvo /opt/shuvo-hosting
sudo chmod -R 755 /opt/shuvo-hosting
```

### Database issues
```bash
# Reset database (WARNING: loses all data)
sudo -u shuvo rm /opt/shuvo-hosting/data/hosting.db
sudo systemctl restart shuvo-hosting
```

### Hosted bot won't start
1. Open the bot via `🤖 My Bots`
2. Check `📜 Logs` for errors
3. Verify `⚙️ Env Vars` — especially `BOT_TOKEN`
4. Make sure entry file is correct

### Module not found errors in hosted bots
The hosted bot needs its own `requirements.txt`.
Upload a new ZIP with `requirements.txt` included.
The hosting manager will auto-install it.

### Can't receive files larger than 20MB
Telegram limits file downloads to 20MB for bots on the free API.
For larger files, use `@BotFather` to request a higher limit, or
split your project into smaller ZIPs.

---

## Security Notes

- Never share your `.env` file
- The `MASTER_KEY` is used to encrypt all bot tokens in the database
- If `MASTER_KEY` is lost, encrypted tokens cannot be recovered
- Admin IDs are validated on every request — no bypass possible
- ZIP uploads are checked for path traversal before extraction
- Hosted bots run as the same user (`shuvo`) — consider separate users for production

---

## Upgrading

```bash
# Stop service
sudo systemctl stop shuvo-hosting

# Backup database
sudo -u shuvo cp /opt/shuvo-hosting/data/hosting.db \
    /opt/shuvo-hosting/data/hosting.db.bak

# Pull new code
sudo -u shuvo git -C /opt/shuvo-hosting pull

# Update dependencies
sudo -u shuvo /opt/shuvo-hosting/venv/bin/pip install -r \
    /opt/shuvo-hosting/requirements.txt

# Start service
sudo systemctl start shuvo-hosting
```
