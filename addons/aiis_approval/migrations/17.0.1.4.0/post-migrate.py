from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Rejections from before Returned to Me (#56) still waiting to be
    submitted again: put them on the list."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    for request in env['aiis.approval.request'].search([('state', '=', 'rejected')]):
        if request.res_model not in env:
            continue
        document = request._document().exists()
        if (document and document.approval_request_id == request and document.aiis_approval_state == 'rejected'
                and ('state' not in document._fields or document.state not in ('cancel', 'cancelled'))):
            request.returned = True
