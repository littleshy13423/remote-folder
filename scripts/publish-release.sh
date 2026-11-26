#!/bin/bash
# Publish the Windows EXE and Linux binary for one version tag.
set -euo pipefail
fail() { echo "::error::$1"; echo "$1" >&2; exit 1; }

[[ "${RELEASE_TAG:-}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "Use a version tag such as v0.1.0"
test -f dist/windows/remote-folder.exe
test -f dist/linux/remote-folder-linux-x64
sed -i 's/\r$//' dist/windows/SHA256SUMS.txt dist/linux/SHA256SUMS.txt
(cd dist/windows && sha256sum --check SHA256SUMS.txt) || fail "SHA256 check failed for remote-folder.exe"
(cd dist/linux && sha256sum --check SHA256SUMS.txt) || fail "SHA256 check failed for remote-folder-linux-x64"

stage=$(mktemp -d)
cp dist/windows/remote-folder.exe dist/linux/remote-folder-linux-x64 "$stage/"
chmod +x "$stage/remote-folder-linux-x64"
(
  cd "$stage"
  sha256sum remote-folder.exe remote-folder-linux-x64 > SHA256SUMS.txt
  sha256sum --check SHA256SUMS.txt
)

files=(
  "$stage/remote-folder.exe"
  "$stage/remote-folder-linux-x64"
  "$stage/SHA256SUMS.txt"
)
if gh release view "$RELEASE_TAG" --json isDraft,assets >/tmp/release.json 2>/tmp/release.err; then
  state=$(python3 -c 'import json; data=json.load(open("/tmp/release.json")); names=sorted(a.get("name","") for a in (data.get("assets") or [])); kind="draft" if data.get("isDraft") else "published"; print(kind + ":" + ",".join(names))')
  echo "Existing release: $state"
  kind=${state%%:*}
  if [ "$kind" != "draft" ]; then
    fail "Release $RELEASE_TAG already exists and will not be overwritten ($state)"
  fi
  if ! up_err=$(gh release upload "$RELEASE_TAG" "${files[@]}" --clobber 2>&1); then
    fail "Could not upload files to the existing draft ($state): ${up_err//$'\n'/ }"
  fi
  if ! edit_err=$(gh release edit "$RELEASE_TAG" --draft=false --latest 2>&1); then
    fail "Could not publish the existing draft ($state): ${edit_err//$'\n'/ }"
  fi
  exit 0
fi
if ! create_err=$(gh release create "$RELEASE_TAG" "${files[@]}" \
  --verify-tag --draft --title "remote-folder $RELEASE_TAG" \
  --notes-file .github/release-notes.md 2>&1); then
  fail "gh release create failed: ${create_err//$'\n'/ }"
fi
if ! edit_err=$(gh release edit "$RELEASE_TAG" --draft=false --latest 2>&1); then
  fail "Could not publish the draft: ${edit_err//$'\n'/ }"
fi
