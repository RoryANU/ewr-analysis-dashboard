"""Exercise review prompt transport without calling model providers."""

import hashlib
import json
from pathlib import Path

import pytest

from scripts import run_omnigent_review as review


def test_large_prompt_reaches_cli_intact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Exceed both Linux's single-argument limit and macOS's total exec limit.
    # Unicode and shell syntax must arrive as literal data, without truncation.
    prompt = "diff --git a/example b/example\n" + ("é 🌊 ' \" $(touch SHOULD_NOT_EXIST); `false`\n" * 65536)
    package = tmp_path / "omnigent"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "cli.py").write_text(
        """
import hashlib
import json
import sys

def main():
    assert sys.argv[:4] == ["omnigent", "run", ".omnigent/review/gemini_context", "-p"]
    assert len(sys.argv) == 5
    prompt = sys.argv[4]
    print(json.dumps({"length": len(prompt), "sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest()}))
    return 0
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    output = review.run_agent(".omnigent/review/gemini_context", prompt, attempts=1)

    assert json.loads(output) == {"length": len(prompt), "sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest()}
    assert not (tmp_path / "SHOULD_NOT_EXIST").exists()


@pytest.mark.parametrize("failure", [(1, "", "provider unavailable"), (0, "", "")])
def test_retry_preserves_prompt(monkeypatch: pytest.MonkeyPatch, failure: tuple[int, str, str]) -> None:
    calls: list[tuple[list[str], str | None]] = []
    pauses: list[int] = []
    prompt = "original prompt\n🌊"

    def run_command(cmd: list[str], *, input_text: str | None = None) -> tuple[int, str, str]:
        calls.append((cmd, input_text))
        return failure if len(calls) == 1 else (0, "review findings", "")

    monkeypatch.setattr(review, "run_command", run_command)
    monkeypatch.setattr(review.time, "sleep", pauses.append)

    assert review.run_agent("review-spec", prompt) == "review findings"
    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert calls[0][1] == prompt
    assert prompt not in calls[0][0]
    assert pauses == [30]


def test_failed_review_aborts(monkeypatch: pytest.MonkeyPatch) -> None:
    def run_command(cmd: list[str], *, input_text: str | None = None) -> tuple[int, str, str]:
        return 1, "", "provider unavailable"

    monkeypatch.setattr(review, "run_command", run_command)
    with pytest.raises(SystemExit, match="failed after 1 attempts"):
        review.run_agent("review-spec", "prompt", attempts=1)
