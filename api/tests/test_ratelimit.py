"""Rate limiting on the credential endpoints.

The admin account is the one worth guessing, so the interesting cases here are
not "does it count to twenty" but the ways a limiter quietly fails to limit:
a spoofable key, a window an attacker can straddle, a dictionary that grows
without bound, and a 429 that answers a question the 401 refused to.
"""
from __future__ import annotations

import time

import pytest

from app import ratelimit
from app.config import settings
from app.ratelimit import SlidingWindowLimiter


@pytest.fixture(autouse=True)
def clean_limiters():
    ratelimit.reset_all()
    yield
    ratelimit.reset_all()


# --- the limiter itself ----------------------------------------------------


def test_it_allows_up_to_the_limit_then_refuses():
    limiter = SlidingWindowLimiter(limit=3, window_seconds=60)

    for _ in range(3):
        assert limiter.check("k") is None
        limiter.record("k")

    retry_after = limiter.check("k")
    assert retry_after is not None and 0 < retry_after <= 60


def test_keys_are_independent():
    limiter = SlidingWindowLimiter(limit=1, window_seconds=60)
    limiter.record("a")
    assert limiter.check("a") is not None
    assert limiter.check("b") is None


def test_the_window_slides_rather_than_resetting_on_a_boundary():
    """A fixed window hands an attacker 2x the limit across a boundary."""
    limiter = SlidingWindowLimiter(limit=2, window_seconds=0.25)

    limiter.record("k")
    limiter.record("k")
    assert limiter.check("k") is not None

    time.sleep(0.3)
    assert limiter.check("k") is None, "hits older than the window should age out"


def test_retry_after_shrinks_as_the_window_passes():
    limiter = SlidingWindowLimiter(limit=1, window_seconds=1.0)
    limiter.record("k")

    first = limiter.check("k")
    time.sleep(0.2)
    second = limiter.check("k")

    assert first is not None and second is not None
    assert second < first


def test_clearing_a_key_forgets_it():
    limiter = SlidingWindowLimiter(limit=1, window_seconds=60)
    limiter.record("k")
    assert limiter.check("k") is not None

    limiter.clear("k")
    assert limiter.check("k") is None


def test_the_key_space_is_bounded():
    """Otherwise the limiter is itself a memory-exhaustion attack: rotate the
    key per request and the dictionary grows forever."""
    limiter = SlidingWindowLimiter(limit=5, window_seconds=600, max_keys=50)

    for n in range(500):
        limiter.record(f"key-{n}")

    assert len(limiter._hits) <= 50


def test_eviction_drops_the_least_recently_used_key():
    limiter = SlidingWindowLimiter(limit=5, window_seconds=600, max_keys=2)
    limiter.record("old")
    limiter.record("kept")
    limiter.record("kept")      # touched again, so "old" is the stale one
    limiter.record("new")

    assert "old" not in limiter._hits
    assert "kept" in limiter._hits and "new" in limiter._hits


