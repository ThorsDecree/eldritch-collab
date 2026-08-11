from __future__ import annotations

import re
import secrets
from typing import Any, Callable

from .capabilities import CapabilitySpec, object_schema


MAX_EXPRESSION_CHARS = 200
MAX_TERMS = 24
MAX_DICE_PER_TERM = 100
MAX_TOTAL_DICE = 200
MAX_SIDES = 1_000_000
MAX_MODIFIER = 1_000_000

_TERM_RE = re.compile(
    r"(?P<sign>[+-]?)(?:(?P<count>\d*)[dD](?P<sides>\d+)|(?P<modifier>\d+))"
)


def parse_dice_expression(expression: str) -> dict[str, Any]:
    """Parse a small dice expression without eval or executable syntax.

    Supported examples include ``d20``, ``2d6+3`` and ``1d8+1d6-2``. Terms
    may be added or subtracted. Deliberately unsupported syntax includes
    function calls, multiplication, exploding dice, keep/drop operators, and
    arbitrary Python expressions.
    """

    original = str(expression or "").strip()
    if not original:
        raise ValueError("dice expression must not be empty")
    if len(original) > MAX_EXPRESSION_CHARS:
        raise ValueError(f"dice expression exceeds {MAX_EXPRESSION_CHARS} characters")

    compact = re.sub(r"\s+", "", original)
    if not compact:
        raise ValueError("dice expression must not be empty")

    terms: list[dict[str, Any]] = []
    cursor = 0
    total_dice = 0
    saw_dice = False

    for match in _TERM_RE.finditer(compact):
        if match.start() != cursor:
            raise ValueError(f"unsupported dice syntax near: {compact[cursor:]!r}")
        cursor = match.end()
        if len(terms) >= MAX_TERMS:
            raise ValueError(f"dice expression exceeds {MAX_TERMS} terms")

        sign = -1 if match.group("sign") == "-" else 1
        sides_text = match.group("sides")
        if sides_text is not None:
            count = int(match.group("count") or "1")
            sides = int(sides_text)
            if not 1 <= count <= MAX_DICE_PER_TERM:
                raise ValueError(
                    f"dice count must be between 1 and {MAX_DICE_PER_TERM} per term"
                )
            if not 1 <= sides <= MAX_SIDES:
                raise ValueError(f"die sides must be between 1 and {MAX_SIDES}")
            total_dice += count
            if total_dice > MAX_TOTAL_DICE:
                raise ValueError(
                    f"dice expression exceeds the {MAX_TOTAL_DICE}-die total limit"
                )
            saw_dice = True
            terms.append(
                {
                    "kind": "dice",
                    "sign": sign,
                    "count": count,
                    "sides": sides,
                }
            )
            continue

        modifier = int(match.group("modifier") or "0")
        if modifier > MAX_MODIFIER:
            raise ValueError(f"modifier magnitude exceeds {MAX_MODIFIER}")
        terms.append({"kind": "modifier", "sign": sign, "value": modifier})

    if cursor != len(compact):
        raise ValueError(f"unsupported dice syntax near: {compact[cursor:]!r}")
    if not terms or not saw_dice:
        raise ValueError("dice expression must include at least one die term")

    normalized_parts: list[str] = []
    for index, term in enumerate(terms):
        sign = "-" if term["sign"] < 0 else ("+" if index else "")
        if term["kind"] == "dice":
            count = "" if term["count"] == 1 else str(term["count"])
            body = f"{count}d{term['sides']}"
        else:
            body = str(term["value"])
        normalized_parts.append(sign + body)

    return {
        "original": original,
        "normalized": "".join(normalized_parts),
        "terms": terms,
        "total_dice": total_dice,
    }


def roll_dice_expression(
    expression: str,
    *,
    randbelow: Callable[[int], int] | None = None,
) -> dict[str, Any]:
    parsed = parse_dice_expression(expression)
    draw = randbelow or secrets.randbelow

    rolled_terms: list[dict[str, Any]] = []
    dice_total = 0
    modifier_total = 0
    breakdown_parts: list[str] = []

    for index, term in enumerate(parsed["terms"]):
        sign_value = int(term["sign"])
        sign_text = "-" if sign_value < 0 else ("+" if index else "")
        if term["kind"] == "modifier":
            value = int(term["value"])
            signed = sign_value * value
            modifier_total += signed
            rolled_terms.append(
                {
                    "kind": "modifier",
                    "sign": sign_value,
                    "value": value,
                    "signed_subtotal": signed,
                }
            )
            breakdown_parts.append(f"{sign_text}{value}")
            continue

        count = int(term["count"])
        sides = int(term["sides"])
        values: list[int] = []
        for _ in range(count):
            raw = int(draw(sides))
            if raw < 0 or raw >= sides:
                raise ValueError("random source returned a value outside the requested die range")
            values.append(raw + 1)
        subtotal = sum(values)
        signed = sign_value * subtotal
        dice_total += signed
        rolled_terms.append(
            {
                "kind": "dice",
                "sign": sign_value,
                "count": count,
                "sides": sides,
                "values": values,
                "subtotal": subtotal,
                "signed_subtotal": signed,
            }
        )
        count_text = "" if count == 1 else str(count)
        breakdown_parts.append(
            f"{sign_text}{count_text}d{sides}[{','.join(str(value) for value in values)}]"
        )

    total = dice_total + modifier_total
    return {
        "expression": parsed["original"],
        "normalized": parsed["normalized"],
        "terms": rolled_terms,
        "total_dice": parsed["total_dice"],
        "dice_total": dice_total,
        "modifier_total": modifier_total,
        "total": total,
        "breakdown": " ".join(breakdown_parts) + f" = {total}",
        "randomness": "local_os_csprng",
    }


def _handle_roll(
    _house: Any,
    payload: dict[str, Any],
    _context: dict[str, Any],
) -> dict[str, Any]:
    result = roll_dice_expression(str(payload.get("expression") or ""))
    label = str(payload.get("label") or "").strip()
    if label:
        result["label"] = label
    result.update(
        {
            "outward_effect": "none",
            "memory_promotion": False,
            "identity_effect": False,
        }
    )
    return result


def _register(house: Any) -> None:
    after = {"type": "string", "enum": ["continue", "finish"]}
    house.registry.register(
        CapabilitySpec(
            name="dice.roll",
            description=(
                "Roll a bounded local dice expression such as d20, 2d6+3, or "
                "1d8+1d6-2. The parser never evaluates code and the result has no "
                "outward, memory, or identity effect."
            ),
            effects=("entropy:local_os",),
            cost_class="free",
            confirmation="none",
            default_after="continue",
            result_visibility="resident_private",
            group="play",
            input_schema=object_schema(
                {
                    "action": {"type": "string", "const": "dice.roll"},
                    "expression": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": MAX_EXPRESSION_CHARS,
                    },
                    "label": {"type": "string", "maxLength": 120},
                    "after": after,
                },
                required=("action", "expression"),
            ),
            example_envelopes=(
                {
                    "action": "dice.roll",
                    "expression": "1d20+7",
                    "label": "Spot check",
                    "after": "continue",
                },
                {
                    "action": "dice.roll",
                    "expression": "2d6+3",
                    "label": "damage",
                    "after": "continue",
                },
            ),
            next_step=(
                "Use the returned total and per-die breakdown in the current private turn. "
                "Rolling does not write memory or send anything outward."
            ),
        ),
        lambda payload, context: _handle_roll(house, payload, context),
    )


def register_composition() -> None:
    from .composition import register_capability_installer

    register_capability_installer("gaming.dice", _register, order=70)
