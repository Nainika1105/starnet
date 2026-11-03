"""Redemption fulfilment is simulated, and cannot quietly stop being.

Points in this system are not money and are never exchanged for it. That is a
project constraint rather than a stage the code is passing through, so these
tests assert it is enforced: the flag cannot be switched off and still work,
a production instance refuses to boot with it off, and every redemption says
on its own record that nothing was issued.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app import fulfilment
from app.catalog import BY_SKU, CATALOG
from app.config import Settings, settings


def test_a_redemption_is_marked_simulated():
    delivery = fulfilment.fulfil(user_id="u", sku="CAFE-COFFEE", cost=Decimal("25"))
    assert delivery.simulated is True
    assert delivery.reference.startswith(fulfilment.SIMULATED_PREFIX)


def test_the_reference_cannot_be_mistaken_for_a_voucher_code():
    delivery = fulfilment.fulfil(user_id="u", sku="BUS-PASS-1D", cost=Decimal("40"))
    assert "SIM" in delivery.reference
    assert "simulated" in delivery.description.lower()
    assert "nothing was issued" in delivery.description.lower()


def test_references_are_unique():
    seen = {
        fulfilment.fulfil(user_id="u", sku="CAFE-COFFEE", cost=Decimal("25")).reference
        for _ in range(20)
    }
    assert len(seen) == 20


def test_turning_simulation_off_fails_loudly(monkeypatch):
    """Nothing implements real delivery, so the only honest behaviour is to
    refuse. An endpoint that reports success while issuing nothing is worse
    than one that errors."""
    monkeypatch.setattr(settings, "payments_simulated", False, raising=False)

    with pytest.raises(NotImplementedError) as exc:
        fulfilment.fulfil(user_id="u", sku="CAFE-COFFEE", cost=Decimal("25"))

    message = str(exc.value)
    assert "no real fulfilment backend" in message.lower()
    assert "app/fulfilment.py" in message


def test_production_refuses_to_boot_with_simulation_off():
    base = dict(
        environment="production",
        jwt_secret="x" * 48,
        cors_origins="https://a.example.org",
        storage_backend="s3",
    )
    assert Settings(**base).production_problems() == []
    problems = Settings(**base, payments_simulated=False).production_problems()
    assert any("PAYMENTS_SIMULATED" in p for p in problems)


def test_no_payment_dependency_is_installed():
    """A guard against the constraint being eroded by a convenient import."""
    import pathlib

    requirements = (
        pathlib.Path(__file__).resolve().parent.parent / "requirements.txt"
    ).read_text().lower()

    for package in ("stripe", "razorpay", "paypal", "braintree", "square", "adyen", "paytm"):
        assert package not in requirements, f"{package} is a payment rail"


def test_every_catalogue_item_says_it_is_simulated():
    """"Rs. 50 off your water bill" reads like real value. A demo audience
    should not have to guess which parts are pretend."""
    for item in CATALOG:
        if item.sku == "BADGE-CIVIC":
            continue  # a profile badge is the one thing that is real
        assert "simulated" in item.description.lower(), item.sku


# --- through the endpoint --------------------------------------------------


def test_redeeming_reports_that_nothing_was_issued(db, client, register):
    from sqlalchemy import select

    from app.engines import reward
    from app.enums import EntryType, Stage
    from app.models import LedgerEntry, Redemption

    user, headers = register("spender")
    item = BY_SKU["BADGE-CIVIC"]

    # Fund the account with a plain ledger entry. Going through the full
    # lifecycle would work too and would test nothing extra here.
    db.add(
        LedgerEntry(
            user_id=user.id,
            stage=Stage.NONE,
            entry_type=EntryType.VEST,
            amount=Decimal("100.0000"),
            note="test funding",
            idempotency_key=f"test-funding-{user.id}",
        )
    )
    db.flush()

    response = client.post(
        "/wallet/redeem",
        headers=headers,
        json={"sku": item.sku, "nonce": "nonce-1"},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["simulated"] is True
    assert body["fulfilment_reference"].startswith("SIM")
    assert "nothing was issued" in body["message"].lower()

    stored = db.execute(select(Redemption).where(Redemption.user_id == user.id)).scalars().one()
    assert stored.simulated is True
    assert stored.fulfilment_reference == body["fulfilment_reference"]

    # And the points really did leave the ledger.
    assert reward.balance(db, user.id) == Decimal("100.0000") - item.cost
