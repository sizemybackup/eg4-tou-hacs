"""Find the inverter entities a user already has, so the setup can offer them in a drop-down.

Pure logic (no Home Assistant imports): it is given plain records of the entity registry and returns
the best guess for each role. The guess is only ever a *pre-selection*: the user sees it, can change
it, and nothing is written until they confirm. Entities of this integration are never suggested.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .drivers import NUMBER_DOMAINS, ROLES, TIME_DOMAINS

# Words that make an entity something else (a different setting that merely sounds alike).
_NOT = {"generator", "discharge", "forced", "voltage", "current", "grid", "off", "on", "battery_charge"}


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", text.lower()) if t}


@dataclass(frozen=True)
class Rule:
    domains: tuple[str, ...]
    need: frozenset[str]
    forbid: frozenset[str]
    number: int | None = None   # window number the entity must carry (1 also matches "no number at all")


def _rules() -> dict[str, Rule]:
    rules: dict[str, Rule] = {}
    for kind, words in (("charge", {"ac", "charge"}), ("hold", {"ac", "first"})):
        for n in (1, 2, 3):
            for edge in ("start", "end"):
                forbid = _NOT | {"soc", "power"} | ({"first"} if kind == "charge" else {"charge"})
                rules[f"{kind}_{n}_{edge}"] = Rule(TIME_DOMAINS, frozenset(words | {edge}), frozenset(forbid), n)
    for edge in ("start", "end"):
        rules[f"soc_{edge}"] = Rule(NUMBER_DOMAINS, frozenset({"ac", "charge", edge, "soc"}), frozenset(_NOT - {"battery_charge"}))
    rules["power"] = Rule(NUMBER_DOMAINS, frozenset({"ac", "charge", "power"}), frozenset(_NOT | {"soc", "start", "end"}))
    return rules


RULES = _rules()
assert set(RULES) == set(ROLES), "every role needs a detection rule"


def _matches(rule: Rule, domain: str, tokens: set[str]) -> bool:
    if domain not in rule.domains or not rule.need <= tokens or tokens & rule.forbid:
        return False
    if rule.number is None:
        return True
    digits = {t for t in tokens if t.isdigit()}
    return rule.number in {int(d) for d in digits} or (rule.number == 1 and not digits)


@dataclass(frozen=True)
class Suggestion:
    mapping: dict[str, str]
    source: str            # what the matches belong to (a device name or integration), for the screen's note


def suggest(records: Iterable[Mapping[str, Any]]) -> Suggestion | None:
    """records: dicts with entity_id, platform, device_id, name (any of the entity's names, may be None)
    and disabled (bool). Returns the best group of matches (one device, so two inverters never mix),
    or None when nothing looks like an AC charge / AC First setting."""
    groups: dict[tuple[str, str], dict[str, tuple[int, str]]] = {}
    labels: dict[tuple[str, str], str] = {}
    for rec in records:
        if rec.get("disabled") or rec.get("platform") == "eg4_tou":
            continue
        entity_id = str(rec["entity_id"])
        domain, _, object_id = entity_id.partition(".")
        tokens = _tokens(f"{object_id} {rec.get('name') or ''}")
        key = (str(rec.get("platform") or ""), str(rec.get("device_id") or ""))
        for role, rule in RULES.items():
            if _matches(rule, domain, tokens):
                found = groups.setdefault(key, {})
                score = len(tokens)   # fewer extra words = a closer match
                if role not in found or score < found[role][0]:
                    found[role] = (score, entity_id)
                    labels[key] = str(rec.get("device_name") or rec.get("platform") or "")
    if not groups:
        return None
    key = max(groups, key=lambda k: len(groups[k]))
    best = groups[key]
    if len(best) < 3:    # one stray match is a coincidence, not an inverter
        return None
    return Suggestion({role: eid for role, (_, eid) in best.items()}, labels.get(key) or key[0])
