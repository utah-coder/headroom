# AGENT_BRIEF — Headroom (Option 2)

## Mission
Build **Headroom** from an empty folder to a **public URL on AWS CloudFront** for the Torus $5K bounty at the Hard Tech Week hackathon in Salt Lake City.

- **Today is Tuesday Oct 27, 2026, 9:00 AM MT.**
- **Feature freeze: 10 PM MT tonight.**
- **Demo: Wednesday Oct 28.**

**Track:** Option 2, "Compute Follows the Sun": *build something that decides where and when compute should run.*
**Sponsor's question:** *How would you route the cheapest and cleanest energy?*
**Judged on:** creativity, clarity of insight, execution.
**Rules:** free and open-source data only; simulated data is fine if labeled.

### Principles
- **Deploy first.** A placeholder page goes live on CloudFront in the first 90 minutes. Every later change ships through the same pipeline. Infrastructure risk is retired before lunch.
- **Static and serverless.**
  - S3 + CloudFront, defined in AWS CDK (TypeScript).
  - No Lambda functions of our own, no API Gateway, no database, no Serverless Framework.
  - All numbers are precomputed in Python at build time and shipped as static JSON.
- **Nothing to start before a demo.**
  - The page loads its data once.
  - After that, it makes **zero network calls**, so it keeps working if venue Wi-Fi drops.

## The idea
An AI cluster sits in Salt Lake City (PacifiCorp East, EIA code `PACE`). For every flexible job in every time slot, Headroom decides where its electricity comes from:

1. the grid, now
2. on-site storage (battery + flywheel), now
3. later (defer)
4. another region

Then it shows how much grid capacity that frees up for the utility.

**Headroom** = the gap between what the grid can carry and what's flowing through it. The grid is built for its busiest hour and has the least spare room exactly then.

**Why this framing:** the sponsor, Torus, builds flywheel + battery systems that sit at one site. They control how much power the site draws from the grid and when, and they answer utility demand-response events. Our "where" includes that on-site storage.

Every design choice should serve one story: *the AI site becomes a flexible grid asset instead of a grid problem.*

## The demo the UI must support (3 minutes, from the public URL)
1. **Open on a real hot-week peak in PACE (EIA data).** Show the site's baseline grid draw against the grid's own demand peak.
2. **Toggle the Headroom scheduler on.**
   - The site's grid draw flattens.
   - Training shifts to cheaper and cleaner hours.
   - Inference is untouched.
3. **Click "Fire demand-response event."**
   - Grid draw drops to the requested cap.
   - The flywheel covers the first seconds and the battery holds the event.
   - The decision log shows what each job did.
4. **Watch the KPIs update:**
   - kW of headroom returned
   - $ saved
   - CO₂ avoided
   - inference SLA (100%)
   - training deadlines met (%)
5. **The footer is always visible:** what is real vs simulated, plus a link to the GitHub repo.

## Prerequisites (verify first, Windows + PowerShell)
| Tool | Check |
|---|---|
| Git | `git --version` |
| GitHub CLI, logged in | `gh auth status` |
| Node 20+ | `node -v` |
| Python 3.11+ | `python --version` |
| AWS CLI v2, profile set | `$env:AWS_PROFILE="<profile>"` then `aws sts get-caller-identity` |
| AWS CDK v2 | used via `npx cdk` (no global install needed) |
| EIA API key (free, eia.gov/opendata) | stored in `.env` as `EIA_API_KEY=...` |

- **Default region:** `us-west-2`. It is configurable in `infra/cdk.json` context.
- **If any check fails, stop and report exactly which one.** Don't work around missing credentials.

## Step 0 — Bootstrap the repo (PowerShell)
```powershell
mkdir headroom; cd headroom
git init -b main
# scaffold folders/files per "Repo layout" below, including .gitignore and .env.example
git add -A; git commit -m "chore: initial scaffold"
gh repo create headroom --public --source . --remote origin --push
```

