import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import ctypes
from ctypes import wintypes
import time
import re
from datetime import datetime


ROOT = Path(sys.executable if getattr(sys, 'frozen', False) else __file__).resolve().parent
NORMAL = Path(os.environ['LOCALAPPDATA']) / 'lagrange_online_branch/Documents'
AREAS = {'patch', 'res', 'res_ex', 'script'}
SKIP = {'cache.lock', 'share_image.jpg'}
MARKERS = ('patch/patch_list', 'res/all_img_size.txt', 'res/all_plist.txt')
PATCH_VERSION_KEYS = {'_key_current_version_', 'patch_normal_dds_ver',
                      'patch_ex_dds_ver', 'patch_ex_dds_base_ver'}


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def box_is_running(name='2box.exe'):
    class Entry(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('usage', wintypes.DWORD),
                    ('pid', wintypes.DWORD), ('heap', ctypes.c_size_t),
                    ('module', wintypes.DWORD), ('threads', wintypes.DWORD),
                    ('parent', wintypes.DWORD), ('priority', wintypes.LONG),
                    ('flags', wintypes.DWORD), ('name', wintypes.WCHAR * 260)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    entry = Entry()
    entry.size = ctypes.sizeof(entry)
    try:
        found = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        if not found:
            raise ctypes.WinError(ctypes.get_last_error())
        while found:
            if entry.name.lower() == name.lower():
                return True
            found = kernel.Process32NextW(snapshot, ctypes.byref(entry))
        return False
    finally:
        kernel.CloseHandle(snapshot)


def markers_match(docs, normal):
    if not (docs / MARKERS[0]).is_file() or not (normal / MARKERS[0]).is_file():
        return False
    for relative in MARKERS:
        source, destination = normal / relative, docs / relative
        if destination.is_file():
            if not source.is_file() or digest(source) != digest(destination):
                return False
    return True


def candidates(docs):
    for path in docs.rglob('*'):
        if not path.is_file():
            continue
        relative = path.relative_to(docs)
        if path.name in SKIP or 'shadercache' in relative.parts:
            continue
        if (relative.parts[0] in AREAS or
                (len(relative.parts) == 1 and path.suffix == '.script')):
            yield path, relative


def protected_files(env):
    user_name = Path(os.environ['USERPROFILE']).name.lower()
    local = env / f'users/{user_name}/appdata/local/lagrange_online_branch'
    paths = [ROOT / '2Box.exe', local / 'user_default.json', local / 'lglr_settings.xml']
    mpay = env / f'users/{user_name}/appdata/roaming/Netease/Mpay'
    if mpay.exists():
        paths.extend(mpay.glob('*.db'))
    return {str(p): digest(p) for p in paths if p.is_file()}


def write_report(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def sync_patch_version_fields(normal_file, local_file, backup_file, apply=True):
    normal = json.loads(normal_file.read_text(encoding='utf-8'))
    original_bytes = local_file.read_bytes()
    local = json.loads(original_bytes.decode('utf-8'))
    if not isinstance(normal, dict) or not isinstance(local, dict):
        raise RuntimeError('Unexpected game settings format.')
    if normal.get('_base_version_') != local.get('_base_version_'):
        raise RuntimeError('Base game versions differ; finish normal game update first.')
    updates = {}
    for key in sorted(PATCH_VERSION_KEYS):
        if key in normal and key in local and normal[key] != local[key]:
            if not re.fullmatch(r'\d+(?:\.\d+)*', str(normal[key])):
                raise RuntimeError('Unexpected patch version format.')
            updates[key] = normal[key]
    if not updates or not apply:
        return sorted(updates)
    if box_is_running() or box_is_running('infinite_lagrange_cn.exe'):
        raise RuntimeError('Close 2Box and the game before syncing patch metadata.')
    if backup_file.exists():
        raise RuntimeError('Version backup already exists.')
    backup_file.parent.mkdir(parents=True, exist_ok=True)
    backup_file.write_bytes(original_bytes)
    if backup_file.read_bytes() != original_bytes:
        raise RuntimeError('Version backup verification failed.')
    updated = dict(local)
    updated.update(updates)
    staged = local_file.with_name(local_file.name + '.version-sync-tmp')
    if staged.exists():
        raise RuntimeError('Version staging file already exists.')
    try:
        # The game-generated settings are single-line JSON, not a formatted report.
        staged.write_bytes(json.dumps(updated, ensure_ascii=True, separators=(',', ':')).encode('utf-8'))
        verified = json.loads(staged.read_text(encoding='utf-8'))
        if any(verified.get(key) != value for key, value in local.items() if key not in updates):
            raise RuntimeError('A non-version setting changed.')
        if local_file.read_bytes() != original_bytes:
            raise RuntimeError('Game settings changed during preparation.')
        os.replace(staged, local_file)
    finally:
        if staged.exists():
            staged.unlink()
    return sorted(updates)


def reset_resource_caches(jobs, normal, backup):
    root = ROOT.resolve()
    if not backup.resolve().is_relative_to(root):
        raise RuntimeError('Backup must stay inside the 2Box folder.')
    marker_data = {marker: (normal / marker).read_bytes() for marker in MARKERS}
    protected = {}
    plans = []
    new_dirs = []
    for env, docs in jobs:
        if not docs.resolve().is_relative_to(root) or not docs.resolve().is_relative_to(env.resolve()):
            raise RuntimeError('Unexpected environment path.')
        protected.update(protected_files(env))
        for area in sorted(AREAS):
            original = docs / area
            if original.is_symlink() or original.is_junction():
                raise RuntimeError('Linked cache directories require manual review.')
            if original.exists() and not (normal / area).is_dir():
                raise RuntimeError('Normal game resource directory is missing: ' + area)
            if original.exists():
                plans.append((original, backup / original.relative_to(ROOT)))
            if original.exists() or area in {Path(marker).parts[0] for marker in MARKERS}:
                new_dirs.append((original, backup / '.fresh' / env.name / area))
        # Only hash-named hotpatch files are reset; user uploads stay in place.
        for path in docs.iterdir():
            if path.is_file() and re.fullmatch(r'[0-9a-fA-F]{32}\.script', path.name):
                if path.is_symlink():
                    raise RuntimeError('Linked hotpatch file requires manual review.')
                plans.append((path, backup / path.relative_to(ROOT)))
    if box_is_running() or box_is_running('infinite_lagrange_cn.exe'):
        raise RuntimeError('Close 2Box and all game windows before refreshing resources.')
    backup.mkdir(parents=True, exist_ok=False)
    result = {'state': 'planned_resource_reset', 'backup': str(backup),
              'environments': [env.name for env, _ in jobs],
              'archived_items': len(plans), 'protected_file_count': len(protected),
              'archive_operations': [str(path.relative_to(ROOT)) for path, _ in plans]}
    write_report(backup / 'manifest.json', result)
    for original, staged in new_dirs:
        staged.mkdir(parents=True)
        for marker, content in marker_data.items():
            relative = Path(marker)
            if relative.parts[0] == original.name:
                (staged / Path(*relative.parts[1:])).write_bytes(content)
    moved, installed = [], []
    try:
        for marker, content in marker_data.items():
            if digest(normal / marker) != hashlib.sha256(content).hexdigest():
                raise RuntimeError('Normal game update changed during preparation.')
        for original, saved in plans:
            saved.parent.mkdir(parents=True, exist_ok=True)
            os.replace(original, saved)
            moved.append((original, saved))
        for original, staged in new_dirs:
            os.replace(staged, original)
            installed.append(original)
        if not all(markers_match(docs, normal) for _, docs in jobs):
            raise RuntimeError('Update marker verification failed.')
        if any(digest(Path(path)) != old for path, old in protected.items()):
            raise RuntimeError('Protected account or settings files changed.')
        result['state'] = 'stale_resources_archived'
        result['protected_files_unchanged'] = True
    except Exception:
        # Move fresh directories aside instead of deleting any resource data.
        for original in reversed(installed):
            quarantine = backup / '.failed-new' / original.relative_to(ROOT)
            quarantine.parent.mkdir(parents=True, exist_ok=True)
            os.replace(original, quarantine)
        for original, saved in reversed(moved):
            os.replace(saved, original)
        result['state'] = 'rolled_back_resource_reset'
        write_report(backup / 'manifest.json', result)
        raise
    write_report(backup / 'manifest.json', result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--quick', action='store_true')
    parser.add_argument('--reset-stale', action='store_true')
    parser.add_argument('--sync-version', action='store_true')
    parser.add_argument('--timeout', type=int, default=120)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    started = time.monotonic()
    print('Checking 2Box update cache...', flush=True)
    if (ROOT / 'Env').is_dir() and box_is_running():
        raise RuntimeError('Exit 2Box before refreshing its asset cache.')
    changes = []
    matched = 0
    missing = 0
    protected = {}
    quick_envs = []
    reset_jobs = []
    version_jobs = []
    env_root = ROOT / 'Env'
    if not env_root.is_dir():
        print('No saved environments yet. Starting with a clean installation.', flush=True)
        write_report(args.report, {'state': 'clean_installation', 'changed_files': 0})
        return
    for env in sorted(env_root.iterdir()):
        if not env.is_dir() or not env.name.isdigit():
            continue
        user_name = Path(os.environ['USERPROFILE']).name.lower()
        docs = env / f'users/{user_name}/appdata/local/lagrange_online_branch/Documents'
        if not docs.is_dir():
            continue
        print(f'Environment {env.name}: checking update markers...', flush=True)
        if args.sync_version:
            if not markers_match(docs, NORMAL):
                raise RuntimeError('Refresh resource caches before syncing version metadata.')
            normal_file = NORMAL.parent / 'user_default.json'
            local_file = docs.parent / 'user_default.json'
            if normal_file.is_file() and local_file.is_file():
                version_jobs.append((env, normal_file, local_file))
            continue
        if args.quick and markers_match(docs, NORMAL):
            quick_envs.append(env.name)
            print(f'Environment {env.name}: update markers match; full scan skipped.', flush=True)
            continue
        if args.reset_stale:
            reset_jobs.append((env, docs))
            print(f'Environment {env.name}: outdated resources will be archived; no full scan.', flush=True)
            continue
        protected.update(protected_files(env))
        print(f'Environment {env.name}: scanning changed assets...', flush=True)
        for destination, relative in candidates(docs):
            if time.monotonic() - started > args.timeout:
                raise RuntimeError('Cache scan timed out. No repair has been applied.')
            source = NORMAL / relative
            if not source.is_file():
                missing += 1
                continue
            matched += 1
            if matched % 250 == 0:
                print(f'Checked {matched} cached files...', flush=True)
            if args.quick and relative.as_posix().lower() not in MARKERS:
                source_stat, destination_stat = source.stat(), destination.stat()
                if (source_stat.st_size, source_stat.st_mtime_ns) == (
                        destination_stat.st_size, destination_stat.st_mtime_ns):
                    continue
            old_hash = digest(destination)
            new_hash = digest(source)
            if old_hash != new_hash:
                changes.append({'destination': str(destination), 'source': str(source),
                                'relative': str(destination.relative_to(ROOT)),
                                'old_hash': old_hash, 'new_hash': new_hash,
                                'bytes': source.stat().st_size})
    if args.sync_version:
        backup = ROOT / 'repair-backups' / datetime.now().strftime('version-sync-%Y%m%d-%H%M%S-%f')
        report = {'state': 'patch_versions_current', 'updated_environments': [],
                  'fields': {}, 'backup': None}
        saved = []
        try:
            for env, normal_file, local_file in version_jobs:
                backup_file = backup / env.name / 'user_default.json'
                fields = sync_patch_version_fields(normal_file, local_file, backup_file, args.apply)
                if fields:
                    report['fields'][env.name] = fields
                    if args.apply:
                        saved.append((local_file, backup_file))
                        report['updated_environments'].append(env.name)
            if report['fields']:
                report['state'] = 'patch_version_fields_synced' if args.apply else 'planned_patch_version_sync'
                report['backup'] = str(backup) if args.apply else None
        except Exception:
            for local_file, backup_file in reversed(saved):
                shutil.copy2(backup_file, local_file)
            raise
        write_report(args.report, report)
        print(json.dumps(report, ensure_ascii=True), flush=True)
        return
    if reset_jobs:
        if not args.apply:
            report = {'state': 'planned_resource_reset',
                      'environments': [env.name for env, _ in reset_jobs],
                      'quick_environments': quick_envs}
        else:
            backup = ROOT / 'repair-backups' / datetime.now().strftime('resource-reset-%Y%m%d-%H%M%S-%f')
            report = reset_resource_caches(reset_jobs, NORMAL, backup)
            report['quick_environments'] = quick_envs
        write_report(args.report, report)
        print(json.dumps({k: v for k, v in report.items() if k != 'archive_operations'}, ensure_ascii=True), flush=True)
        return
    report = {'state': 'planned', 'matched_files': matched,
              'missing_original_files_untouched': missing,
              'changed_files': len(changes),
              'replacement_bytes': sum(c['bytes'] for c in changes),
              'changes': changes, 'protected_file_count': len(protected)}
    report['quick_environments'] = quick_envs
    if not changes:
        report['state'] = 'update_markers_current' if quick_envs else 'cached_files_current'
    write_report(args.report, report)
    print(json.dumps({k: v for k, v in report.items() if k != 'changes'}), flush=True)
    if not args.apply or not changes:
        return
    if box_is_running():
        raise RuntimeError('2Box started during the scan; no changes applied.')
    backup = ROOT / 'repair-backups' / datetime.now().strftime('%Y%m%d-%H%M%S')
    backup.mkdir(parents=True, exist_ok=False)
    report['backup'] = str(backup)
    write_report(backup / 'manifest.json', report)
    applied = []
    try:
        for change in changes:
            source = Path(change['source'])
            destination = Path(change['destination'])
            saved = backup / change['relative']
            saved.parent.mkdir(parents=True, exist_ok=True)
            if digest(destination) != change['old_hash']:
                raise RuntimeError('Destination changed during repair; rolling back.')
            shutil.copy2(destination, saved)
            if digest(saved) != change['old_hash']:
                raise RuntimeError('Backup verification failed.')
            staged = destination.with_name(destination.name + '.2box-repair-tmp')
            if staged.exists():
                raise RuntimeError('Temporary file already exists.')
            try:
                shutil.copy2(source, staged)
                if digest(staged) != change['new_hash'] or digest(source) != change['new_hash']:
                    raise RuntimeError('Normal game asset changed during repair.')
                os.replace(staged, destination)
                applied.append(change)
            finally:
                if staged.exists():
                    staged.unlink()
        for change in changes:
            if digest(Path(change['destination'])) != change['new_hash']:
                raise RuntimeError('Installed asset verification failed.')
        if any(digest(Path(path)) != old for path, old in protected.items()):
            raise RuntimeError('A protected file changed during repair.')
        report['state'] = 'applied_and_file_verified'
        report['protected_files_unchanged'] = True
    except Exception:
        for change in reversed(applied):
            shutil.copy2(backup / change['relative'], change['destination'])
        report['state'] = 'rolled_back_after_error'
        write_report(args.report, report)
        write_report(backup / 'manifest.json', report)
        raise
    write_report(args.report, report)
    write_report(backup / 'manifest.json', report)
    print(json.dumps({'state': report['state'], 'backup': str(backup),
                      'updated_files': len(applied), 'protected_files_unchanged': True}))


if __name__ == '__main__':
    main()
