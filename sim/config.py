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
DEADLINE_RISK_USD_PER_KWH = 0.10           # ILLUSTRATIVE
DEADLINE_COMFORT_MIN = 240                 # ILLUSTRATIVE
DEFER_TARGET_STEP_SLOTS = 4                # ILLUSTRATIVE: candidate resume slots on the hour
PEAK_TARGET_SCALE = 1.0                    # ILLUSTRATIVE: multiplier on the initial planned peak P0 (tuning knob)

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

# --- Output rounding (decimal places)
ROUND = dict(kw=1, mw=1, carbon=1, price=4, pct=1, usd=2, t=3)  # ILLUSTRATIVE
