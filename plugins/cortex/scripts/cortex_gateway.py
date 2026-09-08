"""Standalone Cortex Model Gateway entry point."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cortex_runtime.gateway.server import serve  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cortex Model Gateway")
    parser.add_argument("command", nargs="?", default="serve", choices=("serve",))
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    args = parser.parse_args(argv)
    asyncio.run(serve(host=args.host, port=args.port))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

