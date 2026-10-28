# Decisions

This file logs every judgment call made while building Headroom (Requirement 25.4). Add a new entry in the same commit as the change it describes. Append new entries at the end; do not rewrite old ones. If a later decision reverses an earlier one, add a new entry that names the entry it replaces.

## Entry template

```markdown
### YYYY-MM-DD HH:MM MT: <short title>

- **Decision:** what was chosen.
- **Rationale:** why, including the alternatives considered.
- **Affected component:** file, module or system part (for example `sim/scheduler.py`).
```

Times are America/Denver local time (MT).

## Entries

The entries below were seeded from the approved design before implementation started.

### 2026-10-27 12:28 MT: Extra modules `timeutil.py`, `grid.py` and `contract.py`

- **Decision:** Add `sim/timeutil.py` (UTC/Denver helpers, ISO `Z` formatting, slot math), `sim/grid.py` (cache to hourly series, gap fill, windows, slots, stress flags, DR start) and `sim/contract.py` (build, validate, serialize and atomically write Scenario files) to the module layout in the brief.
- **Rationale:** These keep `run_scenarios.py` a thin orchestrator and give the property tests small pure functions to target. Putting this logic inside `run_scenarios.py` would make it hard to test without building a whole scenario.
- **Affected component:** `sim/` package layout.

### 2026-10-27 12:28 MT: EIA-930 hour labelling interpretation

- **Decision:** Parse each EIA `period` (`YYYY-MM-DDTHH`) as a UTC timestamp for the hour that starts at that time, and assign its value unchanged to the four 15-minute slots starting at :00, :15, :30 and :45 of that hour (Requirements 7.10, 7.12).
- **Rationale:** Some EIA-930 views use hour-ending labels, so the true hour may be one hour earlier than the label. The requirements fix the hour-starting interpretation. If the data is hour-ending, every series and both selected windows sit one hour late. That shift does not change the policy comparison or any conclusion of the demo.
- **Affected component:** `sim/timeutil.py` (`parse_eia_period`), `sim/grid.py` (`expand_to_slots`, `build_slots`).

### 2026-10-27 12:28 MT: `GEO` mapped to Other

- **Decision:** Map any fuel code outside Known_Fuel_Codes (`COL`, `NG`, `NUC`, `OIL`, `OTH`, `SUN`, `WAT`, `WND`) to Other at 300 gCO₂eq/kWh. This includes `GEO`, which is not mapped to Geothermal (38 gCO₂eq/kWh). Each distinct unknown code and respondent is logged with its row count.
- **Rationale:** This follows the approved Requirement 8.2. It overstates the intensity of any geothermal generation, which is small in PACE, BPAT and CISO hourly mixes. Mapping `GEO` to Geothermal is a candidate refinement if the requirement is revised.
- **Affected component:** `sim/carbon.py` (`map_fuel`), `sim/config.py` (`FUEL_CODE_MAP`).

### 2026-10-27 12:28 MT: `plan_deadline` clamping and carry-over deadline accounting

- **Decision:** The headroom policy plans each job against `plan_deadline = min(deadline_min, N × 15)`, so it never pushes work past the end of the 48-hour window. A job that cannot finish inside the window even when run continuously from arrival is always at risk and runs exactly like baseline (`GRID_NOW` every slot). For deadline accounting, a job completed in the window is met iff `finish_min ≤ deadline_min`. A job unfinished at the window end is met iff `N × 15 + remaining_min ≤ deadline_min`, projecting that it keeps running after the window (carry-over).
- **Rationale:** Nominal deadlines often fall after the window. Without clamping, the headroom policy could defer work out of the window, which would make its $ and CO₂ numbers look better than they are. The carry-over rule gives baseline 100% deadlines met (Requirement 18.7), because baseline jobs always run continuously from arrival.
- **Affected component:** `sim/scheduler.py` (headroom routing, job outcomes), `sim/metrics.py` (`deadlines_met_pct`).

### 2026-10-27 12:28 MT: "Highest Site_Grid_Draw already planned" read as `P`

- **Decision:** Requirement 11.14's "highest Site_Grid_Draw already planned" is the running planned peak `P`. `P` starts at the site's average facility load, `P0 = PUE × (mean(inference IT kW) + IT_PEAK_KW × TRAINING_SHARE)`, and after each slot becomes `max(P, grid_draw[s])`.
- **Rationale:** Starting from zero would let the first slots set an arbitrarily low or high peak and make peak shaving depend on start-up order. `P0` is a fixed, explainable target, and anything already committed raises it. `P0` is the first tuning knob if the stress-hour peak test (Requirement 23.6) fails.
- **Affected component:** `sim/scheduler.py` (headroom policy, storage dispatch).

### 2026-10-27 12:28 MT: `dr_delivered_pct` clamped, raw value kept in `meta.params`

