"""Run teaching and evaluation in separate processes with shared Mubit memory."""
import subprocess
import sys
import uuid
from pathlib import Path


def main():
    experiment = f"oncall-{uuid.uuid4().hex[:12]}"
    script = str(Path(__file__).with_name("demo.py"))
    print(f"On-call triage — experiment: {experiment}", flush=True)
    for phase, title in (
            ("teach", "Run 1: observe incidents, record step outcomes, reflect, then apply + attribute"),
            ("evaluate", "Run 2: compare Memory OFF vs ON on new incidents")):
        print(f"\n{title}\n", flush=True)
        result = subprocess.run([sys.executable, script, phase, "--experiment", experiment])
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
