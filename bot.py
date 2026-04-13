"""
Polymarket Sports Price Drop Alert Bot for Telegram
=====================================================

This bot monitors SPORTS markets on Polymarket every N minutes and sends
a Telegram notification whenever a major price drop is detected.

Covered sports: NBA, NFL, NHL, MLB, soccer/football (EPL, Champions League,
La Liga, MLS, FIFA World Cup), F1, tennis (Grand Slams, ATP), esports (LoL,
CS2, Dota 2), boxing/MMA, and more.

How it works:
    1. Fetches active sports events from Polymarket's Gamma API (tag=Sports)
    2. Filters to only real sports events using smart keyword matching
    3. Gets current token prices from the CLOB API (no auth needed)
    4. Compares current prices to the previous scan's prices
    5. If a token drops by more than the configured threshold, sends an alert
    6. Saves updated price data for the next comparison cycle

Usage:
    1. pip install -r requirements.txt
    2. Fill in your config.json with your Telegram bot token and chat ID
    3. python bot.py
"""

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent.resolve()
CONFIG_PATH = BASE_DIR / "config.json"
STATE_PATH = BASE_DIR / "price_state.json"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("polymarket_bot")

# ---------------------------------------------------------------------------
# Polymarket API helpers (all public — no auth required)
# ---------------------------------------------------------------------------
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

# Built-in sports keyword detection — used to filter out non-sports events
# that the Gamma API returns even with tag=Sports.
SPORTS_KEYWORDS = [
    # Leagues & Competitions
    "nba", "nfl", "nhl", "mlb", "mls", "fifa", "world cup", "champions league",
    "premier league", "la liga", "serie a", "bundesliga", "ligue 1", "europa league",
    "uefa", "afc", "concacaf", "copa america", "euro", "epl", "fa cup",
    "carabao cup", "club world cup", "nationals league",
    # Basketball
    "basketball", "wnba", "ncaa basketball", "march madness",
    # American Football
    "football", "super bowl", "college football", "ncaa football",
    # Baseball
    "baseball", "world series",
    # Ice Hockey
    "hockey", "stanley cup",
    # Soccer/football
    "soccer", "match", "fc ", " vs ", " vs.", " v ",
    "win the", "cup winner", "cup champion", "league winner",
    # Motorsport
    "f1", "formula 1", "formula one", "motogp", "nascar", "indycar",
    # Tennis
    "tennis", "open winner", "french open", "wimbledon", "us open",
    "australian open", "atp", "wta", "davis cup",
    # Golf
    "golf", "pga", "lpga", "masters", "open championship", "ryder cup",
    # Esports
    "esport", "lol:", "league of legends", "counter-strike", "valorant",
    "dota", "overwatch", "csgo", "cs2",
    # Combat Sports
    "boxing", "ufc", "mma", "bellator", "knockout",
    # Olympics & misc
    "olympic", "olympics", "paralympic", "x games",
    # Teams (partial matches work since we check substring)
    "lakers", "celtics", "warriors", "bulls", "mavericks", "nets", "knicks",
    "heat", "bucks", "nuggets", "76ers", "suns", "clippers", "timberwolves",
    "thunder", "rockets", "grizzlies", "hawks", "magic", "pacers", "cavaliers",
    "raptors", "spurs", "pelicans", "kings", "trail blazers", "hornets",
    "wizards", "pistons", "jazz",
    "chiefs", "eagles", "49ers", "cowboys", "bills", "ravens", "packers",
    "lions", "bengals", "dolphins", "chargers", "steelers", "broncos",
    "seahawks", "vikings", "rams", "bears", "jets", "saints", "buccaneers",
    "falcons", "commanders", "texans", "jaguars", "colts", "titans",
    "panthers", "browns", "raiders", "patriots", "cardinals",
    "yankees", "dodgers", "cubs", "red sox", "astros", "braves", "phillies",
    "padres", "mariners", "giants", "mets", "orioles", "twins", "rangers",
    "guardians", "tigers", "royals", "white sox", "blue jays", "rays",
    "marlins", "reds", "brewers", "rockies", "diamondbacks", "pirates",
    "angels", "athletics",
    "oilers", "avalanche", "panthers", "stars", "rangers", "jets",
    "hurricanes", "bruins", "maple leafs", "lightning", "capitals",
    "flyers", "wings", "wild", "blues", "predators", "flames",
    "canucks", "kraken", "golden knights", "coyotes", "sharks",
    "senators", "sabres", "islanders", "blue jackets", "ducks",
    "manchester united", "liverpool", "arsenal", "chelsea", "man city",
    "real madrid", "barcelona", "bayern", "psg", "inter", "juve",
    "dortmund", "napoli", "atletico", "tottenham", "ajax", "benfica",
    "porto", "sporting",
]

