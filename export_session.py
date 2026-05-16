#!/usr/bin/env python3
"""
KSC Session Smuggling — Export live credentials to a portable JSON file.

Run this on your local Windows machine (with a display) to capture a
valid session. Then copy session.json to your Linux cloud server and
start cloud_cli.py with --load-session session.json to skip browser auth.

Usage:
    python export_session.py
    python export_session.py --output session.json
"""

import argparse
import json
import sys

from core_auth import KeanAuthManager


def main():
    parser = argparse.ArgumentParser(
        description='Export Kean API session to a portable JSON file')
    parser.add_argument(
        '--output', default='session.json',
        help='Output file path (default: session.json)')
    args = parser.parse_args()

    auth = KeanAuthManager()

    print("Launching browser for manual login...")
    auth.launch_browser(headless=False)

    input(">>> Complete SSO login in the browser, then press Enter to extract...")

    creds = auth.extract_credentials()
    if "error" in creds:
        print(f"[ERROR] Extraction failed: {creds['error']}")
        auth.close_browser()
        sys.exit(1)

    payload = {
        "cookies": creds["cookies"],
        "verification_token": creds["verification_token"],
        "student_id": creds["student_id"],
    }

    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    print(f"[SUCCESS] Session exported to {args.output}")
    print(f"  Student ID : {creds['student_id']}")
    print(f"  Cookies    : {len(creds['cookies'])} entries")
    print(f"  Token      : {creds['verification_token'][:20]}...")
    print()
    print("Copy this file to your cloud server and start with:")
    print(f"  python cloud_cli.py --sections 26012,26686 --load-session {args.output}")

    auth.close_browser()


if __name__ == '__main__':
    main()
