#!/usr/bin/env bash
# Build a self-contained wheels tarball for offline installation.
#
# Usage:
#   ./scripts/pack-release.sh
#
# Output: dist/openbad-<version>-wheels.tar.gz
#
# The tarball contains pre-downloaded .whl files for openbad plus all
# runtime dependencies.  Install on a target machine with:
#
#   tar xzf openbad-*-wheels.tar.gz -C /tmp/wheels
#   pip install --no-index --find-links=/tmp/wheels openbad
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

VERSION="$(python3 -c "
import re, pathlib
text = pathlib.Path('pyproject.toml').read_text()
m = re.search(r'^version = \"(.+?)\"', text, re.M)
print(m.group(1))
")"

echo "=== Packing release v${VERSION} ==="

# Clean slate
rm -rf dist/ build/ wheels/
mkdir -p dist wheels

# Build the openbad wheel
echo "Building wheel..."
pip install --quiet build 2>/dev/null || true
python3 -m build --wheel --outdir dist/

# Download all dependency wheels
echo "Downloading dependency wheels..."
pip download \
    --dest wheels/ \
    --only-binary=:all: \
    . 2>&1 | tail -5

# Include the openbad wheel itself
cp dist/*.whl wheels/

# Bundle wheels
TARBALL="dist/openbad-${VERSION}-wheels.tar.gz"
echo "Creating ${TARBALL}..."
tar czf "$TARBALL" -C wheels .

# Build WUI
WUI_TARBALL="dist/openbad-${VERSION}-wui.tar.gz"
if command -v npm &>/dev/null && [ -d wui-svelte ]; then
    echo "Building SvelteKit frontend..."
    cd wui-svelte && npm ci --silent && npm run build && cd ..
    tar czf "$WUI_TARBALL" -C wui-svelte/build .
    echo "WUI tarball: ${WUI_TARBALL} ($(du -h "$WUI_TARBALL" | cut -f1))"
else
    echo "Skipping WUI build (npm not found or wui-svelte missing)"
fi

echo "=== Done ==="
echo "Wheels tarball: ${TARBALL} ($(du -h "$TARBALL" | cut -f1))"
echo "Wheels:  $(ls wheels/*.whl | wc -l) packages"

# Cleanup
rm -rf wheels/ build/
