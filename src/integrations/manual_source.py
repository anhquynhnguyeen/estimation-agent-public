"""Backup when Front isn't available: the user pastes the emails into the app.
Produces the same EmailThread as Front, so the rest of the app works the same way."""

from src.integrations.base import EmailMessage, EmailThread


def build_manual_thread(subject: str, our_issue_email: str, client_reply: str) -> EmailThread:
    messages = []
    if our_issue_email.strip():
        # Our earlier email listing the issues (the analyzer checks each one)
        messages.append(EmailMessage(direction="outbound", body_text=our_issue_email.strip(), subject=subject))
    if client_reply.strip():  # the client's reply we are processing
        messages.append(EmailMessage(direction="inbound", body_text=client_reply.strip(), subject=subject))
    return EmailThread(subject=subject, messages=messages)
