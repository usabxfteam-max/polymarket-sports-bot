"""
Sportsbook vs Polymarket odds difference alert bot for Telegram.

This bot uses Odds-API.io to fetch sports moneyline markets from a configured
bookmaker and Polymarket. Polymarket is treated as the oracle price. When the
configured book's implied probability beats Polymarket by the configured
threshold, the bot sends a Telegram alert.
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
ALERT_STATE_PATH = BASE_DIR / os.getenv("ALERT_STATE_FILE", "alert_state.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("sportsbook_polymarket_bot")

ODDS_API = "https://api.odds-api.io/v3"
POLYMARKET_BOOK = "Polymarket"
DEFAULT_COMPARED_BOOK = "Rainbet"

SPORTS_CONFIG: dict[str, dict[str, Any]] = {
    "basketball": {
        "api_slug": "basketball",
        "league_keywords": [],
        "display_name": "Basketball (NBA, WNBA, international)",
    },
    "nba": {
        "api_slug": "basketball",
        "league_keywords": ["nba"],
        "display_name": "NBA",
    },
    "wnba": {
        "api_slug": "basketball",
        "league_keywords": ["wnba"],
        "display_name": "WNBA",
    },
    "cba": {
        "api_slug": "basketball",
        "league_keywords": ["china", "chinese", "cba"],
        "display_name": "CBA",
    },
    "baseball": {
        "api_slug": "baseball",
        "league_keywords": [],
        "display_name": "Baseball (including MLB)",
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
    "nfl": {
        "api_slug": "american-football",
        "league_keywords": ["nfl", "national football league"],
        "display_name": "NFL",
    },
    "ice_hockey": {
        "api_slug": "ice-hockey",
        "league_keywords": [],
        "display_name": "Ice Hockey",
    },
    "american_football": {
        "api_slug": "american-football",
        "league_keywords": [],
        "display_name": "American Football",
    },
    "football": {
        "api_slug": "football",
        "league_keywords": [],
        "display_name": "Football / Soccer",
    },
    "tennis": {
        "api_slug": "tennis",
        "league_keywords": [],
        "display_name": "Tennis",
    },
    "esports": {
        "api_slug": "esports",
        "league_keywords": [],
        "display_name": "Esports",
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
        self.events_cache: dict[tuple[str, str, str], list[dict]] = {}

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
        cache_key = (sport, status, bookmaker)
        if cache_key in self.events_cache:
            return self.events_cache[cache_key]
        result = self.get("events", {"sport": sport, "status": status, "bookmaker": bookmaker})
        events = result if isinstance(result, list) else []
        self.events_cache[cache_key] = events
        return events

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
    """Return normalized moneyline odds for one bookmaker."""
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
            parsed = {
                "home": home_decimal,
                "away": away_decimal,
                "home_raw": home_raw,
                "away_raw": away_raw,
            }
            draw_raw = float(odds.get("draw") or 0)
            draw_decimal = normalize_to_decimal(draw_raw)
            if draw_decimal > 1:
                parsed["draw"] = draw_decimal
                parsed["draw_raw"] = draw_raw
            return parsed
    return None


def build_difference(
    team: str,
    side: str,
    polymarket_decimal: float,
    compared_decimal: float,
    polymarket_raw: float,
    compared_raw: float,
) -> dict[str, Any]:
    poly_prob = implied_probability(polymarket_decimal)
    compared_prob = implied_probability(compared_decimal)
    probability_diff = compared_prob - poly_prob

    return {
        "team": team,
        "side": side,
        "polymarket_probability": poly_prob,
        "compared_probability": compared_prob,
        "probability_difference": probability_diff,
        "absolute_difference": abs(probability_diff),
        "polymarket_decimal": polymarket_decimal,
        "compared_decimal": compared_decimal,
        "polymarket_raw": polymarket_raw,
        "compared_raw": compared_raw,
        "value_signal": "compared_book_value"
        if probability_diff < 0
        else "compared_book_expensive"
        if probability_diff > 0
        else "even",
    }


def analyze_event(odds_data: dict, threshold_pct: float, compared_book: str) -> dict | None:
    bookmakers = odds_data.get("bookmakers")
    if not isinstance(bookmakers, dict):
        return None

    polymarket = parse_moneyline(bookmakers.get(POLYMARKET_BOOK))
    compared = parse_moneyline(bookmakers.get(compared_book))
    if not polymarket or not compared:
        return None

    home_team = str(odds_data.get("home") or "Unknown Home")
    away_team = str(odds_data.get("away") or "Unknown Away")
    league = odds_data.get("league") if isinstance(odds_data.get("league"), dict) else {}
    sport = odds_data.get("sport") if isinstance(odds_data.get("sport"), dict) else {}

    home_diff = build_difference(
        home_team,
        "home",
        polymarket["home"],
        compared["home"],
        polymarket["home_raw"],
        compared["home_raw"],
    )
    away_diff = build_difference(
        away_team,
        "away",
        polymarket["away"],
        compared["away"],
        polymarket["away_raw"],
        compared["away_raw"],
    )
    differences = [home_diff, away_diff]
    if polymarket.get("draw", 0) > 1 and compared.get("draw", 0) > 1:
        differences.append(
            build_difference(
                "Draw",
                "draw",
                polymarket["draw"],
                compared["draw"],
                polymarket["draw_raw"],
                compared["draw_raw"],
            )
        )
    biggest = max(differences, key=lambda difference: difference["absolute_difference"])

    return {
        "home_team": home_team,
        "away_team": away_team,
        "compared_book": compared_book,
        "league": league.get("name", "Unknown League"),
        "sport": sport.get("name", "Unknown Sport"),
        "status": odds_data.get("status", "unknown"),
        "date": odds_data.get("date"),
        "home_difference": home_diff,
        "away_difference": away_diff,
        "differences": differences,
        "biggest_difference": biggest,
        "is_alert": biggest["absolute_difference"] * 100 >= threshold_pct,
    }


def scan_sport(
    client: OddsApiClient,
    sport_key: str,
    status: str,
    max_events: int,
    threshold_pct: float,
    compared_book: str,
) -> list[dict]:
    config = SPORTS_CONFIG[sport_key]
    compared_events = client.get_events(config["api_slug"], status, compared_book)
    events_to_compare = [
        event
        for event in compared_events
        if league_matches(str(event.get("league", {}).get("name", "")), config["league_keywords"])
    ][:max_events]

    event_ids = [int(event["id"]) for event in events_to_compare if event.get("id")]
    logger.info(
        "%s: %s %s event(s) submitted for Polymarket comparison",
        config["display_name"],
        len(event_ids),
        compared_book,
    )

    analyses: list[dict] = []
    for index in range(0, len(event_ids), 10):
        batch = event_ids[index:index + 10]
        odds_list = client.get_multi_odds(batch, [POLYMARKET_BOOK, compared_book])
        for odds_data in odds_list:
            if isinstance(odds_data, dict):
                analysis = analyze_event(odds_data, threshold_pct, compared_book)
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


def format_difference_line(diff: dict, compared_book: str) -> str:
    sign = "+" if diff["probability_difference"] >= 0 else ""
    label = f"{compared_book} higher" if diff["probability_difference"] > 0 else f"{compared_book} value"
    return (
        f"*{escape_markdown(diff['team'])}* ({diff['side']})\n"
        f"Polymarket: `{diff['polymarket_probability'] * 100:.1f}%` "
        f"({format_odds(diff['polymarket_raw'], diff['polymarket_decimal'])})\n"
        f"{escape_markdown(compared_book)}: `{diff['compared_probability'] * 100:.1f}%` "
        f"({format_odds(diff['compared_raw'], diff['compared_decimal'])})\n"
        f"Diff: *{sign}{diff['probability_difference'] * 100:.1f}%* - {label}"
    )


def format_alert(alert: dict) -> str:
    biggest = alert["biggest_difference"]
    date = alert.get("date") or "unknown time"
    compared_book = alert["compared_book"]
    return (
        f"*{escape_markdown(compared_book)} Betting Opportunity*\n"
        f"Trusted oracle: `{POLYMARKET_BOOK}` | Bet at: `{escape_markdown(compared_book)}`\n"
        f"_{escape_markdown(alert['league'])}_\n"
        f"`{date}`\n\n"
        f"*{escape_markdown(alert['home_team'])} vs {escape_markdown(alert['away_team'])}*\n"
        f"Candidate {escape_markdown(compared_book)} bet: *{escape_markdown(biggest['team'])}* "
        f"`{biggest['probability_difference'] * 100:+.1f}%`\n\n" +
        "\n\n".join(format_difference_line(difference, compared_book) for difference in alert["differences"])
    )


def alert_key(alert: dict) -> str:
    """Create a stable ID for a compared game."""
    return "|".join(
        [
            str(alert.get("league", "")),
            str(alert.get("home_team", "")),
            str(alert.get("away_team", "")),
            str(alert.get("date", "")),
        ]
    )


def alert_signature(alert: dict) -> dict[str, float]:
    """Track displayed odds so unchanged alerts are not sent repeatedly."""
    signature: dict[str, float] = {}
    for difference in alert["differences"]:
        signature[f"poly_{difference['side']}"] = difference["polymarket_decimal"]
        signature[f"compared_{difference['side']}"] = difference["compared_decimal"]
    return signature


def load_alert_state() -> dict[str, dict[str, float]]:
    if not ALERT_STATE_PATH.exists():
        return {}
    try:
        data = json.loads(ALERT_STATE_PATH.read_text(encoding="utf-8"))
        return data.get("alerts", {}) if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        logger.warning("Could not parse alert state; starting a fresh alert baseline.")
        return {}


def save_alert_state(alerts: list[dict]) -> None:
    data = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "alerts": {alert_key(alert): alert_signature(alert) for alert in alerts},
    }
    ALERT_STATE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_config() -> dict:
    config = {
        "telegram": {},
        "odds_api": {},
        "scanner": {
            "interval_minutes": 10,
            "compared_book": DEFAULT_COMPARED_BOOK,
            "difference_threshold_pct": 0,
            "alert_value_only": True,
            "status": "pending,live",
            "max_events_per_sport": 50,
            "max_alerts_per_scan": 20,
            "sports": ["nba", "wnba", "mlb", "nhl", "nfl"],
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
    compared_book = (
        os.getenv("COMPARED_BOOK")
        or str(scanner.get("compared_book") or DEFAULT_COMPARED_BOOK)
    )
    sports = scanner.get(
        "sports",
        ["nba", "wnba", "mlb", "nhl", "nfl"],
    )
    status = scanner.get("status", "pending,live")
    threshold_pct = float(scanner.get("difference_threshold_pct", 0))
    max_events = int(scanner.get("max_events_per_sport", 50))
    max_alerts = int(scanner.get("max_alerts_per_scan", 20))
    value_only = bool(scanner.get("alert_value_only", scanner.get("alert_rainbet_value_only", True)))

    client = OddsApiClient(api_key)
    all_results: list[dict] = []

    logger.info("--- Starting %s vs Polymarket scan ---", compared_book)
    for sport in sports:
        if sport not in SPORTS_CONFIG:
            logger.warning("Skipping unsupported sport key: %s", sport)
            continue
        all_results.extend(scan_sport(client, sport, status, max_events, threshold_pct, compared_book))

    alerts = [result for result in all_results if result["is_alert"]]
    if value_only:
        value_alerts = []
        for alert in alerts:
            value_opportunities = [
                difference
                for difference in alert["differences"]
                if difference["probability_difference"] < 0
                and difference["absolute_difference"] * 100 >= threshold_pct
            ]
            if value_opportunities:
                opportunity = dict(alert)
                opportunity["biggest_difference"] = max(
                    value_opportunities,
                    key=lambda difference: difference["absolute_difference"],
                )
                value_alerts.append(opportunity)
        alerts = value_alerts

    alerts.sort(key=lambda alert: alert["biggest_difference"]["absolute_difference"], reverse=True)
    previous_alerts = load_alert_state()
    changed_alerts = [
        alert
        for alert in alerts
        if previous_alerts.get(alert_key(alert)) != alert_signature(alert)
    ]
    if all_results:
        save_alert_state(alerts)
    notification_alerts = changed_alerts[:max_alerts]
    logger.info(
        "Compared %s matched games, found %s alert(s), %s new or changed. API calls: %s, remaining: %s",
        len(all_results),
        len(alerts),
        len(changed_alerts),
        client.requests_made,
        client.rate_limit_remaining,
    )

    if send_alerts and changed_alerts:
        telegram = config["telegram"]
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        header = (
            f"*{escape_markdown(compared_book)} Betting Opportunities* ({now})\n"
            f"Trusted oracle: `{POLYMARKET_BOOK}`\n"
            f"Betting venue: `{escape_markdown(compared_book)}`\n"
            f"New or updated opportunities: *{len(changed_alerts)}* / matched games: `{len(all_results)}`\n"
            f"Sending top opportunities: `{len(notification_alerts)}`"
        )
        send_telegram_message(telegram["bot_token"], telegram["chat_id"], header)

        for alert in notification_alerts:
            send_telegram_message(telegram["bot_token"], telegram["chat_id"], format_alert(alert))
            time.sleep(0.2)
    elif send_alerts and alerts:
        logger.info("No changed odds since the last alerted comparison.")
    elif not alerts:
        logger.info("No %s/Polymarket differences met the configured threshold.", compared_book)

    return changed_alerts


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare sportsbook odds against Polymarket and alert on Telegram.")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single scan and exit. Intended for GitHub Actions scheduling.",
    )
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("  Sportsbook vs Polymarket Sports Alert Bot")
    logger.info("=" * 60)

    config = load_config()
    interval_min = float(config["scanner"].get("interval_minutes", 10))

    if args.once:
        run_scan_cycle(config)
        return

    send_telegram_message(
        config["telegram"]["bot_token"],
        config["telegram"]["chat_id"],
        "*Sportsbook Odds Bot Started*\n"
        f"Comparing `{config['scanner'].get('compared_book', DEFAULT_COMPARED_BOOK)}` against `{POLYMARKET_BOOK}` every {interval_min:g} minutes.",
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
                "*Sportsbook Odds Bot Stopped*",
            )
            break


if __name__ == "__main__":
    main()
