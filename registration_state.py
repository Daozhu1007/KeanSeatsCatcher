"""Registration evidence shared by Dashboard, Auto-Catch and the CLI.

The Ellucian adapter follows the observed read-only DegreePlans/Current response
(see docs/Registration-Verification.md).
"""
from dataclasses import dataclass, field
from enum import Enum
from math import isfinite
from typing import Mapping, Protocol, Sequence


def valid_credits(value):
    return ((type(value) is int and value >= 0)
            or (type(value) is float and isfinite(value) and value >= 0))


def section_credit_limits(section):
    minimum = section.get("MinimumCredits")
    if not valid_credits(minimum) or "MaximumCredits" not in section:
        return None
    maximum = section["MaximumCredits"]
    if maximum is None:
        return minimum, minimum
    if valid_credits(maximum) and maximum >= minimum:
        return minimum, maximum
    return None


class SectionStatus(str, Enum):
    UNKNOWN = "UNKNOWN"
    REGISTERED = "REGISTERED"
    PLANNED = "PLANNED"
    NOT_REGISTERED = "NOT_REGISTERED"
    REJECTED = "REJECTED"
    WAITLISTED = "WAITLISTED"


class SessionStatus(str, Enum):
    VALID = "VALID"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"


class SubmissionStatus(str, Enum):
    ACKNOWLEDGED = "ACKNOWLEDGED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


@dataclass
class SessionCheck:
    status: SessionStatus
    elapsed_ms: int
    reason: str
    transient_network_error: bool = False
    current: bool = True


@dataclass(frozen=True)
class SessionState:
    """Evidence for the retained jar, with monotonic activity and cooldown clocks."""
    status: SessionStatus | None = None
    last_successful_check_at: float | None = None
    last_auth_cookie_renewal_at: float | None = None
    last_activity: float | None = None
    cooldown_until: float = 0.0


@dataclass
class SectionResult:
    section_id: str
    status: SectionStatus = SectionStatus.UNKNOWN
    reason: str = "not_attempted"
    attempted: bool = False
    awaiting_verification: bool = False
    rejection: str = ""
    term_id: str | None = None
    first_confirmed_at: str | None = None
    # Only a verified reader can supply this; client receipt time is NOT it.
    server_registered_at: str | None = None


@dataclass(frozen=True)
class RegistrationRecord:
    status: SectionStatus
    server_registered_at: str | None = None
    term_id: str | None = None
    reason: str = ""


@dataclass
class RegistrationSnapshot:
    records: Mapping[str, RegistrationRecord] = field(default_factory=dict)
    reason: str = "reader_not_configured"
    planned_credits: Mapping[str, int | float] = field(default_factory=dict)


class RegistrationReader(Protocol):
    def read(self, client, section_ids: Sequence[str]) -> RegistrationSnapshot:
        """Read current account records. Missing/ambiguous records stay UNKNOWN.

        NOT_REGISTERED requires an authoritative, complete view for that Section
        and term; absence from a partial page is insufficient. Use the client's
        request helper for session classification and redacted request logging.
        """
        ...


@dataclass
class SubmissionResult:
    status: SubmissionStatus
    reason: str
    rejections: dict[str, str] = field(default_factory=dict)
    duplicates: set[str] = field(default_factory=set)
    should_complete: bool = False

    def __bool__(self):
        raise TypeError("Inspect SubmissionResult.status; acknowledgment is not enrollment")
