import io
import unittest
import zipfile

from app.subsource import _subtitle_from_zip


class SubsourceZipTests(unittest.TestCase):
    def test_selects_requested_episode_from_multi_sub_archive(self):
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("Tongari Boushi no Atelier - 02.vi.srt", b"episode 2")
            archive.writestr("Tongari Boushi no Atelier - 01.vi.srt", b"episode 1")

        filename, subtitle_format, data = _subtitle_from_zip(payload.getvalue(), {"target_episode": 1})

        self.assertEqual(filename, "Tongari Boushi no Atelier - 01.vi.srt")
        self.assertEqual(subtitle_format, "srt")
        self.assertEqual(data, b"episode 1")

    def test_rejects_multi_sub_archive_without_matching_episode(self):
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("Tongari Boushi no Atelier - 02.vi.srt", b"episode 2")
            archive.writestr("Tongari Boushi no Atelier - 03.vi.srt", b"episode 3")

        with self.assertRaises(ValueError):
            _subtitle_from_zip(payload.getvalue(), {"target_episode": 1})


if __name__ == "__main__":
    unittest.main()
