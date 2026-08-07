from __future__ import annotations

from vestigia.workshop_contract_tools import diagnose_contract


def test_huge_string_constraints_do_not_allocate_huge_examples() -> None:
    result = diagnose_contract(
        {
            "type": "string",
            "minLength": 10_000_000,
            "maxLength": 10_000_001,
        }
    )
    assert result["valid"] is True
    assert result["example_generation_bounded"] is True
    assert result["example_valid"] is None
    assert result["example_invalid"] is None


def test_huge_array_constraints_do_not_allocate_huge_examples() -> None:
    result = diagnose_contract(
        {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 10_000_000,
            "maxItems": 10_000_001,
        }
    )
    assert result["valid"] is True
    assert result["example_generation_bounded"] is True
    assert result["example_valid"] is None
    assert result["example_invalid"] is None
