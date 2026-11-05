"""The rule that keeps invented numbers out of an answer.

A model's draft may not contain a figure at all. It writes ``{{fact.id}}`` where a
figure belongs and code fills it in from the engine's ``Comparison.facts``, so
every digit a reader sees was produced by ``sim/whatif.py``.

``problems`` rejects a draft that names an unknown fact or carries a figure of
its own: any numeric character, or a number written as a word. Words such as
"twice" and "half" are rejected too, because they are figures nobody computed.

This checks figures, not interpretation. A draft could quote correct figures and
still describe them badly, which is why the panel always shows the engine's table
beside the prose.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping

__all__ = ["problems", "render"]

_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_.]+)\s*\}\}")
# "CO2" names a gas; it is the only digit a draft may write itself.
_ALLOWED_DIGIT_WORDS = re.compile(r"\bCO[2₂]\b")
_NUMBER_WORDS = re.compile(
    r"\b("
    r"zero|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|"
    r"twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|"
    r"hundreds?|thousands?|millions?|billions?|trillions?|dozens?|"
    r"half|halved?|halves|halving|thirds?|quarters?|"
    r"twice|thrice|double[sd]?|doubling|triple[sd]?|tripling|quadruple[sd]?|quadrupling|"
    r"percent|percentage"
    r")\b",
    re.IGNORECASE,
)


def problems(draft: str, known_facts: Collection[str], *, max_chars: int) -> list[str]:
    """Why this draft can't be shown. An empty list means it passes."""
    found: list[str] = []
    if not draft.strip():
        return ["The answer is empty."]
    if len(draft) > max_chars:
        found.append(f"The answer is longer than {max_chars} characters.")

    ids = _PLACEHOLDER.findall(draft)
    if not ids:
        found.append("The answer quotes no figures. Use at least one {{fact.id}}.")
    unknown = sorted({i for i in ids if i not in known_facts})
    if unknown:
        found.append("These fact ids do not exist: " + ", ".join(unknown[:5]) + ".")

    prose = _ALLOWED_DIGIT_WORDS.sub("", _PLACEHOLDER.sub("", draft))
    digits = sorted({ch for ch in prose if ch.isnumeric()})
    if digits:
        found.append("The answer writes a figure directly (saw: " + " ".join(digits[:8]) + "). "
                     "Every figure must be a {{fact.id}}.")
    words = sorted({m.group(0).lower() for m in _NUMBER_WORDS.finditer(prose)})
    if words:
        found.append("The answer uses a number word (saw: " + ", ".join(words[:8]) + "). "
                     "Every figure must be a {{fact.id}}.")
    if "{" in prose or "}" in prose:
        found.append("The answer has a malformed placeholder.")
    return found


def render(draft: str, facts: Mapping[str, str]) -> str:
    """Fill the placeholders. Call only on a draft that passed ``problems``."""
    return _PLACEHOLDER.sub(lambda m: facts[m.group(1)], draft)
