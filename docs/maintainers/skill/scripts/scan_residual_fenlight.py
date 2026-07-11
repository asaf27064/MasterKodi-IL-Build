#!/usr/bin/env python3
"""Audit the project working trees for residual FenLight references after migration.

Scans:
- Text files (.py, .xml, .json, .yml, .yaml, .txt, .md, .ini, .iss, .bat) for fenlight literals
- SQLite databases (.db) for fenlight in TEXT/BLOB columns

Reports each finding with location. Exits 1 if any unexpected residue found.

Acceptable residue (ignored by default):
- Changelog text in version JSONs
- Workflow YAML referencing skill name
- Folder name "FenLight_Estuary/" itself

Usage:
    cd C:\\Users\\asaf2\\Desktop\\kodi_project
    python ~/.claude/skills/masterkodi-il-builder/scripts/scan_residual_fenlight.py [--strict]
"""

import argparse
import os
import re
import sqlite3
import sys


TEXT_EXTS = {'.py', '.xml', '.json', '.yml', '.yaml', '.txt', '.md', '.ini', '.iss', '.bat', '.ps1'}
DB_EXTS = {'.db', '.sqlite', '.sqlite3'}

PATTERN = re.compile(r'fenlight|FenLight|FenlightAnony|plugin\.video\.fenlight', re.IGNORECASE)

EXCLUDE_DIRS = {'.git', '__pycache__', 'node_modules', '_work', 'Output'}

# Filename-level acceptable residue (filenames that may contain "fenlight" by design)
ACCEPTABLE_PATHS = {
    # The folder itself is named historically
    'FenLight_Estuary',
}

# Acceptable line-level patterns (matched on the matching line). If line matches any of these regexes, ignore.
ACCEPTABLE_LINE_PATTERNS = [
    re.compile(r'^\s*"changelog":', re.IGNORECASE),
    re.compile(r'FenLight Hebrew Mod Updater skill'),  # workflow body text
    re.compile(r'Migrated.*FenLight.*Gears'),  # commit/changelog
    re.compile(r'#.*FenLight'),  # comments mentioning the migration
]


def scan_text_files(roots, strict=False):
    findings = []
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
            for fn in filenames:
                ext = os.path.splitext(fn)[1].lower()
                if ext not in TEXT_EXTS:
                    continue
                fp = os.path.join(dirpath, fn)
                try:
                    with open(fp, 'r', encoding='utf-8', errors='replace') as fh:
                        for line_no, line in enumerate(fh, 1):
                            if not PATTERN.search(line):
                                continue
                            if not strict and any(p.search(line) for p in ACCEPTABLE_LINE_PATTERNS):
                                continue
                            findings.append((fp, line_no, line.rstrip()[:160]))
                except (OSError,):
                    continue
    return findings


def scan_databases(roots):
    findings = []
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
            for fn in filenames:
                ext = os.path.splitext(fn)[1].lower()
                if ext not in DB_EXTS:
                    continue
                fp = os.path.join(dirpath, fn)
                try:
                    con = sqlite3.connect(fp)
                    cur = con.cursor()
                    tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")]
                    for t in tables:
                        try:
                            cols = [c[1] for c in cur.execute(f"PRAGMA table_info('{t}')")]
                            for c in cols:
                                rows = cur.execute(
                                    f'SELECT count(*), MIN(CAST("{c}" AS TEXT)) FROM "{t}" WHERE CAST("{c}" AS TEXT) LIKE ?',
                                    ('%fenlight%',)
                                ).fetchone()
                                if rows and rows[0]:
                                    sample = (rows[1] or '')[:140]
                                    findings.append((fp, f'{t}.{c}', rows[0], sample))
                        except sqlite3.OperationalError:
                            continue
                    con.close()
                except sqlite3.Error:
                    continue
    return findings


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--root', default='.', help='Project root (default: current dir)')
    ap.add_argument('--strict', action='store_true',
                    help='Report ALL fenlight occurrences (no acceptable-residue exceptions)')
    args = ap.parse_args()

    project_root = os.path.abspath(args.root)
    scan_roots = []
    for sub in ('pov-modified-heb', 'FenLight_Estuary', 'Arctic_Fuse_Skin', 'MasterKodi_Build'):
        p = os.path.join(project_root, sub)
        if os.path.isdir(p):
            scan_roots.append(p)

    if not scan_roots:
        # Fall back to scanning the cwd directly
        scan_roots = [project_root]

    print(f"Scanning roots: {[os.path.relpath(r, project_root) for r in scan_roots]}")
    print(f"Strict mode: {args.strict}\n")

    text_findings = scan_text_files(scan_roots, strict=args.strict)
    print(f"=== Text-file findings: {len(text_findings)} ===")
    for fp, line_no, line in text_findings[:50]:
        rel = os.path.relpath(fp, project_root)
        print(f"  {rel}:{line_no}: {line}")
    if len(text_findings) > 50:
        print(f"  ... and {len(text_findings) - 50} more")

    db_findings = scan_databases(scan_roots)
    print(f"\n=== Database findings: {len(db_findings)} ===")
    for fp, col, n, sample in db_findings:
        rel = os.path.relpath(fp, project_root)
        print(f"  {rel} [{col}]: {n} rows; first: {sample!r}")

    total = len(text_findings) + len(db_findings)
    print(f"\nTotal: {total} findings")
    sys.exit(0 if total == 0 else 1)


if __name__ == '__main__':
    main()
