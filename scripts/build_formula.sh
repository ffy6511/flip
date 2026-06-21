#!/usr/bin/env bash
# Render the Homebrew formula for a released flip tag.
#
# This script is intentionally side-effect free for git state. It never creates
# tags, pushes refs, or edits the tap repository directly. The GitHub Actions
# release workflow calls it after a tag push, then copies the rendered file into
# ffy6511/homebrew-tap.
#
# Usage:
#   scripts/build_formula.sh                         # use version from pyproject.toml
#   scripts/build_formula.sh 0.2.0                   # render tag v0.2.0
#   scripts/build_formula.sh --tag v0.2.0            # render an explicit tag
#   scripts/build_formula.sh --tag v0.2.0 --output /tmp/flip.rb

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GITHUB_OWNER="${GITHUB_OWNER:-ffy6511}"
GITHUB_REPO="${GITHUB_REPO:-flip}"
TEMPLATE="$REPO_ROOT/Formula/flip.rb"
OUT_FILE="$REPO_ROOT/dist/flip.rb"

VERSION=""
TAG=""

usage() {
  sed -n '3,13p' "$0" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tag)
      TAG="${2:-}"
      if [[ -z "$TAG" ]]; then
        echo "error: --tag requires a value" >&2
        exit 2
      fi
      shift 2
      ;;
    --output)
      OUT_FILE="${2:-}"
      if [[ -z "$OUT_FILE" ]]; then
        echo "error: --output requires a value" >&2
        exit 2
      fi
      shift 2
      ;;
    --template)
      TEMPLATE="${2:-}"
      if [[ -z "$TEMPLATE" ]]; then
        echo "error: --template requires a value" >&2
        exit 2
      fi
      shift 2
      ;;
    --owner)
      GITHUB_OWNER="${2:-}"
      if [[ -z "$GITHUB_OWNER" ]]; then
        echo "error: --owner requires a value" >&2
        exit 2
      fi
      shift 2
      ;;
    --repo)
      GITHUB_REPO="${2:-}"
      if [[ -z "$GITHUB_REPO" ]]; then
        echo "error: --repo requires a value" >&2
        exit 2
      fi
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      if [[ -n "$VERSION" ]]; then
        echo "error: unexpected argument: $1" >&2
        exit 2
      fi
      VERSION="$1"
      shift
      ;;
  esac
done

if [[ -z "$TAG" ]]; then
  if [[ -z "$VERSION" ]]; then
    VERSION="$(grep -E '^version[[:space:]]*=' "$REPO_ROOT/pyproject.toml" \
                | head -1 | sed -E 's/.*"([^"]+)".*/\1/')"
    if [[ -z "$VERSION" ]]; then
      echo "error: could not read version from pyproject.toml" >&2
      exit 1
    fi
  fi

  if [[ "$VERSION" == v* ]]; then
    TAG="$VERSION"
  else
    TAG="v$VERSION"
  fi
fi

if [[ ! -f "$TEMPLATE" ]]; then
  echo "error: formula template not found: $TEMPLATE" >&2
  exit 1
fi

URL="https://github.com/$GITHUB_OWNER/$GITHUB_REPO/archive/refs/tags/$TAG.tar.gz"

echo "==> rendering $GITHUB_OWNER/$GITHUB_REPO $TAG"
echo "    url: $URL"

TMP_TARBALL="$(mktemp -t flip-src.XXXXXX).tar.gz"
trap 'rm -f "$TMP_TARBALL"' EXIT

HTTP_CODE=""
for attempt in 1 2 3 4 5; do
  HTTP_CODE="$(curl -sSL --connect-timeout 20 --max-time 120 \
    -w '%{http_code}' -o "$TMP_TARBALL" "$URL" || true)"

  if [[ "$HTTP_CODE" == "200" ]]; then
    break
  fi

  if [[ "$attempt" -lt 5 ]]; then
    echo "    tarball not ready yet (HTTP ${HTTP_CODE:-curl failed}); retrying..."
    sleep 5
  fi
done

if [[ "$HTTP_CODE" != "200" ]]; then
  echo "error: tarball download failed (HTTP ${HTTP_CODE:-curl failed})" >&2
  echo "       check that tag $TAG exists on GitHub." >&2
  exit 1
fi

if command -v sha256sum >/dev/null 2>&1; then
  SHA="$(sha256sum "$TMP_TARBALL" | awk '{print $1}')"
elif command -v shasum >/dev/null 2>&1; then
  SHA="$(shasum -a 256 "$TMP_TARBALL" | awk '{print $1}')"
else
  echo "error: sha256sum or shasum is required" >&2
  exit 1
fi

echo "    sha256: $SHA"

mkdir -p "$(dirname "$OUT_FILE")"

NEW_URL_LINE="  url \"$URL\""
NEW_SHA_LINE="  sha256 \"$SHA\""
awk -v new_url="$NEW_URL_LINE" -v new_sha="$NEW_SHA_LINE" '
  /^  url "/    { print new_url; next }
  /^  sha256 "/ { print new_sha; next }
  { print }
' "$TEMPLATE" > "$OUT_FILE"

echo "==> wrote $OUT_FILE"
