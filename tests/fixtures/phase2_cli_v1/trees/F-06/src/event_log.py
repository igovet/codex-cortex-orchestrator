"""Format security-sensitive event metadata."""


def format_event(event, token):
    """Return a log line for event and token (unsafe fixture implementation)."""
    return f"event={event} token={token}"
