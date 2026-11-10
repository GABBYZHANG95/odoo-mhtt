"""Run via odoo shell against the isolated demo copy, never a live DB."""
import json
import runpy
assert env.cr.dbname.startswith('aiis_flow_test_'), 'Isolated database required'
type(env['ir.mail_server']).send_email = lambda *a, **k: 'isolated-no-delivery'
env = env(context=dict(env.context, tracking_disable=True, mail_notify_force_send=False, lang='en_US'))
# The test copy was upgraded incrementally; exercise the complete data migration.
runpy.run_path('/mnt/extra-addons/aiis_procurement_request/migrations/17.0.2.8.0/post-migrate.py')['migrate'](env.cr, '17.0.2.7.0')
with open('/tmp/aiis-todo-browser-private.json') as f:
    meta = json.load(f)
assert meta['database'] == env.cr.dbname
users = {login: env['res.users'].search([('login', '=', login)], limit=1) for login in meta['users']}
vendor = env['res.partner'].create({'name': 'BILL TERMS UI Vendor', 'supplier_rank': 1})
product = env['product.product'].create({'name': 'BILL TERMS UI Subscription', 'type': 'service', 'purchase_ok': True})
po = env['purchase.order'].with_user(users['procurement']).create({
    'partner_id': vendor.id,
    'order_line': [(0, 0, {'product_id': product.id, 'product_qty': 3, 'price_unit': 400, 'taxes_id': [(6, 0, [])]})],
})
po.button_confirm()
po.with_user(users['admin']).action_approval_approve()
bill = po.order_line.invoice_lines.move_id
meta.update({'purchase_bill_id': bill.id, 'purchase_order_id': po.id, 'purchase_order_name': po.name,
             'bill_action': env.ref('account.action_move_in_invoice_type').id})
with open('/tmp/aiis-purchase-bill-private.json', 'w') as f:
    json.dump(meta, f)
env.cr.commit()
print('Isolated purchase bill UI fixture ready:', po.name, bill.id)
