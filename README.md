# Rainbet vs Polymarket Sports Alert Bot

A Telegram bot that compares **Rainbet** moneyline odds against **Polymarket** prices through [Odds-API.io](https://docs.odds-api.io/).

Polymarket is treated as the oracle. Rainbet is the compared sportsbook. When the implied probability differs by your configured threshold, the bot sends a Telegram alert.

## How It Works

1. Fetches active or upcoming Rainbet events from Odds-API.io
2. Pulls batched odds for `Polymarket,Rainbet`
3. Compares moneyline implied probability for home and away outcomes
4. Sends Telegram alerts for differences that meet the threshold
5. Repeats every configured interval

## Setup

### 1. Install

```bash
git clone https://github.com/usabxfteam-max/polymarket-sports-bot.git
cd polymarket-sports-bot
pip install -r requirements.txt
```

### 2. Configure

```bash
cp config.json.example config.json
```

Edit `config.json`:

```json
{
    "telegram": {
        "bot_token": "123456:ABC-DEF...",
        "chat_id": "987654321"
    },
    "odds_api": {
        "api_key": "YOUR_ODDS_API_KEY"
    },
    "scanner": {
        "interval_minutes": 10,
        "difference_threshold_pct": 0,
        "alert_rainbet_value_only": false,
        "status": "pending,live",
        "max_events_per_sport": 30,
        "sports": ["nba", "nhl", "mlb"]
    }
}
```

You can also set the key with `ODDS_API_KEY` instead of placing it in `config.json`.

The Telegram values can also be supplied with `TELEGRAM_BOT_TOKEN` and
`TELEGRAM_CHAT_ID`.

## Configuration

| Setting | Description |
|---------|-------------|
| `difference_threshold_pct` | Minimum probability gap in percentage points. `0` alerts on any difference; `2` alerts on gaps of at least 2 points. |
| `alert_rainbet_value_only` | If `true`, only alert when Rainbet is cheaper than Polymarket's oracle probability. |
| `status` | Odds-API event status filter, usually `pending,live`. |
| `max_events_per_sport` | Maximum Rainbet events to compare per sport per scan. |
| `sports` | Supported keys: `nba`, `nhl`, `mlb`, `cba`. |

## Run

```bash
python bot.py
```

For a single scan, suitable for scheduled hosting:

```bash
python bot.py --once
```

## Run On GitHub Actions

The included workflow at `.github/workflows/rainbet-alerts.yml` runs one scan
every five minutes, the minimum scheduled interval supported by GitHub Actions.

In your GitHub repository, open **Settings > Secrets and variables > Actions**
and create these repository secrets:

| Secret | Value |
|--------|-------|
| `ODDS_API_KEY` | Your Odds-API.io API key |
| `TELEGRAM_BOT_TOKEN` | Your Telegram bot token |
| `TELEGRAM_CHAT_ID` | The chat ID to receive alerts |

After committing the workflow to the repository default branch, open the
**Actions** tab and run **Rainbet Odds Alerts** once with **Run workflow** to
confirm the secret setup. Scheduled runs then execute automatically.

GitHub scheduled jobs can be delayed during high load. On public repositories,
GitHub disables scheduled workflows after 60 days without repository activity.

## Example Telegram Alert

```text
Rainbet / Polymarket Odds Difference
USA - NBA
2026-05-26T00:00:00Z

Cleveland Cavaliers vs New York Knicks
Largest gap: Cleveland Cavaliers +3.1%

Cleveland Cavaliers (home)
Polymarket: 45.0% (2.22)
Rainbet: 48.1% (2.08)
Diff: +3.1% - Rainbet higher
```

## Notes

- Only moneyline (`ML`) markets are compared.
- Rainbet lower implied probability than Polymarket means Rainbet is offering a better price than the oracle.
- Rainbet higher implied probability than Polymarket means Rainbet is pricing that side more expensively than the oracle.
