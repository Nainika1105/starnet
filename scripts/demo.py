#!/usr/bin/env python3
"""STAR-Net demo: walks the acceptance criteria end to end.

A citizen reports a pothole with a photo. Under the confidence fusion a lone
report from a new account does not go straight onto the city's queue -- it is
routed to a nearby trusted user who attends the spot and verifies it. Only then
does the issue reach the municipal queue, get resolved, and go to a THIRD person
to confirm the repair on site. The reporter's points move pending -> paid across
the three vesting stages, backed by an append-only ledger printed at every step.

Four different people, each a gate the one before cannot pass on their own:

    reporter  ->  verifier  ->  city crew  ->  fix-confirmer

    python scripts/demo.py [--base http://localhost:8000]
"""
from __future__ import annotations

import argparse
import io
import random
import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from fractions import Fraction

import httpx
from PIL import Image, ImageDraw

ADMIN = ("admin@starnet.demo", "starnet123")
PASSWORD = "starnet123"

# The seeded citizens who might be routed a task. Which of them the engine
# actually picks depends on trust, proximity and current load, so the demo
# looks for whoever is holding the task rather than assuming.
CITIZENS = [
    "meera@starnet.demo",
    "vikram@starnet.demo",
    "dev@starnet.demo",
    "asha@starnet.demo",
]

# Somewhere in WARD-CENTRAL, inside the serviced area. Jittered per run by a
# few hundred metres: clustering is on, so filing at the identical spot would
# attach this run's report to the previous run's issue as a corroboration
# instead of opening a new one.
BASE_LAT, BASE_LNG = 12.9705, 77.5921
LAT = BASE_LAT + random.uniform(-0.004, 0.004)
LNG = BASE_LNG + random.uniform(-0.004, 0.004)


def rule(title: str) -> None:
    print(f"\n\033[1m{'=' * 72}\n{title}\n{'=' * 72}\033[0m")


def step(text: str) -> None:
    print(f"\n\033[1m-> {text}\033[0m")


def _dms(value: float) -> tuple[Fraction, Fraction, Fraction]:
    degrees = int(abs(value))
    minutes = int((abs(value) - degrees) * 60)
    seconds = (abs(value) - degrees - minutes / 60) * 3600
    return (Fraction(degrees, 1), Fraction(minutes, 1), Fraction(round(seconds * 100), 100))


def photo_bytes(label: str, *, lat: float = LAT, lng: float = LNG) -> bytes:
    """A stand-in photo carrying the EXIF a live camera capture carries.

    The capture time and GPS are not decoration: the server scores a photo with
    no EXIF as a re-encoded or borrowed image, and from a brand-new account that
    is enough on its own to fall under theta_lo. The mobile app asks the camera
    for EXIF for exactly this reason, so the demo sends what the app sends.
    """
    img = Image.new("RGB", (640, 480), (90, 95, 105))
    draw = ImageDraw.Draw(img)
    draw.rectangle([30, 30, 610, 450], outline=(240, 240, 240), width=4)
    draw.text((56, 60), f"DEMO PHOTO\n{label}", fill=(245, 245, 245))

    exif = Image.Exif()
    exif[36867] = datetime.now(timezone.utc).strftime("%Y:%m:%d %H:%M:%S")  # DateTimeOriginal
    exif[34853] = {1: "N", 2: _dms(lat), 3: "E", 4: _dms(lng)}              # GPSInfo
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=70, exif=exif)
    return buf.getvalue()


