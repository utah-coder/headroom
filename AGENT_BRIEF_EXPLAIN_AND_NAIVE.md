# AGENT_BRIEF — Explainable decisions, then the "Cheapest hour" comparison

Two features for Headroom, in this order. Ship and deploy Feature 1 before starting Feature 2.
Follow HANDOFF.md in full: stage files by name, log every judgment call in DECISIONS.md in the
same commit, keep "Torus" out of the web app, no network calls after page load, all 493 pytest
tests + vitest + build + secret grep + 3-of-3 smoke must pass before each deploy.

Purpose (judged on creativity, clarity of insight, execution):
- Feature 1 lets a judge watch the engine think: click any decision, see how all four routes were priced.
- Feature 2 shows why the obvious fix fails: a scheduler that sends every job to its own cheapest,
  cleanest hour piles them into the same hours and blows up the site's peak. Headroom coordinates.

---

## Feature 1 — Click a decision, see the four prices

### Sim (`sim/scheduler.py`, `sim/contract.py`)
Goal: every Decision carries the priced options that were compared, split into cost components.
**The decision path must not change.** Routing still uses `RouteCandidate.cost` exactly as today.

1. Add a frozen dataclass `OptionCost` with: `route`, `target` (same encoding as `Decision.target`),
   `offered: bool`, and `total_usd, energy_usd, carbon_usd, peak_usd, deadline_usd, storage_usd, fee_usd`.
   - energy_usd: electricity price × kWh (site tariff; `ALT_REGION_PRICE_PER_KWH` for MOVE_REGION)
   - carbon_usd: kWh × carbon g/kWh × `CARBON_SHADOW_USD_PER_T` / 1e6 (PACE, or the target region)
   - peak_usd: `DEMAND_CHARGE_PER_KW` × peak excess kW
   - deadline_usd: `deadline_pen(...)`
   - storage_usd: STORAGE_NOW's first-slot stored-energy cost (`storage_cost_basis / eta + STORAGE_WEAR_USD_PER_KWH`)
   - fee_usd: `MOVE_PENALTY_USD` for MOVE_REGION; DEFER's restart energy goes to energy_usd/carbon_usd at slot t
2. Compute components in a separate helper, not inside `price_run`. `eff_cost` is `unit_cost` remapped
   by the persistence index in `begin_slot` (`self.eff_cost[s:] = self.unit_cost[persist]`, same for
   `alt_eff`). Keep parallel `eff_price` and `eff_carbon_usd` arrays remapped with the same `persist`
   index so `eff_price + eff_carbon_usd == eff_cost`. The helper re-prices a candidate's run profile
   with those two arrays.
3. Assert in the helper that the components sum to `RouteCandidate.cost` within 1e-6. If they don't,
   the split is wrong; fix the split, never the cost.
4. Options per decision: GRID_NOW (always priced; `offered=False` when over the demand-response grid
   budget), STORAGE_NOW if offered, the best DEFER target, the best MOVE_REGION region. Pass them
   from `price_routes` into `_headroom_decision` and onto a new `Decision.options` field (default `()`).
5. Contract: add `options` to each `decisions[]` entry; round money with `ROUND["usd"]`. Validate it
   in `_check_decisions`: the chosen route appears once with `offered=True` and has the lowest total
   among offered options (ties by `ROUTE_ORDER`, same as `_route_key`).
6. Update `web/src/types.ts` (`Decision.options: OptionCost[]`) so `test_types_mirror.py` passes.

Tests: components sum to the total (unrounded) for every option of every decision in both scenarios;
chosen = cheapest offered; every other field of `decisions[]`, and every other key of both scenario
files, is unchanged versus the current build (compare structurally, ignoring `meta.built_at`).

### Web (`DecisionLog.tsx`, new `OptionBreakdown.tsx`, `MainChart.tsx`)
- Each log row becomes a button (`aria-expanded`). Expanding shows one row per route: the route chip,
  a horizontal stacked bar of the six components scaled to the largest offered total, and the total in $.
  The chosen route is marked "chosen". A route that wasn't offered shows "not offered this slot".
- Small legend: Energy, Carbon, Peak charge, Deadline risk, Storage, Fees.
- Expanding a row draws a vertical ReferenceLine on the main chart at that decision's slot.
- Any new text/background pair goes into `TEXT_PAIRS` (colors.test.ts enforces 4.5:1).
- Guide "Engine" card: append "Click any decision in the log to see how each option was priced."
- Log caption (Headroom mode): "Newest first. Click a decision to see how its four options were priced."

Deploy. Then Feature 2.

---

## Feature 2 — "Cheapest hour": the obvious fix, and why it fails

### Gate already run (2026-10-28, seed 42, current cache)
A peak-blind scheduler — each job, on arrival, independently picks the start that minimises its own
price + carbon ($50/t shadow) before its deadline; no storage, no moves, no demand-response response,
no coordination — gives:

