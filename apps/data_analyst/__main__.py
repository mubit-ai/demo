"""Run the phases in separate processes with shared Mubit memory."""
import subprocess
import sys
import uuid
from pathlib import Path


def main():
    experiment = "demo-" + uuid.uuid4().hex[:12]
    script = str(Path(__file__).with_name("demo.py"))
    print(f"experiment: {experiment}", flush=True)
    for phase in ['seed', 'migrate', 'evaluate']:
        result = subprocess.run([sys.executable, script, phase, "--experiment", experiment])
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
