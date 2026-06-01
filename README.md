# Rainbet vs Polymarket Sports Alert Bot

A Telegram bot that compares **Rainbet** moneyline odds against **Polymarket** prices through [Odds-API.io](https://docs.odds-api.io/).

Polymarket is treated as the trusted oracle. Rainbet is the betting venue. The bot sends a Telegram signal only when Rainbet offers a cheaper implied probability than Polymarket for the same outcome.

## How It Works

1. Fetches active or upcoming Rainbet events for the configured leagues through Odds-API.io
2. Pulls batched odds for `Polymarket,Rainbet`; events without both prices are ignored
3. Compares moneyline implied probability for home and away outcomes
4. Sends Telegram alerts only for candidate Rainbet bets priced better than the Polymarket oracle
5. Sends a follow-up only when the compared odds change
6. Repeats every configured interval

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
        "compared_book": "Rainbet",
        "difference_threshold_pct": 0,
        "alert_value_only": true,
        "status": "pending,live",
        "max_events_per_sport": 50,
        "max_alerts_per_scan": 20,
        "sports": ["nba", "wnba", "mlb", "nhl", "nfl"]
    }
}
```

You can also set the key with `ODDS_API_KEY` instead of placing it in `config.json`.

The Telegram values can also be supplied with `TELEGRAM_BOT_TOKEN` and
`TELEGRAM_CHAT_ID`.

## Configuration

| Setting | Description |
|---------|-------------|
| `compared_book` | Betting venue to compare against Polymarket. Keep this as `Rainbet` for the live GitHub workflow. |
| `difference_threshold_pct` | Minimum probability gap in percentage points. `0` alerts on any difference; `2` alerts on gaps of at least 2 points. |
| `alert_value_only` | Keep `true` to alert only when the candidate bet is priced better at Rainbet than at the Polymarket oracle. |
| `status` | Odds-API event status filter, usually `pending,live`. |
| `max_events_per_sport` | Maximum sportsbook events per configured league to submit for Polymarket comparison per scan. |
| `max_alerts_per_scan` | Caps Telegram detail messages per run; all matched events are still checked and ranked. |
| `sports` | Default major-league keys: `nba`, `wnba`, `mlb`, `nhl`, `nfl`. Optional broad keys include `basketball`, `baseball`, `ice_hockey`, `american_football`, `football`, `tennis`, `esports`. |

## Run

```bash
python bot.py
```

For a single scan, suitable for scheduled hosting:

```bash
python bot.py --once
```

## Run On GitHub Actions

The included Rainbet workflow at `.github/workflows/rainbet-alerts.yml` runs one scan every ten minutes.
It caches the last alerted odds so an unchanged difference is not resent on
each scheduled run.

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
Rainbet Betting Opportunity
Trusted oracle: Polymarket | Bet at: Rainbet
USA - NBA
2026-05-26T00:00:00Z

Cleveland Cavaliers vs New York Knicks
Candidate Rainbet bet: Cleveland Cavaliers -3.1%

Cleveland Cavaliers (home)
Polymarket: 48.1% (2.08)
Rainbet: 45.0% (2.22)
Diff: -3.1% - Rainbet value
```

## Notes

- Only moneyline (`ML`) markets are compared.
- Three-way football/soccer moneylines include draw outcomes.
- Polymarket is used as the trusted reference price.
- Rainbet's lower implied probability than Polymarket means it offers the candidate betting opportunity.
- Alerts provide signals for betting at Rainbet; the bot does not submit wagers.
- A league such as MLB is tracked, but no comparison is possible during scans where Rainbet has no matching MLB event listed.
- Odds-API.io permits 100 requests per hour on the configured key. The default major-league scan is designed for the 10-minute workflow; enabling broad football, tennis, or esports coverage may exceed that limit.
