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

That is the whole thing: one command, two containers, and **one URL**.

| Surface | URL | Sign in |
| --- | --- | --- |
| Municipal dashboard | **http://localhost:8000** | `admin@starnet.demo` / `starnet123` |
| API docs (OpenAPI) | http://localhost:8000/docs | — |
| Mechanism knobs | http://localhost:8000/config/mechanism | — |

The API image builds the dashboard and serves it at `/`, so one host answers
for both. That means one link to open or share and no CORS to configure —
`VITE_API_BASE` is empty in the image, which tells the dashboard to call the
same origin it was served from.

For front-end work there is still a Vite dev server with hot reload, behind a
profile so it does not start by default:

```bash
cd infra && docker compose up admin
```

It serves on http://localhost:5173 and talks to the API on 8000.

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

169 tests. The ones that touch the database need Postgres; the config, signal, storage and rate-limit tests are pure and run without it.

| File | Covers |
| --- | --- |
| `test_vesting.py` | The ledger: value conservation, idempotency, forfeiture, reopen/reconfirm cycles, pool splitting, the anti-fabrication bound, and that no entry is ever mutated |
| `test_signals.py` | EXIF parsing and GPS/time coherence, impossible-travel detection, the confidence fusion's decision bands, corroboration saturation, priority ageing, equity bounds |
| `test_routing.py` | The independence rules, tier gates, proximity and load-spreading, verify-task expiry and re-routing |
| `test_clustering.py` | Dedup → corroboration through the real HTTP endpoint, and reopen-on-re-report with its ledger reversal |
| `test_storage.py` | That the stored photo carries none of the EXIF read off the upload, and both storage backends' contracts |
| `test_config_guard.py` | That an unsafe production configuration refuses to boot |
| `test_migrations.py` | That the migrations build the schema from a bare database, that the models have not drifted from them, and that applying them does not silence the app's logging |
| `test_ratelimit.py` | The credential limits: window sliding, bounded key space, thread safety, that a spoofable header is not trusted, and that an unknown address costs the same work as a known one |
| `test_collusion.py` | The take-turns ring, and the honest patterns that resemble it: a prolific volunteer, two neighbours on one street, and the first users of a small deployment |
| `test_imagecheck.py` | Photo re-use: what the perceptual hash must match (re-encoding, resizing, blur) and must not (different photographs), plus the crop it cannot catch |
| `test_fulfilment.py` | That redemptions are simulated and cannot quietly stop being: the flag fails loudly, production refuses to boot without it, and no payment dependency is installed |

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
2. **Photo re-use** — every photo is perceptually hashed and compared against
   what has been submitted before. Re-using one picture as several faults is
   the cheapest fabrication available: the live-camera rule is UI enforcement
   only, EXIF can be stripped or forged, and a screenshot defeats both — but
   none of that changes the pixels. A match discounts the image evidence rather
   than rejecting the report, weighted hardest when it is the same account,
   because one photograph submitted as several faults has no innocent reading.
3. **Confidence** `C(r) = σ(w₀ + w₁s_img + w₂s_geo + w₃s_cor + w₄τ)`.
   `C ≥ θ_hi` auto-accepts, `C < θ_lo` rejects, in between routes a verify task to a trusted user.
   In practice a lone uncorroborated report from an ordinary account lands in the middle band —
   that is the design, not a tuning accident.
4. **Cluster** — a nearby report of the same category joins the existing issue as corroboration,
   which raises its confidence and can carry it over `θ_hi` with no verifier needed. Duplicates
   are never deleted. A report matching an issue the city has already signed off **reopens** it.
5. **Prioritise** — `P(I) = Σ λᵢxᵢ` over severity, exposure, vulnerability and escalation risk,
   times an equity multiplier `E(ℓ)` that favours under-reported wards. The escalation term grows
   with the issue's age, so a neglected fault climbs the queue by itself.
6. **Dispatch & resolve** — the admin assigns a crew and marks the work done.
7. **Fix-confirm** — the *system* picks a nearby high-trust user who is not a reporter, not a
   contributor in any role, and not chosen by the contractor. They must submit a photo. No
   confirmation inside the window, or a "still broken" verdict, reopens the issue.
8. **Settle** — reward vests across the three stages.

### Who may do what

Routing enforces three rules that are structural, not preferences — each is a way the mechanism
could be gamed if it bent:

