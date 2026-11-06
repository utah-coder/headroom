"""Single source of simulation constants for Headroom.

Every constant carries a trailing ``# REAL: <source>`` or ``# ILLUSTRATIVE``
comment. No other module under ``sim/`` may define these values as literals
(Requirement 10.1); they must be imported from here.

Derived values (computed by consumers, not stored as literals):
facility peak ``IT_PEAK_KW * PUE``, inference peak
``IT_PEAK_KW * INFERENCE_SHARE``, expected training IT kW
``IT_PEAK_KW * TRAINING_SHARE``, and the job arrival rate
``expected_training / (mean_power * mean_duration_h)`` (about 2.35 jobs/hour).
"""

# --- Run / reproducibility
SEED = 42                                  # ILLUSTRATIVE
ENABLE_SUN_SCENARIO = True                 # ILLUSTRATIVE (cut flag #2)
ENABLE_MOVE_REGION = True                  # ILLUSTRATIVE (cut flag #3)

# --- EIA (REAL: EIA-930 via EIA API v2)
EIA_BASE = "https://api.eia.gov/v2/electricity/rto"   # REAL: EIA API v2 base URL
EIA_PAGE_LENGTH = 5000                     # REAL: EIA API v2 max rows per JSON page
HOME_REGION = "PACE"                       # REAL: EIA-930 respondent (PacifiCorp East)
ALT_REGIONS = ("BPAT", "CISO")             # REAL: EIA-930 respondents
REGION_TYPES = ("D", "DF", "NG", "TI")     # REAL: EIA-930 region-data type codes
DATA_START = "2026-06-01T00"               # REAL: EIA-930 Data_Range start (range choice per brief)
DATA_END = "2026-09-30T23"                 # REAL: EIA-930 Data_Range end (range choice per brief)
DISPLAY_TZ = "America/Denver"              # REAL: IANA zone for PACE local time

# --- Emission factors, gCO2eq/kWh (average intensity, not marginal)
EMISSION_FACTORS = {  # REAL: IPCC AR5 lifecycle medians, except ILLUSTRATIVE Oil and Other
    "Coal": 820,        # REAL: IPCC AR5 lifecycle median
    "Natural gas": 490, # REAL: IPCC AR5 lifecycle median
    "Biomass": 230,     # REAL: IPCC AR5 lifecycle median
    "Solar": 48,        # REAL: IPCC AR5 lifecycle median
    "Hydro": 24,        # REAL: IPCC AR5 lifecycle median
    "Nuclear": 12,      # REAL: IPCC AR5 lifecycle median
    "Wind": 11,         # REAL: IPCC AR5 lifecycle median
    "Geothermal": 38,   # REAL: IPCC AR5 lifecycle median
    "Oil": 650,         # ILLUSTRATIVE
    "Other": 300,       # ILLUSTRATIVE
}
FUEL_CODE_MAP = {  # REAL: EIA-930 fuel-type codes; any other code (e.g. GEO) maps to Other
    "COL": "Coal",          # REAL: EIA-930 fuel code
    "NG": "Natural gas",    # REAL: EIA-930 fuel code
    "NUC": "Nuclear",       # REAL: EIA-930 fuel code
    "OIL": "Oil",           # REAL: EIA-930 fuel code
    "OTH": "Other",         # REAL: EIA-930 fuel code
    "SUN": "Solar",         # REAL: EIA-930 fuel code
    "WAT": "Hydro",         # REAL: EIA-930 fuel code
    "WND": "Wind",          # REAL: EIA-930 fuel code
}

# --- Time
WINDOW_HOURS = 48                          # ILLUSTRATIVE (brief)
SLOT_MINUTES = 15                          # ILLUSTRATIVE (brief)
SLOTS_PER_HOUR = 4                         # ILLUSTRATIVE (brief)
STRESS_TOP_FRACTION = 0.05                 # ILLUSTRATIVE
LOOKAHEAD_SLOTS = 96                       # ILLUSTRATIVE (24 h)

