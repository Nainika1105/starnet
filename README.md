# STAR-Net

Civic fault reporting where **reward vests on physically confirmed resolution, not on submission**.

A report earns almost nothing when filed. The bulk of its reward is released only once the fault
is actually repaired *and* an independent user confirms the repair on site. Fabrication is
unprofitable by construction: a fake fault can never be repaired, so it can never reach the paid
stages. Duplicate reports become corroborating evidence rather than noise, and neglected wards get
their rewards multiplied up instead of being starved.

This repository is **Phase 2**: the full mechanism with the confidence fusion, clustering, trust
routing and priority ageing all live. See [Phase status](#phase-status) for exactly what is real
and what is stubbed.

---

## Quick start

```bash
cd infra && docker compose up --build
```

Then:

| Surface | URL | Sign in |
| --- | --- | --- |
| Municipal dashboard | http://localhost:5173 | `admin@starnet.demo` / `starnet123` |
| API docs (OpenAPI) | http://localhost:8000/docs | — |
| Mechanism knobs | http://localhost:8000/config/mechanism | — |

The database is seeded on first start with six demo accounts and eight issues spread across the
lifecycle, so the dashboard opens with work in every state.

### The demo

```bash
python scripts/demo.py
```

Walks the whole mechanism and asserts the acceptance criteria. **Four different people**, each a
gate the one before cannot pass alone:

```
reporter  ->  verifier  ->  city crew  ->  fix-confirmer
```

A citizen reports a pothole with a live photo; the report does *not* go straight onto the city's
queue, because one stranger's photo should not commission public work — it is routed to a nearby
trusted user who attends the spot and verifies it. Only then is it queued, assigned and resolved.
A third person is then routed to photograph the repair on site. The reporter's points move
pending → paid across the three vesting stages, with the ledger printed at each step.

The last step files the fault *again* after the city signed it off, which reopens the issue and
claws the confirmation slice back by a negative ledger entry — never by editing the entry that
paid it.

Needs `httpx` and `pillow`; `api/.venv/bin/python scripts/demo.py` works if you set up the venv below.

### Mobile app

```bash
cd mobile && npm install && npx expo start
```

Open in Expo Go. On a physical device, set `expo.extra.apiBase` in `mobile/app.json` to your
machine's LAN IP — `localhost` inside the app means the phone itself. Requires Node 20+ for recent
Expo tooling (Expo SDK 51 works on Node 18).

### Running the API without Docker

```bash
cd api
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
cd ../infra && docker compose up -d db        # just Postgres+PostGIS
cd ../api && STARNET_SEED_ON_STARTUP=true ./.venv/bin/uvicorn app.main:app --reload
```

### Tests

```bash
cd api && ./.venv/bin/python -m pytest tests/ -q
```

63 tests, all against a real Postgres.

| File | Covers |
| --- | --- |
| `test_vesting.py` | The ledger: value conservation, idempotency, forfeiture, reopen/reconfirm cycles, pool splitting, the anti-fabrication bound, and that no entry is ever mutated |
| `test_signals.py` | EXIF parsing and GPS/time coherence, impossible-travel detection, the confidence fusion's decision bands, corroboration saturation, priority ageing, equity bounds |
| `test_routing.py` | The independence rules, tier gates, proximity and load-spreading, verify-task expiry and re-routing |
| `test_clustering.py` | Dedup → corroboration through the real HTTP endpoint, and reopen-on-re-report with its ledger reversal |

---

## Architecture

```
/api      FastAPI + SQLAlchemy + PostGIS. Engines, lifecycle, ledger.
/admin    React (Vite) + Leaflet municipal dashboard.
/mobile   Expo React Native citizen app.
/infra    docker-compose: Postgres+PostGIS, API, dashboard.
/scripts  demo.py -- the end-to-end acceptance walk.
```

### The mechanism, in one pass

1. **Report** — photo, GPS, timestamp, category. Live camera only; there is deliberately no
   gallery picker in the mobile app.
2. **Confidence** `C(r) = σ(w₀ + w₁s_img + w₂s_geo + w₃s_cor + w₄τ)`.
   `C ≥ θ_hi` auto-accepts, `C < θ_lo` rejects, in between routes a verify task to a trusted user.
   In practice a lone uncorroborated report from an ordinary account lands in the middle band —
   that is the design, not a tuning accident.
3. **Cluster** — a nearby report of the same category joins the existing issue as corroboration,
   which raises its confidence and can carry it over `θ_hi` with no verifier needed. Duplicates
   are never deleted. A report matching an issue the city has already signed off **reopens** it.
4. **Prioritise** — `P(I) = Σ λᵢxᵢ` over severity, exposure, vulnerability and escalation risk,
   times an equity multiplier `E(ℓ)` that favours under-reported wards. The escalation term grows
   with the issue's age, so a neglected fault climbs the queue by itself.
5. **Dispatch & resolve** — the admin assigns a crew and marks the work done.
6. **Fix-confirm** — the *system* picks a nearby high-trust user who is not a reporter, not a
   contributor in any role, and not chosen by the contractor. They must submit a photo. No
   confirmation inside the window, or a "still broken" verdict, reopens the issue.
7. **Settle** — reward vests across the three stages.

### Who may do what

Routing enforces three rules that are structural, not preferences — each is a way the mechanism
could be gamed if it bent:

- **The system picks.** A contractor can never nominate whoever signs off their own work.
- **Nobody with a stake decides.** Reporters are excluded, and so is anyone holding a contribution
  on the issue in *any* role — including the on-site verifier, who has already staked their
  reputation on the fault being real. One person cannot carry an issue from report to payout.
- **Tier gates.** A verification admits work to the city's queue, so it demands
  `VERIFIER_ELIGIBLE` with no fallback: if nobody eligible is reachable, the issue waits, unpaid
  and unrejected. A fix-confirmation falls back to `TRUSTED`, because refusing to route one would
  cost honest reporters their final 45% over the city's thin verifier coverage.

Within those candidates, routing prefers whoever is likeliest to actually walk past the spot
(great-circle distance from their own last report, via PostGIS), then spreads load, then prefers
trust. A verify task nobody attends is re-routed to the next candidate up to
`max_verify_reroutes`, and the lapsed assignee is **not** penalised — docking trust for not
walking somewhere would push verifiers toward rubber-stamping tasks they cannot attend.

### The reward model

```
R_I = R0 · P(I) · E(ℓ) · τ(discoverer)          bounded to [0, R0 · E_max]

role split      f_D = 0.50   f_C = 0.20   f_V = 0.10   f_F = 0.20
vesting split   g_v = 0.10   g_r = 0.45   g_c = 0.45
```

The Phase-1 shortcut that auto-accepted a single plausible report is still reachable with
`STARNET_CONFIDENCE_MODE=stub`, and clustering with `STARNET_CLUSTERING_ENABLED=false`, which is
useful for seeing what each mechanism is actually buying.

`g_v` is the single anti-fraud knob. At 0.10 a fabricated report can collect at most
`g_v · f_D = 5%` of an issue's pool, while an honest reporter still gets an immediate token credit
for a correct report. Every coefficient lives in [`api/app/config.py`](api/app/config.py) and is
overridable by a `STARNET_`-prefixed environment variable.

### The ledger

The ledger is the part that has to be right, so it holds three invariants:

- **Append-only.** Balances are never stored. A balance is the signed sum of a user's entries.
  Vesting appends positive entries; reversals and redemptions append negative ones. Nothing is
  ever updated or deleted — a test asserts this directly.
- **Idempotent.** Every transition derives a deterministic `idempotency_key` with a unique index
  behind it. A retried webhook, a double-tapped button or a replayed lifecycle event collides and
  is dropped rather than paying twice. The key embeds the issue's reopen cycle, so a genuine
  reopen → reconfirm vests again under a fresh key.
- **Exact.** Points are `Numeric(18,4)` and `Decimal`, never floats. Stage amounts *telescope*
  (each stage pays the difference between cumulative-through and cumulative-before) so the stages
  sum to exactly the entitlement; quantising each slice independently leaks a rounding unit per
  stage. Pools are split with the residual handed to the largest share, so a pool is always
  distributed in full.

A `Contribution` row carries the vesting state machine
(`PENDING_VERIFICATION → PENDING_RESOLUTION → PENDING_CONFIRMATION → PAID`, plus `FORFEITED`);
the money itself only ever appears as `reward_ledger` rows. Inspect the whole thing at
`GET /admin/ledger` or in the dashboard's Ledger tab.

---

## Phase status

### Implemented and working

**Phase 1.** Auth (JWT) · report intake with photo and GPS · issue lifecycle with full audit
trail · **the vesting ledger** · admin queue, assign, resolve, reject, reopen, off-app close ·
system-selected fix-confirmation with photo requirement · wallet with per-stage vesting
visibility · redemption catalogue · ward accountability metrics · per-user rate caps · mobile app
and municipal dashboard · seed data and a one-command run.

**Phase 2.** The logistic confidence fusion, on by default · geo coherence from the photo's own
EXIF capture time and GPS, with a distance-scaled penalty · impossible-travel detection between a
user's consecutive reports · geo+time clustering, on by default, with reopen-on-re-report ·
the trust engine (Beta reputation, geometric decay, tiers) wired into verifier and confirmer
routing with real PostGIS proximity, load-spreading and independence exclusions · verify-task
expiry and bounded re-routing · age-aware priority with an hourly re-ranking job · the equity
multiplier in queue ordering · `awaiting_verification` per ward, so thin verifier coverage is
visible as an operational problem rather than as thin demand.

### Deliberate stubs

Each is isolated behind a function or flag so the real version drops in without touching callers.

| Stub | Where | Real version |
| --- | --- | --- |
| `s_img` — per-category constant, does not look at the image | `engines/confidence.py: score_image` | CLIP zero-shot, then YOLO fine-tuned on RDD2022 |
| Notifications of a routed task — the user has to open the Tasks tab | `routers/tasks.py` | Push, so a verify task reaches someone while they are still near the spot |
| Clustering predicate — same category, within 30 m, inside 48 h | `routers/reports.py: find_cluster_match` | HDBSCAN over a combined distance that adds CLIP embedding similarity to the metres, so two photos of one pothole from opposite kerbs cluster and two faults at one junction do not |
| Base rates for exposure, vulnerability, escalation and `ρ̂(ℓ)` — per-ward and per-category constants | `engines/priority.py` | Traffic counts, POI lookups for schools and hospitals, weather-aware escalation. (The escalation term's growth with age is computed, not a constant.) |
| Collusion flags — device match and co-submission burst only; `flagged_collusion` and `reciprocal_pair` are never set | `routers/reports.py`, `engines/confidence.py: independence_weight` | Co-corroboration graph with reciprocity and repeated-pair detection |
| Photo storage — local volume | `app/storage.py` | S3 or Supabase storage behind the same `save`/`url_for` interface |
| Redemption catalogue — static list | `app/catalog.py` | Partner coupon and municipal credit integrations |
| Notifications — in-app only | — | FCM |
| Background work — APScheduler in-process | `app/jobs.py` | Celery + Redis |

EXIF handling is **not** a stub: capture time and GPS are read for the coherence check, then the
image is written back stripped, so we do not store the reporter's camera serial and location
history. The mobile camera asks for EXIF on capture for this reason — a photo that arrives without
it scores as a re-encoded or borrowed image, and from a brand-new account that alone is enough to
fall under `θ_lo`.

### Not yet built (Phase 3+)

The image model · the collusion graph · category-specific trust · work-order reconciliation beyond
a manual close · richer dashboards and onboarding.

---

## Honest constraints

- This is a **demo/MVP that implements the mechanism faithfully**, not a production, city-scale,
  fraud-hardened deployment. City-scale fraud resistance and real equity effects cannot be
  demonstrated without real users and real data.
- The image check is a placeholder. Real verification needs a trained model — RDD2022 covers road
  damage; the other five categories would each need their own dataset.
- Live-camera-only capture is UI enforcement, not proof. Someone can still photograph a screen;
  defeating that needs hardware attestation, which is out of scope.
- The equity multiplier reads 3.00× (its cap) across the board in the seeded demo simply because
  observed report rates are near zero with eight issues. It becomes meaningful with real volume.
- The fusion weights (`w₀ = -3.5`, and so on) are reasoned, not fitted. They produce the intended
  bands on the signals we can compute, but calibrating them needs labelled reports — and `s_img`
  being a constant means one of the four inputs currently carries no information, so the fusion is
  effectively a three-signal model wearing a four-signal coat.
- Routing reads a candidate's position from their own most recent report, which is the only
  location signal the system legitimately has. For a user who has never filed, there is none, and
  they sort behind everyone who has.
- A single serviced region is hardcoded in `engines/geo.py` (loosely Bengaluru, five wards).
- CORS is wide open and the JWT secret has a dev default. Both need closing before any real
  deployment.

## Licence

Demo code, no licence specified.
