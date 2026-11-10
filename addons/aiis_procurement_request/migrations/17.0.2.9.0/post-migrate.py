from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    # Preserve already approved commitments and their existing signed snapshots.
    cr.execute('UPDATE purchase_order SET aiis_payment_managed = FALSE')
    for order in env['purchase.order'].search([('state', 'in', ['draft', 'sent', 'to approve'])]):
        was_pending = order.state == 'to approve'
        if was_pending:
            order._approval_cancel('Supplier conditions now require CFO review before CEO approval.')
            order.button_draft()
        vals = {'aiis_payment_managed': True, 'aiis_terms_review_state': 'draft', 'aiis_supplier_confirmed': False}
        if not order.aiis_payment_stage_ids:
            vals['aiis_payment_stage_ids'] = order._default_payment_stages('prepaid')
        order._plan_internal_write(vals)
        if was_pending:
            buyers = env.ref('aiis_procurement_request.group_procurement_buyer').users.filtered(
                lambda u: u.active and order.company_id in u.company_ids)
            assignees = (order.user_id & buyers) or buyers
            for buyer in assignees:
                order.with_context(mail_activity_quick_update=True).activity_schedule(
                    'aiis_procurement_request.mail_activity_revise_order', user_id=buyer.id,
                    summary='Provide supplier terms and resubmit',
                    note='Record the supplier agreement and confirmation evidence, then submit for CFO review before CEO approval.')
