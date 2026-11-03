"""Registration evidence shared by Dashboard, Auto-Catch and the CLI.

There is deliberately no guessed Ellucian schedule endpoint here. Implement
RegistrationReader only after capturing a read-only request and its response
from the authenticated Self-Service page (see docs/Registration-Verification.md).
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Protocol, Sequence


class SectionStatus(str, Enum):
    UNKNOWN = "UNKNOWN"
    REGISTERED = "REGISTERED"
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


@dataclass
class SectionResult:
    section_id: str
    status: SectionStatus = SectionStatus.UNKNOWN
    reason: str = "not_attempted"
    attempted: bool = False
    awaiting_verification: bool = False
    rejection: str = ""
    first_confirmed_at: str | None = None
    # Only a verified reader can supply this; client receipt time is NOT it.
    server_registered_at: str | None = None


@dataclass(frozen=True)
class RegistrationRecord:
    status: SectionStatus
    server_registered_at: str | None = None


@dataclass
class RegistrationSnapshot:
    records: Mapping[str, RegistrationRecord] = field(default_factory=dict)
    reason: str = "reader_not_configured"


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

    def __bool__(self):
        raise TypeError("Inspect SubmissionResult.status; acknowledgment is not enrollment")
