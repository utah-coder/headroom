# Implementation Plan: Headroom

## Overview

The plan follows the brief's Tuesday milestones and the design's build order. Each phase ends with a checkpoint:

1. Secret remediation and repo hygiene
2. Walking skeleton live on CloudFront (by 10:30 MT)
3. Real EIA data cached, carbon series and scenario windows
4. Simulation: jobs, site, storage, both policies, metrics, JSON contract, acceptance tests, then deploy
5. UI wired to the JSON, deployed after each increment
6. DR panel, zoom animation, accessibility and layout polish, then the final deploy before the 22:00 MT freeze

Python 3.11 (`sim/`, pytest + hypothesis) builds the data. TypeScript covers the web app (Vite + React + Recharts + Tailwind; vitest + fast-check are optional) and the infra (AWS CDK v2). PowerShell runs the scripts.

Some steps need the user's credentials or actions. They are marked **USER ACTION**: AWS profile, `cdk bootstrap`, `gh auth`, revoking and reissuing the EIA key, and putting the new key in `.env`. No task reproduces or prints an API key value.

## Tasks

- [x] 1. Secure the repository baseline
  - [x] 1.1 Remediate `.env.example`
    - Overwrite `.env.example` so its only non-empty line is `EIA_API_KEY=`, with nothing after the equals sign. Do not echo, copy or log the old contents.
    - Confirm `.env` is not tracked (`git ls-files .env` returns nothing), then commit the fix.
    - **USER ACTION:** revoke the exposed key and reissue it at eia.gov/opendata. Put the new key only in the local `.env` (`EIA_API_KEY=...`). If the old key exists in pushed history, tell the user that revoking it is the remedy.
    - _Requirements: 6.3, 6.6, 6.7_
  - [x] 1.2 Write `.gitignore`
    - Entries: `.env`, `node_modules/`, `cdk.out/`, `web/dist/`, `__pycache__/`, `.venv/`, `infra/cdk-outputs.json`, `*.json.tmp`
    - Verify with `git check-ignore -v` for `.env`, `infra/cdk.out/`, `web/node_modules/`, `web/dist/`, `sim/__pycache__/`, `.venv/`
    - _Requirements: 6.5_
  - [x] 1.3 Create `DECISIONS.md`
    - Entry template: date/time MT, decision, rationale, affected component
    - Pre-seed the judgment calls the design already makes:
      - extra modules `timeutil.py`, `grid.py`, `contract.py`
      - EIA hour-labelling interpretation
      - `GEO` mapped to Other
      - `plan_deadline` clamping and carry-over deadline accounting
      - "highest planned draw" read as `P`
      - `dr_delivered_pct` clamped, with the raw value kept in `meta.params`
      - stress slots fall in whole hours
      - `random.Random` chosen over numpy
      - per-property test files split for parallel work
    - Later tasks append only new judgment calls, each in the same commit as its change.
    - _Requirements: 25.4_

- [x] 2. Build the walking skeleton
  - [x] 2.1 Scaffold the placeholder web app in `web/`
    - Vite + React + TypeScript, with Tailwind v4 via `@tailwindcss/vite` and `recharts` installed now so later tasks add no dependencies
    - Pin exact versions and commit `package-lock.json`
    - `src/App.tsx` renders only "Headroom — coming online" on the dark theme, using the system font stack
    - `index.html` includes the static `<title>Headroom</title>`, a meta description of 160 characters or fewer, `og:title`, `og:description`, `og:type=website` and the viewport tag, exactly as in the design. No "Torus" anywhere.
    - No external fonts, scripts or analytics
    - _Requirements: 5.1, 17.4, 22.6, 22.11_
  - [x] 2.2 Add stub scenario files
    - Write minimal valid JSON objects at `web/public/scenarios/peak.json` and `sun.json` (for example `{"meta":{"scenario":"peak"}}`), so the smoke check and `deploy.ps1 -SkipSim` work before the simulation exists. Task 9.6 replaces them.
    - _Requirements: 2.4, 2.5, 3.2_
  - [x] 2.3 Implement the CDK stack in `infra/`
    - `package.json` with exact pins and lockfile, `tsconfig.json`, and `cdk.json` with context `"headroom:region": "us-west-2"`
    - `bin/headroom.ts` creates `HeadroomSiteStack` with `env.account` from `CDK_DEFAULT_ACCOUNT` (undefined in CI) and tags the app `Project=headroom`
    - `lib/site-stack.ts`, as in the design:
      - private bucket: BLOCK_ALL, enforceSSL, S3_MANAGED, DESTROY, autoDeleteObjects
      - Distribution with the `S3BucketOrigin.withOriginAccessControl` origin and every listed behaviour setting
      - 403 and 404 error responses map to `/index.html` with status 200 and TTL 0
      - `BucketDeployment` from `../web/dist` with `distribution` and `distributionPaths: ['/*']`
      - outputs `SiteUrl`, `BucketName`, `BucketRegionalDomainName`
    - No custom domain. If an installed API name differs, adapt it and log the change in `DECISIONS.md`.
    - Verify that `npx cdk synth` exits 0 without credentials after `npm run build` in `web/`
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 1.10, 1.11, 1.13, 1.14, 1.15, 24.1_
  - [ ]* 2.4 Write CDK assertion tests in `infra/test/site-stack.test.ts`
    - Assert:
      - OAC origin present
      - no bucket policy statement with a public principal
      - 403/404 error responses go to `/index.html` with status 200
      - `Project=headroom` tags present
      - no Lambda functions other than the CDK-managed custom-resource handlers
    - Add a jest config and test script to `infra/package.json`
    - _Requirements: 1.3, 1.4, 1.6, 1.10, 1.15_
  - [x] 2.5 Implement `scripts/smoke.ps1`
    - Parameters `-SiteUrl` and `-BucketDomain`. A missing `SiteUrl` prints an error, sends no requests and exits 2 (no Mandatory prompt).
    - If `-BucketDomain` is omitted, read it from `aws cloudformation describe-stacks --stack-name HeadroomSiteStack`
    - Use `System.Net.Http.HttpClient` with a 15-second timeout so behaviour is the same on PowerShell 5.1 and 7
    - Checks:
      - `GET /` returns 200
      - `GET /scenarios/peak.json` returns 200 and the full body parses with `ConvertFrom-Json`
      - the direct S3 object URL for `index.html` returns 403
    - Always run all three. Print `name | status | PASS/FAIL` per check, recording `TIMEOUT` or `NETWORK_ERROR` as the status where they occur.
    - Exit 1 on any failure. Otherwise print "3 of 3 checks passed" and exit 0.
    - _Requirements: 1.13, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8_
  - [x] 2.6 Implement `scripts/deploy.ps1`
    - `param([switch]$SkipSim)` and `$ErrorActionPreference = 'Stop'`. An `Invoke-Step` wrapper checks `$LASTEXITCODE` and names the failing step.
    - Step order: sim (or the `-SkipSim` existence check, which names each missing file and reads the scenario list matching `web/src/config.ts` SCENARIOS), `npm ci` and `npm run build` in `web/`, Secret_Grep, `npx cdk deploy --require-approval never --outputs-file cdk-outputs.json`, print `SiteUrl`, then smoke.
    - `Get-EnvKey` parses `.env`. If the file is missing or the value is empty, fail with "Secret grep cannot run: EIA_API_KEY unavailable".
    - Secret_Grep is an ordinal, case-sensitive `Contains` over every file under `web/dist`. Print matching paths only. The key stays in a local variable and is never echoed or passed to child processes.
    - After deploy, run the same grep over `infra/cdk.out` as a defence-in-depth check
    - Exit 0 only when smoke exits 0
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 2.12, 6.1, 6.3, 6.10_
  - [x] 2.7 Create the Python package skeleton and the no-network guard
    - `sim/__init__.py`
    - `sim/requirements.txt` with exact pins for `numpy`, `tzdata`, `pytest`, `hypothesis`
    - `sim/tests/__init__.py`
    - `sim/tests/conftest.py`:
      - autouse fixture patching `socket.socket.connect`, `socket.create_connection` and `socket.getaddrinfo` to raise `NetworkBlockedError`
      - Hypothesis `ci` profile (`max_examples=100`, `deadline=None`, `derandomize=True`) selected by default, with `HYPOTHESIS_PROFILE` to override
    - `sim/tests/test_guard.py` asserts that a socket connection attempt raises `NetworkBlockedError`
    - _Requirements: 23.10_
  - [x] 2.8 Write `.github/workflows/ci.yml`
    - Trigger on push to all branches, with `permissions: contents: read`
    - Steps: checkout, setup-python 3.11, setup-node 20 with npm cache, `pytest` (named step), `web` `npm ci && npm run build`, `infra` `npm ci && npx cdk synth --quiet`
    - No AWS credentials, no secrets, no `EIA_API_KEY`, no deploy or bootstrap. Task 9.6 adds the offline sim step.
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 6.11_
  - [x] 2.9 Write `README.md`
    - The title, then the live Site_URL link as the first content. Use a clearly marked placeholder until checkpoint 3 fills it.
    - PowerShell setup:
      - prerequisite checks: `git --version`, `gh auth status`, `node -v`, `python --version`, `$env:AWS_PROFILE=...` then `aws sts get-caller-identity`, and a check that `.env` contains `EIA_API_KEY`
      - `npx cdk bootstrap aws://<account>/us-west-2`
      - `.\scripts\deploy.ps1`
    - Real-vs-simulated table with one row each for PACE grid data, BPAT/CISO grid data, Emission_Factor_Table, Site_Model, Training_Jobs, Battery and Flywheel, Idle_Capacity and Tariff. Each row has exactly one label, and REAL rows name their source.
    - Duke_Context_Line with attribution, presented as national planning context
    - Every CO₂ mention says "average intensity, not marginal"
    - _Requirements: 5.3, 15.9, 24.3, 25.1, 25.2, 25.3, 25.8_