- **Decision:** Report `metrics.dr_delivered_pct = clamp(100 × (base.grid[dr_start] − max(head.grid[dr_slots])) / 4000, 0, 100)`, rounded to 1 dp, and store the unclamped value in `meta.params.dr_delivered_raw_pct`.
- **Rationale:** The value can exceed 100% when the headroom load already sits below the cap, but the output contract bounds every `_pct` field to [0, 100] (Requirement 16.10). Keeping the raw value means nothing is hidden. Other KPIs (`headroom_kw`, `usd_saved`, `co2_avoided_t`) are reported raw, including negatives (Requirement 15.7).
- **Affected component:** `sim/metrics.py`, `sim/contract.py` (`build_doc`, `validate`).

### 2026-10-27 12:28 MT: Stress slots fall in whole hours

- **Decision:** With `k = ceil(0.05 × N)` (10 for N = 192), the threshold is the k-th largest `pace_demand_mw`, and a slot is a Stress_Slot iff its demand is at or above the threshold. Ties are included, so the count can exceed k.
- **Rationale:** Each hourly EIA value repeats across four slots, so any threshold selects whole hours. A real scenario typically gets 12 stress slots (3 hours), not exactly 10. Breaking ties by slot index would split an hour arbitrarily. The inclusive rule is deterministic and matches the glossary's "top 5% of PACE demand values".
- **Affected component:** `sim/grid.py` (`stress_flags`), `sim/metrics.py` (`headroom_kw`).

### 2026-10-27 12:28 MT: `random.Random` chosen over numpy for seeded randomness

- **Decision:** Use stdlib `random.Random` (Mersenne Twister) for all simulated randomness, with one generator per purpose seeded by `rng_for(seed, scenario, purpose)` from the first 8 bytes of `sha256(f"{seed}:{scenario}:{purpose}")`.
- **Rationale:** Its seeded stream is stable across Python versions and adds no dependency risk, which supports byte-identical Scenario files for the same seed and cache. Per-purpose seeds keep job generation and idle capacity independent, so changing one does not shift the other. numpy is still used for array arithmetic in the scheduler.
- **Affected component:** `sim/jobs.py` (`rng_for`, `generate`), `sim/site.py` (`idle_capacity_kw`).

### 2026-10-27 12:28 MT: One test file per correctness property

- **Decision:** Each correctness property gets its own test file (for example `sim/tests/test_grid_fill.py`, `sim/tests/test_sched_soc.py`), instead of the grouping in the design's testing table.
- **Rationale:** Separate files let independent property tests be written in parallel without edit conflicts. Each test still carries the `# Feature: headroom, Property N: <title>` comment, so the mapping to the design stays traceable.
- **Affected component:** `sim/tests/`, `web/src/**/*.test.ts`.
### 2026-10-27 14:52 MT: Tag CDK-internal raw resources with `Project=headroom`

- **Decision:** Keep `cdk.Tags.of(app).add('Project', 'headroom')` and add a stack Aspect (`TagRawCfnResources` in `infra/lib/site-stack.ts`) that sets `Tags` by property override on raw `CfnResource` nodes of type `AWS::IAM::Role` and `AWS::Lambda::Function` that are not taggable through `TagManager`.
- **Rationale:** `autoDeleteObjects` creates its provider role and handler as raw `CfnResource`s, which `Tags.of()` does not reach, so the synthesized template had two taggable resources without the tag (Requirement 1.10). No `aws-cdk-lib` API name differed from the design, so this adds behaviour rather than substituting an API. The remaining untagged resources (bucket policy, OAC, Lambda layer, IAM policy, custom resources, CDK metadata) do not support tags in CloudFormation.
- **Affected component:** `infra/lib/site-stack.ts`, `infra/bin/headroom.ts`.

### 2026-10-27 14:52 MT: Infra uses TypeScript 5.9.3 with ts-node

- **Decision:** Pin `typescript` 5.9.3 and `ts-node` 10.9.2 in `infra/`, with `cdk.json` running `npx ts-node --prefer-ts-exts bin/headroom.ts`. `web/` keeps its own TypeScript 7.0.2 pin.
- **Rationale:** ts-node depends on the TypeScript compiler's JavaScript API, which the TypeScript 7 native port does not provide in the same form. The standard CDK app setup on TypeScript 5.x synthesizes without a build step. The two packages have separate lockfiles, so different compiler versions do not conflict.
- **Affected component:** `infra/package.json`, `infra/cdk.json`.

### 2026-10-27 15:05 MT: `deploy.ps1 -SkipSim` scenario list and key parsing

