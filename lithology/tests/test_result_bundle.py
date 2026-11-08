import io
import unittest
import zipfile
from lithology.result_bundle import write_source_image


class SourceBundleTests(unittest.TestCase):
    def bundle(self, original, fallback="document.pdf"):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            added = write_source_image(archive, original, fallback, "document")
        with zipfile.ZipFile(io.BytesIO(output.getvalue())) as archive:
            return added, {name: archive.read(name) for name in archive.namelist()}

    def test_uses_stored_image_extension_for_pdf_origin_document(self):
        added, files = self.bundle({"filename": "document.pdf.tif", "data": b"original pixels"})
        self.assertTrue(added)
        self.assertEqual(files, {"document_source.tif": b"original pixels"})

    def test_missing_original_is_backward_compatible(self):
        self.assertEqual(self.bundle(None), (False, {}))

    def test_unsupported_source_does_not_pretend_to_be_tiff(self):
        self.assertEqual(self.bundle({"filename": "original.pdf", "data": b"pdf"}), (False, {}))

    def test_uppercase_supported_extension(self):
        self.assertEqual(self.bundle({"filename": "scan.PNG", "data": b"png"})[1], {"document_source.png": b"png"})

    def test_fallback_name_and_original_bytes_are_preserved(self):
        self.assertEqual(self.bundle({"data": b"raw"}, "scan.tiff")[1], {"document_source.tiff": b"raw"})


if __name__ == "__main__":
    unittest.main()
