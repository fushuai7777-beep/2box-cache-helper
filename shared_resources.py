import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

import repair_2box_assets as repair

AREAS = ('patch', 'res', 'res_ex', 'script')


def guard_idle():
    if repair.box_is_running() or repair.box_is_running('infinite_lagrange_cn.exe'):
        raise RuntimeError('Close all game windows and 2Box first.')


def environment_docs(root, env_id):
    if type(env_id) is not int or not 1 <= env_id <= 999:
        raise ValueError('Invalid environment number.')
    root = Path(root).resolve(strict=True)
    docs = root / f'Env/{env_id}/users/administrator/appdata/local/lagrange_online_branch/Documents'
    for candidate in (docs, *docs.parents):
        if candidate == root:
            break
        if candidate.is_junction() or candidate.is_symlink():
            raise RuntimeError('Unknown connection in environment profile path.')
    if not docs.resolve().is_relative_to(root) or not docs.is_dir():
        raise RuntimeError('Environment directory is missing or outside the 2Box root.')
    return docs


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=True, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def create_junction(link, target):
    environment = dict(os.environ, BOX_LINK=str(link), BOX_TARGET=str(target))
    subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                    "$ErrorActionPreference='Stop'; New-Item -ItemType Junction -Path $env:BOX_LINK -Target $env:BOX_TARGET | Out-Null"],
                   env=environment, check=True, capture_output=True)
    if not link.is_junction() or link.resolve() != target.resolve():
        raise RuntimeError('Directory connection verification failed.')


def locations(root, env_id):
    root = Path(root).resolve(strict=True)
    docs = environment_docs(root, env_id)
    shared = root / 'SharedResources/current'
    if shared.is_junction() or shared.is_symlink() or shared.parent.is_junction() or shared.parent.is_symlink() or not shared.resolve().is_relative_to(root):
        raise RuntimeError('Shared resource path escaped the 2Box root.')
    journal = root / f'SharedResources/migration-env{env_id}.json'
    return docs, shared, journal


def rollback_migration(root, env_id):
    guard_idle()
    docs, shared, journal = locations(root, env_id)
    record = json.loads(journal.read_text(encoding='utf-8'))
    if record.get('environment') != env_id or any(area not in AREAS for area in record['areas']):
        raise RuntimeError('Invalid migration journal.')
    if record.get('resource_backup_pruned'):
        raise RuntimeError('Resource backup was deduplicated; it is not a complete rollback snapshot.')
    owns_resources = record.get('owns_resources', True)
    if owns_resources:
        for other in journal.parent.glob('migration-env*.json'):
            if other != journal and json.loads(other.read_text(encoding='utf-8')).get('state') == 'migrated':
                raise RuntimeError('Detach other shared environments before restoring the resource owner.')
    saved_root = shared if owns_resources else shared.parent / f'environment-backups/env{env_id}'
    if saved_root.is_junction() or saved_root.is_symlink() or not saved_root.resolve().is_relative_to(Path(root).resolve()):
        raise RuntimeError('Invalid environment rollback resource directory.')
    for area in reversed(record['areas']):
        original, moved = docs / area, saved_root / area
        if original.is_junction() or original.is_symlink():
            if original.resolve() != (shared / area).resolve():
                raise RuntimeError('Unexpected directory connection; refusing removal.')
            original.rmdir()
        if moved.exists():
            if original.exists():
                raise RuntimeError('Both resource locations exist; refusing overwrite.')
            if not moved.resolve().is_relative_to(Path(root).resolve()):
                raise RuntimeError('Resource restore target escaped the 2Box root.')
            os.replace(moved, original)
    record['state'] = 'rolled_back'
    save_json(journal, record)
    return record


