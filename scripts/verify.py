#!/usr/bin/env python3
"""SignaAI verify — thin wrapper over the signaai SDK (>= 0.3.0).

Phase 1 conversion: proof format and verification logic live in the signaai
package; this script keeps the original CLI surface and module functions
(`from verify import hash_content, publish_proof, verify_proof`) for sibling
scripts. Pre-conversion implementation: verify.py.pre-sdk

Usage:
  python3 verify.py hash <content> [--sources "url1,url2"]
  python3 verify.py publish <passphrase|-|env:VAR|@worker|@file:PATH> <content_hash> [--label "task"]
  python3 verify.py verify <content> <tx_id> [--sources "url1,url2"]
  python3 verify.py proofs <address> [--limit 20]
  python3 verify.py stamp <passphrase|-|env:VAR|@worker|@file:PATH> <content> [--sources ...] [--label L]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _sdk_compat  # noqa: F401 — patches SDK 0.3.0 get_my_address passphrase leak

from signaai import verify as _verify

hash_content  = _verify.hash_content
publish_proof = _verify.publish_proof
verify_proof  = _verify.verify_proof
get_proofs    = _verify.get_proofs
stamp         = _verify.stamp
check         = _verify.check
main          = _verify.main

if __name__ == "__main__":
    sys.exit(main())
