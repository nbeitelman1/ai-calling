#!/usr/bin/env python3
"""Seed (or refresh) the starter profile in the profile store.

Idempotent — safe to re-run after restores. The store path follows
AI_CALLING_PROFILE_DB / AI_CALLING_LOG_DIR like the server does.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from profile_store import ProfileStore, default_profile_db_path  # noqa: E402
import profile_seed  # noqa: E402


def main():
    path = default_profile_db_path()
    store = ProfileStore(path)
    result = profile_seed.seed_starter_profile(store)
    store.close()
    print(f"profile db: {path}")
    print(f"seed result: {result}")


if __name__ == "__main__":
    main()