- **The system picks.** A contractor can never nominate whoever signs off their own work.
- **Nobody with a stake decides.** Reporters are excluded, and so is anyone holding a contribution
  on the issue in *any* role — including the on-site verifier, who has already staked their
  reputation on the fault being real. One person cannot carry an issue from report to payout.
- **Evidence before privileges.** A tier needs observations, not just a good
  ratio: one confirmed report alone puts a brand-new account at 0.667, over the
  TRUSTED line, which would grant the right to release somebody else's 45% on a
  single data point. The Beta prior keeps the score honest at low evidence;
  `tier_min_evidence` keeps the privileges honest too.
- **Tier gates.** A verification admits work to the city's queue, so it demands
  `VERIFIER_ELIGIBLE` with no fallback: if nobody eligible is reachable, the issue waits, unpaid
  and unrejected. A fix-confirmation falls back to `TRUSTED`, because refusing to route one would
  cost honest reporters their final 45% over the city's thin verifier coverage.

It also refuses a settler who is **entangled with the people the settlement
would pay**. The per-issue rules above do nothing about two accounts taking
turns across many issues — A reports and B confirms, then B reports and A
confirms — and every one of those issues passes every per-issue check while
the pair mints the confirmation slice, 45% of the pool, at will. That is only
visible in aggregate, so `engines/collusion.py` scores it as an edge in the
graph of who appears alongside whom, and routing vetoes above a threshold.

Three things keep that from punishing honest users, and each has a test:
entanglement is a **ratio** of each account's own activity, never an absolute
count, because two neighbours who genuinely report the same street for years
would look identical to a ring under a raw threshold; a pair needs a minimum of
shared history before any ratio is computed; and both accounts need a minimum
of total activity, because in a small or new deployment there is hardly anybody
else to have worked with and every pair looks exclusive. Swapping claim and
settle roles amplifies the score, since that is the ring's actual signature
rather than merely a working relationship.

Entanglement is measured only against the **claim side** — the discoverer and
corroborators — because a verifier's fee already vested at verification and a
confirmer who knows the verifier unlocks nothing for them. Measuring against
every contributor starved routing entirely in a small deployment.

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

### Points are not money

Nothing in this system touches real money, and that is enforced rather than
merely true today:

- Redeeming deducts points and records the choice. No voucher, credit or
  payment is issued, and no provider is called.
- `STARNET_PAYMENTS_SIMULATED` cannot be switched off and still work —
  `fulfilment.fulfil` raises, because no real backend exists, so going live
  would have to be a deliberate piece of work rather than a config change. The
  production boot guard refuses it too: an instance reporting successful
  redemptions while issuing nothing is worse than one that will not start.
- Every redemption records `simulated: true` and a `SIM-…` reference on the row
  and in the API response, so a client cannot present one as delivered.
- The catalogue descriptions say so. "Rs. 50 off your water bill" reads like
  real value, and a demo audience should not have to guess which parts are
  pretend.
- No payment SDK is in `requirements.txt`, and a test asserts none appears.

The seam for real fulfilment is `app/fulfilment.py`. The ledger arithmetic,
idempotency keys and balances are identical either way.

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

