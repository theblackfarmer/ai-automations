# AI Lead-Response Agent

When a new lead comes in, **Claude drafts a personalized reply** and sends it to you (Telegram) to
review or auto-send — so no lead waits hours for a response. This is the single most-requested
business automation.

## Flow
```
New lead (Webhook: name, email, message)
   → Claude (Anthropic API) drafts a warm, concise, on-brand reply
   → Telegram notification with the lead + the draft reply
```
*(Swap the Telegram node for Gmail to auto-send the reply, and add a Google Sheets node to log
every lead.)*

## The AI prompt (system)
> You are a friendly, professional assistant replying to a new business lead. Write a warm,
> concise reply (under 150 words) that answers their question, builds trust, and proposes a clear
> next step. Plain text, no placeholders.

## Setup
1. Import `workflow.json` into n8n.
2. In **Claude — Draft Reply**, replace `YOUR_ANTHROPIC_API_KEY` with your key (or use an n8n
   credential).
3. In **Notify (Telegram)**, set your Telegram credential + `YOUR_TELEGRAM_CHAT_ID`.
4. Activate. Copy the webhook URL and point your contact form / Typeform / site at it (POST JSON
   with `name`, `email`, `message`).

## Test it
```bash
curl -X POST <your-n8n-webhook-url> \
  -H "Content-Type: application/json" \
  -d '{"name":"Jane Doe","email":"jane@example.com","message":"Do you offer monthly plans?"}'
```
You'll get a Telegram message with a ready-to-send, personalized reply.

## Why it matters
Speed-to-lead is the #1 driver of conversion. This turns a manual, hours-long task into an
instant, consistent, on-brand response — the kind of workflow small businesses pay to have built.
