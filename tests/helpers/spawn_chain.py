"""A starts B, B starts C and exits, A stays: C's parent is gone, so a parent-id walk from A cannot find C."""
import subprocess
import sys
import time

NUL = subprocess.DEVNULL
if len(sys.argv) > 1:  # B
    c = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"], stdin=NUL, stdout=NUL, stderr=NUL)
    print(c.pid, flush=True)
    sys.exit(0)
out = subprocess.run([sys.executable, __file__, "b"], capture_output=True, text=True, stdin=NUL).stdout.strip()
print(f"grandchild={out}=", flush=True)
time.sleep(300)