- [x] 3. Checkpoint: walking skeleton live (due 10:30 MT)
  - **USER ACTION** before running anything:
    - `gh auth status` passes
    - `$env:AWS_PROFILE` is set and `aws sts get-caller-identity` succeeds
    - `npx cdk bootstrap aws://<account>/us-west-2` has run once
    - `.env` holds the reissued key
    - If any prerequisite check fails, stop and report it by name with its output (Req 25.5).
  - Create or push the GitHub repo (`gh repo create headroom --public --source . --remote origin --push` if it does not exist yet)
  - Run `.\scripts\deploy.ps1 -SkipSim` and confirm smoke shows 3 of 3 passed
  - Replace the README placeholder with the printed `SiteUrl`, commit and push to `main`, and confirm CI is green for that commit
  - Print the status: what works, what is broken, what is next, live URL
  - Ensure all tests pass, ask the user if questions arise.
  - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 25.5, 25.6, 25.9_

- [x] 4. Config, time helpers and EIA fetcher
  - [x] 4.1 Implement `sim/config.py`
    - Every constant from the design's config listing, each with a trailing `# REAL: <source>` or `# ILLUSTRATIVE` comment. This includes the emission factors labelled per Requirement 8.8, the cut flags `ENABLE_SUN_SCENARIO` and `ENABLE_MOVE_REGION`, storage specs, tariff, DR, MOVE_REGION and rounding.
    - _Requirements: 8.8, 10.1, 10.2, 10.16, 10.17, 10.18, 12.2, 14.1_
  - [x] 4.2 Implement `sim/timeutil.py`
    - `parse_eia_period`, `iso_z`, `slot_times`, `denver` (ZoneInfo through `tzdata`), `hhmm_denver`, `hour_floor`
    - _Requirements: 7.10, 7.11, 13.3_
  - [ ]* 4.3 Write property test for EIA period parsing in `sim/tests/test_period.py`
    - **Property 5: EIA period parsing round trip**
    - **Validates: Requirements 7.10**
  - [ ]* 4.4 Write property test for Python Denver labels in `sim/tests/test_denver_time.py`
    - **Property 41: Denver time labels** (Python half: `hhmm_denver` equals the UTC−6 HH:MM)
    - **Validates: Requirements 7.11, 13.3**
  - [x] 4.5 Implement `sim/fetch_eia.py`
    - `RequestSpec`, `base_params` (never includes `api_key`), `cache_path` (naming as in the design), `read_api_key`
      - Precedence: environment variable first, then the repo-root `.env`. Blank in both raises `MissingKeyError` (exit 2), and the message names `EIA_API_KEY` only.
      - The key is read lazily, on the first cache miss only.
    - Injectable transport, defaulting to stdlib `urllib` with a 30-second timeout. `api_key` is appended only inside `_send()`.
    - Pagination: offsets step by 5,000 until a page has fewer than 5,000 rows
    - Cache-first `fetch_page`. On write, strip `request.params.api_key`, assert the key is not in the serialized text, write a temp file, then `os.replace`. Rows are never altered.
    - Errors raise `EiaFetchError` carrying the endpoint, key-free params and error text, write no cache file, and exit non-zero
    - Offline/test mode: a missing cache file raises `CacheMissingError(path)` instead of fetching
    - `load_all()` covers PACE region-data (D, DF, NG, TI) and fuel-type-data for PACE, BPAT and CISO over the Data_Range. `main()` runs as `python -m sim.fetch_eia`.
    - _Requirements: 6.1, 6.2, 6.8, 6.9, 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.8, 7.9, 7.14, 7.15, 23.11_
  - [ ]* 4.6 Write property test for pagination in `sim/tests/test_fetch_paging.py`
    - **Property 1: Pagination fetches every row exactly once**
    - **Validates: Requirements 7.4**
  - [ ]* 4.7 Write property test for the cache in `sim/tests/test_fetch_cache.py`
    - **Property 2: Raw cache round trip and cache-first loading**
    - **Validates: Requirements 7.5, 7.6**
  - [ ]* 4.8 Write property test for key resolution in `sim/tests/test_fetch_key.py`
    - **Property 3: API key resolution and missing-key rejection** (uses `monkeypatch` and `tmp_path`, never the real `.env`)
    - **Validates: Requirements 6.2, 6.8, 7.14**
  - [ ]* 4.9 Write property test for key leakage in `sim/tests/test_fetch_leak.py`
    - **Property 4: The API key never leaks** (random dummy keys only)
    - **Validates: Requirements 6.9, 7.8, 7.15**
  - [ ]* 4.10 Write fetcher unit tests in `sim/tests/test_fetch_eia.py`
    - `base_params` for both endpoints equals the expected lists, bounded by the Data_Range
    - Fake-transport error paths (HTTP 500, error body, connection error) exit non-zero and leave no cache file
    - `CacheMissingError` names the missing path
    - _Requirements: 7.1, 7.2, 7.3, 7.8, 23.11_

