import os
import subprocess
import sys


def run_command(cmd: list[str]) -> str:
    # An argument list with no shell: the diff is untrusted PR content and must never be interpreted by a shell.
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error running: {' '.join(cmd[:3])}\n{result.stderr}", file=sys.stderr)
        return ""
    return result.stdout.strip()


def run_agent(spec: str, prompt: str) -> str:
    return run_command(["omnigent", "run", spec, "-p", prompt])


def main() -> None:
    diff_file = "pr.diff"
    if not os.path.exists(diff_file) or os.path.getsize(diff_file) == 0:
        print("Empty or missing PR diff. Skipping deep review.")
        sys.exit(0)

    with open(diff_file) as f:
        diff_text = f.read()

    print("Running Gemini Context Audit...")
    gemini_prompt = f"Examine this PR diff and related repo files for system-wide breaking changes:\n\n{diff_text}"
    gemini_out = run_agent(".omnigent/review/gemini_context.yaml", gemini_prompt)

    print("Running Claude Logic Audit...")
    claude_prompt = f"Examine this PR diff for critical bugs, race conditions, and logic errors:\n\n{diff_text}"
    claude_out = run_agent(".omnigent/review/claude_logic.yaml", claude_prompt)

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
    summary = run_agent(".omnigent/review/claude_logic.yaml", synthesis_input)

    with open("review_comment.md", "w") as f:
        f.write("## 🏛️ Deep Multi-Agent Review (Gemini + Claude via Omnigent)\n\n")
        f.write(summary)

    print("Review output written to review_comment.md")


if __name__ == "__main__":
    main()
