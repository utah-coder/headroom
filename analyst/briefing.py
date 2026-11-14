"""The operator briefing: Claude summarizes the plan and its risks, and code checks it.

Same rule as the what-if analyst: the model never writes a figure. Code picks every
fact and every piece of evidence first:

* **Facts** come from the engine's published run (``sim/whatif.py``) and the committed
  stress test (``stress.json``), as display text keyed by id. The model writes
  ``{{plan.usd_saved}}`` and code fills it in (``analyst/verify.py``).
* **Evidence** is a short list code chooses: the decisions that saved the most against
  running on the grid, any decision the demand-response event forced, the largest move,
  and the stress tests. The model may cite only those ids, and every point must cite one.
  The page turns each citation into a button that opens the decision or the test.

There is no visitor text in the prompt, so nothing to inject. Inputs are fixed per
deploy, so a verified briefing is cached per scenario for the life of the Lambda
container: repeat requests cost nothing and don't count against the limits.
"""

from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from analyst.llm import Llm, LlmError
from analyst.verify import problems, render
from sim import config, whatif
from sim.run_scenarios import SCENARIOS_DIR
from sim.timeutil import denver, hhmm_denver

__all__ = ["NOTE", "Evidence", "Briefing", "inputs", "schema", "brief", "engine_only"]

STRESS_FILE = SCENARIOS_DIR / "stress.json"
SCENARIO_LABEL = {"peak": "Peak week", "sun": "Sunny day"}
# Shown with every briefing.
NOTE = ("A briefing on one run of a simulated site, replayed on real grid data. Stress ranges show how "
        "sensitive the result is to assumed failures, not how likely they are. CO2 is average intensity, "
        "not marginal.")

_ROUTE_WORDS = {"GRID_NOW": "run from the grid now", "STORAGE_NOW": "run from storage",
                "DEFER": "wait", "MOVE_REGION": "move"}
_DR_MARK = "demand-response event"
_TESTS = ("forecast", "runtime", "battery", "battery_dr", "everything")
# A figure from a test may appear only in a point that cites that test, so it can't drift
# into another context. Longest prefix first: battery_dr before battery.
# Real drafts mixed up the two battery tests, so their names say which is which.
_EVIDENCE_ID = {"forecast": "test:forecast", "runtime": "test:runtime", "battery": "test:battery_random",
                "battery_dr": "test:battery_dr", "everything": "test:everything"}
_NEEDS = (("risk.forecast_", "test:forecast"), ("risk.runtime_", "test:runtime"),
          ("risk.battery_dr_", "test:battery_dr"), ("risk.battery_random_", "test:battery_random"),
          ("risk.everything_", "test:everything"), ("replay.", "test:replay"))
_LATE_HOURS = 8   # sim/stress.py counts an event starting in a window's last 8 hours as late.
_ATTEMPTS = 3  # A verified briefing is cached per scenario, so a third draft is rare and cheap.
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_.]+)\s*\}\}")
_POINTS = (3, 5)
_HEADLINE_MAX = 200
_HEADLINE_WORDS = 25
# "One reason" is ordinary English and the shared verifier allows it, but "one move" is a
# figure: a real draft wrote "moving one training run" for a plan that made five moves.
_COUNTED_ONE = re.compile(
    r"\b(one|a single|single|a few|a couple of|several|a handful of|many)\s+(\w+\s+)?(jobs?|runs?|moves?|regions?|windows?|decisions?|deadlines?|hours?|days?)\b",
    re.IGNORECASE)


@dataclass(frozen=True)
class Evidence:
    id: str              # what the model cites: d1, d2, ..., or test:<family>, or test:replay
    kind: str            # "decision" or "test"
    label: str           # written by code; may hold figures
    key: str | None      # decision: the page's decisionKey ("t|job"); test: the family id


@dataclass(frozen=True)
class Briefing:
    scenario: str
    facts: Mapping[str, str]          # id -> display text, the only figures a reader sees
    evidence: tuple[Evidence, ...]
    meanings: Mapping[str, str]       # id -> what the figure measures, for the model


