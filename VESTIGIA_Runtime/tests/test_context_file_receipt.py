import json
import hashlib

from vestigia.home import initialize_home
from vestigia.models import NormalizedMessage
from vestigia.providers.fake import FakeProvider
from vestigia.runtime import CoreRuntime


def test_file_receipt_is_opt_in_and_only_shown_locally(tmp_path, monkeypatch):
    home = initialize_home(tmp_path / "home", name="Synthetic")
    source = home / "identity" / "breathprint.md"
    source.write_text("SYNTHETIC_ORIENTATION_MARKER", encoding="utf-8")
    provider = FakeProvider()
    runtime = CoreRuntime.from_home(home, provider=provider)
    first = runtime.chat(NormalizedMessage(content="hello", interface="web"))
    receipt = json.loads(first.receipt_path.read_text(encoding="utf-8"))
    assert "local_file_sources" not in receipt
    assert first.context_file_notice is None

    monkeypatch.setenv("VESTIGIA_CONTEXT_FILE_RECEIPT", "true")
    provider = FakeProvider()
    runtime = CoreRuntime.from_home(home, provider=provider)
    result = runtime.chat(NormalizedMessage(content="hello", interface="web"))
    receipt = json.loads(result.receipt_path.read_text(encoding="utf-8"))
    entry = next(x for x in receipt["local_file_sources"]
                 if x["path"] == "identity/breathprint.md")
    assert entry["status"] == "included"
    assert entry["read_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert "SYNTHETIC_ORIENTATION_MARKER" in provider.requests[-1].messages[0]["content"]
    assert "identity/breathprint.md" in result.context_file_notice
    assert "SYNTHETIC_ORIENTATION_MARKER" not in result.context_file_notice
    discord = runtime.chat(NormalizedMessage(content="hello", interface="discord"))
    assert discord.context_file_notice is None
    assert "identity/breathprint.md" not in discord.text


def test_budgeted_file_reports_partial(tmp_path, monkeypatch):
    home = initialize_home(tmp_path / "home", name="Synthetic")
    source = home / "runtime_contract.md"
    source.write_text("synthetic content " * 10000, encoding="utf-8")
    monkeypatch.setenv("VESTIGIA_CONTEXT_FILE_RECEIPT", "true")
    runtime = CoreRuntime.from_home(home, provider=FakeProvider())
    result = runtime.chat(NormalizedMessage(content="hello", interface="cli"))
    receipt = json.loads(result.receipt_path.read_text(encoding="utf-8"))
    entry = next(x for x in receipt["local_file_sources"] if x["path"] == "runtime_contract.md")
    assert entry["status"] == "partial"
