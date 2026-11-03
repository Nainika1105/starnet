# STAR-Net — project report source

Everything in this document is taken from the implementation rather than from a
design intention: the coefficients are read out of `api/app/config.py`, the
counts from the source tree, and the worked examples from runs against the
live API. Where something is not built, it says so.

Repository: https://github.com/Nainika1105/starnet

---

## 1. Problem

A civic fault reporting app has one structural weakness, and it is not
technical. Any scheme that rewards *reporting* pays out for a photograph and a
pin on a map. Both are cheap to produce and neither is evidence that a fault
exists. The result is predictable: the reward is farmed, the queue fills with
noise, and the municipality learns to distrust the whole channel.

Moderation does not fix this, because moderation is the thing being outrun. A
system that needs a human to inspect each report scales its cost with the
attacker's effort, not with its own usefulness.

## 2. The idea

**Reward vests on physically confirmed resolution, not on submission.**

A report earns almost nothing when filed. The bulk of its reward is released
only once the fault has actually been repaired *and* an independent person has
stood at the location and photographed the repair.

The consequence is the whole design. A fabricated fault cannot be repaired, so
it can never reach the paying stages. Fabrication is unprofitable by
construction rather than by detection — the system does not need to notice the
fraud, because the fraud does not pay.

Three things follow from that premise and are implemented accordingly:

- **Duplicates are evidence, not noise.** A second report of the same fault
  attaches to the first as corroboration and raises its confidence.
- **Under-reported areas are not fault-free areas.** An equity multiplier
  raises rewards where reporting is thinner than the population would predict,
  so a quiet ward is not mistaken for a healthy one.
- **Nobody settles their own claim.** The person who confirms a repair is
  chosen by the system, is never a reporter, and is never someone whose history
  is entangled with the people the settlement would pay.

## 3. Architecture

```
/api        FastAPI + SQLAlchemy 2.0 + PostGIS. Engines, lifecycle, ledger.
/admin      React 18 + Vite. Serves both the municipal and citizen views.
/mobile     Expo / React Native. The citizen client, with a real camera.
/infra      docker-compose: Postgres+PostGIS and the API.
/scripts    demo.py — an end-to-end acceptance walk with 15 assertions.
```

| Layer | Technology | Why |
| --- | --- | --- |
| API | FastAPI, Python 3.12 | Typed request/response models; OpenAPI for free |
| ORM | SQLAlchemy 2.0 + GeoAlchemy2 | Spatial columns as first-class types |
| Database | PostgreSQL 16 + PostGIS 3.4 | `ST_DWithin` / `ST_Distance` for clustering and routing |
| Migrations | Alembic | Schema changes an existing database can actually receive |
| Scheduler | APScheduler, in-process | Expiry and re-ranking jobs without a broker |
| Web | React 18, Vite, Leaflet | One bundle serving both roles |
| Mobile | Expo SDK 51 | Camera and location without native builds |

The API image builds the web dashboard and serves it at `/`, so one host answers
for both. That means one URL, and no CORS configuration at all — requests from
the dashboard are same-origin.

### Size

| Component | Lines |
| --- | --- |
| API application | 5,624 |
| Tests | 3,285 |
| Migrations | 392 |
| Web application | 2,043 |
| Mobile application | 1,412 |
| **Total** | **≈ 12,750** |

---

## 4. The mechanism

### 4.1 Lifecycle

```
REPORTED ──► VERIFIED ──► ASSIGNED ──► RESOLVED ──► CONFIRMED ──► CLOSED
    │            │                         │
    └─► REJECTED │                         └─► REOPENED ──┐
                 └─────────────────────────────────────────┘
```

Exactly three transitions release money, and nothing else does:

| Transition | Releases |
| --- | --- |
| → VERIFIED | `g_v` slice to claimants, plus the verifier's whole fee |
| → RESOLVED | `g_r` slice to claimants |
| → CONFIRMED | `g_c` slice to claimants, plus the confirmer's whole fee |

`REJECTED` forfeits everything unvested. `REOPENED` reverses the confirmation
stage — the slice was paid against a signal now known to be wrong.

### 4.2 Reward model

```
R_I = R₀ · P(I) · E(ℓ) · τ(discoverer)        bounded to [0, R₀ · E_max]
```

