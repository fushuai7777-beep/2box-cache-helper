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
from datetime import datetime


ROOT = Path(sys.executable if getattr(sys, 'frozen', False) else __file__).resolve().parent
NORMAL = Path(os.environ['LOCALAPPDATA']) / 'lagrange_online_branch/Documents'
AREAS = {'patch', 'res', 'res_ex', 'script'}
SKIP = {'cache.lock', 'share_image.jpg'}
MARKERS = ('patch/patch_list', 'res/all_img_size.txt', 'res/all_plist.txt')


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def box_is_running():
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
            if entry.name.lower() == '2box.exe':
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--quick', action='store_true')
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
        if args.quick and markers_match(docs, NORMAL):
            quick_envs.append(env.name)
            print(f'Environment {env.name}: update markers match; full scan skipped.', flush=True)
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