class Client:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.http = httpx.Client(timeout=30.0)
        self.token: str | None = None

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def login(self, email: str, password: str) -> "Client":
        res = self.http.post(f"{self.base}/auth/login", json={"email": email, "password": password})
        res.raise_for_status()
        self.token = res.json()["access_token"]
        return self

    def signup(self, email: str, handle: str, ward: str | None = None) -> "Client":
        res = self.http.post(
            f"{self.base}/auth/signup",
            json={"email": email, "password": PASSWORD, "handle": handle, "home_ward": ward},
        )
        res.raise_for_status()
        self.token = res.json()["access_token"]
        return self

    def get(self, path: str) -> dict | list:
        res = self.http.get(f"{self.base}{path}", headers=self._headers())
        res.raise_for_status()
        return res.json()

    def post(self, path: str, *, json=None, data=None, files=None) -> dict:
        res = self.http.post(
            f"{self.base}{path}", headers=self._headers(), json=json, data=data, files=files
        )
        if res.status_code >= 400:
            raise SystemExit(f"POST {path} failed: {res.status_code} {res.text}")
        return res.json()


def find_task(base: str, issue_id: str, task_type: str) -> tuple[Client | None, dict | None]:
    """Find whoever the engine routed this task to.

    Routing picks on trust, proximity and current load, so the demo cannot know
    the assignee in advance -- which is the point. It signs in as each seeded
    citizen and asks whose task list it is on.
    """
    for email in CITIZENS:
        client = Client(base).login(email, PASSWORD)
        for task in client.get("/tasks/me"):
            if task["issue"]["id"] == issue_id and task["type"] == task_type:
                return client, task
    return None, None


