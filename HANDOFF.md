# Handoff: maintaining Headroom

You are taking over maintenance of **Headroom**, a finished, deployed demo. The repo is at `c:\Users\dougw\repos\headroom` (Windows, PowerShell). GitHub: https://github.com/utah-coder/headroom. Live site: https://dmpmm19aeecph.cloudfront.net.

## What it is

Headroom models a simulated 10 MW AI cluster in PacifiCorp East (EIA code `PACE`). For each flexible training job in each 15-minute slot of a 48-hour window, it picks a route: `GRID_NOW`, `STORAGE_NOW` (battery and flywheel), `DEFER` to a later on-the-hour slot, or `MOVE_REGION` to `BPAT` or `CISO`. It compares that against a baseline where every job runs from the grid on arrival. It reports grid capacity freed during stress hours, dollars saved, CO₂ avoided, deadlines met, inference SLA, and a demand-response (DR) event result. CO₂ is always "average intensity, not marginal".

Python precomputes everything from real EIA-930 data cached in `data/raw/` and writes two static files, `web/public/scenarios/peak.json` and `sun.json`. A Vite and React page reads them once at load and makes no network requests after that. The page is served from a private S3 bucket through CloudFront.

The spec is in `.kiro/specs/headroom/` (`requirements.md`, `design.md`, `tasks.md`). Read it before changing behaviour. `DECISIONS.md` logs every judgment call. Read the entries from 17:49 MT onward before touching the scheduler.

## Repo layout

- `sim/`: Python 3.11-compatible package. Local `.venv` runs Python 3.12; CI runs 3.11. Pinned deps in `sim/requirements.txt`: numpy, tzdata, pytest, hypothesis.
  - `config.py`: every constant, each with a `# REAL: <source>` or `# ILLUSTRATIVE` comment. No other module may hard-code these values (Req 10.1).
  - `timeutil.py`: UTC and Denver helpers. EIA periods are parsed as hour-*starting* UTC.
  - `fetch_eia.py`: cache-first EIA API v2 fetcher. It is the only module that reads `EIA_API_KEY`, and it reads it lazily, only on a cache miss. `load_all(offline=True)` never fetches; a missing file raises `CacheMissingError(path)`.
  - `grid.py`: hourly series, gap fill, peak and sun window selection, stress flags, DR start, `build_slots`, `write_processed`. `python -m sim.grid` prints a sanity check.
  - `carbon.py`: fuel mapping and average carbon intensity. Unknown codes, including `GEO` and `SNB`, map to Other (300 g/kWh).
  - `jobs.py`: seeded synthetic jobs. `rng_for` derives a `random.Random` per purpose from sha256. Use no numpy RNG.
  - `site.py`: tariff, inference profile, `StorageSpec` and `StorageState`, idle capacity, `build_event_zoom`, `DrInfeasibleError`.
  - `scheduler.py`: data models, baseline policy, reason strings, and the greedy Headroom policy (`_HeadroomPlanner`: routing, storage dispatch, DR handling, MOVE_REGION, `trace=True`).
  - `metrics.py`, `contract.py` (build, validate, `dumps`, atomic write), `run_scenarios.py` (the builder; `python -m sim.run_scenarios`).
  - `tests/`: 407 tests. `conftest.py` blocks the network automatically, and the Hypothesis `ci` profile is the default. `test_acceptance.py` holds the required real-data acceptance tests.
- `web/`: Vite 8, React 19, TypeScript **7.0.2**, Tailwind v4, Recharts 3.10.1, exact pins. `src/types.ts` mirrors `contract.INTERFACE_KEYS`. `src/config.ts` has `SCENARIOS`, `ANIMATED_ZOOM` and `REPO_URL`. Also: `data/loadScenarios.ts`, `state/appState.ts` (reducer), `lib/` (format, time, colors with `TEXT_PAIRS` contrast list, kpis, orderDecisions, chartData, useReducedMotion, useZoomPlayback) and `components/`. No test runner is installed.
- `infra/`: CDK v2 with TypeScript **5.9.3** and ts-node (ts-node needs TS 5). It defines `HeadroomSiteStack` in us-west-2, with the outputs `SiteUrl`, `BucketName` and `BucketRegionalDomainName`.
- `scripts/deploy.ps1` (`-SkipSim` optional) and `scripts/smoke.ps1`. Both are tested on Windows PowerShell 5.1 only; PowerShell 7 isn't installed.
- `.github/workflows/ci.yml`: pytest, then `python -m sim.run_scenarios` offline, then web build, then `cdk synth`. It uses no secrets and no AWS.

