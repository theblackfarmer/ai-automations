"""AI Automations — small, production-style automations powered by Claude.

Modules:
    news_monitor    — scan market news, flag NAS100 / Gold impact
    daily_briefing  — pre-market briefing for NAS100 & Gold
    lead_response   — draft a personalized reply to a new lead
    trading_signal  — honest breakout signal (paper / decision-support)

Each has pure, tested logic plus a run() that calls Claude and delivers the result.
"""
__version__ = "0.1.0"
