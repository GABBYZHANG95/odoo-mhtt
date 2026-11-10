#!/usr/bin/env bash
# Write deploy/odoo.conf from deploy/odoo.conf.template and deploy/.env.
#   Usage: deploy/render_conf.sh
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
env_file="$here/.env"
[ -f "$env_file" ] || { echo "missing $env_file (copy deploy/env.example)"; exit 1; }
python3 - "$here/odoo.conf.template" "$env_file" "$here/odoo.conf" <<'PY'
import re, sys
template, env_file, out = sys.argv[1:]
env = {}
for line in open(env_file):
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        key, value = line.split('=', 1)
        env[key.strip()] = value.strip()
missing = set()
def value(match):
    name = match.group(1)
    if not env.get(name) or 'CHANGE_ME' in env[name]:
        missing.add(name)
        return ''
    return env[name]
text = re.sub(r'\$\{(\w+)\}', value, open(template).read())
value(re.match(r'(\w+)', 'ODOO_DOMAIN'))  # used by docker-compose.yml
if missing:
    sys.exit('set in %s: %s' % (env_file, ', '.join(sorted(missing))))
with open(out, 'w') as f:
    f.write(text)
PY
# Readable by the odoo user inside the container (uid 101); the server has
# no other users, the secrets file itself (.env) stays 600.
chmod 644 "$here/odoo.conf"
echo "wrote $here/odoo.conf"
