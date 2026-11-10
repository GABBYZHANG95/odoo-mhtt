from odoo import _, fields, models
from odoo.exceptions import UserError


class PurchaseBillReturn(models.TransientModel):
    _name = 'aiis.purchase.bill.return'
    _description = 'Return Purchase Bill to Procurement'

    bill_id = fields.Many2one('account.move', required=True, readonly=True)
    reason = fields.Text(required=True)

    def action_return(self):
        self.ensure_one()
        bill = self.bill_id
        # Recheck at submission; neither a forged wizard nor a stale form can
        # bypass CFO, record-access, draft-state or posted-bill checks.
        bill.action_return_to_procurement()
        if not (self.reason or '').strip():
            raise UserError(_('Please explain the commercial terms that Procurement needs to revise.'))
        bill._aiis_purchase_orders().sudo()._return_for_commercial_revision(self.reason.strip())
        return {'type': 'ir.actions.act_window_close'}
