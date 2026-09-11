"""Accumulate report values, de-duplicating identical retry events."""


def total(events):
    """Return the total value for (item_id, value) events."""
    result = 0
    for _item_id, value in events:
    result += value
    return result
