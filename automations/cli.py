"""Command-line entry point.

    python -m automations.cli news                 # scan news -> NAS100/Gold alert
    python -m automations.cli briefing             # pre-market briefing
    python -m automations.cli lead --name "Jane" --email j@x.com --message "Do you offer plans?"
    python -m automations.cli signal --symbol NAS100 --demo   # breakout signal (no keys needed)
"""
from __future__ import annotations

import argparse

from . import claude, daily_briefing, lead_response, news_monitor, trading_signal


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="automations", description="AI automations (Claude-powered)")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("news", help="scan market news for NAS100/Gold impact")
    sub.add_parser("briefing", help="pre-market briefing for NAS100 & Gold")

    p_lead = sub.add_parser("lead", help="draft a reply to a new lead")
    p_lead.add_argument("--name", required=True)
    p_lead.add_argument("--email", default="")
    p_lead.add_argument("--message", required=True)

    p_sig = sub.add_parser("signal", help="breakout signal (paper / decision-support)")
    p_sig.add_argument("--symbol", default="NAS100")
    p_sig.add_argument("--demo", action="store_true", help="use sample candles (no keys needed)")

    args = parser.parse_args(argv)
    try:
        if args.cmd == "news":
            news_monitor.run()
        elif args.cmd == "briefing":
            daily_briefing.run()
        elif args.cmd == "lead":
            lead_response.run(args.name, args.email, args.message)
        elif args.cmd == "signal":
            trading_signal.run(args.symbol)  # uses SAMPLE_CANDLES by default
        else:
            parser.print_help()
    except claude.ClaudeError as e:
        print(f"[AI step skipped — {e}]")
        print("(The logic ran; set ANTHROPIC_API_KEY to enable the Claude analysis/reply.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
