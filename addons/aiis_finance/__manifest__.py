{
    'name': 'MHTT Finance',
    'version': '17.0.1.2.1',
    'category': 'Accounting/Accounting',
    'summary': 'Accrual accounts, cost booking rules, personnel costs and the Finance board (Overview and Reports)',
    'description': """
AIIS Finance
============

Design doc (docs/design/审批流程设计.md), sections 6.1-6.8:

- **Accounts**: one account per P&L detail line (G&A / R&D / Sales /
  Cost of revenue), Zakat and Income tax, and the balance sheet accounts
  the company needs. Every account carries its **report line**, which is
  what the reports add up - so the reports work on any chart of accounts
  (Saudi chart in production, generic chart in the test database).
- **Booking rules**: a department has a function (G&A, R&D or Sales); a
  product category is a nature. The *Account Mapping* (nature x function
  -> account, kept by the CFO) books vendor bill lines from purchase
  requests and expense lines; project purchases and delivery trips go to
  Cost of revenue, sales trips and entertainment to Sales travel and
  entertainment. Equipment from 5,000 SAR a unit becomes a fixed asset
  (OCA asset management). Lines also get the department analytic account.
- **Personnel costs** entered by HR per employee (salary, visa, Iqama,
  insurance, GOSI...), posted to the department's Personnel cost. The
  monthly salary job now creates these.
- **Maintenance revenue** is deferred and recognised month by month (OCA
  spread).
- **Finance board** (Finance > Overview / Reports), for the CFO, CEO and
  Chairman only: Overview (Sales Order Value, Recognized Revenue, Gross
  Profit, Order Backlog), Profit & Loss and Balance Sheet (MIS Builder),
  Receivables & Payables (OCA aged balance, partner statements), Cash
  Flow (bank-reconciled cash only) and Checks.
    """,
    'author': 'MHTT',
    'depends': [
        'account',
        'sale',
        'purchase',
        'hr_expense',
        'hr_contract',
        'aiis_department_menu',
        'aiis_finance_dashboard',
        'aiis_procurement_request',
        'aiis_project',
        'aiis_sales_expense',
        'mis_builder',
        'account_financial_report',
        'partner_statement',
        'account_asset_management',
        'account_spread_cost_revenue',
        'account_reconcile_oca',
    ],
    'data': [
        'security/finance_security.xml',
        'security/ir.model.access.csv',
        'data/account_data.xml',
        'data/category_data.xml',
        'data/asset_data.xml',
        'data/personnel_data.xml',
        'data/report_data.xml',
        'data/mis_profit_loss.xml',
        'data/mis_balance_sheet.xml',
        'views/account_views.xml',
        'views/account_mapping_views.xml',
        'views/hr_department_views.xml',
        'views/personnel_cost_views.xml',
        'wizard/finance_report_wizard_views.xml',
        'views/finance_board_views.xml',
        'views/menu.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'aiis_finance/static/src/board/*',
            'aiis_finance/static/src/mis_report_widget_patch.js',
        ],
    },
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
