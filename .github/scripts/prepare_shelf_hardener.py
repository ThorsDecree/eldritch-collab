from pathlib import Path

path = Path(__file__).with_name("harden_script_shelf.py")
text = path.read_text(encoding="utf-8")
old = "tests = '''"
new = "tests = r'''"
if old not in text:
    raise RuntimeError("shelf test template marker not found")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
Path(__file__).unlink()