- [x] 5. Grid series, carbon intensity and scenario windows
  - [x] 5.1 Implement the hourly helpers in `sim/grid.py`
    - `HourlySeries`
    - `build_hourly(raw)`: PACE D, DF, NG and TI; per-region per-fuel generation and totals; `value` coerced from str/None
    - `fill_gaps` (interior gaps by linear interpolation, edges by the nearest present value, returns the count, raises `NoDataError` when all values are missing)
    - `expand_to_slots`
    - _Requirements: 7.12, 7.13, 7.16, 9.7_
  - [ ]* 5.2 Write property test for slot expansion in `sim/tests/test_grid_expand.py`
    - **Property 6: Hourly-to-slot expansion preserves values**
    - **Validates: Requirements 7.12**
  - [ ]* 5.3 Write property test for gap filling in `sim/tests/test_grid_fill.py`
    - **Property 7: Gap filling**
    - **Validates: Requirements 7.13, 7.16, 8.6**
  - [x] 5.4 Implement `sim/carbon.py`
    - `map_fuel` (Known_Fuel_Codes from `FUEL_CODE_MAP`, everything else maps to Other)
    - `intensity_hour` (negative or missing values count as 0; returns None when the total is 0)
    - `hourly_intensity`: fills zero-total hours through `fill_gaps`, logs each filled hour and each distinct unknown (code, respondent) pair with its row count, and raises `NoFuelMixError` when every hour is zero
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7_
  - [ ]* 5.5 Write property test for weighted intensity in `sim/tests/test_carbon_weighted.py`
    - **Property 8: Carbon intensity equals the generation-weighted average**
    - **Validates: Requirements 8.3, 8.4**
  - [ ]* 5.6 Write property test for intensity bounds in `sim/tests/test_carbon_bounds.py`
    - **Property 9: Carbon intensity is bounded by contributing fuels**
    - **Validates: Requirements 8.5**
  - [ ]* 5.7 Write property test for unknown fuel codes in `sim/tests/test_carbon_unknown.py`
    - **Property 10: Unknown fuel codes map to Other with exact counts**
    - **Validates: Requirements 8.2**
  - [x] 5.8 Add window, stress and DR-start selection to `sim/grid.py`
    - `select_peak_window` (peak hour at position 25, minimum shift to stay inside the Data_Range, earliest on ties)
    - `select_sun_window` (prefix sums, negative SUN treated as 0, hours missing either value skipped, zero-total candidates excluded, earliest on ties)
    - `stress_flags` (top `ceil(0.05 N)` by threshold)
    - `select_dr_start` (earliest maximum among `i + 8 ≤ N`)
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.6, 14.2, 15.1_
  - [ ]* 5.9 Write property test for the peak window in `sim/tests/test_grid_peak_window.py`
    - **Property 11: Peak window placement**
    - **Validates: Requirements 9.1, 9.2, 9.4**
  - [ ]* 5.10 Write property test for the sun window in `sim/tests/test_grid_sun_window.py`
    - **Property 12: Sun window matches brute force**
    - **Validates: Requirements 9.3, 9.4, 9.6**
  - [ ]* 5.11 Write property test for stress slots and DR start in `sim/tests/test_grid_stress_dr.py`
    - **Property 13: Stress slots and DR start match reference selection**
    - **Validates: Requirements 14.2, 15.1, 16.5**
  - [x] 5.12 Create `sim/site.py` with the tariff
    - `tariff_per_kwh(t)` and `is_on_peak(t)`: weekdays 15:00–20:00 America/Denver cost 0.085, all other slots 0.045, both read from config
    - _Requirements: 10.16_
  - [ ]* 5.13 Write property test for the tariff in `sim/tests/test_site_tariff.py`
    - **Property 15: Tariff by Denver local time**
    - **Validates: Requirements 10.16**
  - [x] 5.14 Add `GridSlot`, `build_slots` and the sanity output to `sim/grid.py`
    - `build_slots(window_start, hourly, carbon)`: gap-fills the window's hourly series, counts `filled_hours` per series, expands to 192 slots, and attaches PACE and alternate-region carbon, tariff price, `on_peak` and `stress`
    - `write_processed()` writes `data/processed/pace_hourly.json` and `carbon_hourly.json`
    - `python -m sim.grid` prints both selected windows, the peak hour, solar share and filled-hour counts as the sanity check
    - _Requirements: 7.12, 7.13, 7.16, 9.5, 9.7, 16.5_

- [x] 6. Checkpoint: real data cached (due 12:30 MT)
  - **USER ACTION:** with the reissued key in `.env`, run `python -m sim.fetch_eia` once (about 18 requests)
  - Confirm the key value is absent from `data/raw` with a `Select-String -SimpleMatch` that prints file names only, never the key. Then commit `data/raw/`.
  - Run `python -m sim.grid` and sanity-check the windows and carbon ranges. If an endpoint or parameter needed fixing, log it in `DECISIONS.md`.
  - Commit, push and print the milestone status.
  - Ensure all tests pass, ask the user if questions arise.
  - _Requirements: 6.3, 7.7, 7.9, 25.6_

