"""Run setup, adjudicate, and evaluate in separate processes with shared Mubit memory."""
import subprocess
import sys
import uuid
from pathlib import Path


def main():
    experiment = f"policy-{uuid.uuid4().hex[:12]}"
    script = str(Path(__file__).with_name("demo.py"))
    print(f"Policy analyst — experiment: {experiment}", flush=True)
    for phase, title in (("setup", "Phase 1: write the policy facts (valid-time stamped)"),
                         ("adjudicate", "Phase 2: adjudicate historical claims and close the loop"),
                         ("evaluate", "Phase 3: answer held-out questions, bi-temporal vs current-state-only")):
        print(f"\n{title}\n", flush=True)
        result = subprocess.run([sys.executable, script, phase, "--experiment", experiment])
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
