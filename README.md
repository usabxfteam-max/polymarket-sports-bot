# Polymarket Sports Price Drop Alert Bot

A Telegram bot that monitors **sports prediction markets** on [Polymarket](https://polymarket.com) and sends you instant alerts when a major price drop happens.

## How It Works

1. Fetches active sports events from Polymarket's public API every N minutes
2. Filters to only real sports events (NBA, NHL, soccer, F1, tennis, esports, etc.)
3. Compares current prices to the previous scan
4. Sends a Telegram notification when any outcome drops by more than your threshold
5. Persists price state locally for next-cycle comparison

No Polymarket account or API key required — uses only public endpoints.

## Covered Sports

| Category | Includes |
|----------|----------|
| **Soccer** | FIFA World Cup, Champions League, EPL, La Liga, Serie A, Bundesliga, MLS, Copa America, club matchups |
| **Basketball** | NBA, WNBA, NCAA, March Madness |
| **Hockey** | NHL, Stanley Cup |
| **American Football** | NFL, Super Bowl, College Football |
| **Baseball** | MLB, World Series |
| **Motorsport** | F1, MotoGP, NASCAR, IndyCar |
| **Tennis** | Grand Slams, ATP, WTA, Davis Cup |
| **Golf** | PGA, LPGA, Masters, Ryder Cup |
| **Esports** | LoL, Counter-Strike, Dota 2, Valorant, Overwatch |
| **Combat Sports** | UFC, Boxing, MMA |

## Setup

### 1. Get a Telegram Bot Token

- Message **@BotFather** on Telegram
- Send `/newbot`, pick a name
- Copy your **bot token**

### 2. Get Your Chat ID

- Message **@userinfobot** on Telegram
- It will reply with your numeric **chat ID**

### 3. Install & Configure

```bash
git clone https://github.com/YOUR_USERNAME/polymarket-sports-bot.git
cd polymarket-sports-bot

# Install dependencies
pip install -r requirements.txt

# Create your config
cp config.json.example config.json
```

Edit `config.json` with your tokens:

```json
{
    "telegram": {
        "bot_token": "123456:ABC-DEF...",
        "chat_id": "987654321"
    },
    "scanner": {
        "interval_minutes": 10,
        "drop_threshold_pct": 5.0,
        "min_volume": 1000,
        "max_markets_to_scan": 100,
        "sports_filter": []
    }
}
```

### 4. Run

```bash
python bot.py
```

## Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `interval_minutes` | `10` | How often to scan for price drops |
| `drop_threshold_pct` | `5.0` | Minimum drop % to trigger an alert (e.g. `3.0` = more alerts, `10.0` = fewer) |
| `min_volume` | `1000` | Skip markets with 24h volume below this ($USD) |
| `max_markets_to_scan` | `100` | Max events to fetch per scan from the API |
| `sports_filter` | `[]` | Narrow to specific sports, e.g. `["nba", "nhl"]` or `["f1", "champion"]`. Leave empty for all sports. |

## Example Telegram Alert

```
Price Drop Alert
2026 NBA Champion

Market: Will the Boston Celtics win?
Outcome: Yes

Previous: 0.3200
Current: 0.2750
Drop: -14.1%
24h Volume: $3,856,548
```

## Running 24/7

### Screen (simple)

```bash
screen -S polybot
python bot.py
# Detach: Ctrl+A, D
# Reattach: screen -r polybot
```

### systemd (Linux server)

Create `/etc/systemd/system/polymarket-bot.service`:

```ini
[Unit]
Description=Polymarket Sports Alert Bot
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/path/to/polymarket-sports-bot
ExecStart=/usr/bin/python3 bot.py
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable polymarket-bot
sudo systemctl start polymarket-bot
```

## Tech Stack

- **Python 3.9+**
- **Polymarket Gamma API** (public, no auth) — event/market discovery
- **Polymarket CLOB API** (public, no auth) — price data
- **Telegram Bot API** — push notifications
- **requests** — HTTP client

## License

MIT