# --- Inputs -----------------------------------------------------------------------

@cache
def _stress() -> dict[str, Any]:
    return json.loads(Path(STRESS_FILE).read_text(encoding="utf-8"))


def _family(stress: Mapping[str, Any], scenario: str, family: str) -> Mapping[str, Any]:
    return next(f for f in stress["scenarios"][scenario]["families"] if f["id"] == family)


# Formatted exactly as web/src/lib/format.ts shows them, so a figure reads the same in
# the briefing and in the panel it came from. Python rounds halves to even and the page
# rounds them up, which showed 1,452 kW here against 1,453 kW in the stress panel.

def _half_up(v: float, digits: int = 0) -> float:
    scale = 10 ** digits
    return math.floor(round(v * scale, 6) + 0.5) / scale


def _usd(v: float) -> str:
    r = _half_up(v)
    return f"-${abs(r):,.0f}" if r < 0 else f"${r:,.0f}"


def _kw(v: float) -> str:
    return f"{_half_up(v):,.0f} kW"


def _tonnes(v: float) -> str:
    return f"{_half_up(v, 1):,.1f} t"


def _pct(v: float) -> str:
    return f"{math.floor(v):.0f}%"


def _count(v: float) -> str:
    return f"{_half_up(v):,.0f}"


def _runs(count: int, samples: int) -> str:
    return f"{count} of {samples}" if samples > 1 else ("yes" if count else "no")


def _when(t: str) -> str:
    from datetime import datetime

    moment = datetime.fromisoformat(t.replace("Z", "+00:00"))
    return f"{denver(moment):%a} {hhmm_denver(moment)}"


def _target_words(d: Mapping[str, Any]) -> str:
    if d["route"] == "DEFER" and d["target"]:
        return f"wait until {_when(d['target'])}"
    if d["route"] == "MOVE_REGION" and d["target"]:
        return f"move to {d['target']}"
    return _ROUTE_WORDS.get(d["route"], d["route"])


def _option(d: Mapping[str, Any], route: str) -> Mapping[str, Any] | None:
    return next((o for o in d.get("options", ()) if o["route"] == route), None)


def _below_grid(d: Mapping[str, Any]) -> float:
    """How much cheaper the chosen route was than running on the grid now, in route cost."""
    grid, chosen = _option(d, "GRID_NOW"), _option(d, d["route"])
    if grid is None or chosen is None or d["route"] == "GRID_NOW":
        return 0.0
    return grid["total_usd"] - chosen["total_usd"]