## Commands

```powershell
.\.venv\Scripts\python.exe -m pytest -q          # all 407 must pass
.\.venv\Scripts\python.exe -m sim.run_scenarios  # rebuild scenario JSON from data/raw (offline)
cd web; npm ci; npm run build; npm run preview   # preview = local fallback for demos
```

To deploy, run it in a single command, because each agent shell is a fresh process:

```powershell
$env:AWS_SHARED_CREDENTIALS_FILE='C:\Users\dougw\.aws\credentials'; $env:AWS_REGION='us-west-2'; $env:AWS_DEFAULT_REGION='us-west-2'; $env:PATH="$PWD\.venv\Scripts;$env:PATH"; .\scripts\deploy.ps1
```

- The credentials file has only a `[default]` profile, so don't set `AWS_PROFILE`. CDK is already bootstrapped.
- Run `deploy.ps1` directly. Wrapping it in `powershell ... *>&1 | Tee-Object` broke when `cdk deploy` wrote to stderr.
- Allow up to 30 minutes.
- Success looks like: the secret grep reports "no match" twice, then "3 of 3 checks passed", then "DEPLOY OK".
- If a deploy gets killed during `npm ci`, `web/node_modules` is left half-installed and `tsc` goes missing. Rerun `npm ci`.

## Security rules (non-negotiable)

- Never print, log or commit the EIA key or any AWS secret.
  - `.env` is git-ignored and holds `EIA_API_KEY=<value>`. Check it without printing the value: `Select-String -Path .env -Pattern '^EIA_API_KEY=\S' -Quiet`.
  - `.env.example` must stay exactly `EIA_API_KEY=`. The new key was once pasted into it by mistake, and once into `.env` as `EIA_API_KEY=EIA_API_KEY=...`.
- An old key is in pushed history (commit `b6c7a39`). It was revoked; history was not rewritten.
- Stage files by name only. Never `git add -A`, `git add .` or `commit -a`. Before every push, confirm `git ls-files .env` is empty and `git diff HEAD -- .env.example` is empty.
- Never force-push.

## Conventions

- Log judgment calls in `DECISIONS.md` using its template (date/time MT, decision, rationale, affected component), in the same commit as the change. The file is append-only: to reverse a decision, add a new entry that names the old one.
- `web/src/config.ts` must keep `export const SCENARIOS: readonly ScenarioName[] = ['peak', 'sun'];` on one line. `deploy.ps1 -SkipSim` parses it with a regex.
- The scenario JSON files are a single line each, so `git diff` is useless on them. Compare them structurally. Two builds from the same seed and cache differ **only** in `meta.built_at`.
- Git-ignored: `data/processed/` (regenerated on every build) and the Kiro state files `.config.kiro` and `tasks.meta.json`. `data/raw/` (about 14 MB) **is** committed and is the source of truth for every build.
- Use "Torus" nowhere in the web app. The page loads no external fonts, scripts or analytics. The only external link is the GitHub repo.

## Fragile couplings and things that bite

