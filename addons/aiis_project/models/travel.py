from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class TravelRequest(models.Model):
    """Pre-approval of a delivery or maintenance trip (design doc 4.5): the
    reporting line up to the CEO approves the itinerary and budget. After the
    trip the traveller claims the costs on the request itself (#65), which
    builds the expense report linked to it."""
    _name = 'aiis.travel.request'
    _description = 'Travel Request'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'aiis.approval.mixin', 'aiis.expense.claim.mixin']
    _order = 'id desc'

    name = fields.Char(default='New', readonly=True, copy=False)
    employee_id = fields.Many2one(
        'hr.employee', string='Traveller', required=True, tracking=True,
        default=lambda self: self.env.user.employee_id)
    project_id = fields.Many2one('aiis.project', required=True, index=True, tracking=True)
    purpose = fields.Text(required=True)
    destination = fields.Char(required=True)
    date_from = fields.Date(string='From', required=True)
    date_to = fields.Date(string='To', required=True)
    currency_id = fields.Many2one('res.currency', required=True, default=lambda self: self.env.company.currency_id)
    budget = fields.Monetary(required=True, tracking=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('approved', 'Approved'),
    ], default='draft', required=True, tracking=True, copy=False)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    expense_sheet_ids = fields.One2many('hr.expense.sheet', 'aiis_travel_request_id', string='Expense Reports')
    claim_line_ids = fields.One2many('hr.expense', 'aiis_travel_request_id', string='Actual Expenses')
    amount_claimed = fields.Monetary(
        string='Claimed', compute='_compute_amount_claimed', compute_sudo=True,
        help='Total of the expense reports linked to this trip.')

    @api.depends('expense_sheet_ids.total_amount')
    def _compute_amount_claimed(self):
        for request in self:
            request.amount_claimed = sum(request.expense_sheet_ids.mapped('total_amount'))

    @api.constrains('date_from', 'date_to')
    def _check_dates(self):
        if any(r.date_from and r.date_to and r.date_to < r.date_from for r in self):
            raise ValidationError(_("The trip ends before it starts."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('aiis.travel.request') or 'New'
        return super().create(vals_list)

    def action_submit(self):
        for request in self:
            if request.state != 'draft':
                raise UserError(_("Only a draft travel request can be submitted."))
            request.state = 'submitted'
            request._approval_submit()

    # Claim on the request (#65)
    def _claim_link_field(self):
        return 'aiis_travel_request_id'

    def _claim_estimate(self):
        return self.budget

    def _claim_default_product(self):
        return self.env.ref('hr_expense.expense_product_travel_accommodation', raise_if_not_found=False) \
            or super()._claim_default_product()

    def _approval_requester(self):
        return self.employee_id

    def _approval_on_approved(self):
        self.write({'state': 'approved'})

    def _approval_on_rejected(self):
        self.write({'state': 'draft'})

    def _approval_on_withdrawn(self):
        self.write({'state': 'draft'})


class HrExpenseSheet(models.Model):
    """A trip's reimbursement: an expense report linked to the approved
    travel request. Instead of Odoo's own manager approval it goes through
    the approval engine (reporting line up to the CEO); its costs are put on
    the project's analytic account. Accounting and payment stay standard.
    Expense reports without a trip keep Odoo's approval."""
    _name = 'hr.expense.sheet'
    _inherit = ['hr.expense.sheet', 'aiis.approval.mixin']

    aiis_travel_request_id = fields.Many2one(
        'aiis.travel.request', string='Travel Request', index=True, copy=False,
        domain="[('state', '=', 'approved'), ('employee_id', '=', employee_id)]")
    aiis_project_id = fields.Many2one(related='aiis_travel_request_id.project_id', string='Project', store=True)

    @api.constrains('aiis_travel_request_id', 'employee_id')
    def _check_travel_request(self):
        for sheet in self.filtered('aiis_travel_request_id'):
            travel = sheet.aiis_travel_request_id.sudo()
            if travel.employee_id != sheet.employee_id:
                raise ValidationError(_("The travel request belongs to another employee."))
            if travel.state != 'approved':
                raise ValidationError(_("Link an approved travel request."))

    def _approval_gate(self):
        return 'travel'

    def _approval_requester(self):
        return self.employee_id

    def action_submit_sheet(self):
        travel = self.filtered('aiis_travel_request_id')
        res = super(HrExpenseSheet, self - travel).action_submit_sheet() if self - travel else None
        for sheet in travel:
            analytic = sheet.aiis_project_id.sudo().analytic_account_id
            if analytic:
                sheet.expense_line_ids.filtered(lambda e: not e.analytic_distribution).write(
                    {'analytic_distribution': {str(analytic.id): 100.0}})
            sheet.write({'approval_state': 'submit'})
            sheet._approval_submit()
        return res

    def _approval_on_approved(self):
        self.with_context(aiis_approval_engine=True)._do_approve()

    def _check_can_approve(self):
        # the approval engine already checked who approves
        if self.env.context.get('aiis_approval_engine'):
            return
        return super()._check_can_approve()

    def _approval_on_rejected(self):
        self._do_reset_approval()

    def _approval_on_withdrawn(self):
        self._do_reset_approval()

    def action_reset_approval_expense_sheets(self):
        # back to draft while the engine is still asking: drop that approval
        self.filtered(lambda s: s.aiis_approval_state == 'pending')._approval_cancel(_("the report was reset to draft"))
        return super().action_reset_approval_expense_sheets()

    def action_approve_expense_sheets(self):
        if self.filtered('aiis_travel_request_id'):
            raise UserError(_("Trip reimbursements are approved through To do."))
        return super().action_approve_expense_sheets()

    def action_refuse_expense_sheets(self):
        if self.filtered('aiis_travel_request_id'):
            raise UserError(_("Trip reimbursements are rejected through To do."))
        return super().action_refuse_expense_sheets()
