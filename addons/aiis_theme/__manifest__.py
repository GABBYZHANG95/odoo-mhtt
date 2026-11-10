{
    'name': 'MHTT Theme',
    'version': '17.0.1.1.0',
    'category': 'Hidden',
    'summary': 'Interface in the brand colour of this database (the company primary colour)',
    'description': """
Brand colour per database
=========================

MHTT navy #233D7E (the logo's colour). The colour is data, not code:

- Set it once: Settings > Companies > Document Layout > Primary Color of the
  main company (printed documents use the same colour).
- Saving recompiles this database's interface: buttons, links, active states,
  the navbar gradient, the login page and the AIIS dashboards. Lighter and
  darker shades are derived from the one colour.
- No valid colour set: MHTT navy.
- scripts/setup_company_brand.py sets the logo and the colour at deployment.
    """,
    'author': 'MHTT',
    'depends': ['web'],
    'data': [
        'data/brand_data.xml',
    ],
    'uninstall_hook': 'uninstall_hook',
    'assets': {
        # prepended so these win over Odoo's own `!default` values
        'web._assets_primary_variables': [
            ('prepend', 'aiis_theme/static/src/scss/primary_variables.scss'),
        ],
        'web.assets_backend': [
            'aiis_theme/static/src/scss/theme.scss',
        ],
        'web.assets_frontend': [
            'aiis_theme/static/src/scss/theme.scss',
        ],
    },
    'installable': True,
    'auto_install': False,
    'license': 'LGPL-3',
}
