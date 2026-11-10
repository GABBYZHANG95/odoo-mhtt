{
    'name': 'MHTT Operations Dashboard',
    'version': '17.0.1.0.1',
    'category': 'Productivity',
    'summary': 'Daily, monthly and quarterly operating figures for the CEO and the Chairman',
    'description': """
AIIS Operations Dashboard
=========================

Design doc (docs/design/审批流程设计.md) 6.9, decision #66.

A top-level **Dashboard** menu for the CEO and the Chairman only (kept in
step with the holders of those positions): what happens in the business,
by document and workflow - separate from the Finance board, which reads
the books, but calling its calculation where both show the same figure.

- Period: Day / Month / Quarter / Year, this month by default, compared
  with the same span of the previous period. Actual figures only.
- Pages: Overview, Expenses, Procurement, Approvals, Projects & Sales,
  Maintenance & People.
- Figures are company-wide; documents open from the tables only when the
  viewer may open them.
    """,
    'author': 'MHTT',
    'depends': [
        'hr_contract',
        'aiis_approval',
        'aiis_procurement_request',
        'aiis_project',
        'aiis_sales_expense',
        'aiis_finance',
    ],
    'data': [
        'security/dashboard_security.xml',
        'views/dashboard_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'aiis_dashboard/static/src/dashboard/*',
        ],
    },
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