# Keywords that indicate an event is NOT a real sports market
EXCLUDE_KEYWORDS = [
    "temperature", "bitcoin", "ethereum", "crypto", "elon musk", "tweet",
    "presidential", "election", "prime minister", "parliamentary", "congress",
    "fed decision", "fed rate", "interest rate", "crude oil", "stock",
    "ai model", "company", "regime", "iran", "israel", "ceasefire",
    "eurovision", "fed chair", "turnout", "votes", "poll",
    "military", "invasion", "nato", "diplomatic", "greenland", "jesus",
    "leader out", "tariff", "trump",
]


def fetch_active_events(limit: int = 100) -> list[dict]:
    """Fetch active sports events from the Gamma API.

    Uses the tag=Sports parameter to fetch sports-specific events.
    Each event contains nested markets with token_ids, which is the
    most efficient way to bulk-load tradeable markets.

    Args:
        limit: Maximum number of events to return.

    Returns:
        List of event dictionaries from the Gamma API.
    """
    try:
        resp = requests.get(
            f"{GAMMA_API}/events",
            params={
                "active": "true",
                "closed": "false",
                "limit": limit,
                "order": "volume24hr",
                "ascending": "false",
                "tag": "Sports",
            },
            timeout=30,
        )
        resp.raise_for_status()
        events = resp.json()
        logger.info(f"Fetched {len(events)} sports events from Gamma API")
        return events
    except requests.RequestException as e:
        logger.error(f"Failed to fetch events: {e}")
        return []


def is_sports_event(title: str, question: str = "") -> bool:
    """Determine if an event is a real sports market.

    The Gamma API's tag=Sports returns some non-sports events, so this
    function applies a keyword-based filter to ensure only genuine
    sports markets are included.

    Args:
        title: The event title.
        question: Optional market question text.

    Returns:
        True if the event appears to be a real sports market.
    """
    combined = f"{title} {question}".lower()

    # First, exclude known non-sports topics
    for exclude in EXCLUDE_KEYWORDS:
        if exclude in combined:
            return False

    # Then check for at least one sports keyword
    for keyword in SPORTS_KEYWORDS:
        if keyword in combined:
            return True

    return False


def extract_markets(events: list[dict], extra_keywords: list[str] | None = None) -> list[dict]:
    """Extract individual sports markets from events.

    Each Polymarket event can contain multiple markets (outcomes).
    This function flattens them into a single list with relevant fields,
    filtering to only real sports events.

    Args:
        events: List of event dicts from the Gamma API.
        extra_keywords: Optional additional keywords to further filter markets
            (e.g. ["NBA", "F1"] to only monitor specific sports).

    Returns:
        List of market dicts with token_id, question, volume, etc.
    """
    markets = []
    extra_lower = [k.lower() for k in extra_keywords] if extra_keywords else []

    for event in events:
        event_title = event.get("title", "")

        for market in event.get("markets", []):
            # Each market has outcomes with token IDs
            question = market.get("question", "Unknown")
            if not question:
                continue

            # Apply built-in sports filter
            if not is_sports_event(event_title, question):
                continue

            # Apply extra keyword filter if configured (e.g. only NBA & F1)
            if extra_lower:
                text = f"{question} {event_title}".lower()
                if not any(kw in text for kw in extra_lower):
                    continue

            # Extract the "Yes" and "No" outcome token IDs
            # NOTE: Gamma API returns these as JSON-encoded strings
            raw_outcomes = market.get("outcomes", "[]")
            raw_token_ids = market.get("clobTokenIds", "[]")
            raw_prices = market.get("outcomePrices", "[]")

            # Parse all JSON-encoded fields safely
            def safe_json_parse(val):
                if isinstance(val, str):
                    try:
                        return json.loads(val)
                    except (json.JSONDecodeError, TypeError):
                        return []
                return val if isinstance(val, list) else []

            outcome_list = safe_json_parse(raw_outcomes)
            clob_token_ids = safe_json_parse(raw_token_ids)
            price_list = safe_json_parse(raw_prices)

            if not clob_token_ids:
                continue

            # Build a market entry for each outcome (Yes / No)
            for i, token_id in enumerate(clob_token_ids):
                outcome_label = outcome_list[i] if i < len(outcome_list) else f"Outcome {i}"
                current_price = float(price_list[i]) if i < len(price_list) else None

                markets.append({
                    "token_id": token_id,
                    "question": question,
                    "event_title": event.get("title", ""),
                    "outcome": outcome_label,
                    "volume_24h": float(market.get("volume24hr", 0) or 0),
                    "current_price": current_price,
                    "market_slug": market.get("slug", ""),
                    "condition_id": market.get("conditionId", ""),
                })

    logger.info(f"Extracted {len(markets)} token outcomes from events")
    return markets