def attach_environment(root, env_id):
    guard_idle()
    docs, current, journal = locations(root, env_id)
    if journal.exists():
        record = json.loads(journal.read_text(encoding='utf-8'))
        if record.get('state') == 'migrated' and all((docs / area).is_junction() and (docs / area).resolve() == (current / area).resolve() for area in AREAS):
            return record
        if record.get('state') != 'rolled_back':
            raise RuntimeError('Existing migration needs review before attachment.')
    backup = current.parent / f'environment-backups/env{env_id}'
    if backup.is_junction() or backup.is_symlink() or not backup.resolve().is_relative_to(Path(root).resolve()):
        raise RuntimeError('Invalid peer backup path.')
    for area in AREAS:
        if not (current / area).is_dir() or (current / area).is_junction() or (current / area).is_symlink():
            raise RuntimeError('Shared resource directory is not ready.')
        original = docs / area
        if not original.is_dir() or original.is_junction() or original.is_symlink() or (backup / area).exists():
            raise RuntimeError('Peer resource directory is invalid or backup is occupied.')
    backup.mkdir(parents=True, exist_ok=True)
    record = {'state': 'attaching', 'environment': env_id, 'owns_resources': False,
              'areas': [], 'game_entry_verified': False}
    save_json(journal, record)
    try:
        for area in AREAS:
            guard_idle()
            record['areas'].append(area)
            save_json(journal, record)
            os.replace(docs / area, backup / area)
            create_junction(docs / area, current / area)
        record['state'] = 'migrated'
        save_json(journal, record)
    except Exception:
        rollback_migration(root, env_id)
        raise
    return record


def migrate_environment(root, env_id):
    guard_idle()
    docs, shared, journal = locations(root, env_id)
    if journal.exists():
        previous = json.loads(journal.read_text(encoding='utf-8'))
        if previous.get('state') == 'migrated':
            if all((docs / area).is_junction() and (docs / area).resolve() == (shared / area).resolve() for area in AREAS):
                return previous
            raise RuntimeError('Migration connections changed; review before continuing.')
        if previous.get('state') != 'rolled_back':
            raise RuntimeError('Incomplete migration; run rollback before proceeding.')
    for area in AREAS:
        original, moved = docs / area, shared / area
        if not original.is_dir() or original.is_junction() or original.is_symlink() or moved.exists():
            raise RuntimeError('Resource directory is missing, linked or destination is occupied.')
    shared.mkdir(parents=True, exist_ok=True)
    record = {'state': 'moving', 'environment': env_id, 'areas': [], 'game_entry_verified': False}
    save_json(journal, record)
    try:
        for area in AREAS:
            guard_idle()
            record['areas'].append(area)
            save_json(journal, record)
            os.replace(docs / area, shared / area)
            create_junction(docs / area, shared / area)
        record['state'] = 'migrated'
        save_json(journal, record)
    except Exception:
        rollback_migration(root, env_id)
        raise
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['migrate', 'attach', 'rollback', 'sync', 'launch', 'confirm-update', 'recover-update'])
    parser.add_argument('--root', type=Path, default=Path(sys.executable if getattr(sys, 'frozen', False) else __file__).resolve().parent)
    parser.add_argument('--environment', type=int, default=2)
    parser.add_argument('--normal', type=Path, default=Path(os.environ['LOCALAPPDATA']) / 'lagrange_online_branch/Documents')
    args = parser.parse_args()
    if args.operation in {'migrate', 'attach', 'rollback'}:
        operation = {'migrate': migrate_environment, 'attach': attach_environment, 'rollback': rollback_migration}[args.operation]
        result = operation(args.root, args.environment)
    elif args.operation == 'confirm-update':
        result = confirm_update(args.root)
    elif args.operation == 'recover-update':
        from shared_update import recover_update
        result = recover_update(args.root)
    else:
        result = sync_shared_resources(args.root, args.normal)
        if args.operation == 'launch':
            if not check_ready(args.root, args.normal):
                raise RuntimeError('Shared cache is not ready. 2Box was not started.')
            subprocess.Popen([str(args.root / '2Box.exe')], cwd=args.root)
    print(json.dumps(result), flush=True)


def sync_shared_resources(root, normal, budget=2 * 1024 ** 3):
    from shared_update import sync_shared_resources as operation
    return operation(root, normal, budget)


def check_ready(root, normal):
    from shared_update import check_ready as operation
    return operation(root, normal)


def confirm_update(root):
    from shared_update import confirm_update as operation
    return operation(root)


if __name__ == '__main__':
    main()
