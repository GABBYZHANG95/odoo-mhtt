{
    'name': 'MHTT Sales Expenses',
    'version': '17.0.1.3.0',
    'category': 'Sales',
    'summary': 'Sales trips, client entertainment and gifts: pre-approval, reimbursement and department budgets',
    'description': """
AIIS Sales Expenses
===================

Design doc (docs/design/审批流程设计.md), section 5:

- **Sales Expense Request** (pre-approval) for a sales trip, client
  entertainment or a gift: customer, purpose, estimated amount, optional
  project. The reporting line up to the CEO approves.
- **Reimbursement**: on the approved request the employee lists the actual
  expenses, attaches the receipts and clicks Submit Claim (#65); this builds
  the expense report linked to the request (one claim per request; over
  the estimate is flagged, not blocked). Making the report by hand in
  Expenses still works. Reporting line up to the CEO, then the CFO approves
  last and gets a to-do to post and pay it. Any rejection goes back to the
  employee.
- **Department budgets** (Sales > Configuration > Sales Expense Budgets),
  kept by the CFO per department and period. Requests and reimbursements
  show the department's budget, what is already used, this document and
  what is left after it - to its approvers, the CFO and the CEO only.
  Going over budget is flagged, not blocked.

These costs belong to Sales > Sales travel and entertainment, also when a
project is linked (the link is for reference only).
    """,
    'author': 'MHTT',
    'depends': ['hr_expense', 'aiis_approval', 'aiis_project', 'aiis_department_menu'],
    'data': [
        'security/sales_expense_security.xml',
        'security/ir.model.access.csv',
        'data/sales_expense_data.xml',
        'views/sales_budget_views.xml',
        'views/sales_expense_request_views.xml',
        'views/hr_expense_sheet_views.xml',
        'views/menu.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
