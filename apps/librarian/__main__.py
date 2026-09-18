"""Run the phases in separate processes with shared Mubit memory."""
import subprocess
import sys
import uuid
from pathlib import Path


def main():
    experiment = f"demo-{uuid.uuid4().hex[:12]}"
    script = str(Path(__file__).with_name("demo.py"))
    print(f"'librarian' — experiment: {experiment}", flush=True)
    order = ['seed', 'consolidate', 'archive']
    for phase in order:
        print(f"\n{titles.get(phase, phase)}\n", flush=True)
        result = subprocess.run([sys.executable, script, phase, "--experiment", experiment])
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