**Secrets**
- `.gitignore` must cover `.env`, `node_modules/`, `cdk.out/`, `web/dist/`, `__pycache__/` and `.venv/`.
- `EIA_API_KEY` is read **only** by `sim/fetch_eia.py` on the local machine.
- It must never appear in the repo, the web bundle, or CDK output.
  - Never prefix it with `VITE_`.
  - Grep `web/dist` for the key before every deploy.

## Repo layout
```
headroom/
  README.md                 # live URL at top, setup + deploy in PowerShell, real-vs-simulated table
  DECISIONS.md              # agent logs every judgment call here
  .gitignore
  .env.example              # EIA_API_KEY=
  .github/workflows/ci.yml  # on push: pytest, web build, cdk synth (no AWS creds, no deploy)
  scripts/
    deploy.ps1              # sim -> web build -> secret grep -> cdk deploy -> print URL
    smoke.ps1               # post-deploy checks against the live URL
  data/raw/                 # cached EIA JSON (committed; public-domain data)
  data/processed/
  sim/
    requirements.txt
    config.py               # ALL constants, each commented REAL or ILLUSTRATIVE
    fetch_eia.py
    carbon.py
    jobs.py
    site.py
    scheduler.py
    metrics.py
    run_scenarios.py        # -> web/public/scenarios/*.json
    tests/
  web/                      # Vite + React + TypeScript + Recharts + Tailwind
    src/types.ts
    public/scenarios/
  infra/                    # AWS CDK v2, TypeScript
    bin/headroom.ts
    lib/site-stack.ts
    cdk.json
    package.json
```

## Step 1 — Infrastructure (`infra/`, CDK v2 TypeScript)
One stack, `HeadroomSiteStack`.

**S3 bucket**
- Private: `BlockPublicAccess.BLOCK_ALL`.
- `enforceSSL: true`, S3-managed encryption.
- `removalPolicy: DESTROY` and `autoDeleteObjects: true` (hackathon project).

**CloudFront distribution**
- Origin: `S3BucketOrigin.withOriginAccessControl(bucket)` from `aws-cdk-lib/aws-cloudfront-origins`. Never use a public bucket or website endpoint.
- `defaultRootObject: 'index.html'`
- `viewerProtocolPolicy: REDIRECT_TO_HTTPS`
- `compress: true`
- `cachePolicy: CACHING_OPTIMIZED`
- `responseHeadersPolicy: SECURITY_HEADERS`
- `priceClass: PRICE_CLASS_100`
- `httpVersion: HTTP2_AND_3`
- `errorResponses`: 403 and 404 → `/index.html` with status 200.

**`BucketDeployment`**
- Source: `../web/dist`.
- `distribution` set, with `distributionPaths: ['/*']` so every deploy invalidates the cache.

**Outputs and tags**
- `CfnOutput` `SiteUrl` = `https://<distributionDomainName>`.
- Tag every resource with `Project=headroom`.

**First-time setup**
- Run once per account and region: `npx cdk bootstrap aws://<account>/us-west-2`.
- If the installed `aws-cdk-lib` API differs from the names above, adapt it and log the change in `DECISIONS.md`.

**No custom domain.** Stretch only, and only if a Route 53 hosted zone already exists. The certificate must be in `us-east-1`.

### `scripts/deploy.ps1` (`$ErrorActionPreference = 'Stop'`)
1. `python -m sim.run_scenarios`, skipped if `-SkipSim` is passed.
2. `npm ci` and `npm run build` in `web/`.
3. Fail if `web/dist` contains the value of `EIA_API_KEY`.
4. `npx cdk deploy --require-approval never` in `infra/`.
5. Print `SiteUrl`, then run `scripts/smoke.ps1`.

### `scripts/smoke.ps1`
- `GET /` returns 200.
- `GET /scenarios/peak.json` returns 200 and parses as JSON.
- The direct S3 object URL returns 403 (the bucket is not public).