def show_wallet(client: Client, who: str) -> dict:
    wallet = client.get("/wallet/me")
    pending = wallet["pending_by_state"] or {}
    print(f"   {who}: balance {wallet['balance']} pts · tier {wallet['tier']}")
    if pending:
        for state, amount in pending.items():
            print(f"      still pending — {state}: {amount} pts")
    else:
        print("      nothing pending")
    for entry in reversed(wallet["ledger"]):
        stage = entry["stage"] if entry["stage"] != "NONE" else "-"
        print(
            f"      ledger  {entry['entry_type']:<10} {str(entry['role'] or '-'):<12} "
            f"{stage:<13} {entry['amount']:>10}"
        )
    return wallet


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:8000")
    args = parser.parse_args()

    tag = uuid.uuid4().hex[:6]

    rule("STAR-Net — reward vests on confirmed resolution")

    mechanism = httpx.get(f"{args.base}/config/mechanism", timeout=30).json()
    print(f"  base points R0        : {mechanism['r0_base_points']}")
    print(f"  role split            : {mechanism['role_split']}")
    print(f"  vesting split         : {mechanism['vesting_split']}")
    print(f"  confidence mode       : {mechanism['confidence_mode']}")
    print(f"  accept / reject bands : {mechanism['thresholds']}")
    print(
        f"  a fabricated report can collect at most "
        f"{mechanism['max_fabrication_payout_fraction'] * 100:.1f}% of an issue's pool"
    )

    # --- 1. a citizen reports ------------------------------------------
    step("1. A citizen signs up and reports a pothole with a live photo")
    reporter = Client(args.base).signup(
        f"reporter-{tag}@starnet.demo", f"reporter_{tag}", "WARD-CENTRAL"
    )
    submitted = reporter.post(
        "/reports",
        data={
            "category": "POTHOLE",
            "lat": str(LAT),
            "lng": str(LNG),
            "description": "Deep pothole opposite the school gate.",
        },
        files={"photo": ("pothole.jpg", photo_bytes("pothole"), "image/jpeg")},
    )
    issue = submitted["issue"]
    issue_id = issue["id"]
    print(f"   decision        : {submitted['decision']}")
    print(f"   {submitted['message']}")
    print(f"   issue           : {issue_id}")
    print(f"   confidence      : {issue['confidence']:.3f}")
    print(f"   priority P(I)   : {issue['priority']:.3f}")
    print(f"   equity E(l)     : {issue['equity_multiplier']:.2f}x")
    print(f"   reward pool R_I : {issue['reward_total']} pts")
    print(f"   status          : {issue['status']}")

    step("   The reporter's wallet right after reporting")
    after_report = show_wallet(reporter, "reporter")
    filed_balance = Decimal(after_report["balance"])
    print(
        "\n   Note: filing earned almost nothing. That is the mechanism — the bulk of the\n"
        "   reward is withheld until the repair is physically confirmed."
    )

    # --- 2. an independent verifier attends ------------------------------
    step("2. The system routed the report to a nearby trusted user to verify on site")
    if submitted["decision"] == "VERIFY":
        verifier, verify_task = find_task(args.base, issue_id, "VERIFY")
        if verifier is None:
            raise SystemExit("FAIL: no verification task reached any eligible user")
        verifier_me = verifier.get("/auth/me")
        print(f"   verifier        : {verifier_me['handle']} (tier {verifier_me['tier']})")
        print("   they are not the reporter, and the reporter did not choose them")
        verified = verifier.post(
            f"/tasks/{verify_task['id']}/complete",
            data={"verdict": "CONFIRMED"},
            files={"photo": ("verify.jpg", photo_bytes("fault verified on site"), "image/jpeg")},
        )
        print(f"   issue status    : {verified['issue_status']}")
        print(f"   {verified['message']}")
        verifier_handle = verifier_me["handle"]
    else:
        print(f"   not needed: the report was {submitted['decision']} on confidence alone")
        verifier, verifier_handle = None, None

    step("   The reporter's wallet after verification")
    after_verification = show_wallet(reporter, "reporter")
    verified_balance = Decimal(after_verification["balance"])
    print(
        "\n   A token credit has vested. Everything else is still conditional on the\n"
        "   fault actually being repaired."
    )

    # --- 3. the issue is in the municipal queue -------------------------
    step("3. The issue appears on the municipal queue")
    admin = Client(args.base).login(*ADMIN)
    queue = admin.get("/admin/queue")
    position = next((i for i, row in enumerate(queue) if row["id"] == issue_id), None)
    if position is None:
        raise SystemExit("FAIL: the issue did not reach the admin queue")
    print(f"   queue length    : {len(queue)}")
    print(f"   our issue is at position {position + 1}, ranked by priority then equity")

    # --- 4. admin dispatches and resolves -------------------------------
    step("4. The admin assigns a crew and marks the work resolved")
    admin.post(f"/admin/issues/{issue_id}/assign", json={"assignee_label": "Road Works Crew 7"})
    resolved = admin.post(f"/admin/issues/{issue_id}/resolve")
    print(f"   status          : {resolved['status']}")

    step("   The reporter's wallet after resolution")
    after_resolution = show_wallet(reporter, "reporter")
    resolved_balance = Decimal(after_resolution["balance"])
    if resolved_balance <= verified_balance:
        raise SystemExit("FAIL: the resolution stage did not vest")
    print("\n   More has vested — but the final slice is still withheld.")

    # --- 5. an independent user confirms the repair ---------------------
    step("5. The system asked a THIRD person to confirm the repair on site")
    confirmer, task = find_task(args.base, issue_id, "CONFIRM")
    if confirmer is None or task is None:
        raise SystemExit("FAIL: no fix-confirmation task reached an independent user")
    confirmer_me = confirmer.get("/auth/me")
    print(f"   confirmer       : {confirmer_me['handle']} (tier {confirmer_me['tier']})")
    print(f"   task            : {task['type']} on issue {issue_id}")
    print("   neither the reporter, nor the verifier, nor chosen by the crew")

    confirmed = confirmer.post(
        f"/tasks/{task['id']}/complete",
        data={"verdict": "CONFIRMED"},
        files={"photo": ("repair.jpg", photo_bytes("repair confirmed"), "image/jpeg")},
    )
    print(f"   issue status    : {confirmed['issue_status']}")
    print(f"   {confirmed['message']}")

    # --- 6. the payout ---------------------------------------------------
    step("6. The reporter's wallet after confirmation")
    final = show_wallet(reporter, "reporter")
    final_balance = Decimal(final["balance"])

    step("   And the confirmer's own fee")
    show_wallet(confirmer, "confirmer")

    # --- 7. the repair did not hold --------------------------------------
    step("7. Someone walks past later and the pothole is still there")
    witness = Client(args.base).signup(
        f"witness-{tag}@starnet.demo", f"witness_{tag}", "WARD-CENTRAL"
    )
    reopened = witness.post(
        "/reports",
        data={
            "category": "POTHOLE",
            "lat": str(LAT + 0.0001),  # ~11 m: the same fault
            "lng": str(LNG),
            "description": "Still open, the patch has already gone.",
        },
        files={"photo": ("again.jpg", photo_bytes("still broken"), "image/jpeg")},
    )
    print(f"   decision        : {reopened['decision']}")
    print(f"   issue status    : {reopened['issue']['status']}")
    print(f"   {reopened['message']}")

    step("   The reporter's wallet after the repair was contradicted")
    clawed_back = show_wallet(reporter, "reporter")
    clawed_back_balance = Decimal(clawed_back["balance"])
    print(
        "\n   The confirmation slice is gone — reversed by a negative entry, never by\n"
        "   editing or deleting the entry that paid it. The slice can be earned once\n"
        "   more if the repair is redone and confirmed, and only once."
    )

    # --- 8. assertions ---------------------------------------------------
    rule("Acceptance checks")

    pool = Decimal(issue["reward_total"])
    discoverer_share = pool * Decimal(str(mechanism["role_split"]["discoverer"]))
    checks = [
        ("report reached the municipal queue", position is not None),
        ("filing alone paid nothing at all", filed_balance == 0),
        (
            "an independent verifier, not the reporter, admitted it to the queue",
            verifier_handle is None or verifier_handle != reporter.get("/auth/me")["handle"],
        ),
        ("the fix-confirmer was a different person again",
         confirmer_me["handle"] != verifier_handle),
        ("verification paid only a token credit", verified_balance < discoverer_share / 2),
        ("resolution vested more than verification", resolved_balance > verified_balance),
        ("confirmation vested more than resolution", final_balance > resolved_balance),
        (
            "discoverer ended with their full entitlement",
            abs(final_balance - discoverer_share) <= Decimal("0.001"),
        ),
        (
            "nothing is left pending",
            not final["pending_by_state"],
        ),
        (
            "every contribution is fully vested",
            all(c["vesting_state"] == "PAID" for c in final["contributions"]),
        ),
        (
            "the ledger sums to the balance",
            sum(Decimal(e["amount"]) for e in final["ledger"]) == final_balance,
        ),
        (
            "the reward vested across three distinct stages",
            len({e["stage"] for e in final["ledger"] if e["entry_type"] == "VEST"}) == 3,
        ),
        (
            "a fault reported again after sign-off reopened the issue",
            reopened["issue"]["status"] == "REOPENED",
        ),
        (
            "the confirmation slice was clawed back, not deleted",
            clawed_back_balance == final_balance - (final_balance - resolved_balance)
            and any(e["entry_type"] == "REVERSAL" for e in clawed_back["ledger"]),
        ),
        (
            "the ledger still sums to the balance after the reversal",
            sum(Decimal(e["amount"]) for e in clawed_back["ledger"]) == clawed_back_balance,
        ),
    ]

    failures = 0
    for label, ok in checks:
        print(f"   [{'PASS' if ok else 'FAIL'}] {label}")
        failures += 0 if ok else 1

    print(
        f"\n   reward pool R_I        : {pool} pts"
        f"\n   discoverer entitlement : {discoverer_share} pts (50% of the pool)"
        f"\n   discoverer was paid    : {final_balance} pts"
    )

    if failures:
        print(f"\n\033[1m{failures} check(s) FAILED\033[0m")
        return 1

    print("\n\033[1mAll acceptance checks passed.\033[0m")
    print("Inspect the full append-only ledger at GET /admin/ledger, or the Ledger tab.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