def fetch_price_from_clob(token_id: str) -> float | None:
    """Fetch the latest buy-side price for a token from the CLOB API.

    This is used as a fallback when the Gamma API doesn't return prices.

    Args:
        token_id: The CLOB token ID.

    Returns:
        The price as a float, or None if unavailable.
    """
    try:
        resp = requests.get(
            f"{CLOB_API}/price",
            params={"token_id": token_id, "side": "buy"},
            timeout=10,
        )
        resp.raise_for_status()
        return float(resp.text)
    except (requests.RequestException, ValueError) as e:
        logger.debug(f"Could not fetch price for {token_id[:12]}...: {e}")
        return None


def refresh_prices(markets: list[dict]) -> list[dict]:
    """Ensure every market has an up-to-date price.

    Uses the Gamma-provided price first; falls back to the CLOB API
    for any tokens missing price data.

    Args:
        markets: List of market dicts (modified in place).

    Returns:
        The same list, with `current_price` populated where possible.
    """
    for market in markets:
        if market.get("current_price") is not None:
            continue
        price = fetch_price_from_clob(market["token_id"])
        if price is not None:
            market["current_price"] = price
    return markets


# ---------------------------------------------------------------------------
# Telegram helpers
# ---------------------------------------------------------------------------
def send_telegram_message(bot_token: str, chat_id: str, text: str) -> bool:
    """Send a message via the Telegram Bot API.

    Args:
        bot_token: Your Telegram bot token from @BotFather.
        chat_id: The target chat ID.
        text: The message body (supports Markdown).

    Returns:
        True if the message was sent successfully, False otherwise.
    """
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        logger.error(f"Failed to send Telegram message: {e}")
        return False


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------
def load_state() -> dict:
    """Load the previous price state from disk.

    Returns:
        Dict mapping token_id -> {"price": float, "timestamp": str}.
    """
    if STATE_PATH.exists():
        try:
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            return data.get("prices", {})
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Could not parse state file, starting fresh: {e}")
    return {}


def save_state(prices: dict) -> None:
    """Persist the current price state to disk.

    Args:
        prices: Dict mapping token_id -> {"price": float, "timestamp": str}.
    """
    data = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "prices": prices,
    }
    STATE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Alert logic
# ---------------------------------------------------------------------------
def detect_drops(
    markets: list[dict],
    prev_state: dict,
    threshold_pct: float,
    min_volume: float,
) -> list[dict]:
    """Compare current prices to the previous state and find significant drops.

    A "drop" is detected when a token's price decreases by at least
    `threshold_pct` percent since the last scan.

    Args:
        markets: List of current market dicts with `current_price`.
        prev_state: Previous price state from `load_state()`.
        threshold_pct: Minimum percentage drop to trigger an alert (e.g. 5.0).
        min_volume: Skip markets with 24h volume below this threshold.

    Returns:
        List of alert dicts with drop details.
    """
    alerts = []

    for market in markets:
        token_id = market["token_id"]
        current_price = market.get("current_price")

        # Skip tokens with no price data
        if current_price is None:
            continue

        # Skip low-volume markets (noise filter)
        if market.get("volume_24h", 0) < min_volume:
            continue

        # Skip tokens we haven't seen before (no baseline to compare)
        if token_id not in prev_state:
            continue

        prev_price = prev_state[token_id].get("price")
        if prev_price is None or prev_price <= 0:
            continue

        # Calculate percentage change
        change_pct = ((current_price - prev_price) / prev_price) * 100

        # Only alert on drops (negative change) exceeding threshold
        if change_pct <= -threshold_pct:
            alerts.append({
                "question": market["question"],
                "event_title": market["event_title"],
                "outcome": market["outcome"],
                "prev_price": prev_price,
                "current_price": current_price,
                "drop_pct": abs(change_pct),
                "volume_24h": market.get("volume_24h", 0),
                "token_id": token_id,
            })

    return alerts


def format_alert(alert: dict) -> str:
    """Format a single drop alert into a Telegram Markdown message.

    Args:
        alert: Alert dict from `detect_drops()`.

    Returns:
        Formatted message string.
    """
    return (
        f"*Price Drop Alert*\n"
        f"_{alert['event_title']}_\n\n"
        f"Market: *{alert['question']}*\n"
        f"Outcome: *{alert['outcome']}*\n\n"
        f"Previous: `{alert['prev_price']:.4f}`\n"
        f"Current: `{alert['current_price']:.4f}`\n"
        f"Drop: *-{alert['drop_pct']:.1f}%*\n"
        f"24h Volume: `${alert['volume_24h']:,.0f}`\n"
    )


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def load_config() -> dict:
    """Load and validate configuration from config.json.

    Returns:
        Parsed config dictionary.

    Raises:
        SystemExit: If config is missing or invalid.
    """
    if not CONFIG_PATH.exists():
        logger.error(
            f"Config file not found: {CONFIG_PATH}\n"
            f"Copy config.json.template to config.json and fill in your values."
        )
        sys.exit(1)

    try:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        logger.error(f"Invalid config.json: {e}")
        sys.exit(1)

    # Validate Telegram settings
    tg = config.get("telegram", {})
    if tg.get("bot_token", "").startswith("YOUR_"):
        logger.error("Please set your Telegram bot token in config.json")
        sys.exit(1)
    if tg.get("chat_id", "").startswith("YOUR_"):
        logger.error("Please set your Telegram chat ID in config.json")
        sys.exit(1)

    return config


