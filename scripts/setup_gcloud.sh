#!/usr/bin/env bash
# One-time dev setup for phase 4: gcloud CLI inside WSL2 Ubuntu, next to
# the Docker Engine it will authenticate for Artifact Registry pushes
# (PHASE4_PLAN Q4). C: stays untouched; the CLI lives in the distro's
# vhdx on D:.
# Run as root from Windows:
#   wsl -d Ubuntu-22.04 -u root -- bash -c "tr -d '\r' < /mnt/c/OM_Source/FlightIntelAgent/scripts/setup_gcloud.sh | bash"
# Idempotent: safe to rerun; skips anything that already exists.
# Auth is NOT here: `gcloud auth login` is interactive and owner-side.
set -euo pipefail

if command -v gcloud >/dev/null 2>&1; then
  echo "gcloud already installed: $(gcloud --version | head -1)"
else
  apt-get update -qq
  apt-get install -y -qq apt-transport-https ca-certificates gnupg curl >/dev/null
  if [ ! -f /usr/share/keyrings/cloud.google.gpg ]; then
    curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg \
      | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg
  fi
  if [ ! -f /etc/apt/sources.list.d/google-cloud-sdk.list ]; then
    echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" \
      > /etc/apt/sources.list.d/google-cloud-sdk.list
  fi
  apt-get update -qq
  apt-get install -y -qq google-cloud-cli >/dev/null
fi

echo "gcloud: $(gcloud --version | head -1)"