| scenario | policy | site window peak | stress-hour peak | cost (energy + demand) | CO2 (no moves) |
|---|---|---|---|---|---|
| peak | baseline | 14,770 kW | 13,594 kW | $295,473 | 297.0 t |
| peak | cheapest hour | **34,396 kW** (Tue 11:00) | 4,703 kW | $647,087 | 275.7 t |
| peak | headroom | 13,299 kW | 7,864 kW | $265,015 | 268.4 t |
| sun | baseline | 14,706 kW | 14,413 kW | $289,671 | 172.8 t |
| sun | cheapest hour | **30,907 kW** (Sat 12:30) | 17,511 kW | $579,716 | 146.4 t |
| sun | headroom | 13,681 kW | 12,298 kW | $269,400 | 168.0 t |

Same scheduler held to today's connection (hard cap = baseline window peak, first-come by arrival,
a job with no on-time start that fits takes the earliest start that fits): **29 of 119** jobs miss
their deadline in peak, **20 of 99** in sun.

**Honesty rule:** cheapest-hour avoids MORE carbon than Headroom on the sunny day, and has a lower
stress-hour peak in peak week. Do not hide or reword that. The story is the site peak, the bill and
the deadlines, not carbon. No copy may say cheapest-hour is dirtier or worse for the grid's stress
hours in peak week.

### Sim
1. `run_naive(inp) -> PolicyResult` in `scheduler.py`, exactly the uncapped definition above
   (tie-break: earliest start; a job that can't finish in the window runs on arrival, like baseline).
   Inference is unchanged in every slot (same rule as Headroom).
2. `naive_capped_missed(inp, cap_kw) -> (missed, total)` with the hard-cap definition above.
3. Contract: new top-level `naive` series (same row shape as `baseline`) and `naive_metrics`:
   `site_peak_kw, stress_peak_kw, usd_saved` (vs baseline; negative here), `co2_avoided_t`,
   `deadlines_met_pct, peak_hour_starts` (jobs starting in the clock hour of the naive peak),
   `capped_missed, capped_total, cap_kw`. Also add `baseline_site_peak_kw` and `headroom_site_peak_kw`
   to `metrics`. Mirror in `types.ts`. Every constant in `config.py` with its REAL/ILLUSTRATIVE label.
4. Story test (like Req 23.6): naive site peak > baseline site peak > headroom site peak in both
   scenarios. If it ever fails, stop and report; don't tune the naive policy to make it pass.
5. Reproduce the table above within rounding before touching the UI.

### Web
- Mode toggle: Baseline | Cheapest hour | Headroom.
- New KPI card in every mode: **Site peak**, note "grid connection the site needs". Baseline shows the
  value; other modes show the value and the change vs baseline.
- Cheapest-hour mode: KPIs from `naive_metrics` (Inference SLA 100%, deadlines 100%); the CO2 note
  stays "average intensity, not marginal" (Req 24.3). Main chart: naive grid draw with baseline faint
  behind it. Demand-response button disabled; helper text "Cheapest hour has no storage and doesn't
  respond to the utility."
- Decision log in cheapest-hour mode (text, no rows):
  "No coordination: each job picked its own cheapest, cleanest start. {peak_hour_starts} jobs started in
  the hour of the new peak. Held to today's {cap_kw} connection instead, {capped_missed} of
  {capped_total} jobs would miss their deadline."
- Chart caption (cheapest-hour mode): "Each job started at its own cheapest, cleanest hour. They chose
  the same few hours, so the site's peak jumps far above today's."
- Guide: four demo steps (grid `sm:grid-cols-4`):
  1. Baseline: today — unchanged
  2. Chase the cheapest hour — "The obvious fix: every job waits for its own cheapest, cleanest hour.
     Nobody coordinates, so they pick the same ones."
  3. Turn on Headroom — "Jobs are priced together, including what they add to the site's peak.
     Inference never moves."
  4. Fire a demand-response event — unchanged text
  `currentStep` follows mode (baseline → 1, naive → 2, headroom idle → 3, headroom fired → 4).
- Plain-terms "Scorecard" card: "We then replay what really happened on the grid and compare three
  versions of the same data center: one that runs every job the moment it arrives, one where every job
  chases its own cheapest hour, and one where Headroom decides." Keep the rest of the card.

### Layout
Six KPI cards must still fit one row at ≥1280 px; at 1920×1080 with the guide hidden, header, KPI row,
chart, DR button and footer stay visible (Req 22.9). Check 390 px for horizontal scroll (Req 22.10).

---

## Done means
- Both features live at https://dmpmm19aeecph.cloudfront.net, smoke 3/3, CI green on the pushed commit.
- DECISIONS.md entries for each feature, including the gate table and the honesty rule.
- A short status: works, broken, next, live URL.
