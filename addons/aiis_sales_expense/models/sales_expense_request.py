from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class SalesExpenseRequest(models.Model):
    """Pre-approval of a sales trip, client entertainment or a gift (design
    doc 5): the reporting line up to the CEO approves, seeing the
    department's budget. Afterwards the employee claims the actual costs on
    the request itself (#65), which builds the expense report linked to it."""
    _name = 'aiis.sales.expense.request'
    _description = 'Sales Expense Request'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'aiis.approval.mixin', 'aiis.sales.budget.mixin',
                'aiis.expense.claim.mixin']
    _order = 'id desc'

    name = fields.Char(default='New', readonly=True, copy=False)
    employee_id = fields.Many2one(
        'hr.employee', string='Employee', required=True, tracking=True,
        default=lambda self: self.env.user.employee_id)
    department_id = fields.Many2one(related='employee_id.department_id', store=True, string='Department')
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    expense_type = fields.Selection([
        ('trip', 'Sales Trip'),
        ('entertainment', 'Client Entertainment'),
        ('gift', 'Gift'),
    ], string='Type', required=True, default='trip', tracking=True)
    partner_id = fields.Many2one('res.partner', string='Customer', required=True, tracking=True)
    project_id = fields.Many2one(
        'aiis.project', string='Project', help='For reference only - sales expenses are not project costs.')
    purpose = fields.Text(required=True)
    destination = fields.Char()
    date_from = fields.Date(string='Date', required=True, default=fields.Date.context_today,
                            help='Day of the event, or first day of the trip.')
    date_to = fields.Date(string='Until', help='Last day of the trip.')
    amount = fields.Monetary(string='Estimated Amount', required=True, tracking=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('approved', 'Approved'),
    ], default='draft', required=True, tracking=True, copy=False)
    expense_sheet_ids = fields.One2many('hr.expense.sheet', 'aiis_sales_request_id', string='Expense Reports')
    claim_line_ids = fields.One2many('hr.expense', 'aiis_sales_request_id', string='Actual Expenses')
    amount_reimbursed = fields.Monetary(
        compute='_compute_consumption', compute_sudo=True, string='Reimbursed',
        help='Approved, posted or paid expense reports linked to this request.')
    budget_consumption = fields.Monetary(
        compute='_compute_consumption', compute_sudo=True,
        help='What it takes from the budget: the reimbursed amount once reimbursed, the estimate until then.')

    @api.depends('amount', 'expense_sheet_ids.state', 'expense_sheet_ids.total_amount')
    def _compute_consumption(self):
        for request in self:
            done = request.expense_sheet_ids.filtered(lambda s: s.state in ('approve', 'post', 'done'))
            request.amount_reimbursed = sum(done.mapped('total_amount'))
            request.budget_consumption = request.amount_reimbursed if done else request.amount

    @api.constrains('expense_type', 'destination', 'date_from', 'date_to')
    def _check_trip(self):
        for request in self:
            if request.expense_type == 'trip' and not (request.destination and request.date_to):
                raise ValidationError(_("A sales trip needs a destination and its last day."))
            if request.date_to and request.date_to < request.date_from:
                raise ValidationError(_("The trip ends before it starts."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('aiis.sales.expense.request') or 'New'
        return super().create(vals_list)

    def action_submit(self):
        for request in self:
            if request.state != 'draft':
                raise UserError(_("Only a draft request can be submitted."))
            request.state = 'submitted'
            request._approval_submit()

    # Claim on the request (#65)
    def _claim_link_field(self):
        return 'aiis_sales_request_id'

    def _claim_estimate(self):
        return self.amount

    def _claim_default_product(self):
        xmlid = {
            'trip': 'hr_expense.expense_product_travel_accommodation',
            'entertainment': 'hr_expense.expense_product_meal',
            'gift': 'hr_expense.expense_product_gift',
        }.get(self.expense_type)
        return (xmlid and self.env.ref(xmlid, raise_if_not_found=False)) or super()._claim_default_product()

    # Budget box
    def _budget_request(self):
        return self

    def _budget_this_amount(self):
        return self.amount

    # Approval engine hooks
    def _approval_requester(self):
        return self.employee_id

    def _approval_on_approved(self):
        self.write({'state': 'approved'})

    def _approval_on_rejected(self):
        self.write({'state': 'draft'})

    def _approval_on_withdrawn(self):
        self.write({'state': 'draft'})