def _decision_evidence(decisions: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Up to six decisions worth citing: the biggest savings, the event's, the largest move."""
    by_saving = sorted((d for d in decisions if _below_grid(d) > 0), key=lambda d: -_below_grid(d))
    forced = [d for d in decisions if _DR_MARK in d["reason"]]
    moves = sorted((d for d in decisions if d["route"] == "MOVE_REGION"), key=lambda d: -_below_grid(d))
    picked: list[Mapping[str, Any]] = []
    for d in [*by_saving[:3], *forced[:2], *moves[:1]]:
        if d not in picked:
            picked.append(d)
    return picked[:6]


def _decision_label(d: Mapping[str, Any]) -> str:
    label = f"{_when(d['t'])}, job {d['job']}: {_target_words(d)}"
    saving = _below_grid(d)
    if saving > 0:
        label += f", route cost {_usd(saving)} lower than running on the grid now"
    if _DR_MARK in d["reason"]:
        label += ", forced by the demand-response event"
    return label


def inputs(scenario: str) -> Briefing:
    """Every fact and piece of evidence the briefing may use, all from code."""
    if scenario not in whatif.SCENARIOS:
        raise ValueError(f"unknown scenario: {scenario}")
    run = whatif.current(scenario)
    v, doc = run.values, run.doc
    stress = _stress()
    fam = {f: _family(stress, scenario, f) for f in ("published", *_TESTS, "battery")}
    replay = stress["replay"]
    samples = stress["meta"]["samples"]
    published = fam["published"]

    runs = "stress runs, of all the runs in that test,"
    meanings: dict[str, tuple[str, str]] = {
        "plan.usd_saved": (_usd(v["usd_saved"]),
                           "money the plan saves over this window against running every job on arrival"),
        "plan.headroom_bill_usd": (_usd(v["headroom_bill_usd"]), "the site's bill for this window with the plan"),
        "plan.baseline_bill_usd": (_usd(v["baseline_bill_usd"]), "the bill if every job ran on arrival"),
        "plan.site_peak_kw": (_kw(v["headroom_site_peak_kw"]), "the site's highest grid draw with the plan"),
        "plan.baseline_site_peak_kw": (_kw(v["baseline_site_peak_kw"]),
                                       "the highest grid draw if every job ran on arrival"),
        "plan.headroom_kw": (_kw(v["headroom_kw"]),
                             "grid capacity the plan frees during the grid's busiest hours"),
        "plan.co2_avoided_t": (_tonnes(v["co2_avoided_t"]), "CO2 avoided, average intensity, not marginal"),
        "plan.deadlines_met_pct": (_pct(v["deadlines_met_pct"]), "share of training deadlines met"),
        "plan.inference_sla_pct": (_pct(v["inference_sla_pct"]), "share of time inference is fully served"),
        "plan.dr_delivered_pct": (_pct(v["dr_delivered_pct"]),
                                  "share of the requested demand-response cut the plan delivers"),
        "plan.dr_cut_kw": (_kw(doc["dr_event"]["requested_shed_kw"]),
                           "the cut the utility asks for during its demand-response event"),
        "plan.dr_start": (_when(doc["dr_event"]["start"]), "when the demand-response event starts, site local time"),
        "plan.storage_decisions": (_count(v["storage_decisions"]),
                                   "routing decisions to run a job from on-site storage; decisions, not jobs"),
        "plan.defer_decisions": (_count(v["defer_decisions"]),
                                 "routing decisions to make a job wait for a later hour; decisions, not jobs"),
        "plan.move_decisions": (_count(v["move_decisions"]),
                                "routing decisions to move a job to another region; decisions, not jobs"),
        "plan.demand_charge_saved_usd": (_usd(published["demand_charge_saved_usd"]["p50"]),
                                         "the part of the saving that comes from a lower site peak"),
        "risk.forecast_usd_saved": (_usd(fam["forecast"]["usd_saved"]["p50"]),
                                    "saved when the planner uses a real carbon forecast instead of knowing actual "
                                    "carbon in advance; nothing else changes"),
        "risk.forecast_site_peak_kw": (_kw(fam["forecast"]["site_peak_kw"]["p50"]),
                                       "the site peak with that real carbon forecast"),
        "risk.runtime_low_usd": (_usd(fam["runtime"]["usd_saved"]["p10"]),
                                 "low end of the middle of the saving's spread when jobs run longer or shorter "
                                 "than estimated"),
        "risk.runtime_high_usd": (_usd(fam["runtime"]["usd_saved"]["p90"]), "high end of that spread"),
        "risk.runtime_losing_runs": (_runs(fam["runtime"]["negative_runs"], samples),
                                     f"{runs}, with jobs running long or short, in which the plan cost more than "
                                     "running every job on arrival"),
        "risk.runtime_deadlines_missed_max": (_count(fam["runtime"]["deadlines_missed_max"]),
                                              "the most training deadlines missed in any single one of those runs"),
        "risk.battery_dr_usd_saved": (_usd(fam["battery_dr"]["usd_saved"]["p50"]),
                                      "saved when the battery fails for the whole demand-response event"),
        "risk.battery_dr_shortfall_kw": (_kw(fam["battery_dr"]["dr_shortfall_max_kw"]),
                                         "how far the grid draw goes over the utility's cap when the battery fails "
                                         "during the event"),
        "risk.battery_random_cut_missed_runs": (_runs(fam["battery"]["dr_missed_runs"], samples),
                                         f"{runs}, with the battery failing at a random time, in which the "
                                         "demand-response cut was missed"),
        "risk.everything_low_usd": (_usd(fam["everything"]["usd_saved"]["p10"]),
                                    "low end of the saving's spread with every failure at once"),
        "risk.everything_high_usd": (_usd(fam["everything"]["usd_saved"]["p90"]), "high end of that spread"),
        "risk.everything_losing_runs": (_runs(fam["everything"]["negative_runs"], samples),
                                        f"{runs}, with every failure at once, in which the plan cost more than "
                                        "running every job on arrival"),
        "replay.windows": (_count(len(replay["windows"])),
                           "back-to-back windows as long as this one, June to September, each with its own simulated jobs"),
        "replay.median_usd": (_usd(replay["forecast_usd"]["p50"]),
                              "the median window's saving, with a real carbon forecast"),
        "replay.losing_windows": (_count(replay["forecast_negative"]),
                                  "windows in which the plan cost more than running every job on arrival"),
        "replay.biggest_drop_usd": (_usd(max(0.0, -replay["change_worst_usd"])),
                                    "across the replay, the most one window's saving fell when the planner used a "
                                    "forecast instead of knowing carbon in advance"),
        "replay.biggest_rise_usd": (_usd(max(0.0, replay["change_best_usd"])),
                                    "across the replay, the most one window's saving rose from the same switch"),
        "replay.cut_missed_windows": (_count(replay["forecast_dr_missed"]),
                                      "windows in which the demand-response cut was missed"),
        "replay.late_event_hours": (f"{_LATE_HOURS} hours",
                                    "how close to a window's end an event counts as late"),
        "replay.late_event_windows": (_count(replay["late_event_windows"]),
                                      "windows whose demand-response event starts late, within the final hours "
                                      "that replay.late_event_hours gives"
                                      + ("; every missed cut was in one of these"
                                         if replay["forecast_dr_missed_late"] == replay["forecast_dr_missed"] else "")),
    }
    facts = {k: text for k, (text, _) in meanings.items()}

    evidence = [Evidence(f"d{i}", "decision", _decision_label(d), f"{d['t']}|{d['job']}")
                for i, d in enumerate(_decision_evidence(doc["decisions"]), start=1)]
    evidence += [Evidence(_EVIDENCE_ID[f], "test", fam[f]["label"], f) for f in _TESTS]
    evidence.append(Evidence("test:replay", "test", "Replay of every 48-hour window, June to September", "replay"))
    return Briefing(scenario, facts, tuple(evidence), {k: m for k, (_, m) in meanings.items()})


# --- The model's part -------------------------------------------------------------

def schema(evidence_ids: Sequence[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "headline": {"type": "string"},
            "points": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "evidence": {"type": "array", "items": {"type": "string", "enum": list(evidence_ids)}},
                    },
                    "required": ["text", "evidence"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["headline", "points"],
        "additionalProperties": False,
    }


def _system(b: Briefing) -> str:
    evidence = {e.id: e.label for e in b.evidence}
    return (
        "You write a short operator briefing for a simulated AI data center's plan for the coming window, for the "
        "person running the site. Headroom is the scheduler: each flexible training job runs from the grid, "
        "runs from on-site storage, waits for a later hour, or moves to another region. Inference never moves.\n\n"
        f"Write a headline of at most {_HEADLINE_WORDS} words, then between {_POINTS[0]} and {_POINTS[1]} points: "
        "what the plan does and why it saves money, then the risks the stress test found and what to watch. "
        "One or two sentences each.\n\n"
        "Hard rule on figures: never write a number yourself. No digits, no number words, and no words like "
        "'twice', 'half' or 'double'. Every figure must be a placeholder such as {{plan.usd_saved}}, and it will "
        "be replaced by the fact's exact text, units included, so don't add a unit after it. Write 'CO2' for the "
        "gas. Days and times are facts too: use {{plan.dr_start}}, and never write a day, a time or a duration. "
        "'One' or 'a single' before a job, run, move, region or window is a figure, so don't write it.\n\n"
        "Evidence rule: every point cites at least one evidence id from the list, in its evidence field, and "
        "only ids that support it. Don't mention the ids in the text.\n\n"
        "Use each fact only for what its 'means' says. Don't stretch it: a count of runs isn't a count of jobs, "
        "and a count of decisions isn't a count of jobs.\n\n"
        "Rules:\n"
        "- plan.* facts describe the published run only. Never use them for a stress test or the replay: a "
        "stress test's deadlines, savings and cuts come only from its own risk.* facts.\n"
        "- A risk.* or replay.* figure goes only in a point that cites its test, as the evidence list names it.\n"
        "- plan.* is the published run, where the planner knows actual carbon and exact run times in advance. "
        "risk.* is the stress test: a real carbon forecast, jobs running long or short, a battery failure and "
        "regions refusing jobs. replay.* repeats the plan over every window from June to September.\n"
        "- The stress ranges show sensitivity to assumed failures. Never call them probabilities or likely.\n"
        "- The bill here puts no price on a missed demand-response cut, so the saving doesn't show its cost. Say "
        "that if you mention a missed cut. Never say it doesn't matter, doesn't affect billing, or is free.\n"
        "- The forecast test swaps advance knowledge of carbon for a real forecast. Don't describe it as the forecast "
        "getting worse or carbon rising.\n"
        "- Replay windows each bill a full month's demand charge, so never add them up or call them yearly.\n"
        "- Don't call any option dirtier or better for the grid unless a fact shows it. Don't predict the real world.\n\n"
        f"Scenario: {SCENARIO_LABEL[b.scenario]}.\n\n"
        "Facts:\n" + json.dumps({k: {"text": t, "means": b.meanings[k]} for k, t in b.facts.items()}, indent=0)
        + "\n\n"
        "Evidence:\n" + json.dumps(evidence, indent=0)
    )


def _need(fact_id: str) -> str | None:
    """The test a fact belongs to, by its most specific prefix; None for the published run."""
    return next((need for prefix, need in _NEEDS if fact_id.startswith(prefix)), None)


def _draft_problems(draft: Mapping[str, Any], b: Briefing) -> list[str]:
    """Why this draft can't be shown. Figures are checked across the whole text."""
    headline, points = draft.get("headline"), draft.get("points")
    if not isinstance(headline, str) or not isinstance(points, list):
        return ["The briefing needs a headline and a list of points."]
    found: list[str] = []
    if not headline.strip() or len(headline) > _HEADLINE_MAX or len(headline.split()) > _HEADLINE_WORDS:
        found.append(f"The headline must be one sentence of at most {_HEADLINE_WORDS} words.")
    if not _POINTS[0] <= len(points) <= _POINTS[1]:
        found.append(f"Write between {_POINTS[0]} and {_POINTS[1]} points.")
    known = {e.id for e in b.evidence}
    texts = [headline]
    for i, point in enumerate(points, start=1):
        if not isinstance(point, dict) or not isinstance(point.get("text"), str) \
                or not isinstance(point.get("evidence"), list):
            found.append(f"Point {i} needs text and an evidence list.")
            continue
        texts.append(point["text"])
        if not point["evidence"]:
            found.append(f"Point {i} cites no evidence.")
        unknown = [e for e in point["evidence"] if e not in known]
        if unknown:
            found.append(f"Point {i} cites evidence that doesn't exist: {', '.join(map(str, unknown[:3]))}.")
    found += problems("\n".join(texts), b.facts, max_chars=config.ANALYST_BRIEFING_MAX_CHARS)
    for i, point in enumerate(points, start=1):
        if isinstance(point, dict) and isinstance(point.get("text"), str) and isinstance(point.get("evidence"), list):
            needed = {_need(fact) for fact in _PLACEHOLDER.findall(point["text"])} - {None}
            missing = sorted(needed - set(point["evidence"]))  # type: ignore[operator]
            if missing:
                found.append(f"Point {i} quotes a test's figures without citing it: add {', '.join(missing)}.")
    if any(fact.startswith("risk.") for fact in _PLACEHOLDER.findall(headline)):
        found.append("Keep stress-test figures out of the headline; put them in a point that cites the test.")
    counted = sorted({m.group(0).lower() for t in texts for m in _COUNTED_ONE.finditer(t)})
    if counted:
        found.append("The briefing counts something itself (saw: " + "; ".join(counted[:4]) + "). "
                     "Every figure must be a {{fact.id}}.")
    return found


def _rendered(draft: Mapping[str, Any], b: Briefing) -> tuple[str, list[dict[str, Any]], list[str]]:
    fact_ids = _PLACEHOLDER.findall("\n".join([draft["headline"], *(p["text"] for p in draft["points"])]))
    return render(draft["headline"], b.facts), [
        {"text": render(p["text"], b.facts), "evidence": list(dict.fromkeys(p["evidence"]))}
        for p in draft["points"]
    ], fact_ids


def engine_only(b: Briefing) -> tuple[str, list[dict[str, Any]], list[str]]:
    """The briefing when no model text passes: written here, from the same facts."""
    first = next((e.id for e in b.evidence if e.kind == "decision"), "test:forecast")
    draft = {
        "headline": "The plan saves {{plan.usd_saved}} against running every job on arrival.",
        "points": [
            {"text": "Most of it comes from holding the site peak at {{plan.site_peak_kw}} instead of "
                     "{{plan.baseline_site_peak_kw}}.", "evidence": [first]},
            {"text": "With a real carbon forecast instead of foresight, it saves {{risk.forecast_usd_saved}}.",
             "evidence": ["test:forecast"]},
            {"text": "If the battery fails during the demand-response event, the cut is missed by "
                     "{{risk.battery_dr_shortfall_kw}}.", "evidence": ["test:battery_dr"]},
            {"text": "Across {{replay.windows}} replayed windows the median saving is {{replay.median_usd}}.",
             "evidence": ["test:replay"]},
        ],
    }
    return _rendered(draft, b)


def _payload(b: Briefing, headline: str, points: list[dict[str, Any]], source: str,
             fact_ids: Sequence[str]) -> dict[str, Any]:
    used = {i for p in points for i in p["evidence"]}
    return {
        "status": "briefed",
        "scenario": b.scenario,
        "scenario_label": SCENARIO_LABEL[b.scenario],
        "headline": headline,
        "points": points,
        "evidence": [{"id": e.id, "kind": e.kind, "label": e.label, "key": e.key}
                     for e in b.evidence if e.id in used],
        "source": source,
        "note": NOTE,
        # Every figure the briefing quotes, with what it measures, so a reader can check the prose.
        "figures": [{"id": f, "text": b.facts[f], "means": b.meanings[f]} for f in dict.fromkeys(fact_ids)],
    }


def brief(scenario: str, llm: Llm, *, clock: Callable[[], float] = time.monotonic) -> dict[str, Any]:
    """Write the briefing: up to three drafts, then the code-only briefing."""
    started = clock()
    b = inputs(scenario)
    system = _system(b)
    prompt = "Write the briefing."
    out_schema = schema([e.id for e in b.evidence])
    for attempt in range(_ATTEMPTS):
        left = config.ANALYST_TIME_BUDGET_S - (clock() - started)
        if attempt > 0 and left < config.ANALYST_RETRY_MIN_S_LEFT:
            break
        try:
            draft = llm.structured(system, prompt, out_schema, config.ANALYST_MAX_TOKENS,
                                   timeout_s=max(1.0, min(left, config.ANALYST_CALL_TIMEOUT_S)))
        except LlmError:
            break  # The facts are still good, so the code-only briefing is shown.
        found = _draft_problems(draft, b)
        if not found:
            headline, points, fact_ids = _rendered(draft, b)
            return _payload(b, headline, points, "model_verified", fact_ids)
        prompt = ("Write the briefing.\n\nYour previous draft was rejected:\n"
                  + "\n".join(f"- {p}" for p in found)
                  + "\n\nWrite it again following the hard rule on figures and the evidence rule.")
    headline, points, fact_ids = engine_only(b)
    return _payload(b, headline, points, "engine_only", fact_ids)