- **Decision:** The `-SkipSim` check reads the scenario ids from `const SCENARIOS = [...]` in `web/src/config.ts`. Until that file exists (task 11.1), it falls back to `peak, sun` and says so in its output. If the file exists but the array cannot be read, the check fails instead of guessing. `Get-EnvKey` takes the last `EIA_API_KEY=` line in `.env`, trims whitespace, strips one pair of surrounding quotes or an unquoted ` # comment`, and treats a blank value as missing. The deploy also runs `npm ci` in `infra/` before `cdk deploy` (as in the design listing) and deletes a stale `infra/cdk-outputs.json` first so an old `SiteUrl` is never smoke-tested.
- **Rationale:** The walking skeleton deploys before the UI config exists, and Requirement 2.5 names exactly `peak.json` and `sun.json`, so that is the safe default. Failing on an unreadable array keeps the check from silently passing when the sun scenario is cut or renamed (Requirement 24.7). Stripping quotes and comments keeps the secret grep from searching for the wrong string, which would make it pass without checking anything.
- **Affected component:** `scripts/deploy.ps1`.

### 2026-10-27 16:58 MT: Carry-over counts a Restart_Penalty for jobs paused at window end

- **Decision:** In `met_deadline`, a job unfinished at the window end is met iff `N × 15 + remaining_min + (5 if paused else 0) ≤ deadline_min`. A job that is running (or has not started) at the window end uses the plain carry-over rule from the earlier entry.
- **Rationale:** Requirement 10.20 counts Restart_Penalties toward a job's full run time, and a paused job must resume to finish, which adds one penalty. Headroom DEFER targets already keep `t*·15 + rem + 5 ≤ plan_deadline ≤ N × 15`, so a job should never end the window paused; the extra term only makes the accounting exact if one does. Baseline never pauses, so its 100% deadlines met is unchanged.
- **Affected component:** `sim/scheduler.py` (`met_deadline`, `job_outcome`).

### 2026-10-27 17:49 MT: Demand_Charge_Risk is the peak rise a route causes

- **Decision:** The headroom route cost prices demand-charge risk as `DEMAND_CHARGE_PER_KW × max(0, max_k(planned[k] + kW_k) − max(P, max_{k ≥ s} planned[k]))`, where `k` runs over the route's run slots. This is the design's `max(0, max_k(planned[k] + kW_k) − P)` whenever no reservation already sits above `P`. The same kW figure is the reason string's "would raise monthly peak by N kW", taken from the job's `GRID_NOW` pricing.
- **Rationale:** The glossary defines Demand_Charge_Risk as the increase in the window's peak caused by a route. Requirement 13.4 asks for 0 kW when the route does not raise the peak. The design formula measures against `P` only. Once reservations exceed `P`, it charges every job the whole existing excess, even when that excess is far larger than the job's own kW (a 520 kW job was charged for 3,005 kW in the real peak scenario). The reason strings then overstated the job's effect, and routes could no longer be told apart by risk. With the marginal form, no job's risk can exceed its own kW.
- **Affected component:** `sim/scheduler.py` (`_HeadroomPlanner.price_run`, `planned_peak_kw`).

### 2026-10-27 17:49 MT: Headroom routing details left open by the design

- **Decision:**
  1. A run slot after the look-ahead `H` (only possible when `H` is a full 24 h) is priced at the unit cost of the slot 24 h earlier, which lies inside `H`. This is a persistence forecast.
  2. `STORAGE_NOW` is offered only if the job's run-now finish is at or before `plan_deadline` (Requirement 11.9 applies to every non-grid route). It also needs the available discharge minus already committed `STORAGE_NOW` kW to be at least the job's full facility kW. A job that cannot finish in the window therefore runs `GRID_NOW` with cause `DEADLINE` every slot, like baseline.
  3. A job is "deadline-blocked" when its first on-the-hour DEFER target fails `t*·15 + rem + 5 ≤ plan_deadline`. Later targets only finish later. Blocked jobs are processed first. A blocked job with no non-grid candidate gets `GRID_NOW` with cause `DEADLINE`.
  4. Reservations are released by resetting `planned[s:]` to inference at the start of each slot. Every reservation from `s` on belongs to a runnable job that is re-added as it is routed. This is the same as releasing each reservation but avoids float drift.
  5. Until task 8.7, storage dispatch only serves committed `STORAGE_NOW` load, battery first and then flywheel, with discharge floors at the spec's 10 %.
  6. The reason-string demand percentile uses PACE `D` over all N slots, as Requirement 13.4 defines it. It is a reporting value. No cost or candidate code reads `D`, and changing future `D` leaves routes, targets, causes and power unchanged (unit-tested).
  7. `config.PEAK_TARGET_SCALE` (1.0, ILLUSTRATIVE) multiplies `P0`, the design's first tuning knob. `scheduler.peak_target_kw(inp)` returns `P0` for `meta.params.peak_target_kw`.
- **Rationale:** These keep the greedy deterministic and explainable, compare routes on the same work, and follow the requirements where the design pseudocode is silent.
- **Affected component:** `sim/scheduler.py` (`run_headroom`, `_HeadroomPlanner`), `sim/config.py`.

