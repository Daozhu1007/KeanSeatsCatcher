"""One bounded recovery attempt, shared by all registration workers."""
import threading

from registration_state import SessionStatus

# Registration workers and the background keepalive share one auth manager;
# a single lock keeps two silent re-logins from racing the same driver.
_recovery_lock = threading.Lock()


def recover_session(client, auth_manager, headless=False, section_ids=()):
    with _recovery_lock:
        if auth_manager is None:
            return False
        try:
            creds = auth_manager.silent_relogin(headless=headless)
            client.update_credentials(creds["cookies"], creds["verification_token"],
                                      student_id=creds["student_id"])
            recovered = client.check_session().status == SessionStatus.VALID
            if recovered and section_ids:
                client.verify_registration(section_ids)
            client._event("session_recovery", recovered=recovered)
            return recovered
        except Exception as exc:
            client._event("session_recovery", recovered=False, reason=type(exc).__name__)
            return False
