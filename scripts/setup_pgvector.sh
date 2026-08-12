#!/usr/bin/env bash
# One-time dev setup for the phase 2 vector store (plan Q7):
# PostgreSQL 17 + pgvector inside WSL2 Ubuntu, reached from Windows
# over localhost. Assumes the pgdg packages are already installed:
#   apt-get install postgresql-17 postgresql-17-pgvector
# Run as root from Windows:
#   wsl -d Ubuntu-22.04 -u root -- bash -c "tr -d '\r' < /mnt/c/OM_Source/FlightIntelAgent/scripts/setup_pgvector.sh | bash"
# Idempotent: safe to rerun; skips anything that already exists.
set -euo pipefail

ENV_FILE=/mnt/c/OM_Source/FlightIntelAgent/.env
test -f "$ENV_FILE" || { echo "missing $ENV_FILE - create it from .env.example first"; exit 1; }

# Port 5433: the laptop already runs a native Windows PostgreSQL 17 service
# (postgresql-x64-17, no pgvector) on 5432, which would shadow WSL's
# localhost forwarding.
echo "port = 5433" > /etc/postgresql/17/main/conf.d/port.conf

# WSL's localhost relay (NAT mode) reaches services via the VM's network
# address, not the VM's loopback (hit 2026-08-13: relay up, handshake
# stalling). Listen on all VM interfaces; the VM sits behind NAT, so only
# the Windows host can reach it, and pg_hba below still requires password
# auth from the network.
echo "listen_addresses = '*'" > /etc/postgresql/17/main/conf.d/listen.conf

# samenet = any subnet the VM is directly on, whatever range WSL picks
# this boot; covers the relay's host-side source address.
HBA=/etc/postgresql/17/main/pg_hba.conf
grep -q 'host flightintel flightintel samenet' "$HBA" || \
  echo 'host flightintel flightintel samenet scram-sha-256' >> "$HBA"

systemctl enable --now postgresql >/dev/null
systemctl restart postgresql

if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='flightintel'" | grep -q 1; then
  PW=$(openssl rand -hex 16)
  sudo -u postgres psql -v ON_ERROR_STOP=1 -c "CREATE ROLE flightintel LOGIN PASSWORD '$PW'"
  sudo -u postgres createdb -O flightintel flightintel
  if ! grep -q '^PG_DSN=' "$ENV_FILE"; then
    printf '\n# Local pgvector store (WSL2 Ubuntu, plan Q7). Local-dev password only.\nPG_DSN=postgresql://flightintel:%s@localhost:5433/flightintel\n' "$PW" >> "$ENV_FILE"
  fi
fi

sudo -u postgres psql -v ON_ERROR_STOP=1 -d flightintel -c "CREATE EXTENSION IF NOT EXISTS vector" >/dev/null

echo "postgres: $(sudo -u postgres psql -tAc 'SELECT version()' | cut -d' ' -f1-2)"
echo "pgvector: $(sudo -u postgres psql -d flightintel -tAc "SELECT extversion FROM pg_extension WHERE extname='vector'")"
echo "smoke L2 distance [1,2,3]<->[4,5,6]: $(sudo -u postgres psql -d flightintel -tAc "SELECT '[1,2,3]'::vector <-> '[4,5,6]'::vector")"