def run_scan_cycle(config: dict) -> None:
    """Execute a single scan cycle: fetch → compare → alert → save.

    Args:
        config: Parsed configuration dictionary.
    """
    scanner_cfg = config["scanner"]
    threshold = scanner_cfg["drop_threshold_pct"]
    min_vol = scanner_cfg["min_volume"]
    max_markets = scanner_cfg["max_markets_to_scan"]
    keywords = scanner_cfg.get("sports_filter", [])

    tg_cfg = config["telegram"]
    bot_token = tg_cfg["bot_token"]
    chat_id = tg_cfg["chat_id"]

    logger.info("--- Starting scan cycle ---")

    # 1. Fetch active events
    events = fetch_active_events(limit=max_markets)
    if not events:
        logger.warning("No events fetched. Skipping this cycle.")
        return

    # 2. Extract individual markets/outcomes
    markets = extract_markets(events, keywords=keywords)
    if not markets:
        logger.warning("No markets extracted. Skipping this cycle.")
        return

    # 3. Refresh prices (fallback to CLOB API for any missing)
    markets = refresh_prices(markets)

    # 4. Load previous price state
    prev_state = load_state()

    # 5. Detect drops
    alerts = detect_drops(markets, prev_state, threshold, min_vol)

    # 6. Send alerts
    if alerts:
        logger.info(f"Detected {len(alerts)} price drop(s)!")

        # Send summary header
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        header = (
            f"*Polymarket Sports Scanner* ({now})\n"
            f"{len(alerts)} alert(s) found\n"
        )
        send_telegram_message(bot_token, chat_id, header)

        # Send individual alerts (Telegram rate limit: ~30 msg/sec)
        for alert in sorted(alerts, key=lambda a: a["drop_pct"], reverse=True):
            msg = format_alert(alert)
            success = send_telegram_message(bot_token, chat_id, msg)
            if success:
                logger.info(
                    f"  Alert: {alert['outcome']} for '{alert['question']}' "
                    f"dropped {alert['drop_pct']:.1f}%"
                )
            time.sleep(0.1)  # Brief pause between messages
    else:
        logger.info("No significant price drops detected.")

    # 7. Save current prices for next cycle
    new_state = {}
    for market in markets:
        price = market.get("current_price")
        if price is not None:
            new_state[market["token_id"]] = {
                "price": price,
                "question": market["question"],
                "outcome": market["outcome"],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
    save_state(new_state)
    logger.info(f"Saved price state for {len(new_state)} tokens.")


def main():
    """Main entry point. Loads config and runs the scan loop."""
    logger.info("=" * 60)
    logger.info("  Polymarket Sports Price Drop Alert Bot")
    logger.info("=" * 60)

    config = load_config()

    interval_min = config["scanner"]["interval_minutes"]
    interval_sec = interval_min * 60
    logger.info(f"Scan interval: every {interval_min} minutes")
    logger.info(f"Drop threshold: {config['scanner']['drop_threshold_pct']}%")
    logger.info(f"Min 24h volume: ${config['scanner']['min_volume']:,.0f}")

    # Send startup message
    send_telegram_message(
        config["telegram"]["bot_token"],
        config["telegram"]["chat_id"],
        "*Polymarket Sports Bot Started*\nMonitoring sports markets for "
        f"price drops every {interval_min} minutes.\nThreshold: "
        f"-{config['scanner']['drop_threshold_pct']}%",
    )

    # Run the scan loop
    while True:
        try:
            run_scan_cycle(config)
        except Exception as e:
            logger.error(f"Error during scan cycle: {e}", exc_info=True)
            send_telegram_message(
                config["telegram"]["bot_token"],
                config["telegram"]["chat_id"],
                f"*Bot Error*\n{str(e)[:500]}",
            )

        logger.info(f"Sleeping for {interval_min} minutes...")
        try:
            time.sleep(interval_sec)
        except KeyboardInterrupt:
            logger.info("Bot stopped by user.")
            send_telegram_message(
                config["telegram"]["bot_token"],
                config["telegram"]["chat_id"],
                "*Polymarket Sports Bot Stopped*\nGoodbye!",
            )
            break


if __name__ == "__main__":
    main()