### Walking skeleton (due by 10:30 AM)
- A Vite placeholder page reading "Headroom — coming online" is live at the CloudFront URL.
- The repo is on GitHub, CI is green, and the README shows the live URL.

## Real data (EIA-930 via EIA API v2)
- **Region data:** `GET https://api.eia.gov/v2/electricity/rto/region-data/data/`
  - `frequency=hourly`
  - `data[0]=value`
  - `facets[respondent][]=PACE`
  - `facets[type][]=D`, `DF`, `NG`, `TI` (demand, day-ahead demand forecast, net generation, total interchange)
  - sorted by `period` ascending
  - `length=5000`, paginate with `offset`
- **Fuel mix:** `GET https://api.eia.gov/v2/electricity/rto/fuel-type-data/data/`
  - Same pattern as region data.
  - Respondents: `PACE`, plus `BPAT` and `CISO` for the move-region option.
- **Date range:** 2026-06-01 through 2026-09-30.
- **Time zone:** periods are UTC. Convert to America/Denver for display.
- **Unexpected fuel codes:** map known codes (`COL NG NUC OIL OTH SUN WAT WND`). Send unknown codes to "other" and log them.
- **API errors:** if an endpoint or parameter errors, check the EIA API browser (eia.gov/opendata) and fix it. Log the change. Don't guess silently.
- **Caching:** all raw responses go to `data/raw/` and are committed. Builds never need the API once data is cached.

**Pick scenario windows from the data, not by hand:**
- `peak`: the 48 hours around the highest PACE demand hour in the range.
- `sun`: the 48-hour window with the highest solar share in PACE.

**Carbon intensity:** average intensity weighted by the fuel mix. Use IPCC AR5 lifecycle medians (gCO₂eq/kWh):

| Fuel | gCO₂eq/kWh | Source |
|---|---|---|
| Coal | 820 | IPCC |
| Natural gas | 490 | IPCC |
| Biomass | 230 | IPCC |
| Solar | 48 | IPCC |
| Hydro | 24 | IPCC |
| Nuclear | 12 | IPCC |
| Wind | 11 | IPCC |
| Geothermal | 38 | IPCC |
| Oil | 650 | ILLUSTRATIVE |
| Other | 300 | ILLUSTRATIVE |

- The UI must say **"average intensity, not marginal."**

## Site model (SIMULATED — label it everywhere)
All values live in `sim/config.py` and are ILLUSTRATIVE.

**Facility**
- IT peak 10 MW; PUE 1.3 (about 13 MW facility peak).

**Inference**
- 35% of IT load.
- Never deferred or moved.
- Daily shape peaks in the afternoon and evening.

**Training**
- 65% of IT load.
- Synthetic jobs with Poisson arrivals.
- Each job is 50–800 kW and runs 1–12 h.
- Deadline = arrival + duration + slack, with slack of 6–24 h.
- A job can pause and resume at slot boundaries; each restart adds 5 minutes of extra run time.
- Seeded RNG.

**Battery**
- 4 MW / 16 MWh.
- 90% round-trip efficiency.
- State of charge stays between 10% and 95%.

**Flywheel**
- 2 MW / 0.5 MWh.
- 85% round-trip efficiency.
- Instant response; handles event onset and fast ramps.