def test_it_is_safe_under_concurrent_hits():
    """APScheduler runs jobs on other threads, so this is not asyncio-only."""
    import threading

    limiter = SlidingWindowLimiter(limit=10_000, window_seconds=600)

    def hammer():
        for _ in range(200):
            limiter.record("shared")

    threads = [threading.Thread(target=hammer) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(limiter._hits["shared"]) == 8 * 200


# --- client address resolution --------------------------------------------


class FakeRequest:
    def __init__(self, *, peer: str | None = "10.0.0.1", headers: dict | None = None):
        self.headers = headers or {}

        class _Client:
            host = peer

        self.client = _Client() if peer is not None else None


def test_the_socket_peer_is_used_by_default(monkeypatch):
    monkeypatch.setattr(settings, "client_ip_header", "", raising=False)
    assert ratelimit.client_ip(FakeRequest(peer="203.0.113.7")) == "203.0.113.7"


def test_an_unconfigured_header_is_ignored(monkeypatch):
    """The whole point: a header nobody vouched for must not become the key,
    or an attacker sets a different one per request and is never limited."""
    monkeypatch.setattr(settings, "client_ip_header", "", raising=False)

    spoofed = FakeRequest(
        peer="203.0.113.7",
        headers={"X-Forwarded-For": "1.2.3.4", "Fly-Client-IP": "5.6.7.8"},
    )
    assert ratelimit.client_ip(spoofed) == "203.0.113.7"


def test_a_configured_header_is_used(monkeypatch):
    monkeypatch.setattr(settings, "client_ip_header", "Fly-Client-IP", raising=False)
    request = FakeRequest(peer="10.0.0.1", headers={"Fly-Client-IP": "203.0.113.9"})
    assert ratelimit.client_ip(request) == "203.0.113.9"


def test_a_configured_header_falls_back_when_absent(monkeypatch):
    monkeypatch.setattr(settings, "client_ip_header", "Fly-Client-IP", raising=False)
    assert ratelimit.client_ip(FakeRequest(peer="10.0.0.1")) == "10.0.0.1"


def test_a_missing_peer_still_yields_a_key(monkeypatch):
    """Keyed as "unknown" rather than crashing -- and they all share a bucket,
    which is the safe direction."""
    monkeypatch.setattr(settings, "client_ip_header", "", raising=False)
    assert ratelimit.client_ip(FakeRequest(peer=None)) == "unknown"


# --- the endpoints ---------------------------------------------------------


def _login(client, email: str, password: str, **kwargs):
    return client.post("/auth/login", json={"email": email, "password": password}, **kwargs)


def test_repeated_wrong_passwords_eventually_get_429(client, register, monkeypatch):
    monkeypatch.setattr(settings, "client_ip_header", "", raising=False)
    user, _ = register("victim")

    limit = settings.login_max_attempts_per_account
    for n in range(limit):
        assert _login(client, user.email, "wrong-password").status_code == 401, n

    throttled = _login(client, user.email, "wrong-password")
    assert throttled.status_code == 429
    assert int(throttled.headers["Retry-After"]) >= 1


def test_the_correct_password_still_works_below_the_limit(client, register):
    user, _ = register("careless")

    assert _login(client, user.email, "nope").status_code == 401
    assert _login(client, user.email, "testpassword").status_code == 200


def test_a_successful_login_clears_the_account_bucket(client, register):
    """Someone who mistypes twice and then succeeds should not be left one
    attempt away from a lockout."""
    user, _ = register("mistypes")

    for _ in range(3):
        _login(client, user.email, "wrong")
    assert _login(client, user.email, "testpassword").status_code == 200

    assert ratelimit.login_by_account.check(user.email.lower()) is None


def test_throttling_does_not_reveal_whether_an_account_exists(client, register, monkeypatch):
    monkeypatch.setattr(settings, "client_ip_header", "", raising=False)

    limit = settings.login_max_attempts_per_account
    for _ in range(limit):
        _login(client, "nobody-here@starnet-demo.org", "guess")

    refused = _login(client, "nobody-here@starnet-demo.org", "guess")
    assert refused.status_code == 429
    # The same wording a real address would get.
    assert "Too many attempts" in refused.json()["detail"]


def test_the_account_bucket_is_case_insensitive(client, register):
    """Otherwise the limit is bypassed by changing one letter's case."""
    user, _ = register("mixedcase")

    for _ in range(settings.login_max_attempts_per_account):
        _login(client, user.email.upper(), "wrong")

    assert _login(client, user.email.lower(), "wrong").status_code == 429


def test_signup_is_limited_per_address(client, monkeypatch):
    monkeypatch.setattr(settings, "client_ip_header", "", raising=False)
    import uuid as _uuid

    for n in range(settings.signup_max_per_ip):
        suffix = _uuid.uuid4().hex[:8]
        response = client.post(
            "/auth/signup",
            json={
                "email": f"a{suffix}@starnet-demo.org",
                "password": "testpassword",
                "handle": f"h{suffix}",
            },
        )
        assert response.status_code == 201, (n, response.text)

    suffix = _uuid.uuid4().hex[:8]
    blocked = client.post(
        "/auth/signup",
        json={
            "email": f"a{suffix}@starnet-demo.org",
            "password": "testpassword",
            "handle": f"h{suffix}",
        },
    )
    assert blocked.status_code == 429


def test_an_unknown_address_costs_the_same_work_as_a_known_one(client, register):
    """The timing oracle. Without the dummy-hash comparison an unknown address
    returns in microseconds and a known one takes a bcrypt round, which answers
    "is this registered?" to anyone with a stopwatch.

    Asserted as a ratio with wide tolerance -- this is a wall-clock test and
    the point is the order of magnitude, not the milliseconds.
    """
    user, _ = register("known")

    def elapsed(email: str) -> float:
        ratelimit.reset_all()
        start = time.perf_counter()
        _login(client, email, "some-wrong-password")
        return time.perf_counter() - start

    known = min(elapsed(user.email) for _ in range(3))
    unknown = min(elapsed("definitely-not-here@starnet-demo.org") for _ in range(3))

    ratio = max(known, unknown) / max(1e-6, min(known, unknown))
    assert ratio < 5, f"timing differs too much: known={known:.4f}s unknown={unknown:.4f}s"


def test_the_burst_window_is_short_enough_to_not_be_a_denial_of_service(client, register):
    """A throttled account is refused even with the right password, so anyone
    who knows an address can deny its owner access for the length of the
    window. That is the safe choice -- it gives an attacker no signal -- but it
    makes the window length a real decision, not a detail.
    """
    assert settings.login_window_seconds <= 300, (
        "the burst window locks out the legitimate owner for its full length; "
        "keep it in minutes"
    )

    user, _ = register("targeted")
    for _ in range(settings.login_max_attempts_per_account):
        _login(client, user.email, "guess")

    refused = _login(client, user.email, "testpassword")
    assert refused.status_code == 429
    assert int(refused.headers["Retry-After"]) <= settings.login_window_seconds + 1


def test_the_daily_band_caps_sustained_guessing(client, register, monkeypatch):
    """The short burst window alone would allow thousands of guesses a day,
    which is enough to find a weak password. The long band is what stops that.
    """
    daily = settings.login_max_attempts_per_account_daily
    burst = settings.login_max_attempts_per_account
    assert daily > burst, "the daily band must be the looser of the two"

    user, _ = register("persistent")

    # Spend the daily budget, stepping past the burst window each time it fills
    # by clearing only the burst band -- which is what waiting would do.
    for n in range(daily):
        response = _login(client, user.email, "guess")
        assert response.status_code == 401, (n, response.status_code)
        ratelimit.login_by_account.clear(user.email.lower())

    # Burst band clear, daily band full: still refused.
    assert ratelimit.login_by_account.check(user.email.lower()) is None
    assert _login(client, user.email, "guess").status_code == 429


def test_a_success_clears_both_bands(client, register):
    user, _ = register("recovers")

    for _ in range(3):
        _login(client, user.email, "wrong")
    assert _login(client, user.email, "testpassword").status_code == 200

    key = user.email.lower()
    assert ratelimit.login_by_account.check(key) is None
    assert ratelimit.login_by_account_daily.check(key) is None
