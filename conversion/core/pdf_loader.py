"""
PDF → image bytes for OpenEFT step 1.

Accepts a PDF as raw bytes and returns an image rendering of page 0 that
the existing cv2-based pipeline can consume. When the page wraps a single
embedded raster with no rotation and a cv2-decodable format, the native
image bytes are returned unchanged to preserve resolution. Anything else
(rotated page, composite, exotic format like JPEG 2000 / JBIG2 / JPEG XR)
falls through to PyMuPDF's renderer, which produces a PNG in the correct
viewer orientation.
"""

from __future__ import annotations

import fitz

# Image formats OpenCV's standard build can decode. PyMuPDF can surface
# others (jpx/JPEG 2000, jb2/JBIG2, jxr/JPEG XR) that opencv-python-headless
# does not support; those take the render path instead.
_CV2_SAFE_EXTS = frozenset({"jpeg", "jpg", "png"})


def pdf_to_image_bytes(pdf_bytes: bytes) -> tuple[bytes, str, str | None]:
    """
    Convert page 1 of a PDF (the first page) to image bytes.

    Returns (image_bytes, extension, warning_or_None). Extension is a bare
    format name without the dot, e.g. "jpeg" or "png".

    Raises ValueError on PDFs that cannot be opened or contain no usable
    first page.
    """
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise ValueError(f"Could not open PDF: {exc}") from exc

    with doc:
        if len(doc) == 0:
            raise ValueError("PDF has no pages.")

        warning = None
        if len(doc) > 1:
            warning = f"Your PDF has {len(doc)} pages — using page 1."

        page = doc[0]
        images = page.get_images(full=True)
        # Native extract only when the page is a simple unrotated wrapper
        # around a single cv2-decodable image. Any rotation hint, composite,
        # or exotic format falls through to get_pixmap, which honors
        # page.rotation (producing the image right-side up) and always
        # produces PNG bytes.
        if len(images) == 1 and page.rotation == 0:
            xref = images[0][0]
            info = doc.extract_image(xref)
            if info["ext"] in _CV2_SAFE_EXTS:
                return info["image"], info["ext"], warning

        pix = page.get_pixmap(dpi=600)
        return pix.tobytes("png"), "png", warning
