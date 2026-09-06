#!/usr/bin/env python3
"""Single Python entry point for the installed Cortex lifecycle hooks."""
from cortex_runtime.hooks import main

if __name__ == "__main__":
    raise SystemExit(main())
