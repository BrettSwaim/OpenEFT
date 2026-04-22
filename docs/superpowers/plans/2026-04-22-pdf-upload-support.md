# PDF Upload Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Accept PDF uploads in OpenEFT's step-1 file picker, preserving the scanner's native resolution so the existing fingerprint sectioning pipeline works unchanged.

**Architecture:** A single Django-independent module (`conversion/core/pdf_loader.py`) converts a PDF's first page to image bytes. The step-1 view detects the `.pdf` extension and routes through this module before writing the conventional `input.png` the rest of the pipeline expects. Multi-page PDFs trigger a non-blocking warning banner; a single embedded raster is extracted natively, otherwise the page is rendered at 600 DPI.

**Tech Stack:** Python 3.13, Django 4.1, OpenCV, PyMuPDF (`pymupdf` / `fitz`). Tests use the Python standard `unittest` module (no Django test harness — the loader is pure-Python).

**Reference spec:** `docs/superpowers/specs/2026-04-22-pdf-upload-support-design.md`

---

## File Structure

**Created files**
- `conversion/core/pdf_loader.py` — single public function `pdf_to_image_bytes(pdf_bytes)`.
- `conversion/tests/__init__.py` — makes `tests/` a package (replaces the current stub `conversion/tests.py`).
- `conversion/tests/test_pdf_loader.py` — unit tests for the loader. Uses `unittest` directly so Django's import-time network fetches (`OpenEFT/settings.py:141-159`) don't run.
- `conversion/tests/fixtures/__init__.py` — empty, so the directory ships via Python packaging if needed.
- `conversion/tests/fixtures/ricoh_duplex.pdf` — the real 2-page Ricoh MP C4504ex scan (owner has opted out of redaction).

**Modified files**
- `requirements.txt` — add `pymupdf`.
- `conversion/views.py` — branch on `.pdf` extension in `step1()`; include `warning` key in the JSON response.
- `conversion/templates/conversion/partials/step1.html` — broaden `accept` attribute; handle PDF selection in `readURL()` without attempting a raster preview.
- `conversion/templates/conversion/new.html` — render a dismissible Bootstrap alert in `showFingerprints()` when `json.warning` is present.

**Deleted files**
- `conversion/tests.py` — replaced by the `tests/` package. Current content is just `from django.test import TestCase` with no test cases.

---

## Task 1: Add PyMuPDF, set up tests package, install fixture

**Files:**
- Modify: `requirements.txt`
- Delete: `conversion/tests.py`
- Create: `conversion/tests/__init__.py`
- Create: `conversion/tests/fixtures/__init__.py`
- Create: `conversion/tests/fixtures/ricoh_duplex.pdf`

- [ ] **Step 1: Add PyMuPDF to requirements.txt**

Append one line at the end of `requirements.txt` (no version pin, matching `pillow` on line 14):

```
pymupdf
```

- [ ] **Step 2: Install the new dependency into the active Python environment**

Run: `pip install pymupdf`
Expected: `Successfully installed pymupdf-<version>` (PyMuPDF wheels are pre-built; no compilation).

- [ ] **Step 3: Verify import works**

Run: `python -c "import fitz; print(fitz.__version__)"`
Expected: a version string (e.g., `1.24.0`), no traceback.

- [ ] **Step 4: Convert the test stub into a package**

Delete `conversion/tests.py`. Create the directory `conversion/tests/` with an empty `__init__.py`:

```bash
rm conversion/tests.py
mkdir -p conversion/tests/fixtures
touch conversion/tests/__init__.py
touch conversion/tests/fixtures/__init__.py
```

