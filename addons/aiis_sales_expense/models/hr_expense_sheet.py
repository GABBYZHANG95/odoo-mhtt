from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class HrExpenseSheet(models.Model):
    """Reimbursement of an approved sales expense request: reporting line
    up to the CEO, then the CFO, who then posts and pays it."""
    _name = 'hr.expense.sheet'
    _inherit = ['hr.expense.sheet', 'aiis.sales.budget.mixin']

    aiis_sales_request_id = fields.Many2one(
        'aiis.sales.expense.request', string='Sales Expense Request', index=True, copy=False,
        domain="[('state', '=', 'approved'), ('employee_id', '=', employee_id)]")

    @api.constrains('aiis_sales_request_id', 'aiis_travel_request_id', 'employee_id')
    def _check_sales_request(self):
        for sheet in self.filtered('aiis_sales_request_id'):
            request = sheet.aiis_sales_request_id.sudo()
            if sheet.aiis_travel_request_id:
                raise ValidationError(_("Link either a project trip or a sales expense request, not both."))
            if request.employee_id != sheet.employee_id:
                raise ValidationError(_("The sales expense request belongs to another employee."))
            if request.state != 'approved':
                raise ValidationError(_("Link an approved sales expense request."))

    # One claim per request (#65)
    def _aiis_claim_links(self):
        return super()._aiis_claim_links() + ['aiis_sales_request_id']

    @api.constrains('aiis_sales_request_id', 'state')
    def _check_one_claim_sales(self):
        self._aiis_check_one_claim()

    @api.depends('total_amount', 'state', 'aiis_travel_request_id', 'aiis_sales_request_id')
    def _compute_aiis_claim(self):
        return super()._compute_aiis_claim()

    def _approval_gate(self):
        return 'sales' if self.aiis_sales_request_id else super()._approval_gate()

    def action_submit_sheet(self):
        sales = self.filtered('aiis_sales_request_id')
        res = super(HrExpenseSheet, self - sales).action_submit_sheet() if self - sales else None
        for sheet in sales:
            sheet.write({'approval_state': 'submit'})
            sheet._approval_submit()
        return res

    def _approval_on_approved(self):
        res = super()._approval_on_approved()
        cfo = self.env['aiis.approval.position']._require_finance_holder() if self.filtered('aiis_sales_request_id') else False
        for sheet in self.filtered('aiis_sales_request_id'):
            sheet.with_context(mail_activity_quick_update=True).activity_schedule(
                'aiis_sales_expense.mail_activity_pay_expense', user_id=cfo.id,
                summary=_("Post and pay %s", sheet.name),
                note=_("Approved sales expense reimbursement - post the journal entries and register the payment."))
        return res

    def action_approve_expense_sheets(self):
        if self.filtered('aiis_sales_request_id'):
            raise UserError(_("Sales expense reimbursements are approved through To do."))
        return super().action_approve_expense_sheets()

    def action_refuse_expense_sheets(self):
        if self.filtered('aiis_sales_request_id'):
            raise UserError(_("Sales expense reimbursements are rejected through To do."))
        return super().action_refuse_expense_sheets()

    # Budget box
    def _budget_request(self):
        return self.aiis_sales_request_id

    def _budget_this_amount(self):
        return self.total_amount if self.aiis_sales_request_id else 0.0


class HrExpense(models.Model):
    _inherit = 'hr.expense'

    aiis_sales_request_id = fields.Many2one(
        'aiis.sales.expense.request', string='Sales Expense Request', index=True, copy=False, ondelete='set null')

    @api.constrains('aiis_sales_request_id', 'employee_id')
    def _check_aiis_sales_request(self):
        for expense in self.filtered('aiis_sales_request_id'):
            if expense.aiis_sales_request_id.sudo().employee_id != expense.employee_id:
                raise ValidationError(_("The sales expense request belongs to another employee."))
