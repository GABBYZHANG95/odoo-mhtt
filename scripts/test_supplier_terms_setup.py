"""Run through odoo shell against the isolated demo copy only."""
import base64
import json
assert env.cr.dbname.startswith('aiis_flow_test_'), 'Isolated database required'
type(env['ir.mail_server']).send_email = lambda *a, **k: 'isolated-no-delivery'
env = env(context=dict(env.context, tracking_disable=True, mail_notify_force_send=False, lang='en_US'))
with open('/tmp/aiis-todo-browser-private.json') as f:
    meta = json.load(f)
assert meta['database'] == env.cr.dbname
users = {login: env['res.users'].search([('login', '=', login)], limit=1) for login in meta['users']}
vendor = env['res.partner'].create({'name': 'SUPPLIER TERMS UI Vendor', 'supplier_rank': 1})
product = env['product.product'].create({'name': 'SUPPLIER TERMS UI Equipment', 'purchase_ok': True, 'type': 'service'})
order = env['purchase.order'].with_user(users['procurement']).create({
    'partner_id': vendor.id, 'aiis_settlement_mode': 'staged',
    'order_line': [(0, 0, {'product_id': product.id, 'product_qty': 1, 'price_unit': 1000, 'taxes_id': [(6, 0, [])]})],
    'aiis_payment_stage_ids': [(0, 0, {'sequence': 10, 'name': 'Advance', 'percentage': 30, 'trigger': 'approval'}),
                              (0, 0, {'sequence': 20, 'name': 'Acceptance balance', 'percentage': 70, 'trigger': 'acceptance'})],
})
evidence = env['ir.attachment'].create({'name': 'Vendor agreement.txt', 'type': 'binary',
    'datas': base64.b64encode(b'30% advance, 70% after acceptance'), 'res_model': 'purchase.order', 'res_id': order.id})
order.write({'aiis_supplier_offer': '30% advance, 70% after acceptance',
    'aiis_supplier_evidence_ids': [(6, 0, evidence.ids)], 'aiis_supplier_confirmed': True})
order.button_confirm()
meta.update({'terms_order_id': order.id, 'terms_order_name': order.name,
             'bill_action': env.ref('account.action_move_in_invoice_type').id})
with open('/tmp/aiis-supplier-terms-private.json', 'w') as f:
    json.dump(meta, f)
env.cr.commit()
print('Isolated supplier terms fixture ready:', order.name)
