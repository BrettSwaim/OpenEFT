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
        # Only extract natively when the page wraps exactly one image. Composites
        # (multiple embedded images) are rendered as a whole to preserve layout.
        if len(images) == 1:
            xref = images[0][0]
            info = doc.extract_image(xref)
            return info["image"], info["ext"], warning

        # Fallback: render the page at 600 DPI as PNG.
        pix = page.get_pixmap(dpi=600)
        return pix.tobytes("png"), "png", warning