`R_I` is frozen at the moment the issue is created, so later drift in trust or
priority cannot retroactively change what contributors were promised.

| Parameter | Value | Meaning |
| --- | --- | --- |
| `R₀` | 100 | Base points |
| `E_max` | 3.0 | Cap on the equity multiplier |

**Role split** — who shares the pool:

| Role | Share |
| --- | --- |
| Discoverer | 50% |
| Corroborators | 20% (split among them) |
| Verifier | 10% |
| Fix-confirmer | 20% |

A role nobody fills is never minted. With no corroborator on an issue, that 20%
is simply not created.

**Vesting split** — when a claimant's share is released:

| Stage | Share |
| --- | --- |
| Verification | 10% |
| Resolution | 45% |
| Confirmation | 45% |

**The anti-fraud bound.** A fabricated fault cannot be repaired, so it can never
pass the resolution or confirmation stages. The most it can collect is

```
g_v × f_D = 0.10 × 0.50 = 5% of the pool
```

and only if a real person physically attends and verifies it first. This single
number is the design's central claim, and it follows from the structure rather
than from any detection mechanism.

### 4.3 Confidence model

```
C(r) = σ(w₀ + w₁·s_img + w₂·s_geo + w₃·s_cor + w₄·τ)
```

| Weight | Value |
| --- | --- |
| `w₀` (intercept) | −3.5 |
| `w₁` image | 2.0 |
| `w₂` geo | 1.5 |
| `w₃` corroboration | 2.5 |
| `w₄` trust | 1.0 |

| Decision | Condition |
| --- | --- |
| ACCEPT | `C ≥ θ_hi` = 0.75 |
| VERIFY | `θ_lo ≤ C < θ_hi` |
| REJECT | `C < θ_lo` = 0.40 |

**Worked example** (pothole, WARD-CENTRAL, new account, live photo with EXIF):

```
s_img = 0.82    s_geo = 1.00    s_cor = 0.00    τ = 0.50
z = −3.5 + 2.0(0.82) + 1.5(1.00) + 2.5(0) + 1.0(0.50) = 0.14
C = σ(0.14) = 0.535  →  VERIFY
```

Measured against the running API: **0.535**. A lone uncorroborated report lands
in the verify band by design — one stranger's photo should not commission
public work. The same report with no EXIF scores **0.387**, below the floor.

**Signal components**

- `s_img` — the one remaining stub. A per-category constant (0.74–0.86); it does
  not look at the image. See §8.
- `s_geo` — computed from four real checks: service-area membership, EXIF
  capture-time staleness, EXIF GPS agreement with the claimed pin (match within
  120 m, penalty scaling to 1,000 m), and implied travel speed since the
  reporter's previous submission (above 120 km/h the geographic evidence is
  void — one account cannot be in two places at once).
- `s_cor = 1 − e^(−S/s₀)`, `s₀` = 1.5, where `S` is the sum of `ρᵢ·τᵢ` over
  corroborators. Saturating: the first independent corroboration moves the
  needle a great deal, the tenth almost none, so flooding an issue with
  accounts buys nearly nothing.
- `ρᵢ = max(ρ_min, (1−d)(1−c)(1−p)(1−b))`, `ρ_min` = 0.05 — independence weight
  from shared device, flagged collusion, reciprocal pairing and temporal burst.
  Each term is a degree in [0,1] rather than a flag, so suspicions compound
  multiplicatively.

### 4.4 Trust

```
τ(u) = (a + 1) / (a + b + 2)
```

A Beta(1,1) prior: a brand-new account sits at exactly 0.5 and carries little
weight. Evidence decays geometrically at **0.95 per week**, so nobody banks a
reputation and coasts, and nobody who games their way up stays there.

| Tier | Requires |
| --- | --- |
| NEW | below the thresholds |
| TRUSTED | τ ≥ 0.60 |
| VERIFIER_ELIGIBLE | τ ≥ 0.75 |

**Evidence floor.** A tier additionally requires `a + b ≥ 3` observations. τ is
a ratio, and a ratio from one observation says nothing: a single confirmed
report alone puts an account at (1+1)/(1+0+2) = 0.667, past the TRUSTED line,
which would grant it the right to release somebody else's 45% on one data
point. The floor is checked against raw counts, not decayed ones — it asks
whether we have observed an account enough to judge it, and that does not
un-happen.

