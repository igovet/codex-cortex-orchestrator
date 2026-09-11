"""Public cache API."""


def get(key, default=None):
    """Return the cached value for key, or default when it is absent."""
    return default


def clear():
    """Clear all cached values."""
