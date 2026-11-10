# Post-import check: run after loading a company's real data into a fresh
# production database (docs/deploy/正式上线计划.md, step 5), and again before
# staff get their logins. Read-only: it lists what is missing, it fixes
# nothing.
#
#   docker exec -i mhtt-app odoo shell -d <db> --no-http < scripts/post_import_check.py
#
# Several settings were only guessed by department name at install time,
# when the database had no departments yet; this is where they get checked.
problems, notes = [], []


def ok(text):
    print('  OK    ' + text)


def bad(text):
    problems.append(text)
    print('  FIX   ' + text)


def note(text):
    notes.append(text)
    print('  NOTE  ' + text)


company = env.ref('base.main_company')
Module = env['ir.module.module'].sudo()
print('\n== Company: %s' % company.name)
(ok if company.currency_id.name == 'SAR' else bad)('base currency %s (must be SAR)' % company.currency_id.name)
(ok if Module.search_count([('name', '=', 'l10n_sa'), ('state', '=', 'installed')]) else bad)(
    'Saudi chart of accounts l10n_sa installed')
(ok if company.logo else note)('company logo set' if company.logo else 'no company logo yet')

print('\n== Positions (Settings > Approvals > Positions)')
for position in env['aiis.approval.position'].sudo().search([]):
    user = position.user_id
    if not user:
        bad('%s: nobody holds it' % position.name)
    elif not user.active or user.share:
        bad('%s: %s is not an active internal user' % (position.name, user.login))
    elif not user.employee_ids:
        bad('%s: %s has no employee record' % (position.name, user.login))
    else:
        ok('%s: %s' % (position.name, user.name))

board = env.ref('aiis_finance.group_finance_board', raise_if_not_found=False)
if board:
    holders = env['aiis.approval.position'].sudo().search([('code', 'in', ('cfo', 'ceo', 'chairman'))]).user_id
    (ok if board.users == holders else bad)('finance board = CFO, CEO, Chairman (%s)' % ', '.join(board.users.mapped('login')))

print('\n== Company settings (Settings > Companies)')
for field, label in (('aiis_rd_department_id', 'R&D department'),
                     ('aiis_procurement_department_id', 'Procurement department')):
    value = company[field] if field in company._fields else None
    (ok if value else bad)('%s: %s' % (label, value.name if value else 'not set'))

print('\n== Departments')
Department = env['hr.department'].sudo()
departments = Department.search([])
if not departments:
    bad('no departments imported')
for department in departments:
    if not department.manager_id:
        bad('%s: no department head' % department.complete_name)
    elif not department.manager_id.user_id.active:
        bad('%s: head %s has no login' % (department.complete_name, department.manager_id.name))
top = departments.filtered(lambda d: not d.parent_id and not d.aiis_function)
if top:
    note('top-level departments without an expense function count as G&A: %s' % ', '.join(top.mapped('name')))
ok('%d departments' % len(departments))

print('\n== Reporting lines')
Employee = env['hr.employee'].sudo()
employees = Employee.search([])
managers = employees.parent_id
for manager in managers.filtered(lambda m: not m.user_id.active):
    bad('%s manages %d people but has no login: their approvals skip to the next level' % (
        manager.name, len(employees.filtered(lambda e: e.parent_id == manager))))
ceo = env['aiis.approval.position']._holder('ceo')
chairman = env['aiis.approval.position']._holder('chairman')
for employee in employees.filtered(lambda e: not e.parent_id and e.user_id not in (ceo | chairman)):
    note('%s has no manager: their approvals go straight to the CEO' % employee.name)
no_dept = employees.filtered(lambda e: not e.department_id)
if no_dept:
    bad('%d employees without a department: %s' % (len(no_dept), ', '.join(no_dept[:10].mapped('name'))))
ok('%d employees, %d of them with a login' % (len(employees), len(employees.filtered('user_id'))))

print('\n== Users')
users = env['res.users'].sudo().search([('share', '=', False)])
no_employee = users.filtered(lambda u: not u.employee_ids and u.login not in ('admin', '__system__', 'default'))
if no_employee:
    bad('users without an employee record (approvals cannot find them): %s' % ', '.join(no_employee.mapped('login')))
admin = env.ref('base.user_admin')
if admin.employee_ids:
    bad('admin is linked to an employee: keep it for system maintenance only')
if 'totp_enabled' in admin._fields and not admin.totp_enabled:
    note('admin has no two-factor authentication yet')
Ldap = env['res.company.ldap'] if 'res.company.ldap' in env else None
confs = Ldap._get_ldap_dicts() if Ldap is not None else []
if not confs:
    note('no LDAP configured: users sign in with local passwords')
else:
    conf = confs[0]
    missing = [u.login for u in users - admin if u.login not in ('__system__', 'default') and not Ldap._get_entry(conf, u.login)[0]]
    (bad if missing else ok)('users not found in LDAP %s: %s' % (conf['ldap_server'], ', '.join(missing) or 'none'))

print('\n== Accounting')
categories = env['product.category'].sudo().with_company(company).search([])
no_account = categories.filtered(lambda c: not c.property_account_expense_categ_id)
(bad if no_account else ok)('product categories without an expense account: %s' % (
    ', '.join(no_account.mapped('complete_name')) or 'none'))
for code in ('USD', 'CNY'):
    currency = env['res.currency'].sudo().with_context(active_test=False).search([('name', '=', code)], limit=1)
    rate = env['res.currency.rate'].sudo().search(
        [('currency_id', '=', currency.id), ('company_id', 'in', (company.id, False))], order='name desc', limit=1)
    if not currency.active:
        note('%s is not active' % code)
    elif not rate:
        bad('%s has no exchange rate' % code)
    else:
        note('%s rate from %s: check it is current' % (code, rate.name))

print('\n%d to fix, %d notes' % (len(problems), len(notes)))