### 2026-10-27 18:04 MT: Storage dispatch and DR handling details left open by the design

- **Decision:**
  1. DR handling (the GRID_NOW budget `cap + DR_STORAGE_SUPPORT_KW`, the DR hold, the reserve floors, `DrInfeasibleError` and cause `DR`) runs only when `ScenarioInputs.cap_kw` is set. Without a cap, DR slots still exclude STORAGE_NOW, DEFER targets and charging, as in task 8.6.
  2. A DR slot runs step 1 only: the battery and flywheel discharge `R = max(0, load − cap)` and nothing else, so grid = min(load, cap). Peak and stress shaving are skipped in DR slots so they cannot drain the energy later DR slots need. At the DR start the flywheel supplies `R × DR_BATTERY_RAMP_S / 2 / 900 s` (the zoom's ramp energy averaged over the slot) and the battery the rest. The battery takes over from the flywheel if it is short, and the flywheel the other way round.
  3. `DrInfeasibleError(slot=s)` is raised when `R` exceeds what battery + flywheel can discharge in the slot (rating and 10 % floor). In the start slot `R` is also limited by each device's rating, because the zoom has the flywheel take the whole step at second 0 and the battery hold it after its ramp. A negative cap is infeasible.
  4. Battery reserve floor while the event is ahead: the design formula, `10 % + DR_STORAGE_SUPPORT_KW × dr_hours / eta × DR_RESERVE_MARGIN` (31.7 %). The flywheel's 50 % is a value in the design, not a formula, so it is the new config constant `DR_FLYWHEEL_RESERVE_PCT = 50` (ILLUSTRATIVE).
  5. "Cheapest `CHARGE_RANK_FRACTION` of H" means `unit_cost[s]` ≤ the k-th smallest unit cost in H, with `k = ceil(0.25 × |H|)` and ties included (the same rule as stress slots). The stress shave is battery only and limited so that grid draw stays ≥ 0.
  6. `storage_cost_basis` after a charge is the average of the old basis, weighted by the stored energy converted back to kWh drawn (`soc / eta` per device), and this slot's unit cost, weighted by the kWh drawn.
  7. A DR-slot choice has cause `DR` when the budget excluded GRID_NOW and the job would have picked a different route with GRID_NOW on offer. A job with no candidate left (budget spent and no DEFER target before `plan_deadline`) runs GRID_NOW with cause `DEADLINE`, bypassing the budget.
  8. Broken invariants (negative grid, grid above the cap in a DR slot, simultaneous charge and discharge, SOC outside [10 %, 95 %]) raise `AssertionError` naming the scenario and slot, per the design's error table. The negative-grid check raised `RuntimeError` before.
- **Rationale:** These follow the design's priority order and floors where it is explicit and pick the simplest deterministic rule where it is silent. On the real peak scenario the DR cap now holds (`R` about 1,450–1,500 kW, battery at the 31.7 % reserve when the event starts), and the zoom is feasible from that state.
- **Affected component:** `sim/scheduler.py` (`_HeadroomPlanner`, `run_headroom`), `sim/config.py`.
### 2026-10-27 18:16 MT: MOVE_REGION details left open by the design
- **Decision:**
  1. A moved job is in transit for `MOVE_DELAY_SLOTS` (it runs nowhere), then runs continuously in the Alternate_Region from `s + 2` until done. A job that has already run pays one Restart_Penalty for the remote start (`rem' = rem + 5` if started, the design's MOVE cost term), and `resumes` goes up by 1. The move is rejected if `(s + 2)·15 + rem' > plan_deadline` (Requirement 12.7).
  2. The idle-capacity check and the `alt_used` ledger use IT kW (Idle_Capacity is a percentage of IT_Peak). A job books its full `power_kw` in every hour that contains at least one of its remote run slots, including a partly used last slot. An hour missing from `idle_capacity_kw` has no capacity.
  3. The MOVE cost is the design formula: `MOVE_PENALTY_USD` + facility kWh × (`ALT_REGION_PRICE_PER_KWH` + region carbon × shadow price) + deadline penalty, with no demand-charge risk. Run slots past the look-ahead use the same 24 h persistence as the other routes. Both regions are priced; the lower cost is offered, BPAT first on ties.
  4. `SlotResult.moved_kw` and `MoveRecord.kwh_by_slot` are facility-equivalent (IT × PUE), the basis `sim/metrics.py` reads. Moved load is never in `training_kw` or `grid_kw`. The moved job's IT kWh count in `JobOutcome.delivered_kwh`, and its finish time is the remote finish.
  5. A job is deadline-blocked only when no DEFER target and (with the flag on) no MOVE meets plan_deadline. A MOVE rejected only for capacity does not make the job blocked.
  6. The cut flag is read at call time: `run_headroom(inp, move_enabled=None)` reads `config.ENABLE_MOVE_REGION` when the run starts, and an explicit `move_enabled` overrides it. With it off, MOVE_REGION is never priced or offered.
  7. `RouteCandidate.target_slot` holds the remote start slot for MOVE_REGION; `JobTrace.move_options` lists every region that passed the filter.
- **Rationale:** These follow the design's cost formula and filter table and keep the energy and emission accounting consistent with `sim/metrics.py`. On the real peak scenario, earlier moves leave the DR-start load under the cap (`R` = 0), so no DR-caused decisions appear with moves on. The storage DR test therefore runs with moves off and on, and requires DR causes only when nothing moved. This is recorded for checkpoint 10.
- **Affected component:** `sim/scheduler.py` (`_HeadroomPlanner.move_options`, `apply_move`, `run_headroom`), `sim/tests/test_scheduler_storage_unit.py`.

### 2026-10-27 18:55 MT: Scenario_Builder details left open by the design
- **Decision:**
  1. `meta.built_at` is the build's UTC start time, truncated to the second, and is the same in every file of one run. `build_scenario` and `main` accept a `built_at` override for tests. No `SOURCE_DATE_EPOCH` support.
  2. The Event_Zoom starts from the Headroom run's Battery and Flywheel SOC at the *start* of the DR start slot, which is the end-of-slot SOC of the previous slot (the configured initial SOC if the DR starts in slot 0). `L` is the Headroom `inference_kw + training_kw` in the DR start slot, before storage.
  3. `DrResult` is defined in `sim/run_scenarios.py`. It carries the fields `sim/metrics.py` and `sim/contract.py` read.
  4. All files are written in two phases. Every `*.json.tmp` is written and fsynced first, then each is moved into place with `os.replace`. With `ENABLE_SUN_SCENARIO = False`, a stale `sun.json` is deleted only after `peak.json` has been written. `data/processed/` is written only after the Scenario_Files succeed, and it stays untracked as generated output.
  5. The cut flags are read at call time from `sim.config`, so tests can switch them.
- **Rationale:** These follow the design's flow. Building and serializing every scenario in memory before writing means a failure in `sun` leaves both existing files unchanged. A wall-clock `built_at` matches the brief's example value, and Requirement 16.13 excludes it from byte comparison.
- **Affected component:** `sim/run_scenarios.py`.

### 2026-10-27 19:33 MT: Checkpoint 10 deferral-rebound fix: arrival forecast in `planned`, then tuning

- **Decision:**
  1. **Logic change.** The headroom planner's `planned` array now includes the expected facility load of training jobs that have not arrived yet: `planned[s + j] = inference[s + j] + arrival_forecast_kw[j] + reservations`. `scheduler.arrival_forecast_kw` uses only the configured job process: the Poisson rate from `jobs.arrival_rate_per_hour`, the mean of `JOB_POWER_KW`, and the uniform stepped `JOB_DURATION_MIN` grid. Entry 0 is 0, because jobs arriving at slot `s` are already known. It rises to `IT_PEAK_KW × TRAINING_SHARE × PUE` (8,450 kW) after 48 slots, the longest job. No actual job, `D` value or future grid value is read (Requirement 11.4 holds, and the future-`NaN` demand test still passes). `price_run`, `planned_peak_kw` and `reserve` are unchanged. They now see the forecast through `planned`.
  2. **Tuning** (`sim/config.py`, all ILLUSTRATIVE): `PEAK_TARGET_SCALE` 1.0 → 1.10 (`P0` 12,090 → 13,299 kW in both scenarios). `DEADLINE_COMFORT_MIN` 240 → 1,440. `DEADLINE_RISK_USD_PER_KWH` 0.10 → 0.30. `STRESS_DISCHARGE_KW` (2,000) and `DR_STORAGE_SUPPORT_KW` (1,500) are unchanged, because sweeping them (0/1,000/2,000 and 1,000/2,000) moved `usd_saved` by less than $600.
  3. **Bug fix.** `DrResult.required_kw` now comes from the zoom's last entry (battery + flywheel). Before, it was `max(0, load − cap)` unrounded. The zoom rounds the load to 0.1 kW and floors the cap to 0.1 kW, so the two could differ by 0.1 kW. `metrics.dr_seconds_to_full` then raised "event zoom never reaches full battery output" (seen at R = 594 kW). It never showed before because R was 0 in both scenarios.
- **Before → after** (real scenarios, seed 42):

  | | peak before | peak after | sun before | sun after |
  |---|---|---|---|---|
  | whole-window peak, baseline → headroom (kW) | 14,769.8 → 22,563.7 | 14,769.8 → 13,299.0 | 14,705.8 → 17,346.5 | 14,705.8 → 13,680.8 |
  | stress-hour max, baseline → headroom (kW) | 13,593.5 → 7,864.0 | 13,593.5 → 7,864.0 | 14,412.5 → 12,090.0 | 14,412.5 → 12,298.0 |
  | `headroom_kw` | 5,729.5 | 5,729.5 | 2,322.5 | 2,114.5 |
  | `usd_saved` | −137,695.68 | 29,333.50 | −45,075.17 | 19,896.15 |
  | `co2_avoided_t` | 29.056 | 19.904 | 6.812 | 3.884 |
  | `deadlines_met_pct` / `inference_sla_pct` | 100 / 100 | 100 / 100 | 100 / 100 | 100 / 100 |
  | DR zoom `R` (kW) / `dr_seconds_to_full` | 0 / 0 | 1,426.1 / 20 | 0 / 0 | 919.0 / 20 |

- **Rationale:** The rebound had two causes. First, the greedy priced every DEFER target against a future that held only inference and already-routed jobs, so slots that later arrivals would need looked free. Night-1 work moved into the next morning for small carbon savings, and new arrivals then pushed the peak up. Once `P` had risen, filling up to it cost nothing. Second, with a 240-minute comfort, a deferral paid no deadline penalty until it was within 4 h of `plan_deadline`. Jobs with deadlines clamped to the window end therefore piled into the final morning. Alternatives tried, all at the old knobs unless noted:
  - **Arrival forecast alone:** peak `usd_saved` −62,948, sun −3,518. The sun stress-hour test failed (15,016 kW vs 14,413 kW).
  - **Hard DEFER filter** (drop any target whose run would put a slot above `P`, the literal "cap DEFER" option): peak −127,291 without the forecast and −44,490 with it. When the filter removes every target, the job runs `GRID_NOW` immediately and raises the peak. With the forecast and `DEADLINE_COMFORT_MIN` 1,440 it raised `DrInfeasibleError` in sun slot 47 (2,320 kW needed, 1,877 kW available). Not adopted.
  - **Pricing DEFER against `P` only** instead of `max(P, planned)`: with the forecast it reached peak −43,855 at the old knobs, still negative. With comfort 1,440 it gave about the same results as the current form (for example `P0` ×1.10: peak +4,572 vs +4,463, sun +23,021 vs +27,128), so the 17:49 marginal Demand_Charge_Risk entry stays.
  - **Tuning without the forecast** (`P0` ×1.15, comfort 1,440): positive, but the sun stress margin fell to 510 kW. With risk 0.30 the peak scenario's window peak was 14,694 kW, only 76 kW under baseline.

  The forecast plus the three knobs gave `usd_saved` > 0 in both scenarios at every `P0` scale from 1.00 to 1.20 (risk 0.30, comfort 1,440). The worst cases were peak +$6,465 at 1.20 and sun +$4,899 at 1.20, so 1.10 is not a knife edge. The higher deadline risk also keeps more jobs running at the DR start (the 1,500 kW DR budget then binds), so storage now carries part of the shed: R = 1,426 kW (peak) and 919 kW (sun), and the Event_Zoom shows the flywheel step and the 20 s battery ramp. The `pulp` LP fallback (Requirement 11.18) was not needed, because Requirement 23.6 passed before and after.
- **Tests changed:** Three tie-break tests in `test_scheduler_headroom_unit.py` now set `DEADLINE_RISK_USD_PER_KWH = 0` through a fixture. DEFER finishes later than GRID_NOW and STORAGE_NOW, so with a 1,440-minute comfort a deadline penalty separates their costs, and an exact tie needs zero penalty. Two MOVE cost tests in `test_scheduler_move_unit.py` now include the design's deadline-risk term in the expected cost. Before, that term happened to be 0 with the old comfort. The peak-shave test in `test_scheduler_storage_unit.py` now sizes its jobs from `peak_target_kw`, so the load still sits between the battery rating and battery + flywheel above `P` for any `P0` scale. New tests cover `arrival_forecast_kw`, the forecast in the planned peak, and the `required_kw` rounding.
- **Affected component:** `sim/scheduler.py` (`arrival_forecast_kw`, `_HeadroomPlanner.__init__`, `begin_slot`), `sim/config.py`, `sim/run_scenarios.py` (`build_dr`), `sim/tests/`.

### 2026-10-28 09:51 MT: Guide panel and in-page explanations (owner request, after the freeze)

- **Decision:**
  1. At the owner's request for the demo, the page explains itself. A new Guide panel (`Guide.tsx`) sits between the header and the KPI row. It holds a two-sentence introduction, a line describing the selected scenario, and the three demo steps from the brief: Baseline, Headroom, then fire a demand-response event. The steps are buttons that dispatch the same `setMode` and `fireDr` actions as the toggles and the DR button. The current step follows `mode` and `dr`, so it stays in sync whichever control the presenter uses. "How it works" expands a four-part explanation: the routes, how a route is chosen, what the KPIs mean, and what is real. "Hide guide" removes the panel and puts a "Show guide" button in the header. That choice is kept in `localStorage` (key `headroom.guideHidden`) behind try/catch, so the guide just shows when storage is unavailable.
  2. Every panel gets a one-line caption. The Main_Chart and DR panel captions differ by mode. Each KPI card now has a note; in Baseline, the first two notes say that Baseline is the reference point, which explains the zeros. The DR panel states the event (start time in Denver, requested shed, duration) from `dr_event`. The Zoom_Panel's empty state says what it will show, with different text in Baseline.
  3. **Requirement 21.5 relaxed.** The Baseline Decision_Log still opens with the exact sentence "Baseline: every job runs on arrival from the grid.", then explains that Baseline makes no choices, gives the scenario's decision count, and lists the four routes in plain language. Before, the panel showed only that sentence above an empty scroll region, and the owner found that confusing. Requirement 20.2's helper text "Switch to Headroom to dispatch storage" is unchanged.
  4. The route chip and the route sentences live in `RouteChip.tsx`, shared by the Decision_Log and the Guide. Figures that are not in the Scenario_File are copied from `sim/config.py` with a comment naming each constant: restart penalty 5 min, move fee $75, move delay 30 min, demand charge $18/kW, carbon price $50/t.
  5. **Layout at 1920×1080.** To make room, the Zoom_Panel plot is 180 px (was 240), the Decision_Log's xl minimum is 9rem (was 12rem), the KPI cards lose their extra xl vertical padding (the values stay 48 px), and the main grid gap is 12 px at xl. With the guide shown, Baseline and Headroom fit without scrolling. After the DR event fires, the KPI DR strip adds 46 px, so the bottom of the Decision_Log scrolls about 45 px. The header, KPI row, Main_Chart, DR button and footer all stay visible, so Requirement 22.9 holds. With the guide hidden, every state fits as before. At 390 px there is no horizontal scroll (Requirement 22.10).
  6. **Browser caching.** `BucketDeployment` now sets `Cache-Control: no-cache` on every uploaded file. Before, the objects had no Cache-Control header, so browsers cached `index.html` heuristically and could keep showing an earlier build after a deploy (the owner saw the pre-task-14.2 DR placeholder). Browsers now revalidate on each load and get a 304 when nothing has changed. The trade-off is that a reload with no network now fails instead of showing a cached copy. The page still makes no network calls after its first load.
- **Rationale:** The page will be shown to people who have not read the brief. Without the guide, it opened on Baseline with $0, 0 kW and 0.0 t, an empty decision log, and a disabled button beside an empty dashed box, and nothing said what any of it meant. The alternatives were a separate About page, which Requirement 22.8 rules out (one page, no in-app links), and a modal walkthrough, which hides the data being explained. The Guide is collapsible so it costs presenters nothing once the audience knows the story. This is a post-freeze change made at the owner's request.
- **Affected component:** `web/src/components/Guide.tsx` (new), `web/src/components/RouteChip.tsx` (new), `web/src/App.tsx`, `web/src/components/{Header,KpiRow,MainChart,DrPanel,ZoomPanel,DecisionLog}.tsx`, `web/src/lib/kpis.ts`, `web/src/lib/colors.ts` (`TEXT_PAIRS`), `infra/lib/site-stack.ts`.

### 2026-10-28 11:14 MT: Optional tasks 9.7, 11.2, 11.10 and 12.8, and the web test runner

- **Decision:**
  1. **11.2, `sim/tests/test_types_mirror.py`.** The test parses every `export interface` in `web/src/types.ts` and requires the same interface names and field sets as `contract.INTERFACE_KEYS`. It refuses `extends`, because the parser would not see inherited fields. To tie `INTERFACE_KEYS` to what `validate` enforces, it deletes each listed key from one instance of each interface in the committed `peak.json` and expects a "missing key" error. It also adds an unlisted key and expects "unexpected key". Before this test existed, the header comment in `types.ts` named it as the guard, but nothing checked.
  2. **9.7, `sim/tests/test_determinism.py`.** Hypothesis properties check that `jobs.generate` and `site.idle_capacity_kw` give equal results twice for any seed (0 to 2^32 − 1), any length and either scenario. `build_scenario` gives the same `dumps` output, or the same error, twice for any seed. The full-build property runs 10 examples instead of the profile's 100, because each build takes about 0.3 s. Two checks go beyond the property:
     - Builds in two subprocesses with `PYTHONHASHSEED` 0 and 4242 must match. Set and dict iteration order can differ between processes but never within one, so the existing in-process acceptance test cannot catch that.
     - The committed `web/public/scenarios/*.json` must equal a fresh build from `data/raw` once `meta.built_at` is fixed. `deploy.ps1 -SkipSim` ships the committed files without rebuilding, so this is the check that they are current. It makes the existing convention a test failure: a commit that changes simulation output must include the rebuilt scenario files. CI runs Python 3.11 and these files were built on 3.12. Python 3.12 changed float `sum()` to compensated summation, so a rebuild with naive left-to-right `sum` was compared to the committed files, and both scenarios were byte-identical, because the contract rounds every value. The suite now takes about 22 s (was 11 s).
  3. **Web test runner.** `vitest` 4.1.12 and `fast-check` 4.10.2 are added as exact-pinned devDependencies, with `"test": "vitest --run"`. vitest 5 needs Node 22 or later, but CI pins Node `^20.19.0`, and moving CI's Node version is a separate open item. 4.1.11 (published 2026-08-18) was tried first, but `npm audit` reported two moderate advisories in `@vitest/mocker` that 4.1.12 fixes. 4.1.12 was chosen even though it was published only 13 days earlier. Test files live next to their modules under `src/`, so `tsc -b` type-checks them in the build; they are not in the bundle. CI runs `npm test` after the web build. fast-check is installed as task 11.6 asks, but no property test uses it yet. 11.6 stays open because its loader tests are not written.
  4. **11.10, `web/src/lib/colors.test.ts`.** The test checks `contrastRatio` against WCAG reference values and requires every `TEXT_PAIRS` entry to reach 4.5:1. It also checks that every route colour is listed on both backgrounds. The guide's link hover pair (sky-200 on slate-900) was added to `TEXT_PAIRS`.
  5. **12.8, `web/src/lib/kpis.test.ts`.** This covers the spec's three cases: Baseline shows zeros and 100% in every DR phase, Headroom shows the formatted metrics, and the DR strip appears only when DR is `done`. It also covers the CO₂ note in every state (Requirement 24.3), the Baseline reference-point notes from the 09:51 entry, and a negative saving.
- **Rationale:** These four tasks guard what changed today or what the handoff relies on: the TypeScript and Python contract agreement, today's KPI and palette changes, and the "same seed and cache give the same files" guarantee that `-SkipSim` deploys depend on. Each guard was seen to fail on purpose. A renamed field and an extra field in a copy of `types.ts` were reported by name, and a $0.01 change to a copy of `peak.json` failed the committed-file check.
- **Affected component:** `sim/tests/test_types_mirror.py`, `sim/tests/test_determinism.py`, `web/src/lib/colors.test.ts`, `web/src/lib/kpis.test.ts`, `web/src/lib/colors.ts` (`TEXT_PAIRS`), `web/package.json`, `web/package-lock.json`, `.github/workflows/ci.yml`, `.kiro/specs/headroom/tasks.md`.

### 2026-10-28 11:34 MT: Spell out "demand response" in the UI and explain it (owner request)

- **Decision:**
  1. No visible text says "DR" any more. The KPI result strip reads "Demand-response cut requested", "Cut delivered" and "Seconds to full response" (was "DR requested shed", "DR delivered"); Requirement 20.5 fixes the values, not the labels. The Decision_Log caption after the event reads "Demand-response decisions first." The built bundle and both Scenario_Files were checked for a stray "DR".
  2. The Reason_String suffix in `sim/scheduler.py` is now " — demand-response event, grid cap N kW" (was " — DR event, cap N kW"). Requirement 13.6 asks only that the reason name the event as the cause and state the Grid_Import_Cap, so the wording was free. This changes 1 reason in `peak.json` and 3 in `sun.json` and nothing else; every metric is unchanged. The scenario files were rebuilt and compared structurally: only those reasons and `meta.built_at` differ. The longest realistic reason in `test_longest_realistic_reason_fits_in_200_chars` is now 198 of the 200 characters Requirement 13.9 allows, so any further text in the reason suffixes needs a shorter phrasing elsewhere.
  3. The term is explained where the event first appears. The DR panel caption opens with a plain definition ("When the grid is strained, the utility asks large customers to use less power for a while") and then states this event's cut, duration and start time. Guide step 3 says the cut is "to ease the grid". "How it works" has a "Demand response" paragraph covering the definition and the three result values. "Shed" is replaced with "cut" in visible text.
  4. To keep the 1920×1080 fit from the 09:51 entry, the DR panel caption no longer has a per-mode sentence. Those sentences moved into the zoom's empty-state text, which already differs by mode. Guide step 3 drops "the battery within 20 s", because the result strip and the zoom show that after firing. With the full step text, the step card grew to three lines and the whole guide got 14 px taller.
- **Rationale:** The owner wants no unexplained abbreviations in front of the demo audience. Code identifiers, comments, config names and build-error messages keep "DR". Nobody in the audience sees them, and renaming them would touch the contract keys (`dr_event`, `dr_delivered_pct`) for no visible gain.
- **Affected component:** `sim/scheduler.py` (`reason_string`), `sim/tests/test_reasons_unit.py`, `sim/tests/test_scheduler_storage_unit.py`, `web/public/scenarios/{peak,sun}.json`, `web/src/lib/kpis.ts`, `web/src/lib/kpis.test.ts`, `web/src/components/{DrPanel,DecisionLog,Guide}.tsx`.
