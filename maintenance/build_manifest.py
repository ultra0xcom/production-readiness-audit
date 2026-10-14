#!/usr/bin/env python3
"""Write or check MANIFEST.json with SHA-256 hashes of all package files.

Hashes establish byte identity, not truth. The manifest does not hash itself.
The previous prompt hash records the unpublished German version 3.2.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

PACKAGE = Path(__file__).resolve().parent.parent
# SHA-256 of the German 3.2 master prompt; no archive is needed to build this package.
PREVIOUS_PROMPT_VERSION = '3.2'
PREVIOUS_PROMPT_SHA256 = '1cadd17c12e36c7f8c7970de1c5c00449d8053c2437e0fce1d0ade5c66adfcb4'
SKIPPED_DIRS = {'__pycache__', '.git', 'audit-results'}
# Not package content: version control, the marker of an audit work copy of this package.
SKIPPED_FILES = {'MANIFEST.json', '.audit-workspace.json'}


def package_files() -> list[dict[str, object]]:
    files = []
    for path in sorted(PACKAGE.rglob('*')):
        relative = path.relative_to(PACKAGE)
        if not path.is_file() or relative.as_posix() in SKIPPED_FILES or SKIPPED_DIRS & set(relative.parts):
            continue
        data = path.read_bytes()
        files.append({'name': relative.as_posix(), 'size_bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
    return files


def build() -> dict[str, object]:
    return {
        'package': 'production-readiness-audit',
        'prompt_version': '4.0',
        'previous_prompt_version': PREVIOUS_PROMPT_VERSION,
        'previous_prompt_sha256': PREVIOUS_PROMPT_SHA256,
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
        expected = {item['name']: item['sha256'] for item in manifest['files']}
        recorded = {item['name']: item['sha256'] for item in current.get('files', [])}
        differences = sorted(name for name in set(expected) | set(recorded) if expected.get(name) != recorded.get(name))
        print(json.dumps({'identical': not differences, 'differences': differences}, indent=2))
        return 0 if not differences else 1
    with open(target, 'w', encoding='utf-8', newline='\n') as handle:  # Path.write_text(newline=) needs 3.10
        handle.write(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'written': str(target), 'files': len(manifest['files'])}, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
