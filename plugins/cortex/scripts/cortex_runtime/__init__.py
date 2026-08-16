"""Cortex runtime components.

The executable ``scripts/cortex.py`` is intentionally retained as the stable
MCP and hook compatibility facade.  Domain logic belongs in this package so
that it can be tested and evolved without turning the executable entrypoint
into another all-purpose control-plane module.
"""
