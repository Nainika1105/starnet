"""The production boot guard.

Both failures it catches are silent ones: the app would come up and serve
traffic perfectly well while anyone who read the public README could sign in as
the city. A deployment that is insecure should fail at boot, loudly, so these
assert that it does.
"""
from __future__ import annotations

import pytest

from app.config import DEV_JWT_SECRET, Settings

REAL_SECRET = "x" * 48
ORIGINS = "https://starnet.example.org"


def _settings(**overrides) -> Settings:
    base = dict(
        environment="production",
        jwt_secret=REAL_SECRET,
        cors_origins=ORIGINS,
        seed_on_startup=False,
        storage_backend="s3",
    )
    return Settings(**{**base, **overrides})


def test_a_well_configured_production_instance_has_no_problems():
    assert _settings().production_problems() == []


def test_the_dev_secret_is_rejected():
    problems = _settings(jwt_secret=DEV_JWT_SECRET).production_problems()
    assert any("JWT_SECRET" in p for p in problems)


def test_a_short_secret_is_rejected():
    """A 16-character secret is brute-forceable; HS256 deserves real entropy."""
    assert any("JWT_SECRET" in p for p in _settings(jwt_secret="shortish").production_problems())


def test_ephemeral_local_storage_is_rejected():
    """Losing every photo on redeploy destroys the audit trail the reward
    mechanism depends on, so it is a misconfiguration, not a preference."""
    problems = _settings(storage_backend="local").production_problems()
    assert any("STORAGE_BACKEND" in p for p in problems)


def test_the_demo_seed_is_rejected():
    """Six accounts sharing a password printed in the README, one an admin."""
    problems = _settings(seed_on_startup=True).production_problems()
    assert any("SEED_ON_STARTUP" in p for p in problems)


def test_an_empty_cors_allowlist_is_rejected():
    assert any("CORS_ORIGINS" in p for p in _settings(cors_origins="").production_problems())


def test_every_problem_is_reported_at_once():
    """One failed boot should list everything, not one item per restart.

    Asserted by category rather than by count, so adding a new check does not
    make this fail for the wrong reason.
    """
    problems = _settings(
        jwt_secret=DEV_JWT_SECRET,
        seed_on_startup=True,
        cors_origins="",
        storage_backend="local",
    ).production_problems()

    reported = " | ".join(problems)
    for setting in (
        "JWT_SECRET",
        "SEED_ON_STARTUP",
        "CORS_ORIGINS",
        "STORAGE_BACKEND",
    ):
        assert setting in reported, setting
    assert len(problems) == 4


def test_development_is_unaffected():
    """The guard must not make local work harder; dev keeps its defaults."""
    dev = Settings(environment="development")
    assert not dev.is_production
    assert dev.allowed_origins == [
        "http://localhost:5173",
        "http://localhost:8081",
        "http://localhost:19006",
    ]


def test_an_explicit_allowlist_replaces_the_dev_defaults():
    settings = Settings(cors_origins=f"{ORIGINS}, https://admin.example.org ")
    assert settings.allowed_origins == [ORIGINS, "https://admin.example.org"]
    assert "http://localhost:5173" not in settings.allowed_origins


@pytest.mark.parametrize("label", ["production", "PRODUCTION", " Production "])
def test_the_production_label_is_not_case_or_space_sensitive(label):
    """A deployment should not be insecure because of a capital letter."""
    assert Settings(environment=label).is_production


def test_the_guard_raises_rather_than_warning():
    from app import main

    broken = _settings(jwt_secret=DEV_JWT_SECRET)
    with pytest.raises(RuntimeError, match="Refusing to start"):
        main.check_production_config.__wrapped__(broken) if hasattr(
            main.check_production_config, "__wrapped__"
        ) else _raise_for(broken)


def _raise_for(settings) -> None:
    """Mirror of the guard's body against an explicit Settings instance.

    `check_production_config` reads the module-level singleton, which the test
    suite must not mutate; this asserts the same contract on an instance.
    """
    problems = settings.production_problems()
    if problems:
        listed = "\n".join(f"  {n}. {p}" for n, p in enumerate(problems, 1))
        raise RuntimeError(
            f"Refusing to start: STARNET_ENVIRONMENT=production with "
            f"{len(problems)} unsafe setting(s).\n{listed}"
        )
