import json
import threading
import logging

import requests

from core_api import SessionExpiredError

logger = logging.getLogger("CloudSniper")


class CloudWorker(threading.Thread):
    """
    Headless polling worker — pure threading.Thread, no PyQt6 dependency.

    Replicates the AutoCatchWorker polling loop with identical logic:
    poll check_section_status → on seat found execute_full_attack →
    on SessionExpiredError silent_relogin → resume.

    All status output goes through Python's standard logging module.
    """

    def __init__(self, api_engine, section_ids, interval,
                 enable_waitlist=False, auth_manager=None,
                 webhook_url=None):
        super().__init__(daemon=True)
        self.api_engine = api_engine
        self.section_ids = section_ids
        self.interval = interval
        self.enable_waitlist = enable_waitlist
        self.auth_manager = auth_manager
        self.webhook_url = webhook_url
        self.is_running = True
        self.recovery_count = 0
        self._stop_event = threading.Event()

        # Route API engine logs to python logging
        def _api_log(msg, level="normal"):
            if level == "error":
                logger.error("API >> %s", msg)
            elif level == "success":
                logger.info("API >> %s", msg)
            else:
                logger.debug("API >> %s", msg)

        self.api_engine.log_callback = _api_log

    def _sleep(self, seconds: float):
        self._stop_event.wait(seconds)

    def _send_webhook(self, title: str, message: str):
        """Fire-and-forget webhook notification. Never blocks the worker."""
        if not self.webhook_url:
            return
        try:
            payload = {"title": title, "message": message}
            requests.post(
                self.webhook_url,
                json=payload,
                timeout=(3, 5),
                headers={'content-type': 'application/json'}
            )
        except Exception:
            logger.debug("Webhook delivery failed (non-fatal)")

    def _recover_session(self) -> bool:
        """
        Attempt silent re-authentication.

        Returns True if recovery succeeded and the worker should continue.
        Returns False if recovery failed and the worker should stop.
        """
        logger.warning("Session expired. Initiating automatic re-authentication...")

        if self.auth_manager is None:
            logger.error("No auth manager available for session recovery. Stopping.")
            self.is_running = False
            return False

        try:
            creds = self.auth_manager.silent_relogin()
            self.api_engine.update_credentials(
                creds["cookies"], creds["verification_token"])
            self.recovery_count += 1
            logger.info("Session recovered (#%d). Resuming Auto-Catch...",
                        self.recovery_count)
            return True
        except Exception as e:
            logger.error("Auto-recovery failed: %s. Stopping.", e)
            self.is_running = False
            return False

    def stop(self):
        """Signal the worker to stop gracefully."""
        self.is_running = False
        self._stop_event.set()

    def run(self):
        self._stop_event.clear()
        round_count = 0

        while self.is_running:
            round_count += 1
            logger.info("--- Round #%d | Recoveries: %d ---",
                        round_count, self.recovery_count)

            found_seats = False
            session_lost = False
            for sec_id in self.section_ids:
                if not self.is_running:
                    break
                try:
                    if self.api_engine.check_section_status(
                            sec_id, enable_waitlist=self.enable_waitlist):
                        found_seats = True
                        logger.info("Seat found! Section %s", sec_id)
                        break
                except SessionExpiredError:
                    session_lost = True
                    break

            if session_lost:
                if not self._recover_session():
                    break
                continue

            if found_seats:
                logger.info("Attacking sections: %s", self.section_ids)

                try:
                    success, msg = self.api_engine.execute_full_attack(
                        self.section_ids,
                        enable_waitlist=self.enable_waitlist)
                except SessionExpiredError:
                    logger.warning(
                        "Session expired during attack. Initiating recovery...")
                    if not self._recover_session():
                        break
                    continue

                if success:
                    logger.info("SUCCESS: %s", msg)
                    self._send_webhook(
                        "KSC Auto-Catch",
                        f"Successfully registered sections: {', '.join(self.section_ids)}")
                    self.is_running = False
                    break
                else:
                    logger.error("Attack failed: %s, resuming polling...", msg)
            else:
                logger.debug("No seats available in target sections.")

            if self.is_running:
                self._sleep(max(1.0, self.interval))

        logger.info("CloudWorker stopped.")
