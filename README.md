# Headroom

**Live site:** [https://dmpmm19aeecph.cloudfront.net](https://dmpmm19aeecph.cloudfront.net)

Where should this kilowatt-hour come from?

> **Simulation for demonstration and evaluation.** Headroom combines historical grid data with simulated workloads and storage and a published utility tariff. Results are scenario estimates, not forecasts, engineering specifications, utility quotations, or operational instructions. Independently validate inputs and outputs before making financial or operational decisions.
>
> **Not open source.** The source is published so you can read it, and you're welcome to use the demo above. No other permission is granted: running, modifying, redistributing, self-hosting or selling it needs the owner's written permission. See [LICENSE](LICENSE) and [Licence](#licence).

Headroom models a simulated 10 MW AI cluster in PacifiCorp East (EIA code `PACE`). For every flexible training job in every 15-minute slot, it picks one route: run from the grid now, run from on-site storage (battery + flywheel) now, defer to later, or move to another region (`BPAT` or `CISO`). It then shows how much grid capacity the site gives back to the utility during stress hours, plus dollars saved and CO₂ avoided (average intensity, not marginal).

The page's numbers are precomputed in Python from real EIA-930 grid data and a labeled simulated site model, written to static JSON, and served from a private S3 bucket through CloudFront. The page loads its data once. The only network call after that is a question someone sends to the [what-if analyst](#what-if-analyst), and every other panel works without it.

## What-if analyst

Ask "what if the demand charge doubled?" and the real scheduler reruns on the changed inputs. The AI never does the maths:

| Step | Done by | What happens |
|---|---|---|
| 1. Understand | Gemini, through Google's Gemini API | Turns the question into inputs, constrained by a JSON schema. |
| 2. Validate | Code (`sim/whatif.py`) | Rejects anything unknown or out of range before the engine runs. |
| 3. Compute | Code (the same engine) | Builds the scenario with and without the change. With no change, the result equals the committed scenario files. |
| 4. Explain | Gemini, through Google's Gemini API | Describes the result, quoting figures only by id, such as `{{whatif.usd_saved}}`. |
| 5. Verify | Code (`analyst/verify.py`) | Rejects a draft containing any figure of its own, whether digits or words like "twice". It allows one rewrite, then falls back to an answer written by code. |

So every number a reader sees comes from the engine. The check covers figures, not interpretation: a draft can still describe correct figures poorly, so the panel always shows the engine's table beside the prose.

**Limits:** 100 questions a day across the site and 10 an hour per visitor, enforced in DynamoDB before any model call. If the counters can't be reached, questions are refused. A briefing counts as one question. See [Protecting the public endpoint](#protecting-the-public-endpoint) for what these limits do and don't bound.

Every reply also carries the notice above and a `provenance` block: the historical data period, which parts are simulated or illustrative, the seed, the build commit and the model. A saved or forwarded reply says what it is. On the page it's under "About this result".

**Adjustable inputs:** on-peak demand (power) charge, facilities charge, on-peak and off-peak energy prices, carbon price, battery and flywheel power and capacity, demand-response cut, the region-move fee and price, and whether jobs may move at all. Ranges are in `WHATIF_BOUNDS` in `sim/config.py`.

Run it locally against the real Gemini API (each question costs a little). Create a key at https://aistudio.google.com/apikey first:

```powershell
pip install -r analyst\requirements.txt
$env:GEMINI_API_KEY = "<your key>"
python -m analyst.local
npm --prefix web run dev
```

`--model <model-id>` picks another Gemini model. The default is `gemini-4-hydrogen`.

## Operator briefing

"Write the briefing" asks Gemini for a short read on the selected scenario's plan and its risks. It uses the analyst's backend (`POST /api/briefing`) and the same rule: the model never writes a figure.

| Step | Done by | What happens |
|---|---|---|
| 1. Gather | Code (`analyst/briefing.py`) | Picks every fact the briefing may quote, from the engine and `stress.json`, each with what it measures. Picks the evidence too: the decisions that saved the most, any the demand-response event forced, the largest move, and the stress tests. |
| 2. Write | Gemini, through Google's Gemini API | Writes a headline and up to five points, quoting figures only as placeholders and citing evidence only by id. |
| 3. Check | Code | Rejects a figure written directly, a count like "one move", an unknown fact or evidence id, a point with no evidence, and a test's figure in a point that doesn't cite that test. Up to three drafts, then a briefing written by code. |

Each citation is a button: a decision opens in the decision log, and a test scrolls to the stress panel. A "Figures in this briefing" table lists every figure with what it measures. The checks cover figures, not meaning, and Haiku 4.5 still attaches a real figure to the wrong meaning in most briefings, so the table is there to check against.

A verified briefing is cached per scenario for the life of the Lambda container. Its inputs only change with a deploy, so repeats cost nothing and don't count against the limits. A new one counts as one question.

## Stress test

The published scenarios give the planner actual carbon for its whole 24-hour look-ahead and every job's exact run time. The panel "How sturdy are the savings?" shows what happens without that foresight. It's precomputed by `sim/stress.py` and read from `web/public/scenarios/stress.json`.

| Part | Real or assumed | Where |
|---|---|---|
| Carbon forecast and its errors | REAL data, using only hours already past | `sim/forecast.py` |
| The utility's day-ahead demand forecast and its errors | REAL (EIA `DF`) | `sim/forecast.py` |
| Jobs running long or short | ASSUMED: true run times centered 10% over the estimate | `STRESS_RUNTIME_*` in `sim/config.py` |
| Battery failures and regions refusing jobs | ASSUMED: unannounced, 2 to 12 hours | `STRESS_OUTAGE_HOURS` in `sim/config.py` |
| Replay of 57 back-to-back 48-hour windows, June to September | REAL grid, simulated jobs | `build_replay` in `sim/stress.py` |

**How to read it:**
- The spread across runs shows sensitivity to the assumptions above. It isn't the probability of any outcome.
- A missed demand-response cut costs nothing on this bill, because the program's penalties aren't modeled. A battery that sits out the event can show a higher saving, so read savings beside the other columns.
- Each replay window bills a full month's demand charge on its own 48-hour peak, so windows can be compared but shouldn't be added up.

**Not tested yet:** surprise demand-response events, jobs arriving differently than expected, and inference load that differs from its profile.

Rebuild it after any change to the engine, and commit the result (about 2 minutes on 8 cores). CI fails if the file is stale:

```powershell
python -m sim.stress
python -m sim.stress --check
```

## Real vs simulated

| Input | Label | Source / notes |
|---|---|---|
| PACE grid data (demand, day-ahead demand forecast, net generation, interchange, fuel mix) | REAL | EIA-930 hourly data via EIA API v2 (`electricity/rto/region-data` and `electricity/rto/fuel-type-data`), 2026-06-01 to 2026-09-30 UTC. The fuel mix gives hourly carbon intensity (average intensity, not marginal). |
| BPAT and CISO grid data (fuel mix) | REAL | EIA-930 hourly data via EIA API v2 (`electricity/rto/fuel-type-data`), same date range. Used for alternate-region carbon intensity (average intensity, not marginal). |
| Emission_Factor_Table (gCO₂eq/kWh per fuel) | REAL | IPCC AR5 lifecycle medians: Coal 820, Natural gas 490, Biomass 230, Solar 48, Hydro 24, Nuclear 12, Wind 11, Geothermal 38. Used only for average intensity, not marginal. See note 1 for Oil and Other. |
| Site_Model (10 MW IT peak, PUE 1.3, inference load profile) | SIMULATED | Generated by `sim/site.py` from constants in `sim/config.py`. |
| Training_Jobs | SIMULATED | Seeded synthetic power profiles from `sim/jobs.py` (power, duration, arrival, deadline). No ML training runs. |
| Battery and Flywheel | SIMULATED | Battery 4 MW / 16 MWh, 90% round-trip; flywheel 2 MW / 0.5 MWh, 85% round-trip. |
| Idle_Capacity (spare GPU capacity in BPAT and CISO) | SIMULATED | 0% to 30% per region per hour; a move fails when there is no capacity. |
| Tariff | REAL | Rocky Mountain Power, Utah Electric Service Schedule No. 9 (General Service – High Voltage, 46 kV and above), P.S.C.U. No. 52: Sheets 9.1–9.2 effective 2026-08-10 (Docket 26-035-T08), time periods Sheet 9.3. June–September: energy 5.5841¢/kWh on-peak and 2.8383¢/kWh off-peak; power charge $15.54/kW on the highest on-peak 15-minute draw; facilities charge $2.47/kW on the highest 15-minute draw at any time. On-peak is 15:00 to 22:00 America/Denver, Monday–Friday except the eight Schedule 9 holidays (October–May rates and hours are in `sim/config.py`). Not modelled, because neither policy changes them: the $399/month customer charge, the Schedule 80 riders and the power-factor adjustment. The 48-hour window's peaks stand in for the month's. The carbon shadow price ($50/t) stays ILLUSTRATIVE. |

Note 1: IPCC AR5 publishes no median for the EIA `OIL` and `OTH` codes, so the table uses Oil 650 and Other 300 gCO₂eq/kWh as assumptions. These two values are labeled ILLUSTRATIVE in `sim/config.py`, and like every other factor they feed average intensity, not marginal.

Every constant lives in `sim/config.py` with its label next to it. Judgment calls made during the build are logged in [`DECISIONS.md`](DECISIONS.md).

## National planning context

Duke Nicholas Institute, *Rethinking Load Growth* (Feb 2025): the largest US balancing areas could integrate about 76 GW of new load with 0.25% average annual curtailment.

This is national planning context from that study. It is not a result or claim about the simulated site in this repo.

## Setup (PowerShell)

### 1. Check prerequisites

Run each check from the repository root. Stop if any check fails and fix that prerequisite before building or deploying.

```powershell
git --version                 # Git installed
gh auth status                # GitHub CLI logged in
node -v                       # Node 20 or later
python --version              # Python 3.11 or later
$env:AWS_PROFILE = '<your-aws-profile>'
aws sts get-caller-identity   # AWS CLI v2; the profile must return an account
(Test-Path .env) -and [bool](Select-String -Path .env -Pattern '^EIA_API_KEY=\S' -Quiet)   # True means EIA_API_KEY is set; prints no value
```

`.env` is git-ignored. Create it from `.env.example` and put your free key from [eia.gov/opendata](https://www.eia.gov/opendata/) after `EIA_API_KEY=`. The key is only needed to refresh the cached EIA responses in `data/raw/`; it never goes into the web bundle.

### 2. Install dependencies

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r sim\requirements.txt
npm ci --prefix web
npm ci --prefix infra
```

### 3. Bootstrap CDK (once per account and region)

Replace `<account>` with the `Account` value from `aws sts get-caller-identity`.

```powershell
cd infra
npx cdk bootstrap aws://<account>/us-west-2
cd ..
```

### 4. Deploy

```powershell
.\scripts\deploy.ps1
```

The script runs, in order: `python -m sim.run_scenarios`, `npm ci` and `npm run build` in `web/`, a check that the EIA key does not appear in `web/dist`, `npx cdk deploy`, and `scripts\smoke.ps1` against the printed `SiteUrl`. It stops at the first failing step and names it.

To reuse the existing scenario files without rerunning the simulation:

```powershell
.\scripts\deploy.ps1 -SkipSim
```

`deploy.ps1` deploys the static site only. It never creates the analyst's backend.

### Run the tests

```powershell
python -m pytest
```

## Deploy from AWS CloudShell

This deploys into whichever account and region CloudShell is open in, including the what-if analyst. It needs only CloudShell: no Docker, no local credentials and no EIA key, because it uses the committed scenario files. You run it by hand, and it asks before creating anything.

```bash
git clone -b <branch> https://github.com/utah-coder/headroom.git ~/headroom
```

```bash
cd ~/headroom && bash scripts/cloudshell-deploy.sh
```

**What it does:** checks Node (installing Node 22 into `~/node` if needed), checks for the Gemini API key, runs `scripts/gemini-probe.sh` to find a Gemini model the key can actually call, builds under `/tmp`, bootstraps CDK if needed, deploys, then runs a smoke test that includes one real question.

**Pick the site's region.** The script deploys to the region CloudShell is open in, and CloudShell may open somewhere else. To set it, run `AWS_REGION=us-west-2 bash scripts/cloudshell-deploy.sh`. Before building anything, `scripts/region-guard.sh` stops the deploy if Headroom already runs in another region of the account, and names that region. A second copy couldn't work anyway: CloudFront names are global, so it fails on a name clash.

**The Gemini API key:** the analyst and the briefing call Google's Gemini API, so they need a key from https://aistudio.google.com/apikey. It lives in the SSM SecureString parameter `/headroom/gemini-api-key`, in the region you deploy to. The first time, the script asks for the key and stores it there, encrypted. The Lambda reads it at run time, so the key is never in the template or the function's configuration. To store or replace it yourself, run this and paste the key when asked:

```bash
read -rs KEY && aws ssm put-parameter --name /headroom/gemini-api-key --type SecureString --overwrite --value "$KEY"; unset KEY
```

Questions typed into the what-if panel are sent to Google, with storage turned off on each request. Usage is billed to the key's Google project, not to AWS.

**Why it probes:** a key can be valid and still not reach a given model. The probe makes small real calls, best model first. If there is no key, or no model answers, the script deploys the static site without the analyst.

**Options:**
- `--no-analyst` deploys only the static site, with no Lambda, DynamoDB or Gemini.
- `--yes` skips the confirmation.

**Time:** allow up to 30 minutes. CloudShell can time out when idle, so press a key now and then. If it disconnects, CloudFormation keeps going: reconnect and rerun the script.

**More options:**
- `--waf` puts AWS WAF in front of `/api/*`, as a second stack in us-east-1. It is paid: see [Costs](#costs).
- `--bot-control`, with `--waf`, adds AWS's Bot Control rule group. It costs more, and it blocks `curl`, so the smoke test skips its real question.

**To change limits or the model,** pass context to `cdk deploy`: `headroom:analystDailyCap`, `headroom:analystHourlyPerVisitor`, `headroom:analystModel`, `headroom:analystEffort` (the model's thinking level), `headroom:analystKeyParameter`, `headroom:analystConcurrency` (default 5, or `none`), `headroom:analystEnabled` (`false` keeps the analyst switched off across deploys), `headroom:waf`, `headroom:wafRateLimit` (default 30 per 5 minutes per IP), `headroom:wafMaxBodyBytes` (default 4096), `headroom:wafBotControl`.

**To remove WAF but keep the site:** deploy again without `--waf`, which detaches the web ACL, then delete the edge stack:

```bash
aws cloudformation delete-stack --region us-east-1 --stack-name HeadroomEdgeStack
```

**To remove it:**

```bash
cd ~/headroom/infra && npx cdk destroy --force -c headroom:region=$AWS_REGION -c headroom:analyst=false
```

The stack doesn't own the Gemini API key, so delete it separately:

```bash
aws ssm delete-parameter --name /headroom/gemini-api-key
```

## Protecting the public endpoint

The analyst is the only part of the site that spends money per request. Its protections, cheapest first:

| Layer | What it does | Where |
|---|---|---|
| Origin access | The function URL needs IAM auth, and only this distribution's CloudFront may sign for it. A direct call gets 403. The smoke test checks this. | `infra/lib/site-stack.ts`, `infra/lib/analyst.ts` |
| AWS WAF (optional, paid) | For `/api/*` only: blocks bodies over 4 kB, more than 30 requests per IP in 5 minutes, and IPs on AWS's reputation list. Bot Control is a further option. The static site is never blocked. | `infra/lib/edge-waf.ts`, `--waf` |
| Request checks | Only `POST` to `/api/whatif` or `/api/briefing`. Bodies over 4096 bytes get 413 before they are decoded or parsed. Questions over 400 characters get 400. | `analyst/handler.py` |
| Switches | `analystEnabled=false` refuses everything. `scripts/analyst-switch.sh off` stops new model calls without a deploy. | below |
| Visitor identity | Only `CloudFront-Viewer-Address`, which CloudFront sets. If it is missing, malformed or repeated, the request joins one shared bucket rather than getting its own allowance. An IPv6 visitor is counted by its /64. `X-Forwarded-For` and similar headers are ignored. | `analyst/limits.py` |
| Counters | Conditional atomic updates in DynamoDB, checked before the model: 10 an hour per visitor and 100 a day for the site. If DynamoDB can't be read or written, the request is refused. | `analyst/limits.py` |
| Concurrency | At most 5 requests run at once (reserved concurrency). | `headroom:analystConcurrency` |
| Model calls | At most 3 per request (what-if: 1 to understand and up to 2 to explain; briefing: up to 3 drafts). Up to 4,000 output tokens each, including thinking. One HTTP attempt per call: the SDK's own retries, up to 5 attempts by default, are off. 25 s per call, 50 s per request, and Lambda stops at 58 s. | `analyst/llm.py`, `sim/config.py` |
| Errors | Replies never include stack traces, keys or exception text. Logs record status, timing, token counts and error types, never questions or IPs. | `analyst/handler.py` |

**What the limits bound, and what they don't:**
- **They are request limits, not a spending limit.** The daily cap bounds model work: at most 100 × 3 = 300 model calls a day, each with up to 4,000 output tokens plus its prompt. What that costs depends on the model's price, which the logs let you work out (`input_tokens`, `output_tokens` and `model_calls` per request).
- **They don't bound AWS charges.** CloudFront, Lambda invocations (refused ones included), DynamoDB reads and writes, CloudWatch Logs and WAF all bill per request, whether or not a question is answered. A flood of refused requests is cheap per request, but not free. WAF and reserved concurrency reduce that, and neither caps it.
- **A budget alert is not enforcement.** AWS Budgets and Google Cloud billing budgets notify you; they don't stop anything. To enforce a cap on the model side, set a request quota on the Gemini API key's Google Cloud project, or revoke the key.

### Emergency switch

```bash
AWS_REGION=us-west-2 bash scripts/analyst-switch.sh off      # or: on, status
```

It writes a control item to the counters table. The Lambda reads it, strongly consistent, before every model call. The static site keeps working, and the panels say the analyst is switched off. A briefing already cached in a warm Lambda can still be served, at no model cost. Blunter options, from the script's header: set the function's reserved concurrency to 0 (this resets on the next deploy), deploy with `-c headroom:analystEnabled=false`, or revoke the Gemini key.

## Costs

Approximate list prices; check current AWS and Google pricing before you rely on them.

| Item | When | Approximate cost |
|---|---|---|
| Gemini API | Each question or new briefing | Per token, billed to the key's Google project. Bounded by the request limits above, not by AWS. |
| Lambda, DynamoDB, CloudWatch Logs | Each `/api/*` request, refused or not | Per request and per GB-second; small at demo traffic. Reserved concurrency itself costs nothing. |
| CloudFront, S3 | Every page view | Per request and per GB transferred. |
| AWS WAF (`--waf`) | Always, once deployed | About $5 a month for the web ACL, $1 a month per rule (3 rules), and $0.60 per million requests inspected. |
| WAF Bot Control (`--bot-control`) | Always, once deployed | About $10 a month more, plus about $1 per million requests it inspects (scoped to `/api/*`). |

WAF and Bot Control are off by default. If you turn them on, check for false positives: a blocked visitor gets the page with the analyst unavailable. WAF's rate rule is approximate and reacts within about a minute, so the DynamoDB counters remain the exact limit.

## Licence

Copyright is held by the repository owner; see [LICENSE](LICENSE). The source is published so it can be read. Using the hosted demo is welcome. Nothing else is granted: running, modifying, redistributing, self-hosting, sublicensing or selling the code needs the owner's written permission. These terms don't technically prevent copying. They also don't remove rights you have under GitHub's Terms of Service, applicable law, or a licence granted separately. Third-party data and dependencies keep their own licences.