**Tariff (ILLUSTRATIVE, not Rocky Mountain Power's actual rates)**
- Energy: $0.045/kWh off-peak, $0.085/kWh on-peak (weekdays 3–8 PM MT).
- Demand charge: $18/kW on the monthly peak 15-minute draw.
- Carbon shadow price: $50/t.

**Demand-response event**
- Default: shed 4 MW for 2 hours.
- Grid import cap = baseline draw − requested shed.

**Time steps**
- 15-minute slots for the 48-hour view.
- Event zoom: 1-second resolution for 10 minutes from event start.
  - The flywheel takes the step instantly.
  - The battery ramps to full output in about 20 s.

## Scheduler
There are two policies with the same interface.

**`baseline`**
- Every job runs on arrival from the grid.
- No storage.

**`headroom`**
- Rolling-horizon greedy scheduler with a 24-hour look-ahead.
- Uses the EIA day-ahead forecast (`DF`), carbon intensity and the tariff.
- Each slot, each runnable training job gets one of four routes:
  - `GRID_NOW`
  - `STORAGE_NOW`
  - `DEFER`: move the job to a target slot.
  - `MOVE_REGION`: send to BPAT or CISO. Training jobs only, and **never free**:
    - a fixed $ transfer penalty per job
    - a 30-minute delay
    - simulated idle GPU capacity per region per hour (0–30%); the move fails if there is no capacity.

**Route cost**
- Cost = energy $ + demand-charge risk + carbon × shadow price + deadline-risk penalty.
  - Demand-charge risk = the increase in the monthly peak (kW) × $/kW.
- Take the cheapest route. Fall back to `GRID_NOW` when a deadline is at risk.

**Storage dispatch**
- Charge in cheap, clean slots.
- Discharge to shave the site's peak and during demand-response events.

**Hard constraints**
- Inference is never deferred or moved.
- State of charge stays within bounds.
- No simultaneous charge and discharge.
- Deadlines are met.
- The demand-response cap is respected.

**Every decision emits a reason string**, for example:
`14:15 job#4412 → DEFER to 01:00 — PACE demand 97th pct, 610 g/kWh, would raise monthly peak by 380 kW`

**Transparency beats optimality.** Only switch to a linear program (`pulp`) if greedy is clearly broken.

## Metrics
- **Headroom returned (kW):** baseline peak grid draw − headroom peak grid draw, measured during PACE's top 5% demand hours.
- **$ saved:** energy plus demand charge.
- **CO₂ avoided:** tonnes.
- **Training deadlines met:** %.
- **Inference SLA:** must be 100%.
- **Demand-response event:** shed requested vs delivered; seconds to full response.

**Context line (static, in footer and README)**
- Duke Nicholas Institute, *Rethinking Load Growth* (Feb 2025): the largest US balancing areas could integrate about 76 GW of new load with 0.25% average annual curtailment.
- Present it as planning context, not a claim about this site.

## Output contract
`python -m sim.run_scenarios` writes `web/public/scenarios/peak.json` and `sun.json`:

```json
{
  "meta": { "scenario": "peak", "window": ["...", "..."], "seed": 42, "built_at": "ISO8601",
            "sources": { "grid": "REAL: EIA-930 PACE", "site": "SIMULATED", "jobs": "SIMULATED", "storage": "SIMULATED", "tariff": "ILLUSTRATIVE" },
            "params": {} },
  "grid": [ { "t": "ISO8601", "pace_demand_mw": 0, "pace_forecast_mw": 0, "carbon_g_per_kwh": 0, "price_per_kwh": 0, "stress": false } ],
  "baseline": [ { "t": "...", "grid_kw": 0, "inference_kw": 0, "training_kw": 0 } ],
  "headroom": [ { "t": "...", "grid_kw": 0, "inference_kw": 0, "training_kw": 0, "battery_kw": 0, "flywheel_kw": 0, "soc_pct": 0, "moved_kw": 0 } ],
  "decisions": [ { "t": "...", "job": "4412", "route": "DEFER", "target": "...", "reason": "..." } ],
  "dr_event": { "start": "...", "end": "...", "requested_shed_kw": 4000, "cap_kw": 0,
                "zoom": [ { "s": 0, "grid_kw": 0, "flywheel_kw": 0, "battery_kw": 0 } ] },
  "metrics": { "headroom_kw": 0, "usd_saved": 0, "co2_avoided_t": 0, "deadlines_met_pct": 0, "inference_sla_pct": 100,
               "dr_delivered_pct": 0, "dr_seconds_to_full": 0 }
}
```
Mirror this exactly in `web/src/types.ts`.

## UI (one screen; works at 1920×1080 on stage and on a phone)
- **Data loading:** both scenario files are fetched once on load. After that, no network calls.
- **Header**
  - Title "Headroom" with tagline "Where should this kilowatt-hour come from?"
  - Scenario toggle: Peak week / Sunny day.
  - Baseline / Headroom toggle.
- **KPI row:** the five metrics, in large type.
- **Main chart (48 h)**
  - Baseline site grid draw as a ghost line; Headroom grid draw as a solid line.
  - Stacked supply: grid / battery / flywheel / moved.
  - PACE demand on a secondary axis.
  - Stress hours shaded; carbon intensity as a color band.
- **"Fire demand-response event" button**
  - Animates the 1-second zoom panel.
  - Updates the KPIs.
- **Decision log:** scrolling feed, newest first, color-coded by route.
- **Footer (always visible)**
  - "Grid data: EIA-930, PacifiCorp East (real). Site, jobs, storage, tariff: simulated and illustrative."
  - The Duke context line.
  - GitHub repo link.
  - Build timestamp.
- **Sharing:** `<title>Headroom</title>`, a meta description and Open Graph tags, so the link previews cleanly when shared.
- **Style:** dark theme, high contrast, no secondary pages.
- **No Torus logos or branding.** This is our tool, not their product.

## Milestones (Tuesday, Mountain Time)
| When | Deliverable |
|---|---|
| **9:00–10:30** | Prerequisites checked, repo on GitHub, CDK stack deployed, placeholder page live on CloudFront, CI green |
| **10:30–12:30** | EIA fetch and cache (PACE, BPAT, CISO), carbon series, scenario windows, sanity plot |
| **12:30–3:00** | Jobs, site and storage sim; both policies; reason strings; metrics; JSON; tests pass. **Deploy.** |
| **3:00–7:00** | UI wired to the JSON. **Deploy after each working increment.** |
| **7:00–10:00** | Event zoom, polish, final deploy, smoke test. **FEATURE FREEZE 10 PM** |
| **Wed AM** | Rehearse from the public URL over a phone hotspot. Keep `npm run preview` of the same build as a local fallback. |

After each milestone:
- commit and push
- print a short status: works, broken, next, live URL.

## Acceptance tests
**pytest**
- Inference kW is identical in baseline and headroom in every slot.
- State of charge stays within bounds in every slot; no slot charges and discharges at once.
- Delivered training energy = requested energy + restart penalties. Any job that misses its deadline is flagged.
- During the demand-response event, `grid_kw ≤ cap_kw` at every second.
- **Headroom's peak grid draw during stress hours < baseline's.** If not, the story is broken; fix the scheduler before touching the UI.
- Same seed → byte-identical JSON (ignoring `built_at`).

**Infrastructure**
- `npx cdk synth` succeeds in CI.
- `smoke.ps1` passes against the live URL.
- The secret grep of `web/dist` comes back clean.

## Non-goals
- No Lambda functions or API Gateway of our own.
- No database, no Serverless Framework, no auth.
- No custom domain (stretch only, see above).
- No ML training.
- No runtime calls to the EIA API.
- No multi-page app.
- No marginal-emissions claims.
- No Torus branding.

## Working rules
- Ask only when truly blocked; missing credentials count as blocked. Otherwise decide, log the decision in `DECISIONS.md`, and keep going.
- Every constant goes in `config.py`, commented REAL or ILLUSTRATIVE.
- **If behind schedule, cut in this order:**
  1. GitHub Actions CI (keep a local `cdk synth`)
  2. Sunny-day scenario
  3. `MOVE_REGION`
  4. Animated event zoom (keep a static event chart)
- **Never cut:**
  - the live CloudFront URL
  - real PACE data
  - the baseline vs headroom chart
  - the KPI row
  - the decision log
  - real/simulated labels
