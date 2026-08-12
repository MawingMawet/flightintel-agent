#!/usr/bin/env bash
# One-time dev setup for phase 3 (plan Q4): Docker Engine inside WSL2
# Ubuntu - NOT Docker Desktop (ADR 0008 direction; C: stays untouched,
# images live in the distro's vhdx on D:).
# Run as root from Windows:
#   wsl -d Ubuntu-22.04 -u root -- bash -c "tr -d '\r' < /mnt/c/OM_Source/FlightIntelAgent/scripts/setup_docker.sh | bash"
# Idempotent: safe to rerun; skips anything that already exists.
set -euo pipefail

if command -v docker >/dev/null 2>&1; then
  echo "docker already installed: $(docker --version)"
else
  apt-get update -qq
  apt-get install -y -qq ca-certificates curl >/dev/null
  install -m 0755 -d /etc/apt/keyrings
  if [ ! -f /etc/apt/keyrings/docker.asc ]; then
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
  fi
  if [ ! -f /etc/apt/sources.list.d/docker.list ]; then
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
      > /etc/apt/sources.list.d/docker.list
  fi
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin >/dev/null
fi

systemctl enable --now docker >/dev/null

# Let the default user run docker without root (takes effect in NEW sessions).
DEFAULT_USER=$(getent passwd 1000 | cut -d: -f1)
if [ -n "$DEFAULT_USER" ]; then
  usermod -aG docker "$DEFAULT_USER"
fi

echo "docker:  $(docker --version)"
echo "compose: $(docker compose version)"
echo "daemon:  $(systemctl is-active docker)"
