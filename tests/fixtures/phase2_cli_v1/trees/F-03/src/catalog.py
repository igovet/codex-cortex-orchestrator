"""A tiny catalog used by the status feature fixture."""


class Catalog:
    def __init__(self):
        self._items = {}

    def put(self, key, value):
        self._items[key] = value

    def count(self):
        return len(self._items)