- [x] 7. Jobs and site model
  - [x] 7.1 Implement `sim/jobs.py`
    - `TrainingJob` with a `deadline_min` property
    - `rng_for(seed, scenario, purpose)` seeded from sha256
    - `generate(n_slots, seed, scenario)`:
      - Poisson arrivals with rate λ derived from config (expected training load 6,500 kW IT), floored to the slot start
      - power, duration and slack drawn on stepped grids with inclusive bounds
      - ids counting up from `JOB_ID_BASE`
    - _Requirements: 10.4, 10.6, 10.7, 10.8, 10.19, 24.4_
  - [ ]* 7.2 Write property test for job bounds in `sim/tests/test_jobs_bounds.py`
    - **Property 16: Training job bounds**
    - **Validates: Requirements 10.6, 10.7, 10.8**
  - [x] 7.3 Add the inference profile, storage and idle capacity to `sim/site.py`
    - `inference_it_kw` (cosine profile peaking at 3,500 kW at 18:00 Denver, floor 60%)
    - `StorageSpec` and `StorageState`:
      - `eta = sqrt(rte)`
      - `max_discharge_kw` and `max_charge_kw` with floor and ceiling arguments
      - `step`, which asserts exclusivity and bounds
    - `idle_capacity_kw` (integer 0–30% of IT_Peak per region per hour, seeded through `rng_for`)
    - _Requirements: 10.2, 10.3, 10.4, 10.5, 10.11, 10.12, 10.13, 10.14, 12.4_
  - [ ]* 7.4 Write property test for the inference shape in `sim/tests/test_site_inference.py`
    - **Property 14: Inference profile shape**
    - **Validates: Requirements 10.4, 10.5**
  - [ ]* 7.5 Write property test for storage physics in `sim/tests/test_site_storage.py`
    - **Property 17: Storage round-trip efficiency and limits**
    - **Validates: Requirements 10.11, 10.12**
  - [x] 7.6 Add `build_event_zoom` and `DrInfeasibleError` to `sim/site.py`
    - Integer deci-kW arithmetic
    - With `R = max(0, L − cap)`: the battery ramps linearly to R over 20 s, the flywheel supplies `R − battery`, and grid is `L − R`, over 600 entries
    - Raise `DrInfeasibleError(second)` if R exceeds the flywheel or battery rating, or if per-second SOC leaves [10%, 95%]
    - _Requirements: 10.13, 10.14, 10.15, 14.5, 14.6, 14.7, 14.8, 14.11_
  - [ ]* 7.7 Write property test for the event zoom in `sim/tests/test_site_zoom.py`
    - **Property 33: Event zoom shape and feasibility**
    - **Validates: Requirements 14.5, 14.6, 14.7, 14.8, 14.10, 14.11, 23.5**
  - [ ]* 7.8 Write real-data window tests in `sim/tests/test_windows_real.py`
    - From the committed Raw_Cache:
      - the peak hour is hour 25 of `peak` (or the shift follows 9.2)
      - `sun` equals the brute-force best window
      - both windows are 48 h inside the Data_Range
    - _Requirements: 9.1, 9.2, 9.3, 9.6_

