def migrate(cr, version):
    # Preserve the original PO even if somebody later tries to remove line links.
    # Only backfill unambiguous single-PO vendor bills; no amounts are changed.
    cr.execute("""
        UPDATE account_move bill SET aiis_purchase_order_id = link.order_id
        FROM (
            SELECT aml.move_id, min(pol.order_id) AS order_id
            FROM account_move_line aml
            JOIN purchase_order_line pol ON pol.id = aml.purchase_line_id
            GROUP BY aml.move_id HAVING count(DISTINCT pol.order_id) = 1
        ) link
        WHERE bill.id = link.move_id AND bill.move_type = 'in_invoice'
          AND bill.aiis_purchase_order_id IS NULL
    """)

    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['purchase.order'].search([
        ('state', 'in', ['purchase', 'done']), ('aiis_approval_state', '=', 'approved'),
    ]).filtered(lambda o: not o.aiis_approved_terms)._seal_approved_terms()
