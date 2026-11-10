"""Move PR / PO approval onto the approval engine (aiis_approval).

- The CEO / Chairman positions get the users who held the old approver
  groups, unless an administrator already set a holder.
- Requests and orders half-way through the old approval go back to draft
  and are submitted again under the new rules (design doc 7.7); rejected
  requests are plain drafts now.
- To-dos of the old PR / PO approval activity types are removed, so the
  types themselves can be deleted with the rest of the old data.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})

    for code, group in (('ceo', 'group_po_approver_ceo'), ('chairman', 'group_po_approver_chairman')):
        position = env['aiis.approval.position'].search([('code', '=', code)], limit=1)
        holders = env.ref('aiis_procurement_request.%s' % group, raise_if_not_found=False)
        if position and not position.user_id and holders and holders.users:
            position.user_id = holders.users.sorted('id')[:1]

    requests = env['procurement.request'].search([('state', 'in', ('submitted', 'rejected'))])
    for request in requests:
        request.message_post(body="Approval moved to the new approval engine - please submit this request again.")
    cr.execute("UPDATE procurement_request SET state = 'draft' WHERE state IN ('submitted', 'rejected')")

    orders = env['purchase.order'].search([('state', '=', 'to approve')])
    for order in orders:
        order.message_post(body="Approval moved to the new approval engine - please confirm this order again.")
    cr.execute("UPDATE purchase_order SET state = 'draft' WHERE state = 'to approve'")

    old_types = [env.ref('aiis_procurement_request.%s' % xmlid, raise_if_not_found=False)
                 for xmlid in ('mail_activity_po_approval', 'mail_activity_pr_approval')]
    old_type_ids = [t.id for t in old_types if t]
    if old_type_ids:
        env['mail.activity'].search([('activity_type_id', 'in', old_type_ids)]).unlink()
