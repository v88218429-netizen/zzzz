#!/usr/bin/env python3
"""Cumulative hotfix patch for Avito AI Manager.

This file is intentionally safe to run repeatedly. Future fixes are added here.
It never touches .env, .venv, or data/avito_ai.db.
"""
from __future__ import annotations
import argparse
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', required=True)
    ap.add_argument('--target-version', required=True)
    args = ap.parse_args()
    root = Path(args.root).resolve()
    protected = [root / '.env', root / '.venv', root / 'data' / 'avito_ai.db']
    # v0.5.0 baseline: no code changes are necessary.
    # Future cumulative edits will be placed above the VERSION write.
    (root / 'VERSION').write_text(str(args.target_version).strip() + '\n', encoding='utf-8')
    print(f'Patched Avito AI Manager -> {args.target_version}')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
