#!/usr/bin/env python3
"""Offline Cortex metadata migration; not an MCP operation."""
from cortex_runtime.migrate import main

if __name__ == '__main__':
    raise SystemExit(main())
