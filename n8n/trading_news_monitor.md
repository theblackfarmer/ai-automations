# AI Trading News Monitor — NAS100 & Gold

Every couple of hours, this scans market headlines and uses **Claude to flag only the news likely
to move NAS100 or Gold (XAUUSD)** — with direction, impact level, and a one-line reason — then
sends it to your phone. No more reading 30 headlines to find the 2 that matter.

## Flow
```
Schedule (every 2 hrs)
   → Read market news (RSS)
   → Collect the latest ~30 headlines
   → Claude filters for NAS100 / Gold impact (direction, HIGH/MED/LOW, why)
   → Telegram alert
```

## The AI prompt (system)
> You are a markets analyst. From the headlines, identify ONLY items likely to move NAS100
> (US tech / Nasdaq) or GOLD (XAUUSD). For each: [NAS100 or GOLD] | direction (up/down/volatile)
> | impact (HIGH/MED/LOW) | one-line why. Ignore everything irrelevant. If nothing is relevant,
> reply: "No high-impact NAS100/Gold news right now." Be concise and honest — decision-support,
> not financial advice.

## Setup
1. Import `workflow.json` into n8n.
2. Add your **Anthropic API key** in the *Claude — Analyze Impact* node.
3. Set your **Telegram** credential + chat ID.
4. (Optional) Swap the RSS URL for your preferred source (ForexLive, Investing.com, Reuters
   markets, an economic-calendar feed). You can add multiple RSS nodes and merge them.
5. Activate.

## Example output
> 📊 NAS100 / GOLD news scan
> • GOLD | up | HIGH | Softer CPI print raises rate-cut odds, USD weaker — bullish gold.
> • NAS100 | volatile | MED | Big-tech earnings after the close; expect a gap.
> • No other high-impact items.

## Why it matters
It turns a constant, noisy feed into **two clean, market-specific alerts** — pairing AI automation
with real domain knowledge of the instruments. Decision-support only; it never places trades.
