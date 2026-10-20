import os
import subprocess
import sys
import time

# Build the CLI arguments inside the child process: Linux limits each exec argument
# to about 128 KiB, so large diffs (and synthesis inputs) must travel over stdin.
# Use the same entry point as the omnigent console script, installed by CI's pip.
_OMNIGENT_LAUNCHER = """
import sys
from omnigent.cli import main

sys.argv = ["omnigent", "run", sys.argv[1], "-p", sys.stdin.read()]
sys.exit(main())
"""


def run_command(cmd: list[str], *, input_text: str | None = None) -> tuple[int, str, str]:
    # An argument list with no shell: the diff is untrusted PR content and must never be interpreted by a shell.
    result = subprocess.run(cmd, input=input_text, capture_output=True, text=True, encoding="utf-8")
    return result.returncode, result.stdout.strip(), result.stderr


def run_agent(spec: str, prompt: str, attempts: int = 3) -> str:
    # Model providers return transient 503s, so retry with a growing pause. A run that still fails or returns
    # nothing aborts the review, so that no comment is posted with missing findings.
    for attempt in range(1, attempts + 1):
        code, output, stderr = run_command([sys.executable, "-c", _OMNIGENT_LAUNCHER, spec], input_text=prompt)
        if code == 0 and output:
            return output
        print(f"Agent {spec} failed (attempt {attempt}/{attempts}):\n{stderr}", file=sys.stderr)
        if attempt < attempts:
            time.sleep(30 * attempt)
    sys.exit(f"Agent {spec} failed after {attempts} attempts")


def main() -> None:
    diff_file = "pr.diff"
    if not os.path.exists(diff_file) or os.path.getsize(diff_file) == 0:
        print("Empty or missing PR diff. Skipping deep review.")
        sys.exit(0)

    with open(diff_file) as f:
        diff_text = f.read()

    print("Running Gemini Context Audit...")
    gemini_prompt = f"Examine this PR diff and related repo files for system-wide breaking changes:\n\n{diff_text}"
    gemini_out = run_agent(".omnigent/review/gemini_context", gemini_prompt)

    print("Running Claude Logic Audit...")
    claude_prompt = f"Examine this PR diff for critical bugs, race conditions, and logic errors:\n\n{diff_text}"
    claude_out = run_agent(".omnigent/review/claude_logic", claude_prompt)

    # Synthesize findings to prevent redundant comment spam
    print("Synthesizing multi-agent consensus...")
    synthesis_input = f"""
    You are the Lead Staff Engineer conducting a final PR triage.
    Review the findings from the Gemini Context Reviewer and the Claude Logic Reviewer below.

    Gemini Findings:
    {gemini_out}

    Claude Findings:
    {claude_out}

    Your instructions:
    1. Filter out duplicates or conflicting assessments.
    2. Discard false positives or stylistic nitpicks.
    3. Format into a clean, unified Markdown report with three sections:
       - 🚨 **Blockers & Breaking Changes** (if any)
       - ⚠️ **Key Recommendations**
       - ✅ **Architecture Clearance** (summary verdict)
    """

    # Run synthesis through Claude or Gemini
    summary = run_agent(".omnigent/review/claude_logic", synthesis_input)

    with open("review_comment.md", "w") as f:
        f.write("## 🏛️ Deep Multi-Agent Review (Gemini + Claude via Omnigent)\n\n")
        f.write(summary)

    print("Review output written to review_comment.md")


if __name__ == "__main__":
    main()
