#!/usr/bin/env python3
"""
Parcel tracker -> Discord webhook, powered by the Ship24 API. Standard library only.

Run it on a schedule (cron, Windows Task Scheduler or GitHub Actions). Each run:
  1. asks Ship24 for the parcel's current tracking events
  2. posts to a Discord webhook if there are events it hasn't posted before
  3. remembers what it already posted in a small state file

Usage:
  python track.py            normal run
  python track.py --check    send a test message to the webhook and exit
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API = os.environ.get("SHIP24_API_BASE", "https://api.ship24.com/public/v1")
USER_AGENT = "parcel-webhook/1.0"  # Discord rejects urllib's default user agent

MILESTONES = {
    "pending": ("⏳", "Pending"),
    "info_received": ("📝", "Info received"),
    "in_transit": ("🚚", "In transit"),
    "out_for_delivery": ("🛵", "Out for delivery"),
    "failed_attempt": ("⚠️", "Delivery attempt failed"),
    "available_for_pickup": ("📬", "Available for pickup"),
    "delivered": ("✅", "Delivered"),
    "exception": ("❗", "Exception"),
}
COLOURS = {
    "delivered": 0x2ECC71,
    "exception": 0xE74C3C,
    "failed_attempt": 0xE74C3C,
    "out_for_delivery": 0xF1C40F,
}
MAX_EVENTS = 8


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def env(name, default=""):
    return os.environ.get(name, default).strip()


def load_dotenv(path=".env"):
    """Tiny .env reader so it works locally without extra packages."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def http(method, url, headers=None, payload=None, timeout=90):
    """Return (status, parsed_json_or_empty_dict). Network errors propagate."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("User-Agent", USER_AGENT)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, raw = resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read().decode("utf-8", "replace")
    try:
        body = json.loads(raw) if raw else {}
    except ValueError:
        body = {}
    return status, body


def fetch_tracking(api_key, number, origin, destination):
    """Create the tracker if needed and return the current results.

    Ship24 treats repeated calls with the same payload as the same tracker, so
    calling this every run does not create new shipments.
    """
    payload = {"trackingNumber": number}
    if origin:
        payload["originCountryCode"] = origin
    if destination:
        payload["destinationCountryCode"] = destination

    for attempt in range(3):
        status, body = http(
            "POST", f"{API}/trackers/track", {"Authorization": f"Bearer {api_key}"}, payload
        )
        if status == 429 and attempt < 2:
            time.sleep(5)
            continue
        break

    if status >= 400:
        errors = body.get("errors") if isinstance(body, dict) else None
        first = (errors or [{}])[0] or {}
        raise RuntimeError(
            f"Ship24 error {status}: {first.get('code', 'unknown')} {first.get('message', '')}".strip()
        )
    trackings = ((body.get("data") or {}).get("trackings")) or []
    return trackings[0] if trackings else None


def post_discord(webhook, payload):
    for attempt in range(2):
        status, body = http("POST", webhook, payload=payload, timeout=30)
        if status == 429 and attempt == 0:
            time.sleep(min(float(body.get("retry_after", 2)), 30))
            continue
        if status >= 400:
            # Never print the webhook URL: it works like a password.
            raise RuntimeError(f"Discord webhook error {status}: {body.get('message', '')}".strip())
        return


# --------------------------------------------------------------------------- #
# Formatting
# --------------------------------------------------------------------------- #
def mask(number):
    return number if len(number) <= 8 else f"{number[:2]}…{number[-4:]}"


def milestone_of(tracking):
    milestone = ((tracking.get("shipment") or {}).get("statusMilestone")) or "pending"
    emoji, label = MILESTONES.get(milestone, ("📦", milestone.replace("_", " ").title()))
    return milestone, emoji, label


def event_line(ev):
    when = (ev.get("occurrenceDatetime") or "")[:16].replace("T", " ")
    line = f"`{when}` {ev.get('status') or 'Update'}"
    if ev.get("location"):
        line += f" — {ev['location']}"
    return line[:300]


def build_embed(name, number, tracking, events):
    milestone, emoji, label = milestone_of(tracking)
    lines = [event_line(e) for e in events[:MAX_EVENTS]]
    if len(events) > MAX_EVENTS:
        lines.append(f"…and {len(events) - MAX_EVENTS} older update(s)")
    fields = [
        {"name": "Status", "value": label, "inline": True},
        {"name": "Number", "value": f"`{mask(number)}`", "inline": True},
    ]
    eta = ((tracking.get("shipment") or {}).get("delivery") or {}).get("estimatedDeliveryDate")
    if eta:
        fields.append({"name": "Estimated delivery", "value": eta[:10], "inline": True})
    return {
        "title": f"{emoji} {name}",
        "description": "\n".join(lines) or "No tracking events yet. I'll post when Ship24 finds some.",
        "color": COLOURS.get(milestone, 0x5865F2),
        "fields": fields,
        "footer": {"text": "via Ship24"},
    }


def webhook_payload(embed=None, content=None, user_id=""):
    payload = {"username": "Parcel tracker"}
    if embed:
        payload["embeds"] = [embed]
    if content:
        payload["content"] = content
    payload["allowed_mentions"] = {"users": [user_id]} if user_id else {"parse": []}
    return payload


# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #
def load_state(path):
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(path, state):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), "utf-8")
    tmp.replace(path)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def run():
    load_dotenv()
    webhook = env("DISCORD_WEBHOOK_URL")
    user_id = env("DISCORD_USER_ID")
    if not webhook or "/api/webhooks/" not in webhook:
        raise SystemExit("DISCORD_WEBHOOK_URL is missing or doesn't look like a Discord webhook URL.")

    if "--check" in sys.argv:
        post_discord(webhook, webhook_payload(content="✅ Webhook works. Parcel tracker is set up."))
        print("Test message sent.")
        return 0

    api_key = env("SHIP24_API_KEY")
    number = env("TRACKING_NUMBER").upper()
    name = env("PARCEL_NAME") or f"Parcel {mask(number)}"
    origin = env("ORIGIN_COUNTRY").upper()
    destination = env("DESTINATION_COUNTRY").upper()

    if not api_key or not number:
        raise SystemExit("Set SHIP24_API_KEY and TRACKING_NUMBER (see .env.example).")
    if not re.fullmatch(r"[A-Za-z0-9\-_/.]{5,50}", number):
        raise SystemExit("TRACKING_NUMBER must be 5-50 characters: letters, digits, - _ / .")
    for code in (origin, destination):
        if code and not re.fullmatch(r"[A-Z]{2}", code):
            raise SystemExit("Country codes must be 2 letters, e.g. CN or ES.")

    state_path = Path(env("STATE_FILE", "state.json"))
    state = load_state(state_path)
    if state.get("number") != number:  # new parcel: start fresh
        state = {"number": number, "started": False, "seen": [], "done": False}
    if state["done"]:
        print(f"{mask(number)}: already finished, nothing to do.")
        return 0

    tracking = fetch_tracking(api_key, number, origin, destination)
    if not tracking:
        print(f"{mask(number)}: Ship24 returned no data yet.")
        return 0

    events = tracking.get("events") or []  # newest first
    milestone = milestone_of(tracking)[0]
    seen = set(state["seen"])
    new = [e for e in events if e.get("eventId") and e["eventId"] not in seen]

    if not state["started"]:
        # First run: post the current picture once.
        embed = build_embed(name, number, tracking, events)
        post_discord(webhook, webhook_payload(embed, "📦 Now tracking this parcel.", user_id))
        state["started"] = True
        print(f"{mask(number)}: first run, posted current status ({milestone}).")
    elif new:
        embed = build_embed(name, number, tracking, new)
        mention = f"<@{user_id}>" if user_id else None
        post_discord(webhook, webhook_payload(embed, mention, user_id))
        print(f"{mask(number)}: posted {len(new)} new event(s) ({milestone}).")
    else:
        print(f"{mask(number)}: no new events ({milestone}).")

    state["seen"] = sorted(seen | {e["eventId"] for e in events if e.get("eventId")})

    tracker = tracking.get("tracker") or {}
    if milestone == "delivered" or tracker.get("isTracked") is False:
        state["done"] = True
        if milestone != "delivered":
            post_discord(
                webhook,
                webhook_payload(
                    content=f"Ship24 has stopped tracking **{name}** (no activity for a long time).",
                    user_id=user_id,
                ),
            )
    save_state(state_path, state)  # only after a successful post, so failures retry next run
    return 0


def main():
    try:
        return run()
    except SystemExit:
        raise
    except Exception as e:  # network errors, API errors...
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
