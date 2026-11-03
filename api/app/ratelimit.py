"""Rate limiting for the credential endpoints.

The per-user report caps in `routers/reports.py` protect the mechanism from
being flooded with fabricated faults. This protects the front door: without it,
nothing slows down someone guessing passwords against `/auth/login`, and the
admin account is the one worth guessing.

Deliberately in-process and in-memory. The deployment runs a single API
instance (APScheduler lives in it, see fly.toml), so a shared store would buy
nothing but a Redis dependency. Two honest consequences:

  - A restart forgets the counters. For slowing down password guessing that is
    acceptable; an attacker cannot force restarts, and a deploy resetting the
    window costs them nothing they did not already have.
  - Scaling to N instances divides the effective limit by N, because each
    keeps its own counts. The swap point is the same one as for the scheduler:
    when this moves to several machines, both need a shared backend.

Two things that are easy to get wrong and are handled here: the key space is
bounded, so the limiter cannot itself be turned into a memory-exhaustion
attack; and the clock is monotonic, so a system time adjustment cannot either
stall the window or clear it.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque

from fastapi import HTTPException, Request, status

from app.config import settings


class SlidingWindowLimiter:
    """Counts hits per key inside a rolling window.

    A sliding window rather than a fixed one: with fixed windows an attacker
    gets 2x the limit by straddling a boundary, which for a login endpoint is
    exactly the behaviour being defended against.
    """

    def __init__(self, *, limit: int, window_seconds: float, max_keys: int = 10_000):
        self.limit = limit
        self.window = window_seconds
        # An attacker rotating source addresses or emails would otherwise grow
        # this dict without bound. Oldest-touched keys are evicted first; an
        # evicted key starts fresh, which is the safe direction to fail for a
        # throttle (it never locks out a legitimate user forever).
        self.max_keys = max_keys
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()
        # APScheduler runs jobs on other threads, so this is not asyncio-only.
        self._lock = threading.Lock()

    def _prune(self, stamps: deque[float], now: float) -> None:
        cutoff = now - self.window
        while stamps and stamps[0] <= cutoff:
            stamps.popleft()

    def check(self, key: str) -> float | None:
        """Seconds to wait if `key` is over the limit, else None. Records nothing."""
        now = time.monotonic()
        with self._lock:
            stamps = self._hits.get(key)
            if stamps is None:
                return None
            self._prune(stamps, now)
            if len(stamps) < self.limit:
                return None
            return max(0.0, self.window - (now - stamps[0]))

    def record(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            stamps = self._hits.get(key)
            if stamps is None:
                stamps = deque()
                self._hits[key] = stamps
            else:
                self._hits.move_to_end(key)
            self._prune(stamps, now)
            stamps.append(now)

            while len(self._hits) > self.max_keys:
                self._hits.popitem(last=False)

    def clear(self, key: str) -> None:
        """Forget a key -- used on a successful sign-in, so somebody who
        mistyped their password twice is not still counted against."""
        with self._lock:
            self._hits.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


# One limiter per thing being protected. Login is split in two because the two
# attacks are different shapes: many guesses at one account, and a few guesses
# at each of many accounts.
login_by_ip = SlidingWindowLimiter(
    limit=settings.login_max_attempts_per_ip,
    window_seconds=settings.login_window_seconds,
)
# Two bands on the same key. The short one keeps a locked-out owner's wait
# down to minutes; the long one is what actually caps an attacker's guesses.
login_by_account = SlidingWindowLimiter(
    limit=settings.login_max_attempts_per_account,
    window_seconds=settings.login_window_seconds,
)
login_by_account_daily = SlidingWindowLimiter(
    limit=settings.login_max_attempts_per_account_daily,
    window_seconds=settings.login_account_daily_window_seconds,
)
signup_by_ip = SlidingWindowLimiter(
    limit=settings.signup_max_per_ip,
    window_seconds=settings.signup_window_seconds,
)

ALL_LIMITERS = (login_by_ip, login_by_account, login_by_account_daily, signup_by_ip)


def reset_all() -> None:
    """For tests. Nothing in the app should need this."""
    for limiter in ALL_LIMITERS:
        limiter.reset()


def client_ip(request: Request) -> str:
    """The caller's address, as well as it can be known.

    Behind a proxy the socket peer is the proxy, so every caller would share one
    bucket. The fix is to read the address from a header -- but a header is
    attacker-controlled unless something trustworthy overwrites it, and
    trusting a spoofable one turns the limiter off completely (a different key
    per request means nobody is ever over the limit).

    So the header is opt-in by name, through STARNET_CLIENT_IP_HEADER, and is
    meant for one a proxy *overwrites*: `Fly-Client-IP` on Fly,
    `CF-Connecting-IP` behind Cloudflare. Do not point it at
    `X-Forwarded-For`, which is appended to and whose left-hand entries are
    whatever the client sent.
    """
    header = settings.client_ip_header.strip()
    if header:
        value = request.headers.get(header)
        if value:
            return value.split(",")[0].strip()

    return request.client.host if request.client else "unknown"


def _too_many(retry_after: float) -> HTTPException:
    seconds = max(1, int(retry_after + 0.5))
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        "Too many attempts. Try again shortly.",
        headers={"Retry-After": str(seconds)},
    )


def guard_login(request: Request, email: str) -> tuple[str, str]:
    """Refuse the attempt if either bucket is full. Returns the two keys.

    Checked before the password is verified, so a throttled attacker does not
    even get the timing signal of a bcrypt comparison.

    The account bucket is keyed on the submitted address whether or not it
    exists, and the 429 says the same thing either way, so this does not become
    a way to discover which emails are registered.
    """
    ip_key = client_ip(request)
    account_key = email.strip().lower()

    for limiter, key in (
        (login_by_ip, ip_key),
        (login_by_account, account_key),
        (login_by_account_daily, account_key),
    ):
        retry_after = limiter.check(key)
        if retry_after is not None:
            raise _too_many(retry_after)

    return ip_key, account_key


def record_failed_login(ip_key: str, account_key: str) -> None:
    login_by_ip.record(ip_key)
    login_by_account.record(account_key)
    login_by_account_daily.record(account_key)


def clear_login(ip_key: str, account_key: str) -> None:
    """A correct password clears both account bands.

    Somebody who mistypes twice and then succeeds should not be left near a
    threshold. The IP bucket is left alone on purpose: holding one valid
    account is exactly the position an attacker enumerating others from the
    same address would be in.
    """
    login_by_account.clear(account_key)
    login_by_account_daily.clear(account_key)


def guard_signup(request: Request) -> str:
    key = client_ip(request)
    retry_after = signup_by_ip.check(key)
    if retry_after is not None:
        raise _too_many(retry_after)
    return key
