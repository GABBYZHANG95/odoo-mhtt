from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ApprovalPosition(models.Model):
    """A position that approves as such - CEO, Chairman, CFO. Exactly one
    user holds it and approves for it; handing the job to someone else is
    just changing the holder. Holding a position grants no access rights:
    the holder still needs the groups that let them open the documents."""
    _name = 'aiis.approval.position'
    _description = 'Approval Position'
    _order = 'sequence, id'

    name = fields.Char(required=True, translate=True)
    code = fields.Char(
        required=True,
        help='Technical key used by the approval logic, e.g. "ceo" - '
             'the reporting-line rules look up the CEO and the Chairman by it.')
    sequence = fields.Integer(default=10)
    user_id = fields.Many2one(
        'res.users', string='Holder', domain=[('share', '=', False)],
        help='The one person who approves for this position.')
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ('code_unique', 'unique(code)', 'Each position needs its own code.'),
    ]

    @api.model
    def _holder(self, code):
        """User holding the position, or an empty recordset"""
        return self.sudo().search([('code', '=', code)], limit=1).user_id

    def _require_holder(self):
        self.ensure_one()
        if not self.user_id:
            raise UserError(_(
                "Nobody holds the position %s yet, so there is no one to approve this. "
                "Ask an administrator to set it (Settings > Approvals > Positions).", self.name))
        return self.user_id

    @api.model
    def _require_finance_holder(self):
        cfo = self._holder('cfo')
        if not cfo or not cfo.active or cfo.share:
            raise UserError(_(
                "Configure an active internal user as CFO before creating a finance task "
                "(Settings > Approvals > Positions). Finance tasks cannot be assigned "
                "to the buyer or the person approving the document."))
        return cfo

    def _sync_finance_tasks(self):
        """Outstanding finance activities follow the explicitly configured CFO."""
        for position in self.sudo().filtered(lambda p: p.code == 'cfo' and p.active):
            cfo = position.user_id
            if not cfo or not cfo.active or cfo.share:
                continue
            types = self.env['mail.activity.type']
            for xmlid in ['aiis_procurement_request.mail_activity_pay_bill',
                          'aiis_sales_expense.mail_activity_pay_expense']:
                activity_type = self.env.ref(xmlid, raise_if_not_found=False)
                if activity_type:
                    types |= activity_type
            if types:
                self.env['mail.activity'].sudo().with_context(mail_activity_quick_update=True).search([
                    ('activity_type_id', 'in', types.ids), ('user_id', '!=', cfo.id),
                ]).write({'user_id': cfo.id})

    def write(self, vals):
        result = super().write(vals)
        if {'user_id', 'active', 'code'} & vals.keys():
            self._sync_finance_tasks()
        return result