- [x] 8. Scheduler: baseline, reasons, headroom
  - [x] 8.1 Implement scheduler models, job mechanics and the baseline policy in `sim/scheduler.py`
    - Data models from the design: `ScenarioInputs`, `JobState`, `StorageFlows`, `SlotResult`, `Decision`, `JobOutcome`, `MoveRecord`, `PolicyResult`
    - Shared mechanics: per-slot `u = min(15, remaining)`, facility kW = IT × PUE, delivered IT kWh, the resume penalty (+5 min, `resumes += 1`), pause and resume only at slot boundaries, deadline accounting with carry-over
    - `run(policy, inp)` dispatches to `run_baseline`, which runs each job continuously from arrival, keeps storage at zero and gives one `GRID_NOW` decision per job at its arrival slot
    - _Requirements: 10.3, 10.9, 10.10, 10.20, 11.1, 11.2, 13.2_
  - [x] 8.2 Add reason-string helpers to `sim/scheduler.py`
    - `ordinal`, `demand_percentile`, `reason_string`:
      - Denver HH:MM, `job#id`, route, optional target, ordinal percentile, integer g/kWh, non-negative integer peak delta
      - compact MOVE form with region, $75, +30 min and the target region's g/kWh
      - DR suffix `— DR event, cap N kW`
      - `[LATE +N min]` suffix
      - 200 characters or fewer
    - _Requirements: 12.8, 13.3, 13.4, 13.5, 13.6, 13.8, 13.9_
  - [ ]* 8.3 Build Hypothesis strategies in `sim/tests/strategies.py`
    - `fuel_mix`, `optional_series`, `hourly_demand`, `scenario_inputs(n_slots 24–192, with_dr, move_enabled)`, `decision_context`, `scenario_doc`
    - _Requirements: 23.10_
  - [ ]* 8.4 Write property test for the baseline policy in `sim/tests/test_sched_baseline.py`
    - **Property 23: Baseline runs on arrival without storage**
    - **Validates: Requirements 11.2, 13.2**
  - [ ]* 8.5 Write property test for reason strings in `sim/tests/test_reasons.py`
    - **Property 32: Reason strings are well-formed and consistent**
    - **Validates: Requirements 12.8, 13.3, 13.4, 13.5, 13.6, 13.8, 13.9**
  - [x] 8.6 Implement headroom routing in `sim/scheduler.py`
    - Per slot:
      - look-ahead `H` of up to 96 slots, truncated at N
      - forecast stress from DF only; `D` is never read for future slots
      - `unit_cost` = price + carbon × shadow price
      - `planned` array; `P0` and `P`
      - `plan_deadline = min(deadline, N×15)`
    - Route costs: `GRID_NOW`, `STORAGE_NOW`, `DEFER(t*)` with on-the-hour targets outside DR and forecast-stress slots, and the deadline penalty
    - Candidate filtering per the design table, including the `STORAGE_NOW` availability check
    - Deadline-risk fallback to `GRID_NOW` (`cause=DEADLINE`)
    - Lowest-cost choice with costs rounded to 1e-9, ties broken by `ROUTE_ORDER`
    - Processing order: at-risk jobs first, then earliest `plan_deadline`, then id
    - Reservations released and re-added per slot
    - Decisions emitted only when the route changes; late jobs flagged at the end
    - Optional `trace=True` output of candidates, costs, pre-storage load and `P`
    - _Requirements: 11.3, 11.4, 11.5, 11.6, 11.7, 11.8, 11.9, 11.10, 11.11, 11.12, 11.16, 11.17, 13.1, 13.7_
  - [x] 8.7 Implement storage dispatch and DR handling in `sim/scheduler.py`
    - Priority order:
      1. DR slot: the battery holds R, the flywheel covers the start transient, no charging
      2. committed `STORAGE_NOW`: battery first, then flywheel
      3. peak shave above `P`
      4. forecast-stress shave of up to `STRESS_DISCHARGE_KW`
      5. charging only in non-DR, non-stress slots within the cheapest `CHARGE_RANK_FRACTION` of `H`, with `grid + charge ≤ P`; update `storage_cost_basis`
    - SOC floors: a DR reserve floor (about 32% battery, 50% flywheel) while the event is still ahead; ceiling 95%
    - DR routing budget: `GRID_NOW` in DR slots is offered only while `load + kW ≤ cap + DR_STORAGE_SUPPORT_KW`, and `STORAGE_NOW` is not offered in DR slots
    - Raise `DrInfeasibleError(slot)` when the DR load exceeds the storage limit. Assert grid ≥ 0, exclusivity and SOC bounds per slot.
    - _Requirements: 10.13, 10.14, 10.15, 11.13, 11.14, 11.15, 14.4, 14.11_
  - [x] 8.8 Implement `MOVE_REGION` behind `ENABLE_MOVE_REGION` in `sim/scheduler.py`
    - Offered to training jobs only, targets BPAT or CISO
    - Cost: $75 penalty once + alternate-region price + alternate carbon × shadow price + deadline penalty
    - The job starts 2 slots later. Reject the move if it would miss `plan_deadline` or exceed idle capacity in any hour (`alt_used` ledger), in which case the next-cheapest route is chosen.
    - At most one move per job. `moved_kw` is recorded and excluded from `grid_kw`. `MoveRecord` keeps kWh by slot.
    - When the flag is off, the route is never offered
    - _Requirements: 12.1, 12.2, 12.3, 12.5, 12.6, 12.7, 12.8, 12.9, 24.8_
  - [ ]* 8.9 Write property test for power balance in `sim/tests/test_sched_power_balance.py`
    - **Property 18: Power balance**
    - **Validates: Requirements 10.3, 10.15, 12.6**
  - [ ]* 8.10 Write property test for inference in `sim/tests/test_sched_inference.py`
    - **Property 19: Inference is never touched**
    - **Validates: Requirements 11.10, 15.5, 23.1**
  - [ ]* 8.11 Write property test for SOC in `sim/tests/test_sched_soc.py`
    - **Property 20: State of charge bounds and exclusivity**
    - **Validates: Requirements 10.13, 10.14, 23.2**
  - [ ]* 8.12 Write property test for energy in `sim/tests/test_sched_energy.py`
    - **Property 21: Training energy conservation**
    - **Validates: Requirements 10.10, 23.3**
  - [ ]* 8.13 Write property test for deadlines in `sim/tests/test_sched_deadlines.py`
    - **Property 22: Deadline accounting**
    - **Validates: Requirements 10.20, 11.11, 11.12, 15.4, 23.4**
  - [ ]* 8.14 Write property test for forecast-only planning in `sim/tests/test_sched_no_actual_demand.py`
    - **Property 24: Planning ignores actual demand** (includes the future-`NaN` demand array case)
    - **Validates: Requirements 11.4**
  - [ ]* 8.15 Write property test for route selection in `sim/tests/test_sched_argmin.py`
    - **Property 25: Route choice is the filtered argmin**
    - **Validates: Requirements 11.5, 11.7, 11.8, 11.9, 11.16**
  - [ ]* 8.16 Write property test for DEFER and MOVE targets in `sim/tests/test_sched_targets.py`
    - **Property 26: DEFER and MOVE targets are feasible**
    - **Validates: Requirements 11.6, 12.3, 12.7**
  - [ ]* 8.17 Write property test for charging in `sim/tests/test_sched_charging.py`
    - **Property 27: Charging only in cheap, clean, non-DR slots**
    - **Validates: Requirements 11.13**
  - [ ]* 8.18 Write property test for peak shaving in `sim/tests/test_sched_peak_shave.py`
    - **Property 28: Peak shaving**
    - **Validates: Requirements 11.14**
  - [ ]* 8.19 Write property test for the DR cap in `sim/tests/test_sched_dr_cap.py`
    - **Property 29: Demand-response cap holds**
    - **Validates: Requirements 11.15, 14.3, 14.4**
  - [ ]* 8.20 Write property test for MOVE_REGION in `sim/tests/test_sched_move.py`
    - **Property 30: MOVE_REGION validity** (includes the flag-off case with zero MOVE decisions)
    - **Validates: Requirements 12.1, 12.2, 12.4, 12.5, 12.9, 24.8**
  - [ ]* 8.21 Write property test for decision emission in `sim/tests/test_sched_decisions.py`
    - **Property 31: Decision emission rule**
    - **Validates: Requirements 13.1, 13.2, 13.7**
  - [ ]* 8.22 Write the tie-break unit test in `sim/tests/test_sched_ties.py`
    - Forced equal route costs pick `GRID_NOW`, then `STORAGE_NOW`, then `DEFER`, then `MOVE_REGION`
    - _Requirements: 11.8_

