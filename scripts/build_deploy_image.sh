#!/usr/bin/env bash
# Build the data-carrying deploy image (PHASE4_PLAN Q1). Run from the
# repo root INSIDE WSL (where the Docker Engine lives):
#   cd /mnt/c/OM_Source/FlightIntelAgent && bash scripts/build_deploy_image.sh
# Optional arg: path to the products.db snapshot to bake.
set -euo pipefail

SNAPSHOT="${1:-/mnt/c/OM_Source/AirflowOSky/data/processed/products.db}"
STAMP="$(date +%Y%m%d)"
TAG="flightintel:0.1.2-data${STAMP}"

if [ ! -f "$SNAPSHOT" ]; then
  echo "snapshot not found: $SNAPSHOT" >&2
  exit 1
fi

mkdir -p .deploy
cp "$SNAPSHOT" .deploy/products.db

docker build -f Dockerfile.deploy -t "$TAG" .
echo "built: $TAG (snapshot: $SNAPSHOT, $(du -h .deploy/products.db | cut -f1))"
