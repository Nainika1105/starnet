"""All STAR-Net tunables live here.

Every coefficient from the design doc is a config field so the mechanism can be
retuned without touching engine code. Override any of them with a STARNET_
prefixed environment variable.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# Sentinel, not a secret: its only job is to be recognisable so a production
# boot can refuse it.
DEV_JWT_SECRET = "dev-secret-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="STARNET_", extra="ignore")

    # --- infrastructure ---------------------------------------------------
    database_url: str = "postgresql+psycopg://starnet:starnet@localhost:55432/starnet"
    jwt_secret: str = DEV_JWT_SECRET
    jwt_algorithm: str = "HS256"
    jwt_ttl_minutes: int = 60 * 24 * 7
    seed_on_startup: bool = False

    # Apply pending Alembic migrations when the API boots. Safe here because
    # the deployment runs a single instance (see fly.toml); with several
    # instances this races, and migrations belong in a release step instead --
    # which fly.toml also configures, making this a no-op there.
    migrate_on_startup: bool = True

    # --- redemptions ------------------------------------------------------
    # Points are not money and are never exchanged for it. Redemptions deduct
    # points and record the choice; nothing is issued and no provider is
    # called. This cannot be switched off and still work -- no real fulfilment
    # backend exists, so `fulfilment.fulfil` raises and the production guard
    # refuses to boot. See app/fulfilment.py.
    payments_simulated: bool = True

    # --- photo storage ----------------------------------------------------
    # "local" (a directory served at /media) or "s3" (any S3-compatible store:
    # AWS, Supabase Storage, Cloudflare R2, MinIO).
    #
    # On a platform with an ephemeral filesystem, "local" loses every photo on
    # redeploy -- and the photos are the evidence the reward mechanism rests
    # on, so the production guard below treats that as a misconfiguration.
    storage_backend: str = "local"
    media_root: str = "./media"

    s3_bucket: str = ""
    s3_endpoint_url: str = ""   # empty = real AWS; set it for Supabase/R2/MinIO
    s3_region: str = "us-east-1"
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    # Photos are served as presigned URLs rather than public objects. An hour
    # is long enough for a dashboard session and short enough that a leaked
    # URL stops working.
    s3_url_ttl_seconds: int = 3600

    # "development" or "production". Production is not a cosmetic label: it
    # makes the app refuse to start with the dev JWT secret, with the demo
    # seed enabled, or with no CORS allowlist. A deployment that is insecure
    # should fail loudly at boot rather than quietly serve traffic.
    environment: str = "development"

    # Comma-separated origins allowed to call the API from a browser. Empty
    # means the local dev defaults; in production it must be set explicitly.
    cors_origins: str = ""

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() == "production"

    @property
    def allowed_origins(self) -> list[str]:
        explicit = [o.strip() for o in self.cors_origins.split(",") if o.strip()]
        if explicit:
            return explicit
        # Vite dev server and the Expo web/dev-client ports.
        return ["http://localhost:5173", "http://localhost:8081", "http://localhost:19006"]

    def production_problems(self) -> list[str]:
        """Everything that makes this configuration unsafe to expose.

        Collected rather than raised one at a time so a deployer sees the whole
        list on the first failed boot instead of fixing them one restart apiece.
        """
        problems: list[str] = []
        if self.jwt_secret == DEV_JWT_SECRET or len(self.jwt_secret) < 32:
            problems.append(
                "STARNET_JWT_SECRET is the dev default or shorter than 32 characters. "
                "Anyone who knows it can mint a token for any account, including an "
                "admin. Generate one with: python -c \"import secrets; "
                "print(secrets.token_urlsafe(48))\""
            )
        if self.seed_on_startup:
            problems.append(
                "STARNET_SEED_ON_STARTUP is on. The seed creates six accounts that "
                f"all share the password published in the README, one of them an "
                "admin. Never enable it on an instance reachable from the internet."
            )
        if not [o.strip() for o in self.cors_origins.split(",") if o.strip()]:
            problems.append(
                "STARNET_CORS_ORIGINS is empty, which would fall back to localhost. "
                "Set it to the dashboard's real origin."
            )
        if not self.payments_simulated:
            problems.append(
                "STARNET_PAYMENTS_SIMULATED is false, but no real fulfilment "
                "backend is implemented, so redemptions would report success "
                "while issuing nothing. Points in this system are not money. "
                "Implement app/fulfilment.py before changing this."
            )
        if self.storage_backend.strip().lower() == "local":
            problems.append(
                "STARNET_STORAGE_BACKEND is 'local'. On a host with an ephemeral "
                "filesystem every photo is lost on redeploy, and the photographs "
                "are the evidence the vesting mechanism rests on. Use 's3' with "
                "object storage, or set this deliberately if STARNET_MEDIA_ROOT is "
                "a genuinely persistent mounted disk."
            )
        return problems

    # --- reward engine (section 5) ---------------------------------------
    r0_base_points: float = 100.0
    e_max: float = 3.0

    # role split, must sum to 1
    f_discoverer: float = 0.50
    f_corroborator: float = 0.20
    f_verifier: float = 0.10
    f_confirmer: float = 0.20

    # vesting split, must sum to 1. g_v is THE anti-fraud knob: keep it small
    # so a fabricated fault (which can never be resolved or confirmed) can
    # collect at most g_v * f_discoverer * R_I.
    g_verification: float = 0.10
    g_resolution: float = 0.45
    g_confirmation: float = 0.45

    max_corroborators: int = 5

    # --- confidence engine (section 6) -----------------------------------
    w0: float = -3.5
    w_img: float = 2.0
    w_geo: float = 1.5
    w_cor: float = 2.5
    w_trust: float = 1.0

    theta_lo: float = 0.40
    theta_hi: float = 0.75

    # "fusion" (the logistic model of section 6, the real mechanism) or "stub"
    # (the Phase-1 shortcut that auto-accepts a single plausible report).
    # Under fusion a lone uncorroborated report from an ordinary account lands
    # in the VERIFY band on purpose: one stranger's photo is not enough to put
    # work on the city's queue.
    confidence_mode: str = "fusion"

    corroboration_s0: float = 1.5
    rho_min: float = 0.05

    # --- collusion graph (section 8) --------------------------------------
    # Appearing alongside someone a couple of times is a street, not a
    # conspiracy. Below this many shared issues the pair scores zero, however
    # concentrated it looks.
    collusion_min_shared_issues: int = 3
    # Both accounts need at least this much history before a ratio about them
    # means anything. Affinity computed from three observations is noise, and
    # in a small or new deployment EVERYONE looks entangled -- there is nobody
    # else to have worked with. Without this floor the first users of a system
    # get flagged for the crime of being early.
    collusion_min_activity: int = 5
    # What share of the less-active account's work has to involve the other
    # before it counts as entanglement. Measured as a ratio, never an absolute
    # count: two neighbours who genuinely report the same street for years
    # would look identical to a ring under a raw threshold. What marks a ring
    # is that the pairing is nearly ALL of what they do.
    collusion_affinity_threshold: float = 0.5
    collusion_window_days: float = 90.0
    # Swapping claim and settle roles -- I report, you confirm, you report, I
    # confirm -- is the signature of the attack, so it pushes the score most of
    # the way up on its own.
    collusion_min_reciprocal_swaps: int = 2
    collusion_reciprocal_weight: float = 0.75
    # Above this, a confirmer candidate is not merely down-ranked but refused.
    # The confirmation slice is 45% of the pool; releasing it is the single
    # most valuable action in the system, so the bar for who may is high.
    collusion_routing_veto: float = 0.5

    # --- clustering (section 6) ------------------------------------------
    cluster_distance_m: float = 30.0
    cluster_window_hours: float = 48.0
    clustering_enabled: bool = True

    # --- photo re-use (s_img) ---------------------------------------------
    # Hamming distance between two 64-bit difference hashes below which the
    # images are treated as the same photograph.
    #
    # Measured, the two cases form clear clusters: the same photo after
    # re-encoding, resizing or blurring lands at 0-2 bits, while unrelated
    # photographs start at 10. Six sits in the middle of that gap rather than
    # at either edge, which is where a threshold between two clusters belongs:
    # four bits of headroom above every true positive and four below the
    # closest false positive. See engines/imagecheck.py for the figures.
    photo_hash_max_distance: int = 6
    photo_reuse_window_days: float = 365.0
    # Re-use by the same account has no innocent reading: one photograph
    # submitted as several faults is the fabrication this catches. The same
    # image from two accounts does have innocent readings -- a widely shared
    # photo, a screenshot passed round a neighbourhood group -- so it costs
    # less.
    photo_reuse_same_reporter_weight: float = 1.0
    photo_reuse_other_reporter_weight: float = 0.6

    # --- geo plausibility -------------------------------------------------
    # How far the photo's own EXIF GPS may sit from the claimed location before
    # it stops corroborating it. Consumer GPS is routinely 20-30 m out, so this
    # is deliberately loose; it is here to catch a photo taken streets away,
    # not to punish a weak fix.
    exif_gps_match_m: float = 120.0
    exif_gps_far_m: float = 1_000.0

    # --- prioritisation (section 7) --------------------------------------
    lambda_severity: float = 0.4
    lambda_exposure: float = 0.3
    lambda_vulnerability: float = 0.2
    lambda_escalation: float = 0.1

    # Age-awareness of the escalation component. A fault starts at
    # escalation_floor of its category's escalation rate and saturates toward
    # the full rate with time constant escalation_tau_hours, so an issue the
    # city leaves alone climbs the queue by itself.
    #
    # This moves the RANKING only. R_I is frozen from the creation-time
    # priority, so ageing can never retroactively change what a contributor
    # was promised.
    escalation_floor: float = 0.4
    escalation_tau_hours: float = 168.0  # one week

    # --- trust engine (section 8) ----------------------------------------
    trust_decay_per_week: float = 0.95
    tier_trusted_min: float = 0.60
    tier_verifier_min: float = 0.75
    # How much evidence a user needs before a tier is granted at all.
    #
    # tau is a ratio, and a ratio from one observation is noise: a single
    # confirmed report puts a brand-new account at (1+1)/(1+0+2) = 0.667, over
    # the TRUSTED line, which would hand it the right to release other
    # people's confirmation slice -- 45% of a pool -- on the strength of one
    # data point. The Beta prior keeps the SCORE honest at low evidence; this
    # keeps the PRIVILEGES honest too.
    tier_min_evidence: float = 3.0

    # --- anti-abuse (section 8) ------------------------------------------
    max_reports_per_hour: int = 10
    max_reports_per_day: int = 40
    max_travel_speed_kmh: float = 120.0

    # --- credential endpoint limits ---------------------------------------
    # Two buckets, because the two attacks have different shapes.
    #
    # Per account, in two bands, because one window cannot do both jobs.
    #
    # A throttled account is refused even with the right password -- the check
    # runs before the hash comparison, so a throttled attacker gets no signal
    # at all, not even a timing one. That is the safe choice, but it means
    # anyone who knows an address can deny its owner access for the length of
    # the window. So the burst window is kept SHORT: eight wrong passwords is
    # far beyond mistyping, and the owner waits two minutes, not a quarter of
    # an hour.
    #
    # Two minutes alone would leave ~5,700 guesses a day, which is enough to
    # find a weak password, so a second much longer band caps the sustained
    # rate. Together: a legitimate user is never locked out for long, and an
    # attacker gets fifty guesses a day against any one account.
    login_max_attempts_per_account: int = 8
    login_window_seconds: float = 120.0           # the burst band
    login_max_attempts_per_account_daily: int = 50
    login_account_daily_window_seconds: float = 86_400.0
    #
    # Per address defends against enumeration across many accounts, and is
    # deliberately loose, because an address is a bad proxy for a person:
    # an office, a campus or a phone network puts hundreds of legitimate users
    # behind one NAT. Tight per-IP limits lock out a building to inconvenience
    # one attacker, and the per-account bucket is what stops the guessing
    # anyway.
    login_max_attempts_per_ip: int = 50

    # Same NAT reasoning. The job here is to stop somebody farming hundreds of
    # sybil accounts, not to ration a classroom signing up together -- and the
    # mechanism already makes sybils unprofitable, since a fabricated report
    # can collect at most g_v * f_D of a pool.
    signup_max_per_ip: int = 30
    signup_window_seconds: float = 3600.0

    # Name of a header carrying the caller's real address, for when the API
    # sits behind a proxy -- "Fly-Client-IP" on Fly, "CF-Connecting-IP" behind
    # Cloudflare. Empty means use the socket peer.
    #
    # It must be a header the proxy OVERWRITES. Pointing this at
    # X-Forwarded-For would let a caller set their own key and sidestep the
    # limit entirely, since a fresh key is never over its limit.
    client_ip_header: str = ""

    # --- verification routing --------------------------------------------
    # A verify task nobody actions is re-routed to the next-best verifier.
    # After max_verify_reroutes the issue waits rather than being rejected:
    # nobody turning up is not evidence that the fault is fake.
    verification_window_hours: float = 48.0
    max_verify_reroutes: int = 2
    # Beyond this, a candidate's last known position tells us nothing useful
    # about whether they can walk past the fault.
    routing_max_distance_m: float = 5_000.0

    # --- fix confirmation ------------------------------------------------
    confirmations_required: int = 1
    confirmation_window_hours: float = 72.0


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