- `sim/grid.py` imports `sim/site.py`, so `site.py` must never import `grid`, or you get a circular import. `carbon.py` imports the private `grid._bucket`; keep that function.
- `python -m sim.grid` and `python -m sim.run_scenarios` re-import their own module in `__main__`, so exception classes match the ones the other modules raise. Keep that pattern.
- `metrics.inference_sla_pct` compares floats with exact `==`. Headroom must copy `inp.inference_fac[s]` unchanged into every slot, or the build fails (Req 15.10).
- `MoveRecord.kwh_by_slot` and `SlotResult.moved_kw` are facility-equivalent (IT kW × PUE 1.3). Moved load is never counted in `grid_kw` or `training_kw`.
- The planner must never read actual demand `D` for future slots, only the forecast `DF`. A unit test sets future `D` to NaN to check this. `D` is used only for the reason-string percentile.
- `DrResult.required_kw` comes from the zoom's last entry, not from `load − cap`. The two differ by 0.1 kW of rounding, and `dr_seconds_to_full` used to crash on that difference.
- Cut flags are read at call time (`config.ENABLE_SUN_SCENARIO`, `config.ENABLE_MOVE_REGION`, `web/src/config.ts ANIMATED_ZOOM`). If you cut the sun scenario, change both `sim/config.py` and `web/src/config.ts`. The builder then deletes a stale `sun.json`.

## Scheduler tuning (from checkpoint 10; see DECISIONS.md, 19:33 MT)

The greedy policy originally produced a "deferral rebound": headroom's window peak reached 22.6 MW against 14.8 MW for baseline, so `usd_saved` was negative. It was fixed by adding `scheduler.arrival_forecast_kw` to the `planned` array, plus three settings: `PEAK_TARGET_SCALE` 1.10, `DEADLINE_COMFORT_MIN` 1440 and `DEADLINE_RISK_USD_PER_KWH` 0.30. Results stay positive for every `PEAK_TARGET_SCALE` from 1.00 to 1.20. A hard "never defer above P" filter made results worse and was rejected. The `pulp` LP fallback (Req 11.18) was never needed.

Current output (seed 42):

| | headroom_kw | usd_saved | co2_avoided_t | deadlines / SLA / DR delivered | DR R | dr_seconds_to_full |
|---|---|---|---|---|---|---|
| peak | 5,729.5 | 29,333.50 | 19.904 | 100 / 100 / 100 | 1,426 kW | 20 |
| sun | 2,114.5 | 19,896.15 | 3.884 | 100 / 100 / 100 | 919 kW | 20 |

If you change any knob, check these before committing:

1. The stress-hour peak test (Req 23.6) still passes in both scenarios.
2. `usd_saved` stays ≥ 0 in both scenarios.
3. R stays above 0, so the zoom isn't a flat line.

## Known open items (none blocking)

- The web JS bundle is about 589 kB, above Vite's 500 kB warning. Code-splitting Recharts would fix it.
- CI warns that the Node 20 GitHub Actions are deprecated. Plan to bump `actions/*` and the Node version.
- The task 14 work (DR panel, zoom, accessibility pass) was deployed after the spec's 22:00 MT freeze on 2026-10-27. Nothing records this in DECISIONS.md yet, so ask the owner whether to.
- CO₂ avoided fell after the checkpoint 10 tuning, as the trade-off for keeping the window peak below baseline.
- No job is late on real data, so the late-flag path is covered only by unit tests.
- **Optional spec tasks not done** (marked `*` in `tasks.md`): property tests 4.3–4.10, 5.2–5.13, 7.2–7.8 and 8.3–8.22; tasks 9.2, 9.4, 9.5, 9.7, 9.9 and 9.10; contract mirror test 11.2; vitest and fast-check setup plus web tests (11.6–11.10, 12.7, 12.8, 14.3); built-output checks 12.9; and CDK assertion tests 2.4. Several unit-test files (`*_unit.py`) already cover parts of these. The property-test file names are reserved per DECISIONS.md, "One test file per correctness property".

## Refreshing EIA data (rarely needed)

The data range is fixed in `config.py` (2026-06-01 to 2026-09-30 UTC). To refetch:

1. Delete the relevant `data/raw/` files.
2. Run `.\.venv\Scripts\python.exe -m sim.fetch_eia` with the key in `.env`. A full fetch is about 18 requests and fully cached runs never read the key.
3. Check that the key isn't in `data/raw` with `Select-String -SimpleMatch -List`, printing paths only.
4. Run `python -m sim.grid` as a sanity check, rebuild, and commit.

## Freeze rules

The demo is frozen. Make only fixes, documentation changes and redeploys; add no new features unless the owner asks. Every deploy must pass the full test suite, the clean secret grep and the 3-of-3 smoke test, with CI green on the pushed commit.