**Phase 3.** The collusion graph, with a routing veto that stops a settler
paying their own long-standing partner · an evidence floor on trust tiers, so a
single confirmed report no longer grants the right to release somebody else's
45% · perceptual hashing of every photo, so re-using one picture as several
faults discounts the evidence.

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
| `s_img`'s *semantic* half — "is there actually a pothole in this photo?" is still a per-category constant | `engines/confidence.py: score_image` | CLIP zero-shot, then YOLO fine-tuned on RDD2022. Needs ~1.2 GB of dependencies and more memory than the configured 512 MB machine, so it wants a bigger instance or a hosted inference endpoint |
| Perceptual hashing cannot detect a **cropped** re-use | `engines/imagecheck.py` | DCT-based pHash, which is far more crop-robust; needs numpy or scipy for the transform |
| Notifications of a routed task — the user has to open the Tasks tab | `routers/tasks.py` | Push, so a verify task reaches someone while they are still near the spot |
| Clustering predicate — same category, within 30 m, inside 48 h | `routers/reports.py: find_cluster_match` | HDBSCAN over a combined distance that adds CLIP embedding similarity to the metres, so two photos of one pothole from opposite kerbs cluster and two faults at one junction do not |
| Base rates for exposure, vulnerability, escalation and `ρ̂(ℓ)` — per-ward and per-category constants | `engines/priority.py` | Traffic counts, POI lookups for schools and hospitals, weather-aware escalation. (The escalation term's growth with age is computed, not a constant.) |
| Device fingerprinting — the client supplies the value, so it is trusted input | `models.py: User.device_fingerprint` | Attested device identity, or at least a fingerprint derived server-side |
| Photo storage — local volume | `app/storage.py` | S3 or Supabase storage behind the same `save`/`url_for` interface |
| Redemption catalogue — static list, and fulfilment is simulated by design | `app/catalog.py`, `app/fulfilment.py` | Partner coupon and municipal credit integrations. Simulation is enforced, not incidental — see **Points are not money** |
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

## Deploying

The target this is configured for is **Fly.io for the API, Supabase for Postgres
and photo storage, and the dashboard as static files**. Nothing about the code
is specific to those; the API is a plain container, the database is plain
Postgres with PostGIS, and storage is any S3-compatible bucket.

### The production guard

`STARNET_ENVIRONMENT=production` makes the API **refuse to boot** if the
configuration would be unsafe to expose, and it lists every problem at once
rather than one per restart:

| It refuses | Because |
| --- | --- |
| the dev JWT secret, or one under 32 chars | anyone who knows it can mint a token for any account, admin included |
| `STARNET_SEED_ON_STARTUP=true` | the seed creates six accounts sharing the password printed in this README, one of them the city admin |
| an empty `STARNET_CORS_ORIGINS` | it would silently fall back to localhost |
| `STARNET_STORAGE_BACKEND=local` | on an ephemeral filesystem every photo is lost on redeploy, and the photos are the evidence the whole mechanism rests on |

The last one is the only check you might legitimately override — set it
deliberately if `STARNET_MEDIA_ROOT` is a genuinely persistent mounted disk.

### Quickest: Render, from the blueprint

`render.yaml` in the repository root describes the whole thing. In Render:
**New → Blueprint → pick this repository.** It creates the web service and a
Postgres database with every setting wired, including a generated JWT secret.
Nothing to fill in by hand.

What that deploys is the **demo** configuration — a deliberate middle ground:

| | |
| --- | --- |
| `STARNET_ENVIRONMENT=demo` | Still refuses a guessable JWT secret, because the URL is public |
| Seeded accounts | Allowed, and the point: whoever opens the link needs data and an account |
| `STARNET_STORAGE_BACKEND=db` | Photos in Postgres, because a free instance's disk is wiped on redeploy and a demo whose images all 404 shows nothing |
| CORS | Empty, and correctly so — the dashboard is served from the same host, and same-origin requests do not use CORS |

The free tier sleeps when idle, so the first visit after a quiet spell takes
around 30 seconds. The free Postgres expires after 30 days.

For a real deployment set `STARNET_ENVIRONMENT=production`,
`STARNET_STORAGE_BACKEND=s3` with object-storage credentials, and turn the seed
off — the app refuses to start otherwise, which is the intent.

### Alternative: Supabase for the database

Create a project, then a **private** storage bucket for the photos. PostGIS
ships enabled, but the app runs `CREATE EXTENSION IF NOT EXISTS postgis` at
startup anyway, so the role needs rights to do that once.

Take the pooled connection string (port 6543) rather than the direct one — Fly
machines reconnect, and the pooler handles that better — and rewrite its scheme
for psycopg 3:

```
postgresql+psycopg://postgres.<ref>:<password>@<host>:6543/postgres
```

Storage credentials come from the project's S3 access keys, not the anon key:

```
STARNET_S3_ENDPOINT_URL=https://<ref>.supabase.co/storage/v1/s3
STARNET_S3_REGION=<the project's region>
STARNET_S3_BUCKET=starnet-photos
```

### 2. API (Fly)

`api/fly.toml` is committed; secrets are not.

```bash
cd api
fly auth login
fly launch --no-deploy --copy-config --name starnet-api
fly secrets set \
  STARNET_JWT_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')" \
  STARNET_DATABASE_URL="postgresql+psycopg://..." \
  STARNET_CORS_ORIGINS="https://your-dashboard-host" \
  STARNET_S3_BUCKET="starnet-photos" \
  STARNET_S3_ENDPOINT_URL="https://<ref>.supabase.co/storage/v1/s3" \
  STARNET_S3_REGION="<region>" \
  STARNET_S3_ACCESS_KEY_ID="..." \
  STARNET_S3_SECRET_ACCESS_KEY="..."
fly deploy
```

`min_machines_running = 1` and no horizontal scaling is deliberate: APScheduler
runs **inside** the API process, so a second machine is a second scheduler —
confirmation windows expired twice, issues reopened twice. The ledger's
idempotency keys stop that double-paying anyone, but the lifecycle events would
still duplicate. Scaling out needs the Celery/Redis swap first.

### 3. Dashboard (static)

The admin `Dockerfile`'s default target builds static assets and serves them
from nginx. Vite **inlines** env vars at build time, so the API URL is a build
argument and not a runtime variable — a rebuild is needed to repoint it.

```bash
cd admin
docker build --build-arg VITE_API_BASE=https://starnet-api.fly.dev -t starnet-admin .
```

Any static host works equally well: `npm run build` with
`VITE_API_BASE=https://starnet-api.fly.dev` and upload `dist/`.

### 4. Mobile

Point `expo.extra.apiBase` in `mobile/app.json` at the deployed API and run
`npx expo start`. App-store distribution is a separate track — it needs EAS
Build plus Apple and Google developer accounts, and is not configured here.

### Schema changes

Alembic, not `create_all`. `create_all` builds a correct schema on an empty
database and then silently does nothing forever: it cannot add a column, widen
a numeric or add an enum value to a database that already has tables. For a
ledger whose precision is load-bearing, "silently does nothing" is the wrong
failure mode.

```bash
cd api
alembic revision --autogenerate -m "what changed"   # after editing models.py
alembic upgrade head
```

The initial revision installs PostGIS itself, so `upgrade head` works against
the bare database a managed Postgres hands you. Migrations run in three places,
and the last two are no-ops once the first has run: Fly's `release_command`
before new machines take traffic, the API at startup (single instance, so no
race), and by hand.

A test guards the thing that actually rots migration chains — a model change
with no revision. It builds a database from the migrations alone and fails if
autogenerate would still find a difference. If you see that failure, you
changed a model and did not generate the revision.

If you have a database created by the old `create_all` path, it has no
`alembic_version` table, so `upgrade` would try to create tables that exist.
Stamp it once: `alembic stamp head`.

### Credential limits

`/auth/login` and `/auth/signup` are rate limited in process. Three buckets,
because the attacks have different shapes:

| Bucket | Default | Job |
| --- | --- | --- |
| per account, burst | 8 / 2 min | the brute-force defence |
| per account, daily | 50 / 24 h | caps sustained guessing, which the short window alone would allow thousands of |
| per address | 50 / 15 min | enumeration across many accounts |
| sign-ups per address | 30 / h | account farming |

Only **failed** logins count, and a success clears both account bands, so
mistyping a password twice costs nothing.

Two decisions worth knowing about. **A throttled account is refused even with
the correct password** — the limit is checked before the hash comparison, so a
throttled attacker gets no signal at all, not even a timing one. The cost is
that anyone who knows an address can deny its owner access for the length of the
window, which is why the burst window is two minutes rather than fifteen. And
**the per-address limits are deliberately loose**, because an address is a poor
proxy for a person: an office or a phone network puts hundreds of legitimate
users behind one NAT, and tight per-IP limits lock out a building to
inconvenience one attacker.

Behind a proxy, set `STARNET_CLIENT_IP_HEADER` to a header the proxy
*overwrites* — `Fly-Client-IP`, `CF-Connecting-IP`. Not `X-Forwarded-For`,
which is appended to: keying on it would let a caller pick their own bucket and
skip the limit entirely, since a fresh key is never over its limit.

The counters are in memory, which has two honest consequences: a restart
forgets them, and running several API instances divides the effective limit by
the instance count. Both are fine for one machine, and both want the same
shared backend the scheduler does when this scales out.

### Before you call it production

- **No error tracking and no backups** beyond whatever the database provider
  does by default.
- The trust and confidence coefficients are reasoned rather than fitted to real
  reports, and `s_img` is still a constant.
- `/auth/signup` still tells a caller when an address is already registered,
  which is a mild enumeration oracle. Kept so the form can explain itself; the
  rate limit is what stops it being enumerated at scale.

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
