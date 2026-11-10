"""Run through odoo shell against an isolated copy of the configured demo DB.
Creates disposable UI fixtures and writes credentials only to container /tmp.
"""
import json, secrets
from odoo import fields
assert env.cr.dbname.startswith('aiis_flow_test_'), 'Isolated test database required'
type(env['ir.mail_server']).send_email = lambda *a, **k: 'isolated-no-delivery'
env = env(context=dict(env.context, mail_notify_force_send=False, tracking_disable=True, no_reset_password=True, lang='en_US'))
users = {login: env['res.users'].search([('login', '=', login)], limit=1) for login in ['demo','jeffrey','procurement','admin','cfo','chairman']}
password = secrets.token_urlsafe(24)
for user in users.values():
    user.write({'password': password, 'lang': 'en_US'})

def pr(label):
    record = env['procurement.request'].with_user(users['demo']).create({
        'employee_id': users['demo'].employee_id.id,
        'line_ids': [(0, 0, {'description': 'TODO UI ' + label, 'quantity': 1})],
    })
    record.action_submit()
    return record
pending = pr('approval')
returned = pr('revision')
returned.with_user(users['jeffrey'])._approval_reject('TODO UI revise specifications')
convert = pr('convert')
convert.with_user(users['jeffrey']).action_approval_approve()
vendor = env['res.partner'].create({'name': 'TODO UI vendor', 'supplier_rank': 1})
product = env['product.product'].create({'name': 'TODO UI service', 'type': 'service', 'purchase_ok': True})
for amount in [100, 300001]:
    po = env['purchase.order'].with_user(users['procurement']).create({
        'partner_id': vendor.id,
        'order_line': [(0, 0, {'product_id': product.id, 'product_qty': 1, 'price_unit': amount, 'taxes_id': [(6, 0, [])]})],
    })
    po.button_confirm()
    if amount > 300000:
        po.with_user(users['admin']).action_approval_approve()
metadata = {'database': env.cr.dbname, 'password': password,
            'action': env.ref('aiis_approval.action_aiis_todo').id, 'users': {}}
for login, user in users.items():
    tasks = env['aiis.todo'].with_user(user).search([])
    assert tasks, login
    tasks.read(['document_name', 'model_name', 'amount', 'currency_id', 'note', 'submitter_id'])
    assert all(t.user_id == user for t in tasks)
    assert all(t.document_name != 'Document unavailable' for t in tasks), (login, tasks.mapped('document_name'))
    metadata['users'][login] = {'id': user.id, 'count': len(tasks)}
with open('/tmp/aiis-todo-browser-private.json', 'w') as f:
    json.dump(metadata, f)
env.cr.commit()
print('ISOLATED UI FIXTURES READY:', {k: v['count'] for k,v in metadata['users'].items()})
