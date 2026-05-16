#!/usr/bin/env python3
"""
KSC Cloud Phantom — Headless CLI daemon for 24/7 course availability monitoring.

Usage:
    python cloud_cli.py --sections 26012,26686 --interval 15 --waitlist
    python cloud_cli.py --sections 26012 --interval 10 --no-headless  # first-time setup
"""

import argparse
import json
import logging
import signal
import sys

from core_auth import KeanAuthManager
from core_api import KeanApiClient
from cloud_worker import CloudWorker


def setup_logging():
    logger = logging.getLogger("CloudSniper")
    logger.setLevel(logging.DEBUG)

    # Console handler — INFO and above
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(
        '%(asctime)s  %(levelname)-7s  %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'))
    logger.addHandler(console)

    # File handler — everything
    fh = logging.FileHandler('cloud_sniper.log', encoding='utf-8')
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        '%(asctime)s  %(levelname)-7s  %(message)s'))
    logger.addHandler(fh)

    return logger


def parse_args():
    parser = argparse.ArgumentParser(
        description='KSC Cloud Phantom — Headless CLI Auto-Catch Daemon')
    parser.add_argument(
        '--sections', required=True,
        help='Target section IDs, comma-separated (e.g. 26012,26686)')
    parser.add_argument(
        '--interval', type=int, default=15,
        help='Polling interval in seconds (default: 15, minimum enforced: 1)')
    parser.add_argument(
        '--waitlist', action='store_true',
        help='Enable waitlist fallback when courses are full')
    parser.add_argument(
        '--no-headless', action='store_true',
        help='Show browser window for initial manual login setup')
    parser.add_argument(
        '--load-session', metavar='FILE',
        help='Load credentials from a session JSON file instead of browser auth')
    parser.add_argument(
        '--webhook', metavar='URL',
        help='Webhook URL for push notification on successful registration')
    return parser.parse_args()


def load_session_from_file(filepath, logger):
    """
    Load credentials from a session JSON file exported by export_session.py.

    Expected format:
        {"cookies": {...}, "verification_token": "...", "student_id": "..."}
    """
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        logger.error("Session file not found: %s", filepath)
        return None
    except json.JSONDecodeError as e:
        logger.error("Invalid session JSON in %s: %s", filepath, e)
        return None

    required = ("cookies", "verification_token", "student_id")
    missing = [k for k in required if k not in data]
    if missing:
        logger.error("Session file missing keys: %s", missing)
        return None

    logger.info("Session loaded from %s (Student ID: %s)",
                filepath, data["student_id"])
    return data


def authenticate(logger, headless: bool, session_file: str = None):
    """
    Authenticate and return a (KeanApiClient, KeanAuthManager) tuple.

    Three paths (in order of priority):
      1. --load-session FILE  → skip browser, load from JSON
      2. headless             → silent_relogin (auto-click Okta)
      3. visible browser      → manual login
    """
    auth = None

    # Path 1: Load from session file
    if session_file:
        creds = load_session_from_file(session_file, logger)
        if creds is None:
            return None, None
        # No auth_manager when loading from file — session recovery
        # won't work, but the worker will log and stop cleanly.
        engine = KeanApiClient(
            cookies_dict=creds["cookies"],
            verification_token=creds["verification_token"],
            student_id=creds["student_id"])
        return engine, None

    auth = KeanAuthManager()

    # Path 2: Headless silent re-login
    if headless:
        logger.info("Attempting silent re-authentication (headless)...")
        try:
            creds = auth.silent_relogin(headless=True)
            logger.info("Silent authentication successful. Student ID: %s",
                        creds.get('student_id'))
        except Exception as e:
            logger.error("Silent authentication failed: %s", e)
            logger.error(
                "Use --no-headless for first-time manual login, or "
                "--load-session to import exported credentials.")
            return None, None

    # Path 3: Visible browser for manual login
    else:
        logger.info("Launching visible browser for manual login...")
        auth.launch_browser(headless=False)
        input(">>> Complete SSO login in the browser, then press Enter...")
        creds = auth.extract_credentials()
        if "error" in creds:
            logger.error("Credential extraction failed: %s", creds["error"])
            return None, None
        logger.info("Manual login successful. Student ID: %s",
                    creds.get('student_id'))

    engine = KeanApiClient(
        cookies_dict=creds["cookies"],
        verification_token=creds["verification_token"],
        student_id=creds["student_id"])
    return engine, auth


def main():
    args = parse_args()
    logger = setup_logging()

    section_ids = [s.strip() for s in args.sections.split(',') if s.strip()]
    if not section_ids:
        logger.error("No valid section IDs provided.")
        sys.exit(1)

    headless = not args.no_headless

    logger.info("=" * 56)
    logger.info("KSC Cloud Phantom — 24/7 Auto-Catch Daemon")
    logger.info("Sections : %s", section_ids)
    logger.info("Interval : %ds (min 1s enforced)", args.interval)
    logger.info("Waitlist : %s", "ON" if args.waitlist else "OFF")
    if args.load_session:
        logger.info("Auth     : Session file (%s)", args.load_session)
    else:
        logger.info("Mode     : %s", "Headless" if headless else "Visible Browser")
    if args.webhook:
        logger.info("Webhook  : %s", args.webhook)
    logger.info("=" * 56)

    engine, auth = authenticate(logger, headless, session_file=args.load_session)
    if engine is None:
        logger.error("Authentication failed. Exiting.")
        sys.exit(1)

    worker = CloudWorker(
        api_engine=engine,
        section_ids=section_ids,
        interval=args.interval,
        enable_waitlist=args.waitlist,
        auth_manager=auth,
        webhook_url=args.webhook,
    )

    # Graceful shutdown on Ctrl+C
    def handle_signal(signum, frame):
        logger.info("Received signal %s. Shutting down gracefully...", signum)
        worker.stop()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    logger.info("Starting Auto-Catch worker...")
    worker.start()

    # Wait for worker to finish
    try:
        while worker.is_alive():
            worker.join(timeout=1)
    except KeyboardInterrupt:
        worker.stop()
        worker.join(timeout=5)

    logger.info("Cloud Phantom exited.")
    # Close browser if still open
    if auth is not None:
        auth.close_browser()


if __name__ == '__main__':
    main()
