from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

BUDGET_GROUP = 'aiis_sales_expense.group_sales_budget_manager'


class SalesBudget(models.Model):
    """A department's sales expense budget for a period (a year or a
    quarter), kept by the CFO (design doc 5)."""
    _name = 'aiis.sales.budget'
    _description = 'Sales Expense Budget'
    _order = 'date_from desc, department_id'

    name = fields.Char(compute='_compute_name', store=True)
    department_id = fields.Many2one('hr.department', required=True, index=True)
    date_from = fields.Date(string='From', required=True)
    date_to = fields.Date(string='To', required=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    amount = fields.Monetary(string='Budget', required=True)
    used = fields.Monetary(compute='_compute_used', help='Approved requests in the period: reimbursed amount once reimbursed, estimate until then.')
    remaining = fields.Monetary(compute='_compute_used')
    note = fields.Char()

    @api.depends('department_id', 'date_from', 'date_to')
    def _compute_name(self):
        for budget in self:
            budget.name = '%s %s - %s' % (budget.department_id.name or '', budget.date_from or '', budget.date_to or '')

    def _compute_used(self):
        for budget in self:
            budget.used = budget._used_except()
            budget.remaining = budget.amount - budget.used

    def _used_except(self, request=None):
        """What the approved requests of the period take, leaving one out"""
        self.ensure_one()
        requests = self.env['aiis.sales.expense.request'].sudo().search([
            ('department_id', '=', self.department_id.id), ('state', '=', 'approved'),
            ('date_from', '>=', self.date_from), ('date_from', '<=', self.date_to),
            ('company_id', '=', self.company_id.id)])
        if request:
            requests -= request
        return sum(requests.mapped('budget_consumption'))

    @api.model
    def _for(self, department, date, company):
        if not department or not date:
            return self.browse()
        return self.sudo().search([
            ('department_id', '=', department.id), ('date_from', '<=', date), ('date_to', '>=', date),
            ('company_id', '=', company.id)], limit=1)

    @api.constrains('department_id', 'date_from', 'date_to')
    def _check_period(self):
        for budget in self:
            if budget.date_to < budget.date_from:
                raise ValidationError(_("The budget period ends before it starts."))
            overlap = self.search_count([
                ('id', '!=', budget.id), ('department_id', '=', budget.department_id.id),
                ('company_id', '=', budget.company_id.id),
                ('date_from', '<=', budget.date_to), ('date_to', '>=', budget.date_from)])
            if overlap:
                raise ValidationError(_("%s already has a budget overlapping this period.", budget.department_id.name))


class BudgetMixin(models.AbstractModel):
    """The budget box on requests and reimbursements: shown to the
    document's approvers, the CFO, the CEO and administrators (#44)."""
    _name = 'aiis.sales.budget.mixin'
    _description = 'Sales Expense Budget Box'

    show_budget = fields.Boolean(compute='_compute_budget_box')
    budget_id = fields.Many2one('aiis.sales.budget', compute='_compute_budget_box', string='Budget Period')
    budget_amount = fields.Monetary(compute='_compute_budget_box', string='Department Budget',
                                    currency_field='budget_currency_id')
    budget_used = fields.Monetary(compute='_compute_budget_box', string='Already Used',
                                  currency_field='budget_currency_id')
    budget_this = fields.Monetary(compute='_compute_budget_box', string='This Document',
                                  currency_field='budget_currency_id')
    budget_remaining = fields.Monetary(compute='_compute_budget_box', string='Left After This',
                                       currency_field='budget_currency_id')
    budget_over = fields.Boolean(compute='_compute_budget_box', string='Over Budget')
    budget_currency_id = fields.Many2one('res.currency', compute='_compute_budget_box')

    def _budget_request(self):
        """The sales expense request this document counts against"""
        raise NotImplementedError

    def _budget_this_amount(self):
        raise NotImplementedError

    @api.depends_context('uid')
    def _compute_budget_box(self):
        Position = self.env['aiis.approval.position']
        privileged = (self.env.user.has_group(BUDGET_GROUP) or self.env.user.has_group('base.group_system')
                      or self.env.user in (Position._holder('ceo') | Position._holder('cfo')))
        for record in self:
            request = record._budget_request().sudo()
            budget = self.env['aiis.sales.budget']._for(request.department_id, request.date_from, request.company_id)
            this = record._budget_this_amount()
            used = budget._used_except(request) if budget else 0.0
            record.show_budget = privileged or self.env.user in record.sudo().approval_user_ids
            record.budget_id = budget
            record.budget_currency_id = request.company_id.currency_id or self.env.company.currency_id
            record.budget_amount = budget.amount
            record.budget_used = used
            record.budget_this = this
            record.budget_remaining = budget.amount - used - this if budget else 0.0
            record.budget_over = bool(budget) and record.budget_remaining < 0
