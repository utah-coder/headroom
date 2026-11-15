"""What every analyst reply carries about its own standing.

A reply can be saved, pasted or forwarded without the page around it, so the
notice and the provenance travel inside the JSON. The page shows the same notice
(``web/src/config.ts``, ``RESPONSIBLE_USE``); a test keeps the two identical.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from sim import config

__all__ = ["NOTICE", "provenance", "build_id"]

NOTICE = (
    "Simulation for demonstration and evaluation. Headroom combines historical grid data with simulated "
    "workloads, storage, and illustrative tariffs. Results are scenario estimates, not forecasts, engineering "
    "specifications, utility quotations, or operational instructions. Independently validate inputs and "
    "outputs before making financial or operational decisions."
)

# Written by scripts/build_analyst.py into the Lambda bundle. Absent in a checkout.
_BUILD_FILE = Path(__file__).with_name("build.json")


@lru_cache(maxsize=1)
def build_id() -> str:
    """The commit the running bundle was built from, or ``"unknown"``."""
    try:
        info = json.loads(_BUILD_FILE.read_text("utf-8"))
        commit = str(info.get("commit") or "unknown")[:40]
        return commit + ("-dirty" if info.get("dirty") else "")
    except (OSError, ValueError, AttributeError):
        return "unknown"


def provenance(model: str | None) -> dict[str, Any]:
    """Where each part of a reply comes from, and what it is not."""
    regions = ", ".join((config.HOME_REGION, *config.ALT_REGIONS))
    return {
        "historical_grid_data": (
            f"EIA-930 hourly data for {regions}, {config.DATA_START[:10]} to {config.DATA_END[:10]} UTC. "
            "Real, but historical: it is replayed, not forecast."
        ),
        "simulated": "The 10 MW site, its training and inference workloads, the battery and flywheel, "
                     "and spare capacity in other regions.",
        "illustrative": "The tariff, the demand charge and the carbon price. Not any utility's actual rates.",
        "outcome": "Scenario estimates from a greedy scheduling heuristic run on these inputs. "
                   "Not forecasts or real-world predictions. Nothing here controls real infrastructure.",
        "seed": config.SEED,
        "build": build_id(),
        "model": model,
    }
