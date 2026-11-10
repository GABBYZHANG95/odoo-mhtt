# Point one database's login at an LDAP directory (Odoo auth_ldap).
# Run in odoo shell; the settings come from environment variables, so the
# same script serves a dev directory and MHTT's production one:
#
#   docker exec -i -e LDAP_SERVER=<MHTT LDAP host> -e LDAP_BASE=<base DN> \
#       mhtt-app odoo shell -d mhtt --no-http < scripts/setup_ldap.py
#
# LDAP_SERVER    host name on the server certificate (required)
# LDAP_BASE      search base (required)
# LDAP_PORT      default 389
# LDAP_TLS       StartTLS, default 1. Never 0 outside a lab: the password
#                would cross the network in clear.
# LDAP_FILTER    default: uid or mail, so an Odoo login of either form works
# LDAP_BIND_DN / LDAP_BIND_PASSWORD   read-only search account; empty = anonymous
#
# The CA that signed the server certificate is /etc/odoo/certs/ldap-ca.pem
# (docker-compose.yml sets LDAPTLS_CACERT). Users are not created on first
# login: create the Odoo user (and link the employee) with the login the
# filter matches. Local passwords keep working, so admin stays usable when
# the directory is down.
import os

conf = {
    'ldap_server': os.environ['LDAP_SERVER'],
    'ldap_server_port': int(os.environ.get('LDAP_PORT', 389)),
    'ldap_base': os.environ['LDAP_BASE'],
    'ldap_filter': os.environ.get('LDAP_FILTER', '(&(objectClass=inetOrgPerson)(|(uid=%s)(mail=%s)))'),
    'ldap_tls': os.environ.get('LDAP_TLS', '1') == '1',
    'ldap_binddn': os.environ.get('LDAP_BIND_DN') or False,
    'ldap_password': os.environ.get('LDAP_BIND_PASSWORD') or False,
    'create_user': False,
}
company = env.ref('base.main_company')
Ldap = env['res.company.ldap'].sudo()
record = Ldap.search([('company', '=', company.id)], limit=1)
if record:
    record.write(conf)
else:
    record = Ldap.create(dict(conf, company=company.id))
(Ldap.search([('company', '=', company.id)]) - record).unlink()

# Check the connection the way a login will use it
dn, entry = record._get_entry(
    next(d for d in record._get_ldap_dicts() if d['id'] == record.id), os.environ.get('LDAP_CHECK_LOGIN', '__nobody__'))
env.cr.commit()
print('LDAP configured: %s:%s base %s, TLS %s, create users %s' % (
    record.ldap_server, record.ldap_server_port, record.ldap_base, record.ldap_tls, record.create_user))
if os.environ.get('LDAP_CHECK_LOGIN'):
    print('lookup %s -> %s' % (os.environ['LDAP_CHECK_LOGIN'], dn or 'NOT FOUND'))
