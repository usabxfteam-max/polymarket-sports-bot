"""
Rainbet vs Polymarket odds difference alert bot for Telegram.

This bot uses Odds-API.io to fetch sports moneyline markets from Rainbet and
Polymarket. Polymarket is treated as the oracle price. When Rainbet's implied
probability differs from Polymarket by the configured threshold, the bot sends
a Telegram alert.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

BASE_DIR = Path(__file__).parent.resolve()
CONFIG_PATH = BASE_DIR / "config.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("rainbet_polymarket_bot")

ODDS_API = "https://api.odds-api.io/v3"
POLYMARKET_BOOK = "Polymarket"
RAINBET_BOOK = "Rainbet"

SPORTS_CONFIG: dict[str, dict[str, Any]] = {
    "nba": {
        "api_slug": "basketball",
        "league_keywords": ["nba"],
        "display_name": "NBA",
    },
    "cba": {
        "api_slug": "basketball",
        "league_keywords": ["china", "chinese", "cba"],
        "display_name": "CBA",
    },
    "mlb": {
        "api_slug": "baseball",
        "league_keywords": ["mlb", "major league baseball"],
        "display_name": "MLB",
    },
    "nhl": {
        "api_slug": "ice-hockey",
        "league_keywords": ["nhl", "national hockey league"],
        "display_name": "NHL",
    },
}


def get_config_value(config: dict, path: list[str], env_name: str | None = None) -> Any:
    """Read a nested config value with an optional environment fallback."""
    value: Any = config
    for key in path:
        if not isinstance(value, dict) or key not in value:
            value = None
            break
        value = value[key]

    if env_name and (value is None or value == ""):
        value = os.getenv(env_name)
    return value


def american_to_decimal(odds: float) -> float:
    """Convert American odds to decimal odds."""
    return odds / 100 + 1 if odds > 0 else 100 / abs(odds) + 1


def normalize_to_decimal(raw_odds: float) -> float:
    """Odds-API.io can return either decimal or American odds."""
    if raw_odds == 0:
        return 0
    return american_to_decimal(raw_odds) if abs(raw_odds) > 100 or raw_odds < -100 else raw_odds


def implied_probability(decimal_odds: float) -> float:
    return 1 / decimal_odds if decimal_odds > 0 else 0


def format_odds(raw_odds: float, decimal_odds: float) -> str:
    if abs(raw_odds) > 100 or raw_odds < -100:
        return f"+{raw_odds:.0f}" if raw_odds > 0 else f"{raw_odds:.0f}"
    return f"{decimal_odds:.2f}"


def league_matches(league_name: str, keywords: list[str]) -> bool:
    """Match league tokens exactly so NBA does not also match WNBA."""
    if not keywords:
        return True

    normalized = league_name.lower()
    tokens = [token for token in re.split(r"[^a-z0-9]+", normalized) if token]

    for keyword in keywords:
        keyword = keyword.lower()
        if " " in keyword:
            if keyword in normalized:
                return True
        elif keyword in tokens:
            return True
    return False


class OddsApiClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.requests_made = 0
        self.rate_limit_remaining: int | None = None

    def get(self, endpoint: str, params: dict[str, str] | None = None) -> Any:
        query = {"apiKey": self.api_key}
        if params:
            query.update(params)

        response = requests.get(f"{ODDS_API}/{endpoint}", params=query, timeout=30)
        self.requests_made += 1

        remaining = response.headers.get("x-ratelimit-remaining")
        if remaining and remaining.isdigit():
            self.rate_limit_remaining = int(remaining)

        if response.status_code == 429:
            raise RuntimeError("Odds-API.io rate limit exceeded")
        response.raise_for_status()
        return response.json()

    def get_events(self, sport: str, status: str, bookmaker: str) -> list[dict]:
        result = self.get("events", {"sport": sport, "status": status, "bookmaker": bookmaker})
        return result if isinstance(result, list) else []

    def get_multi_odds(self, event_ids: list[int], bookmakers: list[str]) -> list[dict]:
        result = self.get(
            "odds/multi",
            {
                "eventIds": ",".join(str(event_id) for event_id in event_ids[:10]),
                "bookmakers": ",".join(bookmakers),
            },
        )
        return result if isinstance(result, list) else []


def parse_moneyline(bookmaker_markets: Any) -> dict[str, float] | None:
    """Return normalized home/away moneyline odds for one bookmaker."""
    if not isinstance(bookmaker_markets, list):
        return None

    for market in bookmaker_markets:
        if not isinstance(market, dict) or market.get("name") != "ML":
            continue

        odds_rows = market.get("odds")
        if not isinstance(odds_rows, list) or not odds_rows:
            continue

        odds = odds_rows[0]
        if not isinstance(odds, dict):
            continue

        home_raw = float(odds.get("home") or 0)
        away_raw = float(odds.get("away") or 0)
        home_decimal = normalize_to_decimal(home_raw)
        away_decimal = normalize_to_decimal(away_raw)

        if home_decimal > 1 and away_decimal > 1:
            return {
                "home": home_decimal,
                "away": away_decimal,
                "home_raw": home_raw,
                "away_raw": away_raw,
            }
    return None


def build_difference(
    team: str,
    side: str,
    polymarket_decimal: float,
    rainbet_decimal: float,
    polymarket_raw: float,
    rainbet_raw: float,
) -> dict[str, Any]:
    poly_prob = implied_probability(polymarket_decimal)
    rainbet_prob = implied_probability(rainbet_decimal)
    probability_diff = rainbet_prob - poly_prob

    return {
        "team": team,
        "side": side,
        "polymarket_probability": poly_prob,
        "rainbet_probability": rainbet_prob,
        "probability_difference": probability_diff,
        "absolute_difference": abs(probability_diff),
        "polymarket_decimal": polymarket_decimal,
        "rainbet_decimal": rainbet_decimal,
        "polymarket_raw": polymarket_raw,
        "rainbet_raw": rainbet_raw,
        "value_signal": "rainbet_value"
        if probability_diff < 0
        else "rainbet_expensive"
        if probability_diff > 0
        else "even",
    }


def analyze_event(odds_data: dict, threshold_pct: float) -> dict | None:
    bookmakers = odds_data.get("bookmakers")
    if not isinstance(bookmakers, dict):
        return None

    polymarket = parse_moneyline(bookmakers.get(POLYMARKET_BOOK))
    rainbet = parse_moneyline(bookmakers.get(RAINBET_BOOK))
    if not polymarket or not rainbet:
        return None

    home_team = str(odds_data.get("home") or "Unknown Home")
    away_team = str(odds_data.get("away") or "Unknown Away")
    league = odds_data.get("league") if isinstance(odds_data.get("league"), dict) else {}
    sport = odds_data.get("sport") if isinstance(odds_data.get("sport"), dict) else {}

    home_diff = build_difference(
        home_team,
        "home",
        polymarket["home"],
        rainbet["home"],
        polymarket["home_raw"],
        rainbet["home_raw"],
    )
    away_diff = build_difference(
        away_team,
        "away",
        polymarket["away"],
        rainbet["away"],
        polymarket["away_raw"],
        rainbet["away_raw"],
    )
    biggest = home_diff if home_diff["absolute_difference"] >= away_diff["absolute_difference"] else away_diff

    return {
        "home_team": home_team,
        "away_team": away_team,
        "league": league.get("name", "Unknown League"),
        "sport": sport.get("name", "Unknown Sport"),
        "status": odds_data.get("status", "unknown"),
        "date": odds_data.get("date"),
        "home_difference": home_diff,
        "away_difference": away_diff,
        "biggest_difference": biggest,
        "is_alert": biggest["absolute_difference"] * 100 >= threshold_pct,
    }


def scan_sport(client: OddsApiClient, sport_key: str, status: str, max_events: int, threshold_pct: float) -> list[dict]:
    config = SPORTS_CONFIG[sport_key]
    events = client.get_events(config["api_slug"], status, RAINBET_BOOK)
    filtered_events = [
        event
        for event in events
        if league_matches(str(event.get("league", {}).get("name", "")), config["league_keywords"])
    ][:max_events]

    event_ids = [int(event["id"]) for event in filtered_events if event.get("id")]
    logger.info("%s: %s Rainbet events to compare", config["display_name"], len(event_ids))

    analyses: list[dict] = []
    for index in range(0, len(event_ids), 10):
        batch = event_ids[index:index + 10]
        odds_list = client.get_multi_odds(batch, [POLYMARKET_BOOK, RAINBET_BOOK])
        for odds_data in odds_list:
            if isinstance(odds_data, dict):
                analysis = analyze_event(odds_data, threshold_pct)
                if analysis:
                    analyses.append(analysis)

    return analyses


def send_telegram_message(bot_token: str, chat_id: str, text: str) -> bool:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    try:
        response = requests.post(url, json=payload, timeout=15)
        response.raise_for_status()
        return True
    except requests.RequestException as exc:
        logger.error("Failed to send Telegram message: %s", exc)
        return False


def escape_markdown(text: str) -> str:
    """Escape enough Markdown syntax for Telegram legacy Markdown mode."""
    return str(text).replace("_", "\\_").replace("*", "\\*").replace("`", "\\`").replace("[", "\\[")


def format_difference_line(diff: dict) -> str:
    sign = "+" if diff["probability_difference"] >= 0 else ""
    label = "Rainbet higher" if diff["probability_difference"] > 0 else "Rainbet value"
    return (
        f"*{escape_markdown(diff['team'])}* ({diff['side']})\n"
        f"Polymarket: `{diff['polymarket_probability'] * 100:.1f}%` "
        f"({format_odds(diff['polymarket_raw'], diff['polymarket_decimal'])})\n"
        f"Rainbet: `{diff['rainbet_probability'] * 100:.1f}%` "
        f"({format_odds(diff['rainbet_raw'], diff['rainbet_decimal'])})\n"
        f"Diff: *{sign}{diff['probability_difference'] * 100:.1f}%* - {label}"
    )


def format_alert(alert: dict) -> str:
    biggest = alert["biggest_difference"]
    date = alert.get("date") or "unknown time"
    return (
        "*Rainbet / Polymarket Odds Difference*\n"
        f"_{escape_markdown(alert['league'])}_\n"
        f"`{date}`\n\n"
        f"*{escape_markdown(alert['home_team'])} vs {escape_markdown(alert['away_team'])}*\n"
        f"Largest gap: *{escape_markdown(biggest['team'])}* "
        f"`{biggest['probability_difference'] * 100:+.1f}%`\n\n"
        f"{format_difference_line(alert['home_difference'])}\n\n"
        f"{format_difference_line(alert['away_difference'])}"
    )


def load_config() -> dict:
    config = {
        "telegram": {},
        "odds_api": {},
        "scanner": {
            "interval_minutes": 10,
            "difference_threshold_pct": 0,
            "alert_rainbet_value_only": False,
            "status": "pending,live",
            "max_events_per_sport": 30,
            "sports": ["nba", "nhl", "mlb"],
        },
    }
    if CONFIG_PATH.exists():
        try:
            file_config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            for section in ("telegram", "odds_api", "scanner"):
                config[section].update(file_config.get(section, {}))
        except json.JSONDecodeError as exc:
            logger.error("Invalid config.json: %s", exc)
            sys.exit(1)

    api_key = os.getenv("ODDS_API_KEY") or get_config_value(config, ["odds_api", "api_key"])
    if not api_key or str(api_key).startswith("YOUR_"):
        logger.error("Set odds_api.api_key in config.json or ODDS_API_KEY in your environment")
        sys.exit(1)

    telegram = config.get("telegram", {})
    telegram["bot_token"] = os.getenv("TELEGRAM_BOT_TOKEN") or telegram.get("bot_token", "")
    telegram["chat_id"] = os.getenv("TELEGRAM_CHAT_ID") or telegram.get("chat_id", "")
    if not telegram["bot_token"] or telegram["bot_token"].startswith("YOUR_"):
        logger.error("Set telegram.bot_token in config.json or TELEGRAM_BOT_TOKEN in your environment")
        sys.exit(1)
    if not telegram["chat_id"] or telegram["chat_id"].startswith("YOUR_"):
        logger.error("Set telegram.chat_id in config.json or TELEGRAM_CHAT_ID in your environment")
        sys.exit(1)

    return config


def run_scan_cycle(config: dict, send_alerts: bool = True) -> list[dict]:
    scanner = config["scanner"]
    api_key = get_config_value(config, ["odds_api", "api_key"], "ODDS_API_KEY")
    sports = scanner.get("sports", ["nba", "nhl", "mlb"])
    status = scanner.get("status", "pending,live")
    threshold_pct = float(scanner.get("difference_threshold_pct", 0))
    max_events = int(scanner.get("max_events_per_sport", 30))
    value_only = bool(scanner.get("alert_rainbet_value_only", False))

    client = OddsApiClient(api_key)
    all_results: list[dict] = []

    logger.info("--- Starting Rainbet vs Polymarket scan ---")
    for sport in sports:
        if sport not in SPORTS_CONFIG:
            logger.warning("Skipping unsupported sport key: %s", sport)
            continue
        all_results.extend(scan_sport(client, sport, status, max_events, threshold_pct))

    alerts = [result for result in all_results if result["is_alert"]]
    if value_only:
        alerts = [
            alert
            for alert in alerts
            if alert["biggest_difference"]["value_signal"] == "rainbet_value"
        ]

    alerts.sort(key=lambda alert: alert["biggest_difference"]["absolute_difference"], reverse=True)
    logger.info(
        "Compared %s matched games, found %s alert(s). API calls: %s, remaining: %s",
        len(all_results),
        len(alerts),
        client.requests_made,
        client.rate_limit_remaining,
    )

    if send_alerts and alerts:
        telegram = config["telegram"]
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        header = (
            f"*Rainbet Odds Scanner* ({now})\n"
            f"Oracle: `{POLYMARKET_BOOK}`\n"
            f"Compared book: `{RAINBET_BOOK}`\n"
            f"Alerts: *{len(alerts)}* / matched games: `{len(all_results)}`"
        )
        send_telegram_message(telegram["bot_token"], telegram["chat_id"], header)

        for alert in alerts:
            send_telegram_message(telegram["bot_token"], telegram["chat_id"], format_alert(alert))
            time.sleep(0.2)
    elif not alerts:
        logger.info("No Rainbet/Polymarket differences met the configured threshold.")

    return alerts


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare Rainbet odds against Polymarket and alert on Telegram.")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single scan and exit. Intended for GitHub Actions scheduling.",
    )
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("  Rainbet vs Polymarket Sports Alert Bot")
    logger.info("=" * 60)

    config = load_config()
    interval_min = float(config["scanner"].get("interval_minutes", 10))

    if args.once:
        run_scan_cycle(config)
        return

    send_telegram_message(
        config["telegram"]["bot_token"],
        config["telegram"]["chat_id"],
        "*Rainbet Odds Bot Started*\n"
        f"Comparing `{RAINBET_BOOK}` against `{POLYMARKET_BOOK}` every {interval_min:g} minutes.",
    )

    while True:
        try:
            run_scan_cycle(config)
        except Exception as exc:
            logger.error("Error during scan cycle: %s", exc, exc_info=True)
            send_telegram_message(
                config["telegram"]["bot_token"],
                config["telegram"]["chat_id"],
                f"*Bot Error*\n`{escape_markdown(str(exc)[:500])}`",
            )

        logger.info("Sleeping for %s minutes...", interval_min)
        try:
            time.sleep(interval_min * 60)
        except KeyboardInterrupt:
            logger.info("Bot stopped by user.")
            send_telegram_message(
                config["telegram"]["bot_token"],
                config["telegram"]["chat_id"],
                "*Rainbet Odds Bot Stopped*",
            )
            break


if __name__ == "__main__":
    main()
