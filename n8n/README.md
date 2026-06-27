# n8n workflows (no-code versions)

Importable n8n workflows that mirror the Python automations. Each is the same trigger → data →
Claude → deliver pattern, built visually.

| File | Automation |
|---|---|
| `trading_news_monitor.json` | Scan news every 2 hrs → flag NAS100 / Gold impact → Telegram |
| `daily_briefing.json` | Weekdays 6:30am → pre-market briefing for NAS100 & Gold → Telegram |
| `lead_response.json` | Webhook (new lead) → Claude drafts reply → Telegram |

## Import & set up
1. n8n → **Workflows → Import from File** → choose a `.json`.
2. In the **Claude** (HTTP Request) node, replace `YOUR_ANTHROPIC_API_KEY` (or attach an n8n
   credential).
3. In the **Telegram** node, set your Telegram credential + `YOUR_TELEGRAM_CHAT_ID`.
4. Set the schedule (or copy the webhook URL), then **Activate**.

> The placeholders (`YOUR_ANTHROPIC_API_KEY`, `YOUR_TELEGRAM_CHAT_ID`) are intentional — no secrets
> are committed. Swap them for n8n credentials after import.
