#!/usr/bin/env bash
# Package the project (with its documents) and verify the archive.
# Run from anywhere. This script lives in tools/, but the source tree is the REPO ROOT — and
# `python3 -c "import core"` below only works from there.
set -u
cd "$(dirname "$0")/.." || exit 1
V=$(python3 -c "import core; print(core.APP_VERSION)")
TARBALL="../wikiportraits-eval-v$V.tar.gz"
rm -f "$TARBALL"
tar --exclude=cache --exclude=node_modules --exclude='*.out' --exclude='*.log' \
    --exclude='__pycache__' --exclude='*.tar.gz' --exclude='serve' --exclude='.venv*' \
    --exclude='.git' --exclude='.playwright-cli' --exclude='.DS_Store' \
    -s ",^\.,wikiportraits-eval-v$V," \
    -czf "$TARBALL" .
ls -la "$TARBALL"
echo "--- files in the archive: $(tar -tzf "$TARBALL" | grep -v '/$' | wc -l)"
echo "--- contents ---"
tar -tzf "$TARBALL" | sort | sed "s,^wikiportraits-eval-v$V/,  ,"
echo "--- documents present ---"
tar -tzf "$TARBALL" | grep -E 'PRD\.md|README\.md|HANDOFF\.md|DEVELOPER-NOTE|research/' | head -12
echo "--- excluded (should be empty) ---"
tar -tzf "$TARBALL" | grep -E 'node_modules|cache/|__pycache__|\.venv|\.playwright-cli' || echo "  none"
