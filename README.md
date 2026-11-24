# Parcel Tracker → Discord Webhook (Ship24)

A tiny script that tracks one parcel, including EMS and other international postal services, and posts to a Discord channel when there is a new tracking event.

- **No bot, no server, no open port.** Discord webhooks are just a URL you post to.
- **No dependencies.** Python 3.8+ standard library only.
- Uses the [Ship24 Tracking API](https://docs.ship24.com), which has a free tier.
- Each run checks once and exits, so you can schedule it with GitHub Actions, cron or Windows Task Scheduler.

> Unofficial project, not affiliated with Ship24 or Discord. Check Ship24's pricing and carrier coverage yourself; they can change.

## What you need

1. A free [Ship24](https://www.ship24.com) account. Copy your key from **Integrations → API Keys**.
2. A Discord webhook: channel settings → **Integrations → Webhooks → New Webhook → Copy Webhook URL**.
3. Your tracking number.

## Option A: GitHub Actions (nothing stays running on your PC)

1. Use this repository as a template (or fork it) so you have **your own copy**.
2. In your copy: **Settings → Secrets and variables → Actions**
   - Add **secrets**: `SHIP24_API_KEY`, `DISCORD_WEBHOOK_URL`, `TRACKING_NUMBER`
   - Add **variables** (optional): `PARCEL_NAME`, `ORIGIN_COUNTRY`, `DESTINATION_COUNTRY`, `DISCORD_USER_ID`
3. Open the **Actions** tab and enable workflows if GitHub asks. Forks have scheduled workflows disabled by default.
4. Run **Track parcel** manually once (**Run workflow**). You should get a "Now tracking" message in Discord.

The workflow runs hourly (edit the `cron` line in `.github/workflows/track.yml` to change it).

Things to know about GitHub's scheduler:
- Scheduled runs are best-effort. They can start late, and busy periods can delay or skip them.
- In a public repository, scheduled workflows are automatically disabled after 60 days without repository activity.
- The "already posted" state is kept in the Actions cache, not in the repo. If the cache is ever lost, the next run just posts the "Now tracking" summary again.

## Option B: run it on your own computer

```bash
cp .env.example .env     # then fill it in
python track.py --check  # sends a test message to your webhook
python track.py          # one real check
```

Then schedule `python track.py` every 30-60 minutes:
- **Linux/macOS:** `crontab -e` and add `*/30 * * * * cd /path/to/repo && python3 track.py`
- **Windows:** Task Scheduler → Create Basic Task → repeat every 30 minutes → run `python track.py` in the repo folder.

## Behaviour

- First run: posts the current status once.
- Later runs: post only events it hasn't posted before, and stay silent otherwise.
- When the parcel is delivered (or Ship24 stops tracking it), it posts a final message and does nothing on later runs.
- Change `TRACKING_NUMBER` and it starts fresh for the new parcel.
- If Ship24 or Discord fails, the run exits with an error and retries next time. Nothing is marked as posted until Discord accepts the message.

## Privacy and limits

- Tracking numbers are personal data. Keep `.env` and `state.json` out of version control (already in `.gitignore`). In GitHub, use **Secrets** for the key, webhook URL and tracking number.
- A webhook URL works like a password. If it leaks, delete it in Discord and create a new one.
- Messages show the tracking number masked, for example `EE…89CN`.
- The free Ship24 tier is small. This script only creates one tracker, and repeated checks reuse it. Check your usage in the Ship24 dashboard after the first day.
- The first lookup of a number can take up to a minute while Ship24 queries the courier.

## License

See `LICENSE`.
