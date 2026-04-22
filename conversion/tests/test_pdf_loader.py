"""
Tests for conversion.core.pdf_loader.

These tests import the loader directly — they do NOT go through Django,
because OpenEFT/settings.py performs blocking network I/O at import time
(see settings.py lines 141-159). Running via `python -m unittest` avoids
that import path entirely.
"""

import io
import struct
import unittest
from pathlib import Path

import fitz
from PIL import Image

from conversion.core.pdf_loader import pdf_to_image_bytes

FIXTURES = Path(__file__).parent / "fixtures"


class TestPdfToImageBytes(unittest.TestCase):
    def test_rotated_ricoh_pdf_renders_png_right_side_up(self):
        """Real Ricoh fingerprint scan has page.rotation=90. We must render
        (not native-extract) so the output is in viewer orientation."""
        pdf_bytes = (FIXTURES / "ricoh_fingerprints.pdf").read_bytes()

        img_bytes, ext, warning = pdf_to_image_bytes(pdf_bytes)

        self.assertEqual(ext, "png")
        self.assertIsNone(warning)  # single-page fixture
        # PyMuPDF renders at the page's visual (post-rotation) dimensions.
        # Letter page (612x792 pts, portrait) rendered at 600 DPI → 5100x6600.
        # The content inside appears right-side up because rotation is applied.
        self.assertEqual(img_bytes[:8], b"\x89PNG\r\n\x1a\n")
        width, height = struct.unpack(">II", img_bytes[16:24])
        self.assertAlmostEqual(width, 5100, delta=5)
        self.assertAlmostEqual(height, 6600, delta=5)

    def test_multi_page_ricoh_triggers_warning(self):
        """Two-page duplex scan: warn, still use page 1."""
        # Build a 2-page PDF by duplicating page 0 of the real fixture.
        src = fitz.open(str(FIXTURES / "ricoh_fingerprints.pdf"))
        multi = fitz.open()
        multi.insert_pdf(src, from_page=0, to_page=0)
        multi.insert_pdf(src, from_page=0, to_page=0)
        pdf_bytes = multi.tobytes()

        img_bytes, ext, warning = pdf_to_image_bytes(pdf_bytes)

        self.assertEqual(ext, "png")
        self.assertIsNotNone(warning)
        self.assertIn("2 pages", warning)
        self.assertGreater(len(img_bytes), 0)

    def test_unrotated_single_image_pdf_returns_native_jpeg(self):
        """Simple, unrotated, single-image PDF: preserve native JPEG bytes."""
        # Build an unrotated letter page, insert a known JPEG.
        img = Image.new("RGB", (200, 150), color=(200, 100, 50))
        jpeg_buf = io.BytesIO()
        img.save(jpeg_buf, format="JPEG", quality=90)
        jpeg_bytes = jpeg_buf.getvalue()

        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        page.insert_image(fitz.Rect(50, 50, 250, 200), stream=jpeg_bytes)
        pdf_bytes = doc.tobytes()

        img_bytes, ext, warning = pdf_to_image_bytes(pdf_bytes)

        self.assertEqual(ext, "jpeg")
        self.assertIsNone(warning)
        # Reopen the PDF and verify we got the exact embedded bytes back.
        verify = fitz.open(stream=pdf_bytes, filetype="pdf")
        xref = verify[0].get_images()[0][0]
        native = verify.extract_image(xref)["image"]
        self.assertEqual(img_bytes, native)

    def test_vector_pdf_falls_back_to_rendered_png(self):
        """A PDF with no embedded raster renders the page at 600 DPI as PNG."""
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

    def test_corrupt_pdf_raises_value_error(self):
        """Non-PDF bytes should raise ValueError with a clear message."""
        with self.assertRaises(ValueError) as ctx:
            pdf_to_image_bytes(b"this is not a pdf")
        self.assertIn("Could not open PDF", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