# --- Facility
IT_PEAK_KW = 10_000                        # ILLUSTRATIVE
PUE = 1.3                                  # ILLUSTRATIVE
INFERENCE_SHARE = 0.35                     # ILLUSTRATIVE
INFERENCE_PEAK_HOUR_LOCAL = 18             # ILLUSTRATIVE (afternoon/evening peak)
INFERENCE_MIN_FRACTION = 0.60              # ILLUSTRATIVE (overnight floor as fraction of peak)
TRAINING_SHARE = 0.65                      # ILLUSTRATIVE

# --- Training jobs (ranges are inclusive)
JOB_POWER_KW = (50, 800)                   # ILLUSTRATIVE
JOB_POWER_STEP_KW = 10                     # ILLUSTRATIVE
JOB_DURATION_MIN = (60, 720)               # ILLUSTRATIVE
JOB_DURATION_STEP_MIN = 5                  # ILLUSTRATIVE
JOB_SLACK_MIN = (360, 1440)                # ILLUSTRATIVE
JOB_SLACK_STEP_MIN = 15                    # ILLUSTRATIVE
RESTART_PENALTY_MIN = 5                    # ILLUSTRATIVE
JOB_ID_BASE = 4001                         # ILLUSTRATIVE

# --- Storage
BATTERY = dict(power_kw=4000, capacity_kwh=16000, rte=0.90,
               soc_min_pct=10, soc_max_pct=95, soc_init_pct=60)   # ILLUSTRATIVE
FLYWHEEL = dict(power_kw=2000, capacity_kwh=500, rte=0.85,
                soc_min_pct=10, soc_max_pct=95, soc_init_pct=90)  # ILLUSTRATIVE
STORAGE_WEAR_USD_PER_KWH = 0.02            # ILLUSTRATIVE
CHARGE_RANK_FRACTION = 0.25                # ILLUSTRATIVE: charge only in cheapest/cleanest 25% of look-ahead
STRESS_DISCHARGE_KW = 2000                 # ILLUSTRATIVE: battery shave in forecast-stress slots

# --- Tariff (ILLUSTRATIVE, not Rocky Mountain Power's actual rates)
TARIFF_OFFPEAK_PER_KWH = 0.045             # ILLUSTRATIVE
TARIFF_ONPEAK_PER_KWH = 0.085              # ILLUSTRATIVE
ON_PEAK_HOURS_LOCAL = (15, 20)             # ILLUSTRATIVE, weekdays, [start, end)
DEMAND_CHARGE_PER_KW = 18.0                # ILLUSTRATIVE
CARBON_SHADOW_USD_PER_T = 50.0             # ILLUSTRATIVE

# --- Scheduler cost knobs
DEADLINE_RISK_USD_PER_KWH = 0.30           # ILLUSTRATIVE (tuned at checkpoint 10; was 0.10)
DEADLINE_COMFORT_MIN = 1440                # ILLUSTRATIVE (tuned at checkpoint 10; was 240)
DEFER_TARGET_STEP_SLOTS = 4                # ILLUSTRATIVE: candidate resume slots on the hour
PEAK_TARGET_SCALE = 1.10                   # ILLUSTRATIVE: multiplier on the initial planned peak P0 (tuned at checkpoint 10; was 1.0)

# --- Demand response
DR_SHED_KW = 4000                          # ILLUSTRATIVE
DR_DURATION_SLOTS = 8                      # ILLUSTRATIVE (2 h)
DR_BATTERY_RAMP_S = 20                     # ILLUSTRATIVE
DR_ZOOM_SECONDS = 600                      # ILLUSTRATIVE
DR_STORAGE_SUPPORT_KW = 1500               # ILLUSTRATIVE: storage share of the shed (<= flywheel power)
DR_RESERVE_MARGIN = 1.10                   # ILLUSTRATIVE
DR_FLYWHEEL_RESERVE_PCT = 50               # ILLUSTRATIVE: flywheel SOC floor while the DR event is ahead

# --- MOVE_REGION
MOVE_PENALTY_USD = 75.0                    # ILLUSTRATIVE
MOVE_DELAY_SLOTS = 2                       # ILLUSTRATIVE (30 min)
IDLE_CAPACITY_PCT = (0, 30)                # ILLUSTRATIVE (SIMULATED per region-hour)
ALT_REGION_PRICE_PER_KWH = 0.05            # ILLUSTRATIVE

