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
