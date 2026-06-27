# ⚡ AI Automations — Claude-powered workflows (Python + n8n)

Four production-style AI automations, built two ways: a **tested Python toolkit** (zero
dependencies, unit-tested) and **importable n8n no-code workflows**. Same logic, your choice of
stack.

By **Isaac McFarland** — github.com/theblackfarmer

| Automation | What it does |
|---|---|
| **Trading News Monitor** | Scans market news and flags only what's likely to move **NAS100 & Gold** — direction, impact, why |
| **Daily Market Briefing** | A concise pre-market briefing for NAS100 & Gold (overnight tone, key news, what to watch) |
| **Lead-Response Agent** | Drafts a personalized reply to a new lead and routes it for review/send |
| **Trading Signal Bot** | An **honest breakout signal** (entry/stop/target) — paper / decision-support, never auto-trades |

## The pattern
```
trigger (schedule / webhook / RSS)
   → gather data (headlines, lead fields, price candles)
   → Claude reasons over it with a structured prompt
   → deliver (Telegram / email / console)
```

## Run it (Python — no dependencies, Python 3.8+)
```bash
python -m automations.cli signal --symbol NAS100      # works offline, no keys (sample data)
python -m automations.cli news                        # set ANTHROPIC_API_KEY first
python -m automations.cli briefing
python -m automations.cli lead --name "Jane" --email j@x.com --message "Do you offer plans?"

python tests/test_logic.py                            # 10/10 unit tests
```
Set keys via environment (see `.env.example`): `ANTHROPIC_API_KEY`, optional `TELEGRAM_BOT_TOKEN`
+ `TELEGRAM_CHAT_ID` (otherwise output prints to the console).

## Run it (n8n — no code)
Import any file from [`n8n/`](n8n/) → *Workflows → Import from File*, add your Anthropic +
Telegram credentials, set the schedule/webhook, and activate. See [`n8n/README.md`](n8n/README.md).

## Engineering notes
- **Tested:** pure logic (RSS parsing, prompt building, the breakout rule) is covered by unit
  tests; run `python tests/test_logic.py`.
- **Graceful:** missing API key or failed delivery never crashes a run — it degrades with a clear
  message and still completes the logic.
- **Zero dependencies:** standard library only (`urllib`, `xml`), so it installs anywhere.
- **Honest by design:** the trading pieces are decision-support and never place trades; the signal
  rule is transparent and stated in plain language.

## Tech
Python 3 (stdlib) · n8n · Anthropic (Claude) API · RSS · Telegram · webhooks

## License
[MIT](LICENSE) — © 2026 Isaac McFarland
