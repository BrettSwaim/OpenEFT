#!/usr/bin/env python3
"""
End-to-end test harness for the OpenEFT pipeline.

Simulates the full browser wizard (step 1 upload -> section -> step 2 EFT
generation) without running the Django server or needing a browser. Runs the
same code paths the web views trigger, so failures surface here rather than
only after a manual click-through.

Usage:
    python scripts/e2e_test.py                          # uses Tony Reese PDF
    python scripts/e2e_test.py path/to/scan.pdf
    python scripts/e2e_test.py path/to/scan.png

Exit code 0 on full-pipeline success, non-zero on any stage failure.
"""

from __future__ import annotations

import os
import sys
import tempfile
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

DEFAULT_FIXTURE = REPO_ROOT / "conversion" / "tests" / "fixtures" / "ricoh_fingerprints.pdf"

# Dummy personal data matching the step 3/4 form fields.
DUMMY_FORM = {
    "fname": "Tony",
    "mname": "NMN",
    "lname": "Reese",
    "alias": "",
    "addr": "990 Valley View Ave Apt 9, Pasadena CA 91107",
    "ssn": "",
    "state": "CA",
    "dob": "1956-12-18",
    "dof": "2026-04-22",
    "sex": "M",
    "race": "W",
    "height": "508",
    "weight": "175",
    "eye": "BRO",
    "hair": "RED",
    "rsn": "Firearms",
    "missing": "",
}

# Approximate FD-258 card corners in 5100x6600 image space. These match the
# Tony Reese fixture closely enough for manual_section to produce usable
# slap regions. Individual scans may need tweaks.
FALLBACK_CORNERS = {
    "p1": "200,250",
    "p2": "4900,250",
    "p3": "4900,5050",
    "p4": "200,5050",
}


def stage(label: str) -> None:
    print(f"\n=== {label} ===", flush=True)


def main() -> int:
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_FIXTURE
    if not source.exists():
        print(f"ERROR: source not found: {source}", file=sys.stderr)
        return 2

    tmp = tempfile.TemporaryDirectory(prefix="openeft_e2e_")
    print(f"TMP_DIR = {tmp.name}")
    os.environ["TMP_DIR"] = tmp.name
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "OpenEFT.settings")

    import django
    django.setup()

    import cv2
    from django.conf import settings
    from conversion.core.pdf_loader import pdf_to_image_bytes
    from conversion.core import core as core_mod

    print(f"settings.TMP_DIR = {settings.TMP_DIR}")
    assert settings.TMP_DIR == tmp.name, "settings.TMP_DIR mismatch"
    print(f"settings.NFSEG_BIN = {settings.NFSEG_BIN}")
    print(f"settings.NFIQ_BIN  = {settings.NFIQ_BIN}")

    raw = source.read_bytes()
    is_pdf = source.suffix.lower() == ".pdf"

    stage(f"Stage 1: ingest ({source.name}, {len(raw)} bytes, is_pdf={is_pdf})")
    input_png = os.path.join(tmp.name, "input.png")
    if is_pdf:
        img_bytes, ext, warning = pdf_to_image_bytes(raw)
        print(f"pdf_to_image_bytes -> ext={ext}, warning={warning!r}, bytes={len(img_bytes)}")
        intermediate = os.path.join(tmp.name, f"input.{ext}")
        with open(intermediate, "wb") as f:
            f.write(img_bytes)
        if ext != "png":
            img = cv2.imread(intermediate)
            if img is None:
                print(f"FAIL: cv2.imread({intermediate}) returned None")
                return 1
            cv2.imwrite(input_png, img)
    else:
        with open(input_png, "wb") as f:
            f.write(raw)

    stage("Stage 2: cv2.imread on input.png")
    img = cv2.imread(input_png)
    if img is None:
        print(f"FAIL: cv2.imread({input_png}) returned None")
        return 1
    print(f"shape={img.shape}, dtype={img.dtype}")

    stage("Stage 3: auto-section (section_fp / GetEFT)")
    out = None
    try:
        out = core_mod.section_fp(fname=input_png)
        print(f"section_fp returned: {out}")
    except Exception as e:
        print(f"section_fp raised {type(e).__name__}: {e}")
        traceback.print_exc()
        out = None

    if not out:
        stage("Stage 3b: manual_section with fallback corners")
        try:
            out = core_mod.manual_section(fname=input_png, data=FALLBACK_CORNERS)
            print(f"manual_section returned: {out}")
        except Exception as e:
            print(f"manual_section raised {type(e).__name__}: {e}")
            traceback.print_exc()
            return 1

    if not out or not core_mod.RESULTS:
        print("FAIL: sectioning produced no RESULTS")
        return 1
    print(f"RESULTS count = {len(core_mod.RESULTS)}")

    stage("Stage 4: generate_eft (runs convert+segment+segmentQuality per fp)")
    try:
        eft_name = core_mod.generate_eft(DUMMY_FORM)
    except Exception as e:
        print(f"generate_eft raised {type(e).__name__}: {e}")
        traceback.print_exc()
        return 1

    eft_path = os.path.join(tmp.name, eft_name)
    if not os.path.exists(eft_path):
        print(f"FAIL: EFT file not written at {eft_path}")
        return 1
    print(f"EFT written: {eft_name} ({os.path.getsize(eft_path)} bytes)")

    stage("ALL STAGES PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
