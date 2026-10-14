import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import json
import os
import sys
import repair_2box_assets as repair


class LegacyLauncherTests(unittest.TestCase):
    def test_legacy_entry_uses_shared_incremental_launcher(self):
        script = (Path(__file__).parent / '更新缓存后启动2Box.cmd').read_text(encoding='ascii')
        self.assertIn('SharedCache.exe" launch --root', script)
        self.assertNotIn('RefreshCache.exe', script)
        self.assertNotIn('--reset-stale', script)


class PatchVersionTests(unittest.TestCase):
    def test_only_patch_versions_are_synchronized(self):
        self.assertTrue(callable(getattr(repair, 'sync_patch_version_fields', None)))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            normal, local, backup = root / 'normal.json', root / 'local.json', root / 'saved.json'
            normal.write_text(json.dumps({'patch_normal_dds_ver': '1304779',
                                         '_key_current_version_': '5.2.1304779',
                                         'account': 'different-fixture-account', 'volume': 9}))
            local.write_text(json.dumps({'patch_normal_dds_ver': '1298886',
                                        '_key_current_version_': '5.2.1298886',
                                        'account': 'fixture-account', 'volume': 3}))
            with patch.object(repair, 'box_is_running', return_value=False):
                repair.sync_patch_version_fields(normal, local, backup)
            result = json.loads(local.read_text())
            self.assertEqual(result['patch_normal_dds_ver'], '1304779')
            self.assertEqual(result['_key_current_version_'], '5.2.1304779')
            self.assertEqual(result['account'], 'fixture-account')
            self.assertEqual(result['volume'], 3)
            self.assertEqual(json.loads(backup.read_text())['patch_normal_dds_ver'], '1298886')
            self.assertNotIn(b'\n', local.read_bytes(), 'Game settings must remain single-line JSON.')


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


class FastResetTests(unittest.TestCase):
    def fixture(self, root):
        normal, box = root / 'normal', root / 'box'
        user = Path(os.environ['USERPROFILE']).name.lower()
        env = box / 'Env/1'
        local = env / f'users/{user}/appdata/local/lagrange_online_branch'
        docs = local / 'Documents'
        for marker in repair.MARKERS:
            source, cached = normal / marker, docs / marker
            source.parent.mkdir(parents=True, exist_ok=True)
            cached.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(b'new-marker')
            cached.write_bytes(b'old-marker')
        for area in repair.AREAS:
            (normal / area).mkdir(exist_ok=True)
            (docs / area).mkdir(exist_ok=True)
            (docs / area / 'stale.bin').write_bytes(b'old-resource')
        local.joinpath('user_default.json').write_bytes(b'fixture-settings')
        mpay = env / f'users/{user}/appdata/roaming/Netease/Mpay'
        mpay.mkdir(parents=True)
        mpay.joinpath('fixture.db').write_bytes(b'fixture-login-state')
        (docs / 'upload_album_file').mkdir()
        (docs / 'upload_album_file/photo.jpg').write_bytes(b'fixture-photo')
        script = docs / ('a' * 32 + '.script')
        script.write_bytes(b'old-hotpatch')
        return normal, box, env, local, docs, mpay, script

    def test_reset_archives_resources_without_touching_login_or_user_files(self):
        self.assertTrue(callable(getattr(repair, 'reset_resource_caches', None)),
                        'Fast cache reset is not implemented yet.')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            normal, box, env, local, docs, mpay, script = self.fixture(root)
            backup = box / 'repair-backups/test-reset'
            with patch.object(repair, 'ROOT', box), patch.object(
                    repair, 'box_is_running', return_value=False):
                result = repair.reset_resource_caches([(env, docs)], normal, backup)
            self.assertEqual(result['state'], 'stale_resources_archived')
            self.assertTrue(repair.markers_match(docs, normal))
            self.assertEqual((backup / (docs / 'res/stale.bin').relative_to(box)).read_bytes(), b'old-resource')
            self.assertFalse((docs / 'res/stale.bin').exists())
            self.assertEqual(local.joinpath('user_default.json').read_bytes(), b'fixture-settings')
            self.assertEqual(mpay.joinpath('fixture.db').read_bytes(), b'fixture-login-state')
            self.assertEqual((docs / 'upload_album_file/photo.jpg').read_bytes(), b'fixture-photo')
            self.assertFalse(script.exists())
            self.assertEqual((backup / script.relative_to(box)).read_bytes(), b'old-hotpatch')

    def test_failed_reset_restores_old_resources(self):
        self.assertTrue(callable(getattr(repair, 'reset_resource_caches', None)),
                        'Fast cache reset is not implemented yet.')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            normal, box, env, local, docs, mpay, script = self.fixture(root)
            original_replace = os.replace
            failed = False

            def fail_one_directory_move(source, destination):
                nonlocal failed
                if Path(source) == docs / 'res' and not failed:
                    failed = True
                    raise PermissionError('fixture locked directory')
                return original_replace(source, destination)

            with patch.object(repair, 'ROOT', box), patch.object(
                    repair, 'box_is_running', return_value=False), patch.object(
                    os, 'replace', side_effect=fail_one_directory_move):
                with self.assertRaises(PermissionError):
                    repair.reset_resource_caches([(env, docs)], normal, box / 'repair-backups/test-reset')
            self.assertEqual((docs / 'patch/patch_list').read_bytes(), b'old-marker')
            self.assertEqual((docs / 'res/stale.bin').read_bytes(), b'old-resource')
            self.assertEqual(mpay.joinpath('fixture.db').read_bytes(), b'fixture-login-state')

    def test_fast_launch_does_not_enter_full_asset_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            normal, box, env, local, docs, mpay, script = self.fixture(root)
            report = root / 'check.json'
            with patch.object(repair, 'ROOT', box), patch.object(repair, 'NORMAL', normal), \
                    patch.object(repair, 'box_is_running', return_value=False), patch.object(
                    repair, 'candidates', side_effect=AssertionError('Unexpected full asset scan')), \
                    patch.object(sys, 'argv', ['helper', '--apply', '--quick', '--reset-stale',
                                             '--report', str(report)]):
                repair.main()
            self.assertEqual(json.loads(report.read_text())['state'], 'stale_resources_archived')


if __name__ == '__main__':
    unittest.main()
