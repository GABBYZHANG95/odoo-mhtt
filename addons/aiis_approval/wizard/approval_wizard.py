from odoo import fields, models


class ApprovalRejectWizard(models.TransientModel):
    _name = 'aiis.approval.reject.wizard'
    _description = 'Reject an Approval'

    res_model = fields.Char(required=True)
    res_id = fields.Integer(required=True)
    reason = fields.Text(required=True)

    def action_confirm(self):
        self.ensure_one()
        self.env[self.res_model].browse(self.res_id)._approval_reject(self.reason)
        return {'type': 'ir.actions.act_window_close'}


class ApprovalReassignWizard(models.TransientModel):
    _name = 'aiis.approval.reassign.wizard'
    _description = 'Reassign an Approval'

    line_id = fields.Many2one('aiis.approval.line', required=True)
    current_user_id = fields.Many2one(related='line_id.user_id', string='Current Approver')
    user_id = fields.Many2one('res.users', string='New Approver', required=True, domain=[('share', '=', False)])

    def action_confirm(self):
        self.ensure_one()
        self.line_id._reassign(self.user_id)
        return {'type': 'ir.actions.act_window_close'}
