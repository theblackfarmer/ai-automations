"""Lead-Response Agent — draft a personalized reply to a new business lead."""
from __future__ import annotations

from . import claude, notify

SYSTEM = (
    "You are a friendly, professional assistant replying to a new business lead. Write a warm, "
    "concise reply (under 150 words) that answers their question, builds trust, and proposes a "
    "clear next step. Plain text, no placeholders."
)


def build_prompt(name: str, email: str, message: str) -> str:
    return (
        "New lead.\n"
        f"Name: {name}\n"
        f"Email: {email}\n"
        f"Message: {message}\n\n"
        "Write the reply."
    )


def run(name: str, email: str, message: str) -> str:
    """Draft a reply with Claude and deliver it for review. Returns the reply text."""
    reply = claude.ask(build_prompt(name, email, message), system=SYSTEM, max_tokens=500)
    notify.deliver(f"🟢 New lead: {name} ({email})\n\nDraft reply:\n{reply}")
    return reply
