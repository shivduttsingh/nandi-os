from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import MutableMapping


class CredentialConfigurationError(RuntimeError):
    """Raised when the app is started without configured credentials."""


@dataclass(frozen=True)
class AuthenticationResult:
    authenticated: bool
    locked: bool
    attempts_remaining: int
    retry_after_seconds: int = 0


class LoginLockout:
    """Session-backed failed-login tracking with a fixed lockout window."""

    state_key = "_nandi_login_attempts"

    def __init__(
        self,
        state: MutableMapping[str, object],
        max_attempts: int = 5,
        lockout_seconds: int = 15 * 60,
    ) -> None:
        if max_attempts < 1 or lockout_seconds < 1:
            raise ValueError("Lockout settings must be positive")
        self.state = state
        self.max_attempts = max_attempts
        self.lockout_seconds = lockout_seconds

    @staticmethod
    def _now(now: datetime | None) -> datetime:
        value = now or datetime.now(timezone.utc)
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    @staticmethod
    def _subject(username: str) -> str:
        return username.strip().casefold()

    def _records(self) -> dict[str, dict[str, object]]:
        records = self.state.get(self.state_key)
        if not isinstance(records, dict):
            records = {}
            self.state[self.state_key] = records
        return records

    def _status(self, username: str, now: datetime) -> tuple[int, int]:
        record = self._records().get(self._subject(username), {})
        failures = int(record.get("failures", 0))
        locked_until = record.get("locked_until")
        if isinstance(locked_until, datetime) and now < locked_until:
            return failures, max(1, int((locked_until - now).total_seconds()))
        if failures >= self.max_attempts:
            self._records().pop(self._subject(username), None)
            return 0, 0
        return failures, 0

    def authenticate(
        self,
        username: str,
        password: str,
        expected_username: str | None,
        expected_password: str | None,
        now: datetime | None = None,
    ) -> AuthenticationResult:
        if not expected_username or not expected_password:
            raise CredentialConfigurationError(
                "Authentication credentials must be configured in Streamlit secrets."
            )

        current = self._now(now)
        failures, retry_after = self._status(username, current)
        if retry_after:
            return AuthenticationResult(False, True, 0, retry_after)

        valid_username = hmac.compare_digest(username.strip(), expected_username)
        valid_password = hmac.compare_digest(password, expected_password)
        if valid_username and valid_password:
            self._records().pop(self._subject(username), None)
            return AuthenticationResult(True, False, self.max_attempts)

        failures += 1
        if failures >= self.max_attempts:
            locked_until = current + timedelta(seconds=self.lockout_seconds)
            self._records()[self._subject(username)] = {
                "failures": failures,
                "locked_until": locked_until,
            }
            return AuthenticationResult(False, True, 0, self.lockout_seconds)

        self._records()[self._subject(username)] = {"failures": failures}
        return AuthenticationResult(False, False, self.max_attempts - failures)