# --- Forecasts (sim/forecast.py), using only hours already past
FORECAST_HISTORY_DAYS = 7                  # ILLUSTRATIVE: picked by backtest (DECISIONS.md)
FORECAST_RESIDUAL_DECAY = 0.95             # ILLUSTRATIVE: picked by backtest; share of the latest error kept per hour ahead
FORECAST_MIN_HISTORY_HOURS = 24            # ILLUSTRATIVE
FORECAST_MAX_LEAD_HOURS = 48               # ILLUSTRATIVE
FORECAST_BAND_HISTORY_DAYS = 28            # ILLUSTRATIVE: past errors a confidence band is built from
FORECAST_BAND_MIN_SAMPLES = 24             # ILLUSTRATIVE
FORECAST_BAND_QUANTILES = (0.10, 0.90)     # ILLUSTRATIVE

# --- Stress test (sim/stress.py). The draws are assumptions, not measurements of any real site.
STRESS_SAMPLES = 100                       # ILLUSTRATIVE
STRESS_RUNTIME_MEDIAN = 1.10               # ILLUSTRATIVE: typical true run time vs the estimate
STRESS_RUNTIME_SIGMA = 0.25                # ILLUSTRATIVE: lognormal spread of that ratio
STRESS_OUTAGE_HOURS = (2, 12)              # ILLUSTRATIVE: outage length, inclusive
REPLAY_START_HOUR_UTC = 6                  # ILLUSTRATIVE: midnight in Denver in summer
REPLAY_HISTORY_DAYS = 7                    # ILLUSTRATIVE: data kept back so forecasts have history

# --- What-if analyst: the inputs a question may change, (min, max) inclusive.
# A value outside these ranges is rejected before the engine runs.
WHATIF_BOUNDS = {  # ILLUSTRATIVE: wide enough for "what if" questions, narrow enough to stay physical
    "demand_charge_usd_per_kw": (0.0, 100.0),     # ILLUSTRATIVE
    "onpeak_usd_per_kwh": (0.0, 1.0),             # ILLUSTRATIVE
    "offpeak_usd_per_kwh": (0.0, 1.0),            # ILLUSTRATIVE
    "carbon_usd_per_t": (0.0, 500.0),             # ILLUSTRATIVE
    "battery_power_kw": (500.0, 20_000.0),        # ILLUSTRATIVE
    "battery_capacity_kwh": (1_000.0, 80_000.0),  # ILLUSTRATIVE
    "flywheel_power_kw": (500.0, 8_000.0),        # ILLUSTRATIVE
    "flywheel_capacity_kwh": (100.0, 4_000.0),    # ILLUSTRATIVE
    "dr_shed_kw": (500.0, 10_000.0),              # ILLUSTRATIVE
    "move_fee_usd": (0.0, 1_000.0),               # ILLUSTRATIVE
    "alt_region_usd_per_kwh": (0.0, 1.0),         # ILLUSTRATIVE
}

# --- What-if analyst: limits on the public endpoint (overridable per deploy)
ANALYST_QUESTION_MAX_CHARS = 400           # ILLUSTRATIVE
ANALYST_DAILY_QUESTION_CAP = 100           # ILLUSTRATIVE: hard stop on model spend, whole site, per UTC day
ANALYST_HOURLY_PER_VISITOR = 10            # ILLUSTRATIVE
ANALYST_ANSWER_MAX_CHARS = 1200            # ILLUSTRATIVE
ANALYST_MAX_TOKENS = 4000                  # ILLUSTRATIVE: per model call, includes any thinking
ANALYST_CALL_TIMEOUT_S = 25                # ILLUSTRATIVE: one model call
ANALYST_TIME_BUDGET_S = 50                 # ILLUSTRATIVE: whole question; CloudFront gives up at 60 s
ANALYST_RETRY_MIN_S_LEFT = 15              # ILLUSTRATIVE: skip the rewrite if less time than this remains

# --- Output rounding (decimal places)
ROUND = dict(kw=1, mw=1, carbon=1, price=4, pct=1, usd=2, t=3)  # ILLUSTRATIVE
