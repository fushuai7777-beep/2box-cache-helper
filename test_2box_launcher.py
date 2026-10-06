import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import json
import os
import sys
import repair_2box_assets as repair


class MarkerTests(unittest.TestCase):
    def test_equal_markers_skip_large_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('normal', 'box'):
                (root / name / 'patch').mkdir(parents=True)
                (root / name / 'patch/patch_list').write_bytes(b'current-version')
            self.assertTrue(repair.markers_match(root / 'box', root / 'normal'))

    def test_old_marker_requires_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('normal', 'box'):
                (root / name / 'patch').mkdir(parents=True)
                (root / name / 'patch/patch_list').write_bytes(name.encode())
            self.assertFalse(repair.markers_match(root / 'box', root / 'normal'))

    def test_new_environment_without_marker_requires_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(repair.markers_match(Path(directory), Path(directory)))

    def test_clean_installation_has_no_environment_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / 'check.json'
            with patch.object(repair, 'ROOT', root), patch.object(
                    sys, 'argv', ['helper', '--apply', '--quick', '--report', str(report)]):
                repair.main()
            self.assertEqual(json.loads(report.read_text())['state'], 'clean_installation')
            self.assertFalse((root / 'Env').exists())

    def test_refresh_backs_up_stale_asset_and_preserves_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            normal = root / 'normal'
            box = root / 'box'
            user = Path(os.environ['USERPROFILE']).name.lower()
            local = box / f'Env/1/users/{user}/appdata/local/lagrange_online_branch'
            docs = local / 'Documents'
            (docs / 'patch').mkdir(parents=True)
            (normal / 'patch').mkdir(parents=True)
            (normal / 'patch/patch_list').write_bytes(b'new-patch')
            destination = docs / 'patch/patch_list'
            destination.write_bytes(b'old-patch')
            source_stat = (normal / 'patch/patch_list').stat()
            os.utime(destination, ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns))
            settings = local / 'user_default.json'
            settings.write_bytes(b'fixture-settings')
            report = root / 'check.json'
            with patch.object(repair, 'ROOT', box), patch.object(repair, 'NORMAL', normal), \
                    patch.object(repair, 'box_is_running', return_value=False), patch.object(
                        sys, 'argv', ['helper', '--apply', '--quick', '--report', str(report)]):
                repair.main()
            result = json.loads(report.read_text(encoding='utf-8'))
            self.assertEqual(result['state'], 'applied_and_file_verified')
            self.assertEqual(destination.read_bytes(), b'new-patch')
            self.assertEqual(settings.read_bytes(), b'fixture-settings')
            saved = Path(result['backup']) / destination.relative_to(box)
            self.assertEqual(saved.read_bytes(), b'old-patch')


if __name__ == '__main__':
    unittest.main()