- [x] 9. Metrics, output contract and scenario builder
  - [x] 9.1 Implement `sim/metrics.py`
    - `compute(inp, base, head, dr)`:
      - `headroom_kw` over stress slots
      - `usd_saved` (energy + demand charge per policy + $75 per successful move)
      - `co2_avoided_t` (PACE carbon on grid energy + alternate-region carbon on moved energy, ÷ 1e6)
      - `deadlines_met_pct`, `inference_sla_pct`
      - raw and clamped `dr_delivered_pct`, `dr_seconds_to_full`
    - Negative values are reported unchanged
    - _Requirements: 10.17, 12.6, 14.9, 14.10, 15.1, 15.2, 15.3, 15.4, 15.5, 15.7_
  - [ ]* 9.2 Write property test for metrics in `sim/tests/test_metrics.py`
    - **Property 34: Metrics match reference formulas**
    - **Validates: Requirements 14.9, 15.1, 15.2, 15.3, 15.7**
  - [x] 9.3 Implement `sim/contract.py`
    - `q()` rounding (`-0.0 → 0.0`, finite assert)
    - `build_doc` in contract key order, with `meta.params` holding `filled_hours`, `peak_target_kw`, `job_count`, `enable_move_region`, `dr_delivered_raw_pct`, `stress_threshold_mw`, `units`
    - `validate(doc)` checks every rule of 16.2–16.11 and returns a list of errors
    - `dumps` (`ensure_ascii=False`, `allow_nan=False`, compact separators, trailing newline)
    - `write_atomic` writes `.json.tmp`, then `os.replace`, and removes the temp file on failure
    - _Requirements: 9.5, 16.2, 16.3, 16.4, 16.5, 16.6, 16.7, 16.8, 16.9, 16.10, 16.11, 16.13, 16.14, 16.15_
  - [ ]* 9.4 Write property test for contract validation in `sim/tests/test_contract_validate.py`
    - **Property 35: Output contract validity** (valid docs pass, and every single mutation fails)
    - **Validates: Requirements 9.5, 16.2, 16.3, 16.4, 16.5, 16.6, 16.7, 16.8, 16.9, 16.10, 16.11**
  - [ ]* 9.5 Write property test for serialization in `sim/tests/test_contract_roundtrip.py`
    - **Property 36: Serialization round trip**
    - **Validates: Requirements 16.14**
  - [x] 9.6 Implement `sim/run_scenarios.py` and add the sim step to CI
    - `build_scenario(name, cache, seed)`: window, slots, jobs, inference, idle capacity, baseline, DR start and `cap_kw` from the baseline draw, headroom, zoom, metrics, doc, validate
    - `main()`:
      - build and validate every enabled scenario in memory
      - exit non-zero naming the scenario if `inference_sla_pct < 100` or validation fails, leaving existing files unchanged
      - otherwise write all files atomically
      - with `ENABLE_SUN_SCENARIO = False`, write only `peak.json` and remove any stale `sun.json`
      - write `data/processed/*` through `grid.write_processed()`
    - This replaces the stub scenario JSON from task 2.2 with real output; commit the generated files
    - Add the step `python -m sim.run_scenarios` (offline, from the committed cache) to `.github/workflows/ci.yml` before the web build
    - _Requirements: 4.1, 4.3, 7.7, 14.1, 14.2, 14.3, 15.6, 15.10, 16.1, 16.15, 24.7_
  - [ ]* 9.7 Write property test for determinism in `sim/tests/test_determinism.py`
    - **Property 37: Determinism**
    - **Validates: Requirements 10.19, 12.4, 16.13**
  - [x] 9.8 Write the acceptance tests in `sim/tests/test_acceptance.py` (required)
    - Session-scoped fixture builds `peak` and `sun` from the committed Raw_Cache, offline, using the no-network guard
    - Tests parametrized by scenario name, so a failure names its scenario:
      - 192 aligned `baseline`/`headroom` entries, and `inference_kw` identical in every slot
      - battery and flywheel SOC within [10%, 95%], with no simultaneous charge and discharge (from `PolicyResult`)
      - every completed job's delivered energy within 0.01 kWh of requested energy + restart penalty energy, under both policies
      - late flags consistent with `deadlines_met_pct`
      - 600 zoom entries with `s` = 0..599 and `grid_kw ≤ cap_kw` in each
      - **stress-hour peak**: at least one stress slot, and the headroom maximum is strictly below the baseline maximum
      - two builds byte-identical with `built_at` masked
      - full Output_Contract conformance of both files through `contract.validate`
      - `inference_sla_pct == 100`
      - a missing Raw_Cache file fails with the file named, without fetching
    - _Requirements: 15.6, 23.1, 23.2, 23.3, 23.4, 23.5, 23.6, 23.8, 23.9, 23.10, 23.11, 23.12_
  - [ ]* 9.9 Write builder failure-path tests in `sim/tests/test_run_scenarios.py`
    - An injected SLA below 100 and an injected invalid doc each exit non-zero, leave existing files byte-unchanged and leave no `.tmp`
    - `ENABLE_SUN_SCENARIO = False` writes only `peak.json`
    - _Requirements: 15.10, 16.15, 24.7_
  - [ ]* 9.10 Write repo hygiene scans in `sim/tests/test_repo_hygiene.py`
    - Every assignment in `config.py` carries a REAL or ILLUSTRATIVE comment, and the emission factors are labelled per 8.8
    - Key literals (for example `16000`, `0.085`, `4000`) do not appear in other `sim/*.py` files
    - No `EIA_API_KEY` reads under `web/` or `infra/`
    - No `VITE_` key variable
    - `.env.example` is exactly `EIA_API_KEY=`
    - `.gitignore` covers the six paths
    - _Requirements: 6.1, 6.4, 6.5, 6.6, 8.8, 10.1_

- [x] 10. Checkpoint: simulation green and deployed (due 15:00 MT)
  - Run `pytest -q sim/tests`. The stress-hour peak test (Req 23.6) must pass for both scenarios before any UI work (Req 23.7).
  - If the stress-hour peak test fails, tune the `config.py` knobs in this order: `P0`, `STRESS_DISCHARGE_KW`, `DR_STORAGE_SUPPORT_KW`. Only if tuning fails, build the `pulp` LP fallback and log the reason in `DECISIONS.md` (Req 11.18).
  - Run `.\scripts\deploy.ps1` (full sim, needs the AWS profile) and confirm smoke shows 3 of 3. Commit, push, confirm CI is green and print the milestone status.
  - Ensure all tests pass, ask the user if questions arise.
  - _Requirements: 11.18, 23.6, 23.7, 25.6, 25.9_

- [ ] 11. Web data layer and helpers
  - [x] 11.1 Write `web/src/types.ts` and `web/src/config.ts`
    - `types.ts` follows the design's interfaces exactly
    - `config.ts`:
      - `SCENARIOS` (`['peak','sun']`, or `['peak']` when the sun scenario is cut)
      - `ANIMATED_ZOOM`
      - `REPO_URL`
    - _Requirements: 16.12, 22.4, 24.7, 24.9_
  - [ ]* 11.2 Write the contract mirror test in `sim/tests/test_types_mirror.py`
    - Parse the `export interface` blocks in `types.ts` and compare their field names with the key sets enforced by `contract.validate`
    - _Requirements: 16.12_
  - [x] 11.3 Implement `web/src/data/loadScenarios.ts`
    - A module-level promise ensures one fetch per file, even under StrictMode
    - URLs are same-origin: `${import.meta.env.BASE_URL}scenarios/<file>`
    - A non-OK response or network error reports `load`. A `JSON.parse` failure or a failed light shape check (top-level keys, 192 grid entries) reports `parse`.
    - No retries
    - _Requirements: 17.1, 17.2, 17.3, 24.2_
  - [x] 11.4 Implement the reducer in `web/src/state/appState.ts`
    - Initial state: `{scenario:'peak', mode:'baseline', dr:'idle', drRun:0}`
    - `setScenario` and `setMode` reset DR to idle
    - `fireDr` is ignored in Baseline mode or while playing. With `instant` it goes straight to `done`; otherwise it sets `playing` and increments `drRun`.
    - `drComplete`
    - _Requirements: 18.2, 18.3, 18.4, 18.9, 20.2, 20.9, 20.10_
  - [x] 11.5 Implement `web/src/lib/format.ts`, `lib/time.ts` and `lib/colors.ts`
    - Formatters use a fixed `en-US` locale: `formatKw`, `formatUsd`, `formatTonnes`, `formatPct` (floor), never producing `-0`
    - `hhmmDenver` uses `Intl.DateTimeFormat` with America/Denver and h23
    - Palette (slate shades plus the four route colours) and a `contrastRatio` helper
    - _Requirements: 7.11, 18.10, 21.2, 21.3, 22.7_
  - [ ]* 11.6 Set up vitest and fast-check, and write loader tests in `web/src/data/loadScenarios.test.ts`
    - Add devDependencies and a `test` script (`vitest --run`)
    - Calling the loader twice issues exactly two fetches
    - Load and parse failures name their file
    - _Requirements: 17.1, 17.3_
  - [ ]* 11.7 Write property test for app state in `web/src/state/appState.test.ts`
    - **Property 38: App state transitions**
    - **Validates: Requirements 18.4, 18.9, 20.2, 20.9, 20.10**
  - [ ]* 11.8 Write property test for KPI formatting in `web/src/lib/format.test.ts`
    - **Property 40: KPI formatting**
    - **Validates: Requirements 18.10**
  - [ ]* 11.9 Write property test for web Denver labels in `web/src/lib/time.test.ts`
    - **Property 41: Denver time labels** (web half)
    - **Validates: Requirements 7.11, 19.1, 21.2**
  - [ ]* 11.10 Write the palette contrast test in `web/src/lib/colors.test.ts`
    - Every text/background pair in the palette has a contrast ratio of at least 4.5:1
    - _Requirements: 22.7_

