"""Redemption fulfilment. Simulated, deliberately and enforceably.

Points in this system are not money and are never exchanged for money. A
redemption moves points out of a user's ledger and records what they chose; no
value leaves the system, no provider is called, and no payment rail exists
anywhere in this codebase.

That is a project constraint, not an incidental state of the code, so it is
enforced rather than documented and hoped for:

  - There is no real backend. `fulfil` raises if simulation is switched off,
    because switching it off cannot work -- somebody would have to write an
    integration first, which makes going live a deliberate act rather than a
    config change.
  - The production boot guard refuses `STARNET_PAYMENTS_SIMULATED=false` for
    the same reason. An instance that reports successful redemptions while
    issuing nothing is worse than one that refuses to start.
  - Every redemption records that it was simulated, in the row and in the
    response, so neither a user nor an auditor can mistake a demo redemption
    for a delivered bus pass.

The seam for real fulfilment is this module's `fulfil`: a partner coupon API, a
municipal billing credit, or a voucher issuer would implement it and return a
reference that means something. Nothing else needs to change -- the ledger
entry, the idempotency key and the balance arithmetic are the same either way.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from app.config import settings

SIMULATED_PREFIX = "SIM"


@dataclass(frozen=True)
class Fulfilment:
    """What a redemption actually delivered."""

    reference: str
    simulated: bool

    @property
    def description(self) -> str:
        if self.simulated:
            return (
                "Simulated redemption: points were deducted and recorded, and "
                "nothing was issued. No payment or voucher system is connected."
            )
        return f"Issued, reference {self.reference}."


def fulfil(*, user_id, sku: str, cost: Decimal) -> Fulfilment:
    """Deliver a redemption, or record that it was simulated.

    Raises when simulation is disabled, because nothing implements the real
    thing. Failing loudly here is the point: the alternative is an endpoint
    that cheerfully tells people their coffee is waiting.
    """
    if not settings.payments_simulated:
        raise NotImplementedError(
            "STARNET_PAYMENTS_SIMULATED is false, but no real fulfilment backend "
            "is implemented. Redemptions in this system are simulated by design: "
            "points are not money and are never exchanged for it. To connect a "
            "real coupon or municipal-credit provider, implement it in "
            "app/fulfilment.py -- do not disable the flag and expect delivery."
        )

    # Deterministic shape, random body: a reference that is obviously not a
    # real voucher code, so it cannot be mistaken for one downstream.
    return Fulfilment(
        reference=f"{SIMULATED_PREFIX}-{sku}-{uuid.uuid4().hex[:10]}",
        simulated=True,
    )
