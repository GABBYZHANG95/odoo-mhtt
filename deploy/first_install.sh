#!/usr/bin/env bash
# Plan step 4: create MHTT's production database, fresh and without demo
# data. The Saudi chart of accounts first (it cannot change once there are
# entries), then the modules in deploy/modules.txt (the same list
# scripts/fresh_install_check.sh tests before every release).
#   Usage: deploy/first_install.sh      (stack already up, see README)
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
db="$(sed -n 's/^DB_NAME=//p' "$here/.env")"
[ -n "$db" ] || { echo "DB_NAME missing in $here/.env"; exit 1; }
if docker exec mhtt-db psql -U odoo -lqt | cut -d'|' -f1 | grep -qw "$db"; then
    echo "database $db already exists: refusing to touch it"; exit 1
fi
odoo=(docker exec mhtt-app odoo -d "$db" --no-http --workers=0 --max-cron-threads=0 --stop-after-init --without-demo=all)
docker exec mhtt-db createdb -U odoo "$db"
echo "[1/2] l10n_sa ..."
"${odoo[@]}" -i l10n_sa
echo "[2/2] modules ..."
"${odoo[@]}" -i "$(tr -d ' \n' < "$here/modules.txt")"
docker restart mhtt-app >/dev/null
echo "done: $db. Next: brand (scripts/setup_company_brand.py), then data (plan step 5)."
