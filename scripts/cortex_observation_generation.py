#!/usr/bin/env python3
"""Create one private pending observation generation for a verified candidate."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--code-home", type=Path)
    parser.add_argument("--session-nonce")
    parser.add_argument("--intent", action="store_true")
    parser.add_argument("--revoke", action="store_true")
    args = parser.parse_args()
    if args.intent or args.revoke:
        if not args.code_home or not args.session_nonce:
            raise SystemExit("--intent requires --code-home and --session-nonce")
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "cortex" / "scripts"))
        from cortex_runtime.observation_generation import create_session_intent, revoke_session  # noqa: PLC0415
        if args.revoke: revoke_session(code_home=args.code_home, session_nonce=args.session_nonce)
        else: create_session_intent(code_home=args.code_home, session_nonce=args.session_nonce)
        return 0
    if not args.receipt:
        raise SystemExit("--receipt is required")
    # This runs after `cortex-dev` has already completed full source/candidate
    # qualification.  Bind the lease only to the immutable installed candidate;
    # do not let a later mutable checkout change a live session's identity.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from cortex_candidate_receipt import read_runtime_verified_receipt  # noqa: PLC0415
    code_home = args.receipt.parent
    owner_home = code_home.parent.parent
    receipt = read_runtime_verified_receipt(
        source_root=Path(__file__).resolve().parents[1], owner_home=owner_home,
        isolated_home=owner_home / ".cortex-dev", isolated_codex_home=code_home,
    )
    candidate = Path(receipt["candidate_path"])
    sys.path.insert(0, str(candidate / "scripts"))
    from cortex import PUBLIC_TOOLS  # noqa: PLC0415
    from cortex_runtime.mcp_api import catalogue_identity  # noqa: PLC0415
    from cortex_runtime.observation_generation import consume_intent, request_generation  # noqa: PLC0415
    identity = catalogue_identity(PUBLIC_TOOLS)
    if args.session_nonce:
        result = consume_intent(code_home=Path(receipt["isolated_codex_home"]), package_root=candidate, build_id=receipt["build_id"], candidate_version=receipt["candidate_version"], session_nonce=args.session_nonce, **identity)
    else:
        pending = request_generation(code_home=Path(receipt["isolated_codex_home"]), build_id=receipt["build_id"], candidate_version=receipt["candidate_version"], **identity)
        result = consume_intent(code_home=Path(receipt["isolated_codex_home"]), package_root=candidate, build_id=receipt["build_id"], candidate_version=receipt["candidate_version"], session_nonce=pending["nonce"], **identity)
    # Generation ID is safe transport state, not a package path or a model
    # instruction. The live helper stores it privately and does not display it.
    print(json.dumps({"generation_id": result["generation_id"]}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
