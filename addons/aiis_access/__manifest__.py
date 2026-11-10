{
    'name': 'MHTT Access',
    'version': '17.0.1.1.0',
    'category': 'Hidden',
    'summary': 'Who sees which app and which documents: not in the flow, not visible',
    'description': """
AIIS Access
===========

Design doc (docs/design/审批流程设计.md), section 1.4: whoever a flow does
not pass through does not see it - neither the app nor the documents.

- **Sales by department** (#52): employees of the Sales department (and its
  sub-departments, i.e. departments whose expense function is Sales) get
  sales rights automatically - their own documents; the head of such a
  department sees all of them. Leaving the department takes them away.
- **Sales app**: sales people, the CEO and the CFO only (#51) - not
  procurement, R&D or HR. Approvers open what they approve from My
  Approvals.
- **Customer contracts** (#53): a salesperson sees the contracts they sell
  or approve, a sales department head those of their department; the CEO,
  the CFO and sales managers see all.
- **Dashboards** (Odoo spreadsheet dashboards) only for system
  administrators.
- **LDAP login**: installs Odoo's auth_ldap so staff sign in with their
  company directory password. Each database points at its own directory
  (scripts/setup_ldap.py); the Odoo user must already exist with the login
  the directory filter matches, no user is created on first login.
    """,
    'author': 'MHTT',
    'depends': [
        'auth_ldap',
        'aiis_department_menu',
        'aiis_customer_contract',
        'aiis_sale_procurement',
        'aiis_sales_expense',
        'aiis_procurement_request',
        'aiis_finance',
    ],
    'data': [
        'security/access_groups.xml',
        'security/access_rules.xml',
        'data/menu_data.xml',
    ],
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
