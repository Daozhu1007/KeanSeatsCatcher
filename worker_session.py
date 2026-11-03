"""One bounded recovery attempt, shared by all registration workers."""
from registration_state import SessionStatus


def recover_session(client, auth_manager, headless=False):
    if auth_manager is None:
        return False
    try:
        creds = auth_manager.silent_relogin(headless=headless)
        client.update_credentials(creds["cookies"], creds["verification_token"],
                                  student_id=creds["student_id"])
        recovered = client.check_session().status == SessionStatus.VALID
        client._event("session_recovery", recovered=recovered)
        return recovered
    except Exception as exc:
        client._event("session_recovery", recovered=False, reason=type(exc).__name__)
        return False
