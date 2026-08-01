from datetime import datetime, timedelta, timezone

from nandi_oi.auth import CredentialConfigurationError, LoginLockout


def test_failed_logins_lock_the_session_and_expire_after_window():
    state = {}
    lockout = LoginLockout(state, max_attempts=3, lockout_seconds=60)
    now = datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc)

    for remaining in (2, 1):
        result = lockout.authenticate("nandi", "wrong", "nandi", "secret", now)
        assert not result.authenticated
        assert not result.locked
        assert result.attempts_remaining == remaining

    locked = lockout.authenticate("nandi", "wrong", "nandi", "secret", now)
    assert locked.locked
    assert locked.retry_after_seconds == 60

    still_locked = lockout.authenticate(
        "nandi", "secret", "nandi", "secret", now + timedelta(seconds=30),
    )
    assert still_locked.locked

    recovered = lockout.authenticate(
        "nandi", "secret", "nandi", "secret", now + timedelta(seconds=61),
    )
    assert recovered.authenticated
    assert not recovered.locked


def test_credentials_must_be_configured_and_success_clears_failures():
    lockout = LoginLockout({}, max_attempts=2, lockout_seconds=60)
    now = datetime(2026, 8, 1, tzinfo=timezone.utc)

    try:
        lockout.authenticate("nandi", "secret", None, None, now)
    except CredentialConfigurationError:
        pass
    else:
        raise AssertionError("Expected missing credentials to be rejected")

    lockout.authenticate("nandi", "wrong", "nandi", "secret", now)
    result = lockout.authenticate("nandi", "secret", "nandi", "secret", now)
    assert result.authenticated
    assert result.attempts_remaining == 2
