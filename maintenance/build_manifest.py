#!/usr/bin/env python3
"""Write or check MANIFEST.json with SHA-256 hashes of all package files.

Hashes establish byte identity, not truth. The manifest does not hash itself.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

PACKAGE = Path(__file__).resolve().parent.parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(PACKAGE / 'tools'))
import audit_tools  # noqa: E402
SKIPPED_DIRS = {'__pycache__', '.git', 'audit-results'}
# Not package content: version control, the marker of an audit work copy of this package.
SKIPPED_FILES = {'MANIFEST.json', '.audit-workspace.json'}


def package_paths() -> list[Path]:
    """Tracked and nonignored package paths; local reports/archives are not shipped."""
    try:
        top = subprocess.run(['git', 'rev-parse', '--show-toplevel'], cwd=PACKAGE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        if top.returncode == 0 and Path(top.stdout.decode('utf-8').strip()).resolve() == PACKAGE.resolve():
            result = subprocess.run(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
                                    cwd=PACKAGE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        else:
            result = None
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        result = None
    if result is not None and result.returncode == 0:
        candidates = [PACKAGE / name for name in set(result.stdout.decode('utf-8').split('\0')) if name]
    else:
        ignores = [line.strip() for line in (PACKAGE / '.gitignore').read_text(encoding='utf-8').splitlines()
                   if line.strip() and not line.startswith('#')]
        candidates = [path for path in PACKAGE.rglob('*') if not any(
            audit_tools.is_excluded(path.relative_to(PACKAGE).as_posix(), [pattern])
            or '/' not in pattern and audit_tools.is_excluded(path.name, [pattern]) for pattern in ignores)]
    return sorted(path for path in candidates if path.is_file() and path.name != '.git'
                  and path.relative_to(PACKAGE).as_posix() != '.audit-workspace.json'
                  and not SKIPPED_DIRS & set(path.relative_to(PACKAGE).parts))


def package_files() -> list[dict[str, object]]:
    files = []
    for path in package_paths():
        relative = path.relative_to(PACKAGE)
        if relative.as_posix() in SKIPPED_FILES:
            continue
        data = path.read_bytes()
        files.append({'name': relative.as_posix(), 'size_bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
    return files


def build() -> dict[str, object]:
    return {
        'package': 'production-readiness-audit',
        'prompt_version': audit_tools.PROMPT_VERSION,
        'tool_version': audit_tools.TOOL_VERSION,
        'schema_version': audit_tools.SCHEMA_VERSION,
        'scope': 'Prompt package files and helper tools; not a software audit or model benchmark.',
        'files': package_files(),
        'note': 'This manifest does not hash itself. Hashes establish byte identity, not truth.',
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='only compare with the existing MANIFEST.json')
    args = parser.parse_args()
    manifest = build()
    target = PACKAGE / 'MANIFEST.json'
    if args.check:
        current = json.loads(target.read_text(encoding='utf-8'))
        expected = {item['name']: item for item in manifest['files']}
        recorded = {item['name']: item for item in current.get('files', [])}
        differences = sorted(name for name in set(expected) | set(recorded) if expected.get(name) != recorded.get(name))
        metadata = sorted(key for key in set(current) | set(manifest) if key != 'files'
                          and current.get(key) != manifest.get(key))
        identical = current == manifest
        print(json.dumps({'identical': identical, 'differences': differences, 'metadata_differences': metadata}, indent=2))
        return 0 if identical else 1
    with open(target, 'w', encoding='utf-8', newline='\n') as handle:  # Path.write_text(newline=) needs 3.10
        handle.write(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'written': str(target), 'files': len(manifest['files'])}, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
