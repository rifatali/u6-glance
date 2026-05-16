"""One-shot PNG generator for GitHub Actions.

Writes three PNG files:
  - u6.png         alternates by current time bucket between directions
  - tegel.png      always direction Tegel/Kurt-Schumacher-Platz (2 departures)
  - mariendorf.png always direction Alt-Mariendorf (2 departures)

If users want separate apps per direction (so Glance cycles between them),
they can configure tegel.png + mariendorf.png as zwei custom URLs.
"""

import os
import sys
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "departures", os.path.join(HERE, "departures.py"))
dep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dep)

out_dir = sys.argv[1] if len(sys.argv) > 1 else "docs"
os.makedirs(out_dir, exist_ok=True)

# Three files
with open(os.path.join(out_dir, "u6.png"), "wb") as fh:
    fh.write(dep.render_png())
with open(os.path.join(out_dir, "tegel.png"), "wb") as fh:
    fh.write(dep.render_png_for_direction(north=True))
with open(os.path.join(out_dir, "mariendorf.png"), "wb") as fh:
    fh.write(dep.render_png_for_direction(north=False))
print(f"wrote u6.png, tegel.png, mariendorf.png to {out_dir}")
