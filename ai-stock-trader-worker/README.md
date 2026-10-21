# AI Stock Trader | persistent PAPER worker

Runs every 5 minutes while process is alive. Strictly paper-only, no live endpoint. Default DRY_RUN=true: analyzes without placing orders.

## Deployment

1. Create a **private** GitHub repository and upload these files without .env or credentials.
2. In Render choose New -> Blueprint and connect the repository, or create Background Worker, Docker runtime, Starter instance (paid).
3. Add ALPACA_PAPER_KEY and ALPACA_PAPER_SECRET as Render environment secrets. Never send keys via chat and never commit them.
4. Keep DRY_RUN=true initially. Review logs / trading-events.jsonl during a regular NY session.
5. Only after verifying correct signals, broker responses, operational logging, and paper account state change DRY_RUN=false **in Render**, to permit paper orders. Never set LIVE_TRADING=true.

## Design caveats

- Does not yet include a persistent distributed lock, PostgreSQL event journal, durable order state or full P&L kill-switch. These are required before unattended live-money trading.
- With local JSONL logs, events can be lost on container restarts unless persistent storage or external DB is added.
- Vercel may still be configured with a separate daily paper cron. **Disable Vercel Cron Jobs** on the old project before activating order submission here, to avoid two independent bot strategies.
- Trade decisions use the free IEX feed, not complete market-wide SIP; historical test may be unrepresentative.
- This initial worker does not close positions automatically. Manual intervention is needed to manage existing paper holdings.
- Short sells may be rejected by Alpaca even when assets are labeled easy-to-borrow.
- No autonomous retraining. Research improvements require separate validation.

## Local test

`python -m unittest discover -s tests -v`
