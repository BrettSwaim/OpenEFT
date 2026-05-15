import os
import cv2
import uuid
import base64
import numpy as np
from django.conf import settings
from conversion.core.align import GetEFT, four_point_transform
from conversion.core.fd258_ocr import OCR_LOCATIONS
from conversion.core.fingerprint import Fingerprint
from conversion.core.eft_helper import Type1, Type2, Type14
CWD = os.getcwd()
RESULTS = []
TMP_DIR = settings.TMP_DIR
THREAD=None

def format_date(datestring):
    year, month, day = datestring.split('-')
    return str(year)+str(month)+str(day)

def generate_eft(data):
    global RESULTS
    global THREAD
    t1 = Type1()
    t2 = Type2(0)
    # Pull data from form
    print(data)
    t2.fname = data.get("fname")
    t2.mname = data.get("mname")
    t2.lname = data.get("lname")
    t2.aka = data.get("alias")
    t2.addr = data.get("addr")
    t2.ssn = data.get("ssn")
    t2.stateBorn = data.get("state")
    t2.dob = format_date(data.get("dob"))
    t2.dfp = format_date(data.get("dof"))
    t2.sex = data.get("sex")
    t2.race = data.get("race")
    t2.height = data.get("height")
    t2.weight = data.get("weight")
    t2.eye = data.get("eye")
    t2.hair = data.get("hair")
    t2.rsn = data.get("rsn")
    t2.amp = data.get("missing")
    t2.name = "{}, {} {}".format(t2.lname, t2.fname, t2.mname[0])
    # Attach
    t1.add_record(t2)
    # Generate tx number
    n = "{}-{}-{}-".format(t2.fname, t2.mname, t2.lname) + str(uuid.uuid1())[0:10]
    t1.set_tcn(n)
    # Get a file name
    fname = n+'.eft'
    # Convert the fingerprints
    os.chdir(TMP_DIR)
    for each in RESULTS:
        each.convert()
    os.chdir(CWD)
    # Generate type 14 records
    i = 1 # Create idc char, starts at 1
    for fp in RESULTS:
        t14 = Type14(fp, i)
        t14.fcd = t2.dfp
        t14.build()
        t1.add_record(t14)
        i+=1
    # Generate file
    t1.write_to_file(os.path.join(TMP_DIR, fname))
    # Clear results so it can be reused
    RESULTS=[]
    return fname


def process_fp():
    os.chdir(TMP_DIR)
    for each in RESULTS:
        each.convert()

def section_fp(fname):
    global RESULTS
    # Read image
    img = cv2.imread(fname)
    # Try to auto-align the FD-258 card by detecting its outline. If detection
    # fails (poor contrast, cropped scan, etc.), fall back to the raw image —
    # an HTTP-API caller (eg. BrettSwaim/eforms) can then POST /new/resection
    # with manual corner points to align, or rely on the Claude-vision verifier
    # to detect and request offsets.
    try:
        aligned = GetEFT(img)
    except Exception as e:
        print(f"GetEFT auto-alignment failed ({e!r}); using unaligned image. "
              "Caller should POST /new/resection with manual corner points.")
        aligned = img
    return _section(aligned)

REGION_COLORS = {
    "R_FOUR":    (255,   0,   0),  # blue (OpenCV is BGR)
    "L_FOUR":    (  0,   0, 255),  # red
    "2_THUMBS":  (  0, 255,   0),  # green
}


def render_overlay_b64(aligned_img, locations) -> str:
    """Draw colored bounding boxes on `aligned_img` for each location and
    return the result as a base64-encoded PNG string.
    Each location's `.bbox` is a (x, y, w, h) tuple."""
    overlay = aligned_img.copy()
    if len(overlay.shape) == 2:
        overlay = cv2.cvtColor(overlay, cv2.COLOR_GRAY2BGR)
    for loc in locations:
        x, y, w, h = loc.bbox
        x2, y2 = x + w, y + h
        color = REGION_COLORS.get(loc.id, (255, 255, 255))
        cv2.rectangle(overlay, (x, y), (x2, y2), color, 4)
        cv2.putText(overlay, loc.id, (x, max(20, y - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
    ok, buf = cv2.imencode(".png", overlay)
    if not ok:
        return ""
    return base64.b64encode(buf.tobytes()).decode("ascii")


def render_region_b64(region_path: str) -> str:
    """Read a saved region PNG and return as base64."""
    with open(region_path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def _section(img):
    t = os.path.join(os.getcwd(),'static')
    template = cv2.imread(os.path.join(t,'fd-258.png'))
    img = cv2.resize(img, (template.shape[0], template.shape[1]))
    img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    region_files = []
    for loc in OCR_LOCATIONS:
        region_files.append(loc.id + '.png')
        f = Fingerprint(loc, src_img=img, tmpdir=TMP_DIR)
        RESULTS.append(f)
        # Note: Fingerprint.__init__ already wrote the region PNG via save_image().
        # We do NOT call f.convert() here — that's heavy (wsl opj_compress + nfseg)
        # and generate_eft() calls it later anyway.
    overlay_b64 = render_overlay_b64(img, OCR_LOCATIONS)
    regions_b64 = {
        loc.id: render_region_b64(os.path.join(TMP_DIR, loc.id + '.png'))
        for loc in OCR_LOCATIONS
    }
    return {
        "segments": region_files,
        "overlay_b64": overlay_b64,
        "regions_b64": regions_b64,
    }

def manual_section(fname, data):
    img = cv2.imread(fname)
    p1 = np.array([np.float32(float(x)) for x in data.get("p1").split(',')])
    p2 = np.array([np.float32(float(x)) for x in data.get("p2").split(',')])
    p3 = np.array([np.float32(float(x)) for x in data.get("p3").split(',')])
    p4 = np.array([np.float32(float(x)) for x in data.get("p4").split(',')])
    points = np.zeros((4,2), np.float32)
    points[0] = p1
    points[1] = p2
    points[2] = p3
    points[3] = p4
    aligned = four_point_transform(img, points)
    return _section(aligned)