- [x] 12. Web panels: header, KPIs, chart, decision log, footer
  - [x] 12.1 Replace the placeholder with the app shell
    - `main.tsx` and `App.tsx`: load data, show a loading state, `useReducer(appState)`, CSS grid layout shell
    - `ErrorBanner.tsx`: "Could not load X.json" / "Could not parse X.json"
    - `Header.tsx`: title and tagline
    - `ToggleGroup.tsx`: `role="group"`, `aria-label`, native buttons with `aria-pressed`, `focus-visible` ring. Hide the scenario toggle when SCENARIOS has one entry.
    - No in-app links or routes
    - _Requirements: 17.1, 17.2, 17.3, 18.1, 18.2, 18.3, 18.4, 18.8, 18.9, 22.8, 24.7_
  - [x] 12.2 Implement `KpiRow.tsx` and the selector `lib/kpis.ts`
    - Five labelled cards. Values are at least 48 px at widths of 1280 px or more and at least 28 px below 768 px.
    - Headroom mode shows `metrics.*`. Baseline mode shows `0 kW`, `$0`, `0.0 t`, `100%`, `100%`.
    - DR strip (`requested_shed_kw`, `dr_delivered_pct`, `dr_seconds_to_full`) renders only when `dr === 'done'`
    - _Requirements: 18.5, 18.6, 18.7, 18.10, 20.5_
  - [x] 12.3 Implement `MainChart.tsx` and `CarbonBand.tsx`
    - Recharts `ComposedChart` over 192 slots, with ticks every 24 slots in Denver HH:MM
    - Baseline mode: a solid baseline line only
    - Headroom mode: a solid headroom line, a dashed baseline ghost at 40% opacity, and stacked grid/battery/flywheel/moved areas with `stackOffset="sign"` and distinct SVG patterns
    - PACE demand on the right MW axis, dotted
    - `ReferenceArea` per contiguous run of stress slots
    - Legend with units, `isAnimationActive={false}`, and `role="img"` with an `aria-label`
    - `CarbonBand`: a 24 px strip of one rect per hour on a min–max scale, with the legend `min–max gCO₂eq/kWh · average intensity, not marginal`
    - _Requirements: 8.9, 19.1, 19.2, 19.3, 19.4, 19.5, 19.6, 19.7, 19.8, 19.9, 19.10, 24.3_
  - [x] 12.4 Implement `DecisionLog.tsx` and `lib/orderDecisions.ts`
    - `orderDecisions(decisions, drWindow | null)`: newest first, stable for equal `t`. DR-window decisions come first when DR has fired.
    - Fixed-height scroll region with `tabIndex=0` and an `aria-label`, containing an `<ol>`
    - Each row: Denver HH:MM, `job#id`, route badge (colour plus text), target, full wrapped reason
    - Baseline mode shows only "Baseline: every job runs on arrival from the grid."
    - An empty array shows "No decisions for this scenario."
    - Scroll to the top on scenario change
    - _Requirements: 20.6, 21.1, 21.2, 21.3, 21.4, 21.5, 21.6, 21.7_
  - [x] 12.5 Implement `Footer.tsx`
    - Fixed to the bottom of the viewport
    - Shows the exact provenance sentence, the Duke line labelled "National planning context", the GitHub link from `REPO_URL`, and the displayed scenario's `meta.built_at`
    - A `ResizeObserver` writes `--footer-h`, and `main` gets `padding-bottom: var(--footer-h)`
    - _Requirements: 15.8, 22.1, 22.2, 22.3, 22.4, 22.5, 22.12_
  - [ ] 12.6 Wire the panels into `App.tsx`
    - Pass the selected `Scenario`, `mode` and `dr` state to `KpiRow`, `MainChart` with `CarbonBand`, `DecisionLog` and `Footer`
    - Toggles update every panel from in-memory data, with no network request
    - _Requirements: 17.2, 17.6, 18.4, 18.9, 19.10, 21.6, 22.5_
  - [ ]* 12.7 Write property test for decision ordering in `web/src/lib/orderDecisions.test.ts`
    - **Property 39: Decision log ordering**
    - **Validates: Requirements 20.6, 21.1**
  - [ ]* 12.8 Write KPI selector unit tests in `web/src/lib/kpis.test.ts`
    - Baseline returns zeros and 100%; Headroom returns the metrics; the DR values appear only when `done`
    - _Requirements: 18.6, 18.7, 20.5_
  - [ ]* 12.9 Write built-output checks in `sim/tests/test_web_dist.py` (skipped when `web/dist` is absent)
    - `dist/index.html` has the title, a description of 160 characters or fewer, matching OG tags, and no "Torus"
    - `dist` contains no external `http(s)://` origins other than the GitHub link
    - _Requirements: 17.4, 22.6, 22.11_

- [x] 13. Checkpoint: UI wired and deployed (due 19:00 MT)
  - Run `.\scripts\deploy.ps1` after each working UI increment and confirm smoke shows 3 of 3. Commit, push and print the milestone status.
  - Optionally run `npx vitest --run` in `web/`.
  - Ensure all tests pass, ask the user if questions arise.
  - _Requirements: 5.6, 24.6, 25.6_