Only **physical confirmation** moves trust upward. Being contradicted by a
reopen moves it down.

### 4.5 Priority and equity

```
P(I) = λ_s·severity + λ_e·exposure + λ_v·vulnerability + λ_g·escalation
λ = (0.4, 0.3, 0.2, 0.1)
```

The escalation term is the one input that moves on its own:

```
x_esc(age) = e_cat · (floor + (1 − floor)·(1 − e^(−age/τ_age)))
floor = 0.4,  τ_age = 168 hours
```

A fault open for a month is worse than the same fault yesterday, and worse by
more if it is the kind that compounds. An hourly job re-ranks open issues, so a
neglected fault climbs the queue by itself. **Ranking only** — `base_priority`
and `reward_total` are frozen at filing, so ageing never changes what anyone was
promised.

Deliberately **not** learned from resolution time: training on how fast the city
actually fixed things would teach the model to deprioritise neglected wards,
which is precisely what the equity multiplier exists to counteract.

```
E(ℓ) = clip( ρ̂(ℓ) / max(r_obs(ℓ), ε), 1, E_max )
```

Bounded below at 1, so it can only ever help.

### 4.6 Clustering

A new report joins an existing issue when it matches on **category**, lies
within **30 m** (PostGIS `ST_DWithin` on the geography type), and falls inside a
**48-hour** window. Duplicates are never discarded.

`RESOLVED` and `CONFIRMED` issues are inside that window on purpose: a fresh
report of a fault the city has just signed off is the strongest available
evidence that the repair did not hold, and it reopens the issue.

### 4.7 Collusion detection

The per-issue independence rules stop one person holding two roles on one issue.
They do nothing about the attack that actually threatens the mechanism:

```
issue 1:  A reports  →  B confirms the repair
issue 2:  B reports  →  A confirms the repair
issue 3:  A reports  →  B confirms the repair      …
```

Every one of those issues passes every per-issue check. The pair can release the
confirmation slice — 45% of each pool — indefinitely. It is visible only in
aggregate, as an edge in the graph of who settles for whom.

For a pair, over a 90-day window:

```
affinity    = shared_issues / min(activity_a, activity_b)
reciprocity = min(a_claimed_b_settled, b_claimed_a_settled)
```

Entanglement is a **ratio of each account's own activity**, never an absolute
count — two neighbours who genuinely report the same street for years would be
indistinguishable from a ring under a raw threshold. What marks a ring is that
the pairing is nearly all of what they do.

| Guard | Value | Purpose |
| --- | --- | --- |
| Minimum shared issues | 3 | Appearing together twice is a street, not a conspiracy |
| Minimum activity each | 5 | A ratio from three observations is noise; and in a small deployment every pair looks exclusive |
| Affinity threshold | 0.5 | Below this, nothing is scored |
| Reciprocal swaps | ≥ 2 | Role-swapping amplifies the score by 0.75 of the remaining range |
| Routing veto | 0.5 | Above this a candidate is refused, not merely down-ranked |

Measured only against the **claim side** — discoverer and corroborators — since
a verifier's fee vested at verification and a confirmer who knows the verifier
unlocks nothing for them.

### 4.8 Photo re-use

Re-using one photograph as several faults is the cheapest fabrication available:
the live-camera rule is UI enforcement, EXIF can be stripped or forged, and a
screenshot defeats both. None of that changes the pixels.

Every photo is reduced to a **64-bit difference hash**. Measured distances:

| Transformation | Hamming distance (of 64) |
| --- | --- |
| Re-encoded at JPEG q35 | 0 |
| Resized to 50% | 2 |
| Resized to 25% | 1 |
| Blurred | 1 |
| **Cropped 5% at the border** | **14** |
| Unrelated photographs | 10–19 |

The two cases cluster cleanly, so the threshold sits in the gap at **6** — four
bits of headroom above every true positive and four below the closest false
positive. **Cropping defeats this hash**, and no threshold fixes that: a 5% crop
lands further from the original than some unrelated photographs do. A DCT-based
pHash would be far more crop-robust and is the documented upgrade.

A match discounts the image evidence rather than rejecting the report. Re-use by
the same account is weighted at 1.0 (no innocent reading); the same image from
two accounts at 0.6 (a widely shared photo, a screenshot passed round a
neighbourhood group).