On Windows bash (this repo's shell), use forward slashes and `rm` / `mkdir -p` as shown.

- [ ] **Step 5: Install the real Ricoh scan as a test fixture**

Copy the source PDF into the fixtures directory:

```bash
cp "/d/Downloads/20260422123323640.pdf" "conversion/tests/fixtures/ricoh_duplex.pdf"
```

(On native Windows paths: `D:\Downloads\20260422123323640.pdf` → `conversion/tests/fixtures/ricoh_duplex.pdf`.)

- [ ] **Step 6: Verify fixture integrity**

Run:

```bash
python -c "import fitz; d = fitz.open('conversion/tests/fixtures/ricoh_duplex.pdf'); print(f'pages={len(d)}'); imgs = d[0].get_images(); print(f'page0_images={len(imgs)}')"
```

Expected output:
```
pages=2
page0_images=1
```

- [ ] **Step 7: Commit**

```bash
git add requirements.txt conversion/tests/__init__.py conversion/tests/fixtures/__init__.py conversion/tests/fixtures/ricoh_duplex.pdf
git rm conversion/tests.py
git commit -m "Add PyMuPDF dependency and test fixtures scaffolding"
```

---

## Task 2: `pdf_loader.py` — multi-page detection and native extraction

**Files:**
- Test: `conversion/tests/test_pdf_loader.py`
- Create: `conversion/core/pdf_loader.py`

This task uses TDD. Each sub-step adds one failing test, then the minimal code to pass it.

- [ ] **Step 1: Write the failing test for native extraction on the real scan**

Create `conversion/tests/test_pdf_loader.py`:

```python
"""
Tests for conversion.core.pdf_loader.

These tests import the loader directly — they do NOT go through Django,
because OpenEFT/settings.py performs blocking network I/O at import time
(see settings.py lines 141-159). Running via `python -m unittest` avoids
that import path entirely.
"""

import unittest
from pathlib import Path

import fitz

from conversion.core.pdf_loader import pdf_to_image_bytes

FIXTURES = Path(__file__).parent / "fixtures"


class TestPdfToImageBytes(unittest.TestCase):
    def test_ricoh_multipage_extracts_native_jpeg_from_page_one(self):
        """The real 2-page Ricoh scan: warn about pages, return native JPEG bytes."""
        pdf_bytes = (FIXTURES / "ricoh_duplex.pdf").read_bytes()

        img_bytes, ext, warning = pdf_to_image_bytes(pdf_bytes)

        self.assertEqual(ext, "jpeg")
        self.assertIsNotNone(warning)
        self.assertIn("2 pages", warning)

        # The returned bytes should be exactly the embedded JPEG from page 0
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        xref = doc[0].get_images()[0][0]
        native = doc.extract_image(xref)["image"]
        self.assertEqual(img_bytes, native)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test — verify it fails with ImportError**

Run: `python -m unittest conversion.tests.test_pdf_loader -v`
Expected: `ModuleNotFoundError: No module named 'conversion.core.pdf_loader'` (or equivalent).

- [ ] **Step 3: Create the minimal `pdf_loader.py` to pass the first test**

Create `conversion/core/pdf_loader.py`:

```python
"""
PDF → image bytes for OpenEFT step 1.

Accepts a PDF as raw bytes and returns an image rendering of page 0 that
the existing cv2-based pipeline can consume. When the page wraps a single
embedded raster (the common MFP scanner case), the native image bytes are
returned unchanged to preserve resolution.
"""

import fitz


def pdf_to_image_bytes(pdf_bytes: bytes) -> tuple[bytes, str, str | None]:
    """
    Convert a PDF's first page to image bytes.

    Returns (image_bytes, extension, warning_or_None). Extension is a bare
    format name without the dot, e.g. "jpeg" or "png".

    Raises ValueError on PDFs that cannot be opened or contain no usable
    first page.
    """
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise ValueError(f"Could not open PDF: {exc}") from exc

    if len(doc) == 0:
        raise ValueError("PDF has no pages.")

    warning = None
    if len(doc) > 1:
        warning = f"Your PDF has {len(doc)} pages — using page 1."

    page = doc[0]
    images = page.get_images(full=True)
    if len(images) == 1:
        xref = images[0][0]
        info = doc.extract_image(xref)
        return info["image"], info["ext"], warning

    # Fallback: render the page at 600 DPI as PNG.
    pix = page.get_pixmap(dpi=600)
    return pix.tobytes("png"), "png", warning
```

- [ ] **Step 4: Run the test — verify it passes**

Run: `python -m unittest conversion.tests.test_pdf_loader -v`
Expected: `OK` with `test_ricoh_multipage_extracts_native_jpeg_from_page_one ... ok`.

- [ ] **Step 5: Write the failing test for a single-page PDF (no warning)**

Append to `conversion/tests/test_pdf_loader.py`:

```python
    def test_single_page_pdf_has_no_warning(self):
        """A single-page PDF should not produce a warning message."""
        # Build a 1-page PDF in-memory by cloning page 0 of the Ricoh fixture.
        src = fitz.open(str(FIXTURES / "ricoh_duplex.pdf"))
        single = fitz.open()
        single.insert_pdf(src, from_page=0, to_page=0)
        pdf_bytes = single.tobytes()

        img_bytes, ext, warning = pdf_to_image_bytes(pdf_bytes)

        self.assertEqual(ext, "jpeg")
        self.assertIsNone(warning)
        self.assertGreater(len(img_bytes), 0)
```

- [ ] **Step 6: Run the test — verify it passes**

Run: `python -m unittest conversion.tests.test_pdf_loader.TestPdfToImageBytes.test_single_page_pdf_has_no_warning -v`
Expected: `OK`. (No implementation change needed — the warning branch was already gated on `len(doc) > 1`. We're confirming the gate works.)

- [ ] **Step 7: Write the failing test for the vector-PDF fallback path**

Append to `conversion/tests/test_pdf_loader.py`:

```python
    def test_vector_pdf_falls_back_to_rendered_png(self):
        """A PDF with no embedded raster renders the page at 600 DPI as PNG."""
        import struct

        doc = fitz.open()
        page = doc.new_page(width=612, height=792)  # US Letter @ 72 DPI
        page.draw_rect(fitz.Rect(50, 50, 562, 742), color=(0, 0, 0), width=2)
        page.insert_text((72, 72), "no raster here", fontsize=24)
        pdf_bytes = doc.tobytes()

        img_bytes, ext, warning = pdf_to_image_bytes(pdf_bytes)

        self.assertEqual(ext, "png")
        self.assertIsNone(warning)

        # Parse PNG IHDR directly to verify dimensions — no extra deps.
        # PNG layout: 8-byte signature, then IHDR chunk whose payload starts
        # at byte 16 with 4-byte width, 4-byte height (big-endian uint32).
        self.assertEqual(img_bytes[:8], b"\x89PNG\r\n\x1a\n")
        width, height = struct.unpack(">II", img_bytes[16:24])
        # 600 DPI of 8.5" x 11" Letter = 5100 x 6600 px (allow a pixel or two slack).
        self.assertAlmostEqual(width, 5100, delta=5)
        self.assertAlmostEqual(height, 6600, delta=5)
```

- [ ] **Step 8: Run the test — verify it passes**

Run: `python -m unittest conversion.tests.test_pdf_loader.TestPdfToImageBytes.test_vector_pdf_falls_back_to_rendered_png -v`
Expected: `OK`. The fallback branch already exists; this test confirms it's reached when `get_images()` returns empty.

- [ ] **Step 9: Write the failing test for corrupt PDF input**

Append to `conversion/tests/test_pdf_loader.py`:

```python
    def test_corrupt_pdf_raises_value_error(self):
        """Non-PDF bytes should raise ValueError with a clear message."""
        with self.assertRaises(ValueError) as ctx:
            pdf_to_image_bytes(b"this is not a pdf")
        self.assertIn("Could not open PDF", str(ctx.exception))
```

- [ ] **Step 10: Run the test — verify it passes**

Run: `python -m unittest conversion.tests.test_pdf_loader.TestPdfToImageBytes.test_corrupt_pdf_raises_value_error -v`
Expected: `OK`. The try/except in `fitz.open()` already raises `ValueError`.

- [ ] **Step 11: Run the full test file one last time**

Run: `python -m unittest conversion.tests.test_pdf_loader -v`
Expected: 4 tests, all pass.

- [ ] **Step 12: Commit**

```bash
git add conversion/core/pdf_loader.py conversion/tests/test_pdf_loader.py
git commit -m "Add pdf_loader with native-extract and 600 DPI fallback"
```

---

## Task 3: `views.py` — branch on PDF uploads in `step1()`

**Files:**
- Modify: `conversion/views.py:40-56`

The view has Django's import-time side effects (network fetches in `settings.py`), so we validate this glue manually in Task 5 rather than with a Django test harness. The loader's unit tests already pin the tricky logic.

- [ ] **Step 1: Update imports and rewrite `step1()`**

In `conversion/views.py`, replace lines 40-56 (the `step1(request)` function) with:

```python
def step1(request):
    global RESULTS
    if request.method == "POST":
        file = request.FILES.get("formFileLg")
        print(file)
        time.sleep(1)
        warning = None
        original_name = (file.name or "").lower()
        if original_name.endswith(".pdf"):
            try:
                img_bytes, ext, warning = pdf_to_image_bytes(file.read())
            except ValueError as e:
                print(f"PDF load failed: {e}")
                return JsonResponse({"values": False, "warning": str(e)}, safe=False)
            intermediate = os.path.join(TMP_DIR, f"input.{ext}")
            with open(intermediate, "wb") as dest:
                dest.write(img_bytes)
            fname = os.path.join(TMP_DIR, "input.png")
            if ext != "png":
                img = cv2.imread(intermediate)
                cv2.imwrite(fname, img)
            else:
                # Already a PNG; the intermediate IS input.png
                if intermediate != fname:
                    os.replace(intermediate, fname)
        else:
            fname = os.path.join(TMP_DIR, "input.png")
            with open(fname, "wb+") as dest:
                for chunk in file.chunks():
                    dest.write(chunk)
        try:
            out = section_fp(fname=fname)
        except Exception as e:
            print(e)
            out = False
        return JsonResponse({"values": out, "warning": warning}, safe=False)
    return JsonResponse({"message": "Invalid request method"}, status=405)
```

And add the new import near the top of the file, alongside line 13's existing import:

```python
from conversion.core.pdf_loader import pdf_to_image_bytes
```

- [ ] **Step 2: Sanity-check the file still parses as Python**

Run: `python -c "import ast; ast.parse(open('conversion/views.py').read()); print('ok')"`
Expected: `ok` (no syntax errors — we can't import it without Django's env set up, so AST-parse is the fast check).

- [ ] **Step 3: Commit**

```bash
git add conversion/views.py
git commit -m "Route PDF uploads through pdf_loader in step1 view"
```

---

## Task 4: Templates — accept attribute, preview handling, warning banner

**Files:**
- Modify: `conversion/templates/conversion/partials/step1.html:21`
- Modify: `conversion/templates/conversion/partials/step1.html:29-52` (the `readURL` script block)
- Modify: `conversion/templates/conversion/new.html:184-202` (the `showFingerprints` function)

- [ ] **Step 1: Broaden the file-input `accept` attribute**

In `conversion/templates/conversion/partials/step1.html`, change line 21 from:

```html
<input class="form-control form-control-lg" id="formFileLg" type="file" name="formFileLg"  accept="image/*" onchange="readURL(this);">
```

to:

```html
<input class="form-control form-control-lg" id="formFileLg" type="file" name="formFileLg"  accept="image/*,application/pdf,.pdf" onchange="readURL(this);">
```

- [ ] **Step 2: Handle PDFs in the `readURL` preview function**

In `conversion/templates/conversion/partials/step1.html`, replace the entire `<script>` block at lines 28-53 with:

```html
<script>
function readURL(input) {
    let fpimg = document.getElementById("fp-img");
    if (input.files && input.files[0]) {
        let selected = input.files[0];
        let isPdf = selected.type === "application/pdf"
                    || selected.name.toLowerCase().endsWith(".pdf");
        if (isPdf) {
            // Browsers can't render PDFs via <img>. Show an inline SVG
            // placeholder so the user sees positive feedback without us
            // shipping a new static asset.
            let svg = "data:image/svg+xml;utf8,"
                + encodeURIComponent(
                    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 140">'
                    + '<rect x="10" y="10" width="100" height="120" fill="#f1f1f1" stroke="#888" stroke-width="2"/>'
                    + '<text x="60" y="65" text-anchor="middle" font-family="sans-serif" font-size="28" font-weight="bold" fill="#c0392b">PDF</text>'
                    + '<text x="60" y="95" text-anchor="middle" font-family="sans-serif" font-size="10" fill="#555">'
                    + selected.name.replace(/[<>&"']/g, "") + '</text>'
                    + '</svg>');
            fpimg.setAttribute("src", svg);
            fpimg.style.width = "120px";
            fpimg.style.height = "140px";
            fpimg.removeAttribute("hidden");
            return;
        }
        var reader = new FileReader();
        reader.onloadend = function() {
            fpimg.setAttribute("src", reader.result);

            // Create a temporary image to get the natural dimensions
            var tempImg = new Image();
            tempImg.src = reader.result;
            tempImg.onload = function() {
                // Calculate the aspect ratio
                var aspectRatio = tempImg.width / tempImg.height;

                // Set width to 500px and calculate height based on aspect ratio
                fpimg.style.width = "500px";
                fpimg.style.height = (500 / aspectRatio) + "px";  // Dynamically set the height
                
                fpimg.removeAttribute("hidden");  // Show the image if it was hidden
            };
        };
        reader.readAsDataURL(input.files[0]);
    }
}
</script>
```

- [ ] **Step 3: Render the warning banner in `showFingerprints`**

In `conversion/templates/conversion/new.html`, replace the `showFingerprints(json)` function at lines 184-202 with:

```javascript
    function showFingerprints(json) {
        // Clear any previous warning banner
        let oldAlert = document.getElementById("pdf-warning");
        if (oldAlert) oldAlert.remove();

        if (json.values==false){
            step[current_step].classList.remove('d-block');
            step[current_step].classList.add('d-none');
            error.removeAttribute("hidden");
            if (json.warning) {
                error.innerText = json.warning;
            }
            clearCanvas();
        } else {
            error.setAttribute("hidden","true");
            step[current_step].classList.remove('d-none');
            step[current_step].classList.add('d-block');
            if (json.warning) {
                let banner = document.createElement("div");
                banner.id = "pdf-warning";
                banner.className = "alert alert-warning alert-dismissible fade show mt-2";
                banner.setAttribute("role", "alert");
                banner.innerHTML = json.warning
                    + ' <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>';
                step[current_step].prepend(banner);
            }
            document.getElementById("thumb").onload = setTimeout(checkImages,1000);
            document.getElementById("rslap").src = "static/" + json.values[0];
            document.getElementById("lslap").src = "static/" + json.values[1];
            document.getElementById("thumb").src = "static/" + json.values[2];
            // Make sure we disallow next button
            nextBtn.disabled=true;
            prevBtn.disabled=true;
        }
    }
```

- [ ] **Step 4: Commit**

```bash
git add conversion/templates/conversion/partials/step1.html conversion/templates/conversion/new.html
git commit -m "Accept PDFs in step 1 and surface multi-page warning banner"
```

---

## Task 5: Manual end-to-end verification

No automated test covers the Django view + template glue (import-time network fetches in settings.py make Django's test harness heavy for this scope). Verify by hand.

**Files:** none changed — this task only exercises the running app.

- [ ] **Step 1: Ensure `TMP_DIR` is set and start the dev server**

On Windows (bash in this repo):

```bash
export TMP_DIR=/tmp/openeft
mkdir -p "$TMP_DIR"
python openeft.py
```

Expected: server logs show `Starting development server at http://0.0.0.0:8080/`. Leave it running.

- [ ] **Step 2: Upload the real Ricoh PDF through the wizard**

Browser: http://localhost:8080 → Start New EFT → step 1 → choose `D:\Downloads\20260422123323640.pdf`.

Expected:
- The preview area shows the inline `PDF` placeholder with the filename underneath (not a broken image).
- Click Next. After a brief spinner, step 2 loads.
- A yellow Bootstrap alert banner appears at the top of step 2 reading: `Your PDF has 2 pages — using page 1.`
- Three fingerprint preview images (right slap, left slap, thumbs) appear and look correctly sectioned.

- [ ] **Step 3: Continue through the wizard to generate an EFT**

Fill in test personal data on steps 3-4 (any valid-looking values; this is just to exercise the pipeline end-to-end), submit, and confirm a `.eft` download link appears on the download page.

- [ ] **Step 4: Regression-check the image path**

Back on step 1, upload `test.png` from the repo root (or any JPG scan). Expected: preview shows the raster image (existing behavior), sectioning proceeds, no warning banner appears.

- [ ] **Step 5: Spot-check a corrupt-PDF error path**

Rename any small non-PDF file to `.pdf` (e.g., `cp requirements.txt /tmp/fake.pdf`) and upload it. Expected: the step 1 red error banner appears with text containing `Could not open PDF`.

- [ ] **Step 6: Stop the dev server and record results**

Ctrl+C the dev server. If everything above passed, the feature is ready to merge.

---

## Self-review

- **Spec coverage:** each section of the spec maps to a task — module (Task 2), view integration (Task 3), template edits including accept attribute, preview handling, and warning banner (Task 4), dependency + fixture (Task 1), manual E2E (Task 5). Error-handling cases (corrupt PDF, imageless PDF, corner-detect failure) are exercised in Task 2 step 9-10 and Task 5 step 5.
- **Placeholder scan:** no TBDs, no "add appropriate error handling" — every step shows exact code, exact commands, or exact expected output.
- **Type consistency:** the tuple `(bytes, str, str | None)` returned by `pdf_to_image_bytes` is used identically in Task 2 tests and Task 3 view code. JSON key `warning` is set by the view in Task 3 and read by the template in Task 4. Warning string (`"Your PDF has N pages — using page 1."`) matches between `pdf_loader.py` and the test assertion (`self.assertIn("2 pages", warning)`).
- **One simplification vs. spec:** the spec mentioned a `static/pdf-icon.png` asset; the plan uses an inline SVG data URL instead, avoiding a new binary commit while achieving the same UX goal. Noted here for traceability.
