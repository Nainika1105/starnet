"""All STAR-Net tunables live here.

Every coefficient from the design doc is a config field so the mechanism can be
retuned without touching engine code. Override any of them with a STARNET_
prefixed environment variable.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="STARNET_", extra="ignore")

    # --- infrastructure ---------------------------------------------------
    database_url: str = "postgresql+psycopg://starnet:starnet@localhost:55432/starnet"
    jwt_secret: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_ttl_minutes: int = 60 * 24 * 7
    media_root: str = "./media"
    seed_on_startup: bool = False

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

    # --- clustering (section 6) ------------------------------------------
    cluster_distance_m: float = 30.0
    cluster_window_hours: float = 48.0
    clustering_enabled: bool = True

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

    # --- anti-abuse (section 8) ------------------------------------------
    max_reports_per_hour: int = 10
    max_reports_per_day: int = 40
    max_travel_speed_kmh: float = 120.0

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
