from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from vestigia.config import load_config
from vestigia.db import ContinuityDB
from vestigia.gaming_tools import parse_dice_expression, roll_dice_expression
from vestigia.home import initialize_home
from vestigia.house_tools import HousePort


class GamingToolTests(unittest.TestCase):
    def test_parse_dice_expression_normalizes_common_dnd_forms(self) -> None:
        parsed = parse_dice_expression(" 2D6 + d4 - 3 ")
        self.assertEqual("2d6+d4-3", parsed["normalized"])
        self.assertEqual(3, parsed["total_dice"])
        self.assertEqual(["dice", "dice", "modifier"], [term["kind"] for term in parsed["terms"]])

    def test_roll_dice_expression_returns_per_die_breakdown(self) -> None:
        draws = iter([0, 5])
        rolled = roll_dice_expression("2d6+3", randbelow=lambda _sides: next(draws))
        self.assertEqual([1, 6], rolled["terms"][0]["values"])
        self.assertEqual(7, rolled["dice_total"])
        self.assertEqual(3, rolled["modifier_total"])
        self.assertEqual(10, rolled["total"])
        self.assertEqual("local_os_csprng", rolled["randomness"])

    def test_dice_parser_rejects_code_and_unbounded_rolls(self) -> None:
        for expression in ("__import__('os')", "1d6*2", "201d6", "14"):
            with self.subTest(expression=expression):
                with self.assertRaises(ValueError):
                    parse_dice_expression(expression)

    def test_dice_roll_is_installed_as_a_resident_capability(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = initialize_home(Path(temp) / "home", name="Test Resident", glyph="🎲")
            config = load_config(home)
            db = ContinuityDB(home / "memory" / "continuity.db")
            port = HousePort(config, db)

            result, spec, after = port.registry.dispatch(
                {"action": "dice.roll", "expression": "d1+3", "label": "certainty"},
                turn_id="turn-dice-test",
                context={"interface": "cli"},
            )
            self.assertEqual("dice.roll", spec.name)
            self.assertEqual("continue", after)
            self.assertEqual(4, result["total"])
            self.assertEqual("certainty", result["label"])
            self.assertEqual("none", result["outward_effect"])
            self.assertFalse(result["memory_promotion"])


class HtmlHouseDocumentTests(unittest.TestCase):
    def test_html_is_visible_to_house_search_but_javascript_is_not(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = initialize_home(Path(temp) / "home", name="Test Resident", glyph="🏮")
            workspace = home / "workspace" / "SRD"
            workspace.mkdir(parents=True, exist_ok=True)
            (workspace / "spells.html").write_text(
                "<html><body><h1>Arcane Mark</h1><p>xylophone spell reference</p></body></html>",
                encoding="utf-8",
            )
            (workspace / "scripts.js").write_text(
                "const javascriptonlytoken = 'should stay outside the text lane';",
                encoding="utf-8",
            )

            config = load_config(home)
            db = ContinuityDB(home / "memory" / "continuity.db")
            port = HousePort(config, db)
            port.refresh_index()

            html_result = port._search(
                {"action": "search", "scope": "workspace", "query": "xylophone", "max_results": 10}
            )
            self.assertIn("workspace/SRD/spells.html", str(html_result))

            js_result = port._search(
                {
                    "action": "search",
                    "scope": "workspace",
                    "query": "javascriptonlytoken",
                    "max_results": 10,
                }
            )
            self.assertEqual([], js_result["results"])


if __name__ == "__main__":
    unittest.main()
