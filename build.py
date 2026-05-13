"""One-shot PNG generator for GitHub Actions.

Writes the current U6 departures PNG to ``docs/u6.png`` (or to whatever
path is given as the first CLI arg). Used by the refresh workflow.
"""

import os
import sys
import importlib.util

# Import departures.py without triggering Flask import side effects
HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "departures", os.path.join(HERE, "departures.py"))
dep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dep)

out = sys.argv[1] if len(sys.argv) > 1 else "docs/u6.png"
os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
with open(out, "wb") as fh:
    fh.write(dep.render_png())
print(f"wrote {out}")
