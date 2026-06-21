#!/usr/bin/env bash
# Build a ready-to-publish Homebrew formula for flip.
#
# Flow:
#   1. Verify the working tree is clean and the version tag exists
#      (creates + pushes it if missing, unless --no-tag).
#   2. Download the GitHub source tarball for that tag.
#   3. Compute its sha256.
#   4. Render Formula/flip.rb -> dist/flip.rb with the real url + sha256.
#
# The resulting dist/flip.rb is what you copy into your homebrew-tap repo:
#   cp dist/flip.rb ../homebrew-tap/Formula/flip.rb
#
# Usage:
#   scripts/build_formula.sh                 # use version from pyproject.toml
#   scripts/build_formula.sh 0.2.0           # override version
#   scripts/build_formula.sh --no-tag 0.1.0  # tag already pushed; just build
#   scripts/build_formula.sh --dry-run       # don't tag/push, only render

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GITHUB_USER="ffy6511"
GITHUB_REPO="flip"
TEMPLATE="$REPO_ROOT/Formula/flip.rb"
OUT_DIR="$REPO_ROOT/dist"
OUT_FILE="$OUT_DIR/flip.rb"

PUSH_TAG=1
DRY_RUN=0
VERSION=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-tag)  PUSH_TAG=0; shift ;;
    --dry-run) DRY_RUN=1; PUSH_TAG=0; shift ;;
    -h|--help)
      sed -n '3,18p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *)
      if [[ -n "$VERSION" ]]; then
        echo "error: unexpected argument: $1" >&2; exit 2
      fi
      VERSION="$1"; shift ;;
  esac
done

# Resolve version from pyproject.toml if not given.
if [[ -z "$VERSION" ]]; then
  VERSION="$(grep -E '^version[[:space:]]*=' "$REPO_ROOT/pyproject.toml" \
              | head -1 | sed -E 's/.*"([^"]+)".*/\1/')"
  if [[ -z "$VERSION" ]]; then
    echo "error: could not read version from pyproject.toml" >&2
    exit 1
  fi
fi

TAG="v$VERSION"
URL="https://github.com/$GITHUB_USER/$GITHUB_REPO/archive/refs/tags/$TAG.tar.gz"

echo "==> flip $TAG"
echo "    url: $URL"

cd "$REPO_ROOT"

# --- 1. working tree must be clean (unless --dry-run) ----------------------
if [[ "$DRY_RUN" -eq 0 ]] && [[ -n "$(git status --porcelain)" ]]; then
  echo "error: working tree is not clean; commit or stash first" >&2
  git status --short >&2
  exit 1
fi

# --- 2. ensure the tag exists & is pushed ----------------------------------
TAG_EXISTS_LOCALLY="$(git tag -l "$TAG")"
if [[ -z "$TAG_EXISTS_LOCALLY" ]]; then
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "    [dry-run] would create tag $TAG"; SHA="0000000000000000000000000000000000000000000000000000000000000000"
  elif [[ "$PUSH_TAG" -eq 0 ]]; then
    echo "error: tag $TAG not found locally and --no-tag given" >&2
    exit 1
  else
    echo "==> creating tag $TAG"
    git tag "$TAG"
    echo "==> pushing tag $TAG"
    git push origin "$TAG"
    echo "    note: wait a few seconds for GitHub to publish the tarball."
    sleep 5
  fi
fi

# --- 3. download tarball + compute sha256 ----------------------------------
if [[ -z "${SHA:-}" ]]; then
  TMP_TARBALL="$(mktemp -t flip-src.XXXXXX).tar.gz"
  trap 'rm -f "$TMP_TARBALL"' EXIT
  echo "==> downloading tarball"
  HTTP_CODE="$(curl -sSL -w '%{http_code}' -o "$TMP_TARBALL" "$URL")"
  if [[ "$HTTP_CODE" != "200" ]]; then
    echo "error: tarball download failed (HTTP $HTTP_CODE)" >&2
    echo "       the tag may not be published yet; retry in a few seconds." >&2
    exit 1
  fi
  SHA="$(shasum -a 256 "$TMP_TARBALL" | awk '{print $1}')"
fi
echo "    sha256: $SHA"

# --- 4. render the formula --------------------------------------------------
[[ -d "$OUT_DIR" ]] || mkdir -p "$OUT_DIR"
# Replace the url/sha256 lines. Anchored on the known template shape so we
# don't accidentally rewrite a comment.
NEW_URL_LINE="  url \"$URL\""
NEW_SHA_LINE="  sha256 \"$SHA\""
awk -v new_url="$NEW_URL_LINE" -v new_sha="$NEW_SHA_LINE" '
  /^  url "/    { print new_url; next }
  /^  sha256 "/ { print new_sha; next }
  { print }
' "$TEMPLATE" > "$OUT_FILE"

echo "==> wrote $OUT_FILE"
echo
echo "Next: copy it into your tap repo"
echo "  cp $OUT_FILE <path-to>/homebrew-tap/Formula/flip.rb"
echo "  cd <path-to>/homebrew-tap && git add Formula/flip.rb && git commit -m 'flip $TAG'"
