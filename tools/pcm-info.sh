#!/usr/bin/env bash
set -euo pipefail

ZIP="${1:?Usage: $0 <package.zip> [extracted-folder]}"

BASENAME="$(basename "$ZIP" .zip)"
EXTRACTED="${2:-$BASENAME}"

SHA256="$(sha256sum "$ZIP" | awk '{print $1}')"
DOWNLOAD_SIZE="$(stat -c %s "$ZIP")"
INSTALL_SIZE="$(du -sb "$EXTRACTED" | awk '{print $1}')"

cat <<EOF
{
  "download_url": "REPLACE_WITH_GITHUB_RELEASE_URL/$ZIP",
  "download_sha256": "$SHA256",
  "download_size": $DOWNLOAD_SIZE,
  "install_size": $INSTALL_SIZE
}
EOF