Measured live: an identical photo resubmitted by the same account dropped the
issue's confidence from **0.387 to 0.094**.

### 4.9 Routing — who may settle

Three rules are structural, not preferences:

1. **The system picks.** A contractor can never nominate whoever signs off their
   own work.
2. **Nobody with a stake decides.** Reporters are excluded, and so is anyone
   holding a contribution in *any* role — the on-site verifier included, since
   they have already staked their reputation on the fault being real.
3. **Tier gates.** A verification admits work to the city's queue, so it demands
   `VERIFIER_ELIGIBLE` with no fallback: if nobody eligible is reachable, the
   issue waits, unpaid and unrejected. A fix-confirmation falls back to
   `TRUSTED`, because refusing to route one would cost honest reporters their
   final 45% over thin verifier coverage. The collusion veto is **not** relaxed
   by that fallback.

Within those candidates, routing prefers great-circle proximity (PostGIS
`ST_Distance` from the candidate's own most recent report, bucketed to 500 m),
then spreads load, then prefers trust.

A verify task nobody attends is re-routed to the next candidate up to twice. The
lapsed assignee is **not** penalised — docking trust for not walking somewhere
would push verifiers toward rubber-stamping tasks they cannot attend.

---

## 5. The reward ledger

The part that has to be correct. Three invariants, each asserted by tests:

**Append-only.** Balances are never stored. A balance is the signed sum of a
user's entries. Vesting appends positive entries; reversals, forfeitures and
redemptions append their own. Nothing is ever updated or deleted — a test
asserts directly that no entry is ever mutated.

**Idempotent.** Every transition derives a deterministic `idempotency_key` with
a unique database index behind it. A retried request, a double-tapped button or
a replayed lifecycle event collides on the index and is dropped rather than
paying twice. The key embeds the issue's reopen cycle, so a genuine
reopen → reconfirm vests again under a fresh key.

**Exact.** Points are `Numeric(18,4)` and Python `Decimal`, never floats, with
one rounding convention (`ROUND_DOWN`) shared by the whole module. Stage amounts
**telescope** — each stage pays cumulative-through minus cumulative-before —
so three stages sum to the entitlement exactly. Quantising each slice
independently leaked a rounding unit per stage; this was a real bug caught by
the tests during development. Pools are split pro-rata with the residual handed
to the largest share, so a pool is always distributed in full.

### Worked example

Pothole in WARD-CENTRAL, reported by a new account.

```
P(I) = 0.4(0.70) + 0.3(0.90) + 0.2(0.70) + 0.1(0.30) = 0.720
E(ℓ) = 3.00 (capped)        τ = 0.500
R_I  = 100 × 0.720 × 3.00 × 0.500 = 108.0000 points
```

| Stage | Discoverer | Verifier | Confirmer | Running balance |
| --- | --- | --- | --- | --- |
| Filed | — | — | — | 0.0000 |
| Verified | +5.4000 | +10.8000 | — | 5.4000 |
| Resolved | +24.3000 | — | — | 29.7000 |
| Confirmed | +24.3000 | — | +21.6000 | **54.0000** |
| *(if reopened)* | −24.3000 | — | −21.6000 | 29.7000 |

The discoverer's 54.0000 is exactly 50% of the pool. The corroborator share
(21.6000) is never minted, because nobody filled that role. These are the
figures produced by `scripts/demo.py` against the running system, not
hand-arithmetic.

---

## 6. Data model

Nine tables:

| Table | Holds |
| --- | --- |
| `users` | Accounts, Beta trust counts, decay anchor, home ward |
| `issues` | A fault: PostGIS geography point, ward, confidence, priority, frozen `reward_total`, reopen count, lifecycle timestamps |
| `reports` | One submission. Unique on (issue, reporter), so nobody farms an issue by corroborating repeatedly |
| `tasks` | Verify and confirm assignments, with expiry and re-route count |
| `contributions` | One row per (issue, user, role). Carries the vesting state machine |
| `reward_ledger` | Append-only entries. Unique `idempotency_key` |
| `redemptions` | What was spent, and that it was simulated |
| `media_objects` | Photos, when the database storage backend is in use |
| `issue_events` | Audit trail of every transition |

Vesting state machine on `contributions`:

```
PENDING_VERIFICATION → PENDING_RESOLUTION → PENDING_CONFIRMATION → PAID
                     ↘ FORFEITED
```

---

## 7. API surface

Six routers, 21 endpoints.

| Prefix | Endpoints |
| --- | --- |
| `/auth` | `signup`, `login`, `me` |
| `/reports` | `POST ""` — report intake, multipart with photo |
| `/issues` | list (spatial), detail, `corroborate` |
| `/tasks` | `me`, `{id}/complete` |
| `/wallet` | `me`, `catalog`, `redeem` |
| `/admin` | `queue`, issue detail, `assign`, `resolve`, `reject`, `reopen`, `close-offapp`, `metrics`, `ledger` |

Plus `/health`, `/config/mechanism` (the live coefficients), `/media/{key}`, and
`/docs` (OpenAPI).

---

## 8. Honest limitations

These are stated plainly because a report that claims more than it built is
worth less than one that does not.

| | Status |
| --- | --- |
| **`s_img` — "does this photo show a pothole?"** | **Not built.** A per-category constant. Needs CLIP zero-shot or a YOLO model fine-tuned on RDD2022, roughly 1.2 GB of dependencies and more memory than the free deployment has. The seam is in place. The cost of being wrong is one verifier's wasted trip, not money — which is why it was the right thing to defer. |
| **Cropped photo re-use** | Not detected, measured and documented rather than glossed. |
| **Points as money** | **Deliberately simulated.** Points are never exchanged for anything. Redemption deducts points and issues nothing; the app refuses to start if that is switched off, since an instance reporting successful redemptions while issuing nothing would be worse than one that does not run. |
| Severity, exposure, vulnerability base rates | Per-category and per-ward constants standing in for trained detectors, traffic counts and POI lookups. The age-growth of escalation is computed, not a constant. |
| Device fingerprinting | Client-supplied, therefore trusted input. |
| Live-camera enforcement | UI enforcement, not proof. Someone can photograph a screen; defeating that needs hardware attestation. |
| Fusion weights | Reasoned, not fitted. Calibrating them needs labelled reports — and with `s_img` constant, the fusion is effectively a three-signal model. |
| Scale | In-process scheduler and in-memory rate limiting mean a single instance. Collusion detection scans pairwise; a city would need the graph materialised. |
| Serviced region | One hardcoded bounding box (loosely Bengaluru, five wards). |

---

## 9. Testing

**175 tests**, run against a real PostgreSQL+PostGIS instance.

| File | Tests | Covers |
| --- | --- | --- |
| `test_signals.py` | 24 | EXIF parsing, geo coherence, impossible travel, fusion decision bands, corroboration saturation, priority ageing, equity bounds |
| `test_ratelimit.py` | 23 | Sliding window, bounded key space, thread safety, proxy-header spoofing, login timing equality |
| `test_collusion.py` | 21 | The take-turns ring, and the honest patterns resembling it |
| `test_imagecheck.py` | 21 | What the perceptual hash must and must not match |
| `test_vesting.py` | 18 | **The ledger**: value conservation, idempotency, forfeiture, reopen/reconfirm, pool splitting, the anti-fabrication bound, immutability |
| `test_routing.py` | 16 | Independence rules, tier gates, proximity, load spreading, task expiry |
| `test_config_guard.py` | 13 | That an unsafe production configuration refuses to boot |
| `test_storage.py` | 13 | Both storage backends, and the EXIF privacy guarantee |
| `test_fulfilment.py` | 8 | That redemptions are simulated and cannot quietly stop being |
| `test_clustering.py` | 7 | Dedup → corroboration through the real HTTP endpoint |
| `test_storage_integration.py` | 6 | Object storage over real HTTP: presigned fetch, 403 unsigned |
| `test_migrations.py` | 5 | Migrations build from a bare database; models have not drifted |
| **Total** | **175** | |

Plus `scripts/demo.py`, which walks the acceptance criteria end to end against a
running API and asserts **15 checks** — four distinct people, points moving
pending → paid across three stages, and a reopen clawing the confirmation slice
back.

### Defects found by the tests, not by inspection

Worth including in a report, because they are the argument for the test suite:

1. **Rounding leak.** Quantising each vesting slice independently lost 0.0001
   per stage, so three stages paid less than the entitlement. Fixed by
   telescoping.
2. **Trust drift between stages.** The corroborator pool was re-split each stage
   using live trust, which moves continuously with decay, breaking the
   telescoping. Fixed by telescoping at pool level.
3. **User-enumeration timing oracle.** `verify_password` ran only when the
   account existed, so an unknown address returned in ~2.7 ms and a known one in
   ~247 ms — a **93× difference** that answers "is this email registered?" to
   anyone with a stopwatch. Fixed with a dummy-hash comparison.
4. **Silent logging loss.** Applying migrations in-process reconfigured logging
   from `alembic.ini`, whose root logger is WARNING; the API then served traffic
   producing five log lines total.
5. **Collusion starving routing.** Measuring entanglement against all
   contributors (verifiers included) vetoed every candidate in a small
   deployment, so nothing could be confirmed.
6. **A form-submit bug in the web capture.** Buttons inside the report form had
   no explicit `type`, so clicking "use a stand-in image" submitted the report
   with no photo, which was then rejected for lack of evidence with nothing on
   screen to explain why.

---

## 10. Security and operations

| Control | Implementation |
| --- | --- |
| Authentication | JWT, bcrypt password hashing |
| Boot-time guard | `STARNET_ENVIRONMENT=production` refuses to start with a guessable JWT secret, the demo seed enabled, an unreachable CORS configuration, ephemeral photo storage, or payments un-simulated — listing every problem at once |
| Credential limits | 8 failed logins per account per 2 min, 50 per day; 50 per address per 15 min; 30 sign-ups per address per hour. Only failures count; success clears both account bands |
| CORS | Explicit allowlist; empty is correct when the API serves its own dashboard, because same-origin requests do not use CORS |
| EXIF privacy | Capture time and GPS are read for the geo check, then the image is re-encoded from its pixels, so no maker notes, XMP packets or thumbnails survive |
| Photo access | Object storage objects are written private and served as expiring presigned URLs |
| Schema | Alembic, with a test that fails if the models drift from the migrations |

**Deliberate trade-off worth defending in a viva:** a throttled account is
refused *even with the correct password*, because the limit is checked before
the hash comparison, so a throttled attacker gets no signal — not even a timing
one. The cost is that anyone who knows an address can deny its owner access for
the window's length, which is why the burst window is two minutes rather than
fifteen, with a separate 50-per-day band doing the long-range work.

---

## 11. Deployment

One command locally:

```bash
cd infra && docker compose up --build     # → http://localhost:8000
```

The API image builds the dashboard and serves it, so one URL covers the
municipal and citizen views. Which view you see depends on the account.

Deployed via a Render blueprint (`render.yaml`) in **demo** mode — a third
environment between development and production: it still refuses a guessable
JWT secret, because the URL is public, but permits the seeded accounts and
database-backed photo storage, which are the point of a demo rather than
mistakes.

---

## 12. Future work

1. **The image model.** CLIP zero-shot for `s_img`, then a detector fine-tuned
   on RDD2022 for road damage; the other five categories each need their own
   dataset.
2. **DCT-based perceptual hashing**, to catch cropped re-use.
3. **Category-specific trust** — being reliable about potholes should not vouch
   for your water-leak reports.
4. **Materialised collusion graph**, so detection does not scan pairwise.
5. **Celery + Redis**, replacing the in-process scheduler, so the service can
   run more than one instance.
6. **Field calibration** of the fusion weights and trust coefficients against
   labelled reports, which is the only way to know whether the thresholds are
   right.

---

## 13. Summary

STAR-Net implements a civic fault reporting mechanism whose central property is
structural rather than procedural: **because reward vests on physically
confirmed resolution, a fabricated report can collect at most 5% of an issue's
pool, and only after a real person has attended the location.**

Around that core it implements multi-signal confidence fusion, a Beta-reputation
trust engine with decay and an evidence floor, spatial clustering that turns
duplicates into corroboration, equity-weighted prioritisation that ages, a
collusion graph that refuses a settler entangled with whoever the settlement
would pay, and perceptual hashing that catches re-used photographs — all on top
of an append-only, idempotent, exact-decimal reward ledger covered by 175 tests.

What is not built is stated as not built. The image model is a constant, cropped
re-use escapes the hash, the coefficients are reasoned rather than fitted, and
points are never money.