- [x] 14. DR panel, zoom playback and polish
  - [x] 14.1 Implement `web/src/lib/useReducedMotion.ts` and `lib/useZoomPlayback.ts`
    - `matchMedia('(prefers-reduced-motion: reduce)')` with a change listener
    - A `requestAnimationFrame` loop where `idx = min(599, floor(elapsedMs × 60 / 1000))`; `onDone` fires at 599; the loop is cancelled on unmount or when playback becomes inactive
    - _Requirements: 20.3, 20.7, 20.9_
  - [x] 14.2 Implement `DrPanel.tsx` and `ZoomPanel.tsx`, and wire DR into `App.tsx`
    - DR button "Fire demand-response event" in both modes. In Baseline mode it has `aria-disabled="true"`, stays focusable, does nothing on click, and is linked through `aria-describedby` to the helper text "Switch to Headroom to dispatch storage".
    - In Headroom mode, `fireDr` uses `instant` when reduced motion is preferred or `ANIMATED_ZOOM=false`
    - `ZoomPanel`:
      - lines for `grid_kw`, `flywheel_kw`, `battery_kw` over `s` 0–599
      - a `cap_kw` `ReferenceLine`, a seconds x-axis and a kW y-axis
      - draws up to the current frame while playing, or all 600 entries when instant or done
      - clears when DR returns to `idle`
    - `drComplete` triggers the KPI DR strip and DR-first ordering in `DecisionLog`
    - _Requirements: 20.1, 20.2, 20.3, 20.4, 20.5, 20.6, 20.7, 20.8, 20.9, 20.10, 24.9_
  - [ ]* 14.3 Write playback unit tests in `web/src/lib/useZoomPlayback.test.ts`
    - With fake timers and rAF, frame 599 is reached at about 9.98 s and `onDone` fires once
    - _Requirements: 20.3, 20.9_
  - [x] 14.4 Accessibility and responsive layout pass
    - Visible `focus-visible` styles and accessible names on every control
    - Chart `aria-label` summaries
    - Layout:
      - at 1280 px and wider: header, KPI row, then `[chart + band | DR + zoom + log]` at 2 : 1, sized so that at 1920×1080 the header, KPIs, chart, DR button and footer fit without scrolling
      - below 768 px: a single column with `overflow-x: hidden`, the footer wrapping, and no content hidden behind the footer
    - Every text colour comes from the contrast-checked palette
    - _Requirements: 18.5, 18.8, 22.1, 22.7, 22.9, 22.10, 22.12_

- [x] 15. Final checkpoint: final deploy and freeze (before 22:00 MT)
  - Run `pytest -q sim/tests` (and optionally `npx vitest --run`), then `.\scripts\deploy.ps1`, and confirm smoke shows 3 of 3 with a clean secret grep. Commit, push, confirm CI is green and print the final status with the live URL.
  - Confirm that the never-cut items are in the deployed build: live URL, real PACE data, baseline vs Headroom chart, KPI row, decision log, REAL/ILLUSTRATIVE/SIMULATED labels. Log any cuts in `DECISIONS.md` with their time and reason.
  - Ask the user to run the design's manual demo checklist (1920×1080, 390 px, DevTools offline, keyboard only, reduced motion) and to keep `npm run preview` of the same `web/dist` as the fallback (Req 17.5). After 22:00 MT, only fixes, documentation and redeploys are allowed.
  - Ensure all tests pass, ask the user if questions arise.
  - _Requirements: 17.5, 24.5, 24.6, 25.6, 25.7, 25.9_

## Notes

- Tasks marked with `*` are optional and can be skipped for a faster MVP. Task 2.7 (no-network guard) and task 9.8 (acceptance tests) are required because they implement Requirement 23.
- Property tests use Hypothesis on the `ci` profile with at least 100 examples. Each one carries the comment `# Feature: headroom, Property N: <title>`. The web property tests (38–41) use fast-check and run locally with `npx vitest --run`. CI does not run them.
- Each property has its own test file, which differs from the grouping in the design's testing table, so independent tests can be written in parallel. Log this in `DECISIONS.md` (task 1.3).
- **Cut order** (Req 24.5). Cut one item at a time and log each cut in `DECISIONS.md`:
  1. CI: disable `ci.yml` and add a local `npx cdk synth` step to `deploy.ps1`
  2. Sun scenario: set `ENABLE_SUN_SCENARIO = False` and `SCENARIOS = ['peak']`
  3. MOVE_REGION: set `ENABLE_MOVE_REGION = False`
  4. Animated zoom: set `ANIMATED_ZOOM = false`
- Requirement 1.12 (custom domain) applies only if a Route 53 hosted zone already exists. It is a stretch goal and has no task.
- **USER ACTION** items: revoking and reissuing the EIA key, putting it in `.env`, `gh auth`, the AWS profile, `cdk bootstrap`, the one-time `python -m sim.fetch_eia`, and the manual demo checklist. No task prints, copies or commits a key value.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "1.3", "2.1", "2.3", "2.5", "2.7"] },
    { "id": 1, "tasks": ["2.2", "2.4", "2.6", "2.8", "2.9"] },
    { "id": 2, "tasks": ["4.1", "4.2"] },
    { "id": 3, "tasks": ["4.3", "4.4", "4.5", "5.1"] },
    { "id": 4, "tasks": ["4.6", "4.7", "4.8", "4.9", "4.10", "5.2", "5.3", "5.4", "5.8", "5.12"] },
    { "id": 5, "tasks": ["5.5", "5.6", "5.7", "5.9", "5.10", "5.11", "5.13", "5.14"] },
    { "id": 6, "tasks": ["7.1", "7.3", "7.8"] },
    { "id": 7, "tasks": ["7.2", "7.4", "7.5", "7.6", "8.1"] },
    { "id": 8, "tasks": ["7.7", "8.2", "8.3"] },
    { "id": 9, "tasks": ["8.4", "8.5", "8.6"] },
    { "id": 10, "tasks": ["8.7"] },
    { "id": 11, "tasks": ["8.8", "9.1"] },
    { "id": 12, "tasks": ["8.9", "8.10", "8.11", "8.12", "8.13", "8.14", "8.15", "8.16", "8.17", "8.18", "8.19", "8.20", "8.21", "8.22", "9.2", "9.3"] },
    { "id": 13, "tasks": ["9.4", "9.5", "9.6"] },
    { "id": 14, "tasks": ["9.7", "9.8", "9.9", "9.10"] },
    { "id": 15, "tasks": ["11.1"] },
    { "id": 16, "tasks": ["11.2", "11.3", "11.4", "11.5"] },
    { "id": 17, "tasks": ["11.6", "12.1", "12.2", "12.3", "12.4", "12.5"] },
    { "id": 18, "tasks": ["11.7", "11.8", "11.9", "11.10", "12.6", "12.7", "12.8", "12.9"] },
    { "id": 19, "tasks": ["14.1"] },
    { "id": 20, "tasks": ["14.2", "14.3"] },
    { "id": 21, "tasks": ["14.4"] }
  ]
}
```
