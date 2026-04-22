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

    def test_corrupt_pdf_raises_value_error(self):
        """Non-PDF bytes should raise ValueError with a clear message."""
        with self.assertRaises(ValueError) as ctx:
            pdf_to_image_bytes(b"this is not a pdf")
        self.assertIn("Could not open PDF", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
