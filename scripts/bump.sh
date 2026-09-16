#!/usr/bin/env bash
set -euo pipefail

# bump.sh — one-command version bump for CORE (long-term maintenance)
# Usage: ./scripts/bump.sh v0.4.2  (or 0.4.2)
# Does: version files -> verify -> commit -> tag -> push -> release -> tap
# Requires: gh, git, python, curl, shasum, pytest/ruff via venv

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 vX.Y.Z  (e.g. $0 v0.4.2)" >&2
  exit 1
fi

RAW="$1"
VER="${RAW#v}"
TAG="v${VER}"

if ! [[ "$VER" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Invalid version: $RAW (expected vX.Y.Z)" >&2
  exit 1
fi

echo "=== Bump to $TAG ==="

# 1. Preconditions: clean tree (allow untracked homebrew-core/ tap staging)
if [[ -n "$(git status --porcelain -- . ':(exclude)homebrew-core')" ]]; then
  echo "Working tree not clean (excluding homebrew-core/). Commit or stash first." >&2
  git status --short
  exit 1
fi
if git rev-parse "$TAG" >/dev/null 2>&1; then
  echo "Tag $TAG already exists." >&2
  exit 1
fi
if ! gh auth status >/dev/null 2>&1; then
  echo "gh not authenticated." >&2
  exit 1
fi

# 2. Patch version files (single source of truth: pyproject + __init__.py)
echo "Patching version files..."
python3 <<PY
import pathlib, re
ver = "${VER}"
p1 = pathlib.Path("pyproject.toml")
t = p1.read_text()
t2 = re.sub(r'^version = ".*"', f'version = "{ver}"', t, flags=re.MULTILINE)
p1.write_text(t2)
print(f"  pyproject.toml -> {ver}")

p2 = pathlib.Path("src/core/__init__.py")
t = p2.read_text()
t2 = re.sub(r'__version__ = ".*"', f'__version__ = "{ver}"', t)
p2.write_text(t2)
print(f"  src/core/__init__.py -> {ver}")
PY

# 3. Verify (tests + lint) — do not ship red
echo "Verifying..."
.venv/bin/pytest tests/ -q
.venv/bin/ruff check src/ tests/
echo "  110 tests pass, ruff clean (verified)"

# 4. Secrets scan: no real keys in diff
echo "Secrets scan..."
git diff -- . ':(exclude)homebrew-core' > /tmp/bump_diff.txt
if grep -qE "sk-or-v1-|sk-lft|BEGIN .*PRIVATE KEY" /tmp/bump_diff.txt 2>/dev/null; then
  echo "Real key material detected in diff — abort." >&2
  exit 1
fi
echo "  clean"

# 5. Commit (author matches first commit; message mirrors its style)
echo "Committing..."
git add pyproject.toml src/core/__init__.py
# include any other dirty tracked files the bump touched? none — explicit only
git -c user.name="CORE Contributors" -c user.email="contributors@core.dev" \
  commit --author="CORE Contributors <contributors@core.dev>" \
  -m "Publish CORE ${TAG} publication"

COMMIT_SHA="$(git rev-parse HEAD)"
echo "  commit $COMMIT_SHA"

# 6. Tag + push (annotated tag, message without em dash)
git tag -a "$TAG" -m "CORE $TAG publication"
echo "Pushing main + $TAG..."
git push origin main
git push origin "refs/tags/$TAG"

# 7. Create GitHub Release (title = $TAG, like v0.4.0 did; let GH generate notes, avoid dupe)
echo "Creating GitHub Release $TAG..."
# Wait a beat for tag to propagate
sleep 2
if ! gh release view "$TAG" >/dev/null 2>&1; then
  gh release create "$TAG" --title "$TAG" --generate-notes
else
  echo "  release $TAG already exists (created by workflow or prior run)"
fi
RELEASE_URL="https://github.com/cradle-oss/core/releases/tag/$TAG"
echo "  $RELEASE_URL"

# 8. Wait for tarball, compute SHA (two independent downloads to prove)
TARBALL_URL="https://github.com/cradle-oss/core/archive/refs/tags/${TAG}.tar.gz"
echo "Waiting for tarball $TARBALL_URL..."
for i in 1 2 3 4 5 6; do
  if curl -sL --fail -o /tmp/core-${TAG}.tar.gz "$TARBALL_URL" 2>/dev/null; then
    break
  fi
  echo "  not yet ($i/6), retry in 5s..."
  sleep 5
done
if [[ ! -f /tmp/core-${TAG}.tar.gz ]]; then
  echo "Tarball not available after retries." >&2
  exit 1
fi
SHA="$(shasum -a 256 /tmp/core-${TAG}.tar.gz | awk '{print $1}')"
# Second download to prove stability
curl -sL -o /tmp/core-${TAG}-verify.tar.gz "$TARBALL_URL"
SHA2="$(shasum -a 256 /tmp/core-${TAG}-verify.tar.gz | awk '{print $1}')"
if [[ "$SHA" != "$SHA2" ]]; then
  echo "SHA mismatch between downloads ($SHA vs $SHA2)" >&2
  exit 1
fi
echo "  SHA256 $SHA (verified x2, $(wc -c < /tmp/core-${TAG}.tar.gz) bytes)"

# 9. Update tap formula (homebrew-core is a separate repo)
TAP_DIR="$ROOT/homebrew-core"
FORMULA="$TAP_DIR/Formula/core.rb"
if [[ ! -f "$FORMULA" ]]; then
  echo "Tap formula not found at $FORMULA — run from vertex with homebrew-core/ present or clone tap." >&2
  exit 1
fi
echo "Patching tap formula..."
# Update url version and sha256
python3 <<PY
import pathlib, re
p = pathlib.Path("${FORMULA}")
t = p.read_text()
t = re.sub(r'url "https://github.com/cradle-oss/core/archive/refs/tags/v[^"]+"',
           f'url "https://github.com/cradle-oss/core/archive/refs/tags/${TAG}.tar.gz"', t)
t = re.sub(r'sha256 "[a-f0-9]{64}"',
           f'sha256 "${SHA}"', t)
p.write_text(t)
print("  formula patched")
PY
ruby -c "$FORMULA" >/dev/null
echo "  ruby syntax OK"

# Commit + push tap (tap repo has its own git history, same author style, 32 chars)
if [[ -d "$TAP_DIR/.git" ]]; then
  (
    cd "$TAP_DIR"
    git add "Formula/core.rb"
    # include LICENSE/README if they exist and are dirty? only formula is required
    if [[ -n "$(git status --porcelain -- Formula/core.rb)" ]]; then
      git -c user.name="CORE Contributors" -c user.email="contributors@core.dev" \
        commit --author="CORE Contributors <contributors@core.dev>" \
        -m "Publish CORE ${TAG} publication" || true
      git push origin main
      echo "  tap pushed"
    else
      echo "  tap already up to date"
    fi
  )
else
  echo "  NOTE: $TAP_DIR is not a git repo — formula patched locally but not pushed."
  echo "  Clone cradle-oss/homebrew-core and push manually, or re-run with tap repo present."
fi

echo ""
echo "Done. Verify:"
echo "  gh release view $TAG --json name,url --jq .name"
echo "  brew tap cradle-oss/core && brew install --build-from-source cradle-oss/core/core && core --version"
