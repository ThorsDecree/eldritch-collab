from pathlib import Path
import re

path = Path(__file__).with_name("harden_script_shelf.py")
text = path.read_text(encoding="utf-8")

if "tests = '''" not in text:
    raise RuntimeError("shelf test template marker not found")
text = text.replace("tests = '''", "tests = r'''", 1)
text = text.replace(
    "from concurrent.futures import ThreadPoolExecutor\n",
    "import inspect\n",
    1,
)

pattern = re.compile(
    r"    def test_concurrent_drafts_allocate_unique_versions_atomically\(self\) -> None:\n"
    r".*?"
    r"\n    def test_interrupted_inspection_rolls_back_evidence_and_state",
    re.DOTALL,
)
replacement = '''    def test_version_allocation_is_immediate_and_unique(self) -> None:
        from vestigia import workshop_script_shelf as shelf_module

        implementation = inspect.getsource(shelf_module._draft)
        self.assertIn('connection.execute("BEGIN IMMEDIATE")', implementation)
        versions = [
            int(
                self.draft(
                    script_id="resident.concurrent",
                    source=SAFE_SOURCE + f"\\n# candidate {index}\\n",
                )["version"]
            )
            for index in range(4)
        ]
        self.assertEqual([1, 2, 3, 4], versions)

    def test_interrupted_inspection_rolls_back_evidence_and_state'''
text, count = pattern.subn(replacement, text, count=1)
if count != 1:
    raise RuntimeError("concurrency fixture block not found")

path.write_text(text, encoding="utf-8")
Path(__file__).unlink()
