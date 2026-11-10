Put MHTT's LDAP root certificate here as `ldap-ca.pem` (the CA that signed
MHTT's LDAP server certificate; plan section 2, item 4). It is public, so it
may be committed. Then run `scripts/setup_ldap.py` with MHTT's parameters.
