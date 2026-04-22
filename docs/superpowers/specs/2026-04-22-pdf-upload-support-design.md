# PDF Upload Support — Design

**Date:** 2026-04-22
**Status:** Approved for implementation planning

## Problem

OpenEFT's step 1 only accepts raster image uploads (`accept="image/*"` in `conversion/templates/conversion/partials/step1.html:21`). Most office multifunction printers and flatbed scanners produce PDFs by default, forcing users to manually export an image before uploading. We want to accept PDFs directly without degrading fingerprint ridge fidelity.

## Goals

- Accept PDF uploads in the step 1 file picker alongside existing image formats.
- Preserve the scanner's native resolution when the PDF is a raster-wrapped scan.
- Keep the downstream sectioning / EFT generation pipeline untouched.
- No new system dependencies (no Poppler, no external binaries in build scripts).

## Non-goals

- Multi-page page-picker UI. First page + warning is sufficient.
- Client-side live PDF preview with pdf.js. A static icon placeholder is enough.
- PDF output (EFT files remain the only generated artifact).
- Changes to `manual_section()`, alignment, fingerprint segmentation, or EFT assembly.

## Real-world input reference

The target scanner profile, confirmed by inspecting a sample from a Ricoh MP C4504ex MFP:

- **Pages:** 2 (auto-duplex default; page 2 is a blank card back)
- **Per page:** exactly one embedded JPEG, 6600×5100 px, DeviceRGB, 8 bpc
- **Effective resolution:** ~600 DPI at Letter (8.5×11") size
- **Page box:** portrait letter with a rotation transform; image content is landscape

This matches the "single embedded raster" case the design optimizes for.

## Architecture

One new module, one integration point, two small template edits.

### New module: `conversion/core/pdf_loader.py`

Single public function:

```python
def pdf_to_image_bytes(pdf_bytes: bytes) -> tuple[bytes, str, str | None]:
    """
    Convert a PDF's first page to image bytes suitable for cv2.imread.

    Returns (image_bytes, extension, warning_message_or_None).
    Raises ValueError on unreadable / password-protected / imageless PDFs.
    """
```

**Contract:**

1. Open with `fitz.open(stream=pdf_bytes, filetype="pdf")`.
2. If `len(doc) > 1`, set `warning = "Your PDF has N pages — using page 1."`.
3. Take page 0. If it has exactly one embedded image, extract via `doc.extract_image(xref)` and return the native bytes + `ext`.
4. Otherwise fall back to `page.get_pixmap(dpi=600).tobytes("png")` and return bytes + `"png"`.
5. On any `fitz` error, wrap and raise `ValueError` with a user-safe message.

**Why this contract:** the view layer stays simple (call it, get bytes back), and all PDF-specific logic — including the "use page 1" decision — is isolated to one module that can be unit-tested without Django.

### Integration: `conversion/views.py:step1()`

Current behavior (lines 40-56) writes the upload directly to `TMP_DIR/input.png` and calls `section_fp()`. New behavior:

1. Inspect the uploaded filename. If it ends in `.pdf` (case-insensitive):
   - Read `file.read()` into memory (PDFs are typically <10 MB).
   - Call `pdf_to_image_bytes(pdf_bytes)` → `(img_bytes, ext, warning)`.
   - Write `img_bytes` to `TMP_DIR/input.<ext>`.
   - Transcode to PNG: `cv2.imwrite(TMP_DIR/input.png, cv2.imread(TMP_DIR/input.<ext>))`. This preserves the existing `input.png` contract the rest of the pipeline depends on.
2. Otherwise: existing path unchanged (write directly to `input.png`).
3. Call `section_fp(fname)` as today.
4. Return `JsonResponse({"values": out, "warning": warning})`. The `warning` key is new but harmless to existing frontend consumers — they'll ignore unknown keys.

**Why this location:** keeps the PDF awareness at the HTTP boundary. `conversion/core/core.py` and its callers continue to operate on `input.png` — zero risk of regression in the signal-processing pipeline.

### Template edits

1. **`conversion/templates/conversion/partials/step1.html`** (line 21): change
   `accept="image/*"` → `accept="image/*,application/pdf,.pdf"`.

2. **`conversion/templates/conversion/partials/step1.html`** (line 29, `readURL`): when `input.files[0].type === "application/pdf"`, set `fp-img` src to a static PDF icon asset (`static/pdf-icon.png`, ~2 KB) instead of attempting `FileReader.readAsDataURL` preview. Everything else stays the same.

3. **`conversion/templates/conversion/new.html`** (line 184, `showFingerprints`): if `json.warning` is present, render a dismissible Bootstrap alert above the thumbnails. Alert text = `json.warning`. No new template file; inline markup built in JS using existing Bootstrap classes.

### Dependency

Add `pymupdf` to `requirements.txt`. No version pin (matches the style of `pillow` on line 14). No changes to `build_windows.sh`, `build_linux.sh`, or `Dockerfile` — `pip install` picks up the wheel automatically.

## Data flow

```
User selects file in step 1
  │
  ├── .pdf?
  │     │
  │     └── views.py:step1()
  │           ├── read bytes
  │           ├── pdf_to_image_bytes(bytes)
  │           │     ├── multi-page? → set warning
  │           │     ├── single embedded image? → extract native bytes
  │           │     └── else → render @ 600 DPI → PNG
  │           ├── write to TMP_DIR/input.<ext>
  │           └── transcode to TMP_DIR/input.png via OpenCV
  │
  └── image/*
        └── views.py:step1() → write directly to TMP_DIR/input.png

        (from here, unchanged)
        ↓
  section_fp("input.png")
    └── GetEFT() → alignment → OCR anchors → Fingerprint() objects
        ↓
  JsonResponse({values, warning})
        ↓
  new.html:showFingerprints()
    ├── warning? → render dismissible alert
    └── update thumbnails
```

## Error handling

Three realistic failure modes, all mapped to the existing `{values: false}` error path so `new.html:186-189` handles them without new UI:

| Failure | Where caught | User experience |
|---|---|---|
| Corrupt / password-protected PDF | `pdf_loader.py` raises `ValueError` → caught in `step1()` | Existing red error banner |
| PDF has no extractable image *and* pixmap render fails | Same | Existing red error banner |
| Rendered image fails card-corner detection in `GetEFT()` | Already handled by `views.py:50-54` try/except | Existing red error banner |

No new error states. No new UI.

## Testing

### Unit tests — `conversion/tests/test_pdf_loader.py`

Fixtures in `conversion/tests/fixtures/`:

1. **`ricoh_duplex.pdf`** — redacted copy of the real 2-page scan (1 card + 1 blank back).
2. **`single_page_scan.pdf`** — a 1-page image-wrapped PDF (can be derived from fixture 1 page 0).
3. **`vector.pdf`** — generated in-test via `fitz` with drawn shapes, no embedded images.
4. **`corrupt.pdf`** — a few bytes that aren't a PDF.

Cases:

- Multi-page PDF → warning present, returned image is from page 0 at ≥ 600 DPI.
- Single-page image-wrapped PDF → no warning, returned bytes are the native JPEG (byte-equal to the embedded image).
- Vector PDF → no warning, returned PNG is ≥ 600 DPI at Letter size.
- Corrupt PDF → raises `ValueError`.

### Manual end-to-end

- Upload `ricoh_duplex.pdf` via the wizard. Confirm:
  - Step 1 preview shows the PDF icon placeholder.
  - After "Next", warning banner appears above the thumbnails.
  - Step 2 shows four correctly sectioned fingerprint regions.
  - EFT generation completes and the file validates in NIST Viewer.
- Upload a JPG (existing path) — confirm nothing regressed.

## Scope boundary — what this change does NOT touch

- `conversion/core/core.py`
- `conversion/core/align.py`, `fingerprint.py`, `eft_helper.py`, `fd258_ocr.py`
- `manual_section()` view and endpoint (operates on already-saved `input.png`)
- EFT generation, download, delete flows
- Build scripts, Dockerfile, CI

## Open questions

None at spec time. All three clarifying decisions confirmed:

- Library: PyMuPDF (Q1-A)
- Multi-page: always use page 1, warn (Q2-A)
- Quality: smart extract native, fall back to 600 DPI render (Q3-A)
