from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

PERSONNEL_ACCOUNTS = {
    'ga': 'aiis_finance.account_ga_personnel',
    'rd': 'aiis_finance.account_rd_personnel',
    'sales': 'aiis_finance.account_sales_personnel',
}


class PersonnelCostType(models.Model):
    _name = 'aiis.personnel.cost.type'
    _description = 'Personnel Cost Type'
    _order = 'sequence, id'

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)


class PersonnelCost(models.Model):
    """Personnel costs entered by HR per employee (design doc 6.6): salary,
    visa and Iqama fees, insurance, GOSI... Posting books it to the
    Personnel cost of the employee's department function, owed to the
    payee. Employees never see these."""
    _name = 'aiis.personnel.cost'
    _description = 'Personnel Cost'
    _inherit = ['mail.thread']
    _order = 'date desc, id desc'

    employee_id = fields.Many2one('hr.employee', required=True, index=True, tracking=True)
    department_id = fields.Many2one(
        'hr.department', compute='_compute_department_id', store=True, readonly=False,
        help='Taken from the employee; decides which Personnel cost line it goes to.')
    type_id = fields.Many2one('aiis.personnel.cost.type', string='Cost Type', required=True, tracking=True)
    date = fields.Date(required=True, default=fields.Date.context_today, tracking=True,
                       help='When the cost was incurred: it counts in this period.')
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    amount = fields.Monetary(required=True, tracking=True)
    partner_id = fields.Many2one(
        'res.partner', string='Payee', compute='_compute_partner_id', store=True, readonly=False,
        help='Who is paid: the employee for salary, otherwise e.g. the insurer or GOSI.')
    description = fields.Char()
    contract_id = fields.Many2one('hr.contract', readonly=True, index=True,
                                  help='Set on salaries posted from the contract every month.')
    state = fields.Selection([
        ('draft', 'Draft'),
        ('posted', 'Posted'),
        ('cancel', 'Cancelled'),
    ], default='draft', required=True, tracking=True, copy=False)
    move_id = fields.Many2one('account.move', string='Journal Entry', readonly=True, copy=False)

    @api.depends('employee_id')
    def _compute_department_id(self):
        for cost in self:
            cost.department_id = cost.employee_id.department_id

    @api.depends('employee_id')
    def _compute_partner_id(self):
        for cost in self:
            cost.partner_id = cost.employee_id.work_contact_id

    @api.depends('employee_id', 'type_id', 'date')
    def _compute_display_name(self):
        for cost in self:
            cost.display_name = ' - '.join(filter(None, [
                cost.employee_id.name, cost.type_id.name, cost.date and cost.date.strftime('%Y-%m-%d')]))

    @api.constrains('amount')
    def _check_amount(self):
        if any(cost.amount <= 0 for cost in self):
            raise ValidationError(_("A personnel cost must be a positive amount."))

    def write(self, vals):
        if self.filtered(lambda c: c.state == 'posted') and set(vals) - {'state', 'move_id', 'description'}:
            raise UserError(_("A posted personnel cost cannot be changed - cancel it and enter it again."))
        return super().write(vals)

    def unlink(self):
        if self.filtered(lambda c: c.state == 'posted'):
            raise UserError(_("Cancel a posted personnel cost instead of deleting it."))
        return super().unlink()

    def _account(self):
        function = self.department_id._aiis_get_function() if self.department_id else 'ga'
        return self.env.ref(PERSONNEL_ACCOUNTS[function])

    def action_post(self):
        journal = self.env.ref('aiis_finance_dashboard.journal_payroll')
        payable = self.env.ref('aiis_finance.account_payroll_payable')
        for cost in self:
            if cost.state != 'draft':
                raise UserError(_("Only a draft personnel cost can be posted."))
            if not cost.partner_id:
                raise UserError(_("%s: set who is paid.", cost.display_name))
            label = '%s: %s' % (cost.employee_id.name, cost.description or cost.type_id.name)
            analytic = cost.department_id._aiis_analytic_distribution() if cost.department_id else False
            move = self.env['account.move'].sudo().create({
                'move_type': 'entry',
                'journal_id': journal.id,
                'company_id': cost.company_id.id,
                'date': cost.date,
                'ref': cost.display_name,
                'aiis_payroll_employee_id': cost.employee_id.id,
                'line_ids': [
                    (0, 0, {'account_id': cost._account().id, 'name': label, 'debit': cost.amount,
                            'analytic_distribution': analytic}),
                    (0, 0, {'account_id': payable.id, 'name': label, 'credit': cost.amount,
                            'partner_id': cost.partner_id.id}),
                ],
            })
            move.action_post()
            cost.write({'state': 'posted', 'move_id': move.id})

    def action_cancel(self):
        for cost in self.filtered(lambda c: c.state == 'posted'):
            move = cost.move_id.sudo()
            if move.line_ids.filtered(lambda line: line.reconciled or line.matched_debit_ids or line.matched_credit_ids):
                raise UserError(_("%s is already paid - undo the payment first.", cost.display_name))
            move.button_draft()
            move.button_cancel()
        self.write({'state': 'cancel'})

    def action_draft(self):
        self.filtered(lambda c: c.state == 'cancel').write({'state': 'draft', 'move_id': False})


class HrContract(models.Model):
    """The monthly salary job now records the salary as a personnel cost
    (owed to the employee) instead of paying it straight from the bank."""
    _inherit = 'hr.contract'

    def _aiis_post_payroll_month(self, period_start):
        self.ensure_one()
        journal = self._aiis_payroll_journal()
        # months posted the old way stay as they are
        legacy_ref = 'Payroll - %s - %s' % (self.employee_id.name, period_start.strftime('%Y-%m'))
        legacy = self.env['account.move'].search([('journal_id', '=', journal.id), ('ref', '=', legacy_ref)], limit=1)
        if legacy:
            return legacy
        period_end = period_start + relativedelta(day=31)
        Cost = self.env['aiis.personnel.cost'].sudo()
        cost = Cost.search([('contract_id', '=', self.id), ('date', '=', period_end), ('state', '!=', 'cancel')], limit=1)
        if not cost:
            cost = Cost.create({
                'employee_id': self.employee_id.id,
                'type_id': self.env.ref('aiis_finance.personnel_type_salary').id,
                'date': period_end,
                'amount': self.wage,
                'contract_id': self.id,
                'company_id': self.company_id.id,
                'description': _('Salary %s', period_start.strftime('%Y-%m')),
            })
        if cost.state == 'draft':
            cost.action_post()
        return cost.move_id
