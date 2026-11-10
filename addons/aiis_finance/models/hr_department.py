from odoo import api, fields, models

FUNCTIONS = [
    ('ga', 'General & administrative'),
    ('rd', 'Research & development'),
    ('sales', 'Sales'),
]


class HrDepartment(models.Model):
    """A department's function decides which group of operating expenses
    its costs go to (design doc 6.3); its analytic account is the
    department dimension of the reports."""
    _inherit = 'hr.department'

    aiis_function = fields.Selection(
        FUNCTIONS, string='Expense Function',
        help='Which group of operating expenses this department\'s costs go to. '
             'Empty: the same as the parent department, General & administrative at the top.')
    aiis_analytic_account_id = fields.Many2one(
        'account.analytic.account', string='Department Analytic Account', copy=False, readonly=True)

    def _aiis_get_function(self):
        self.ensure_one()
        department = self
        while department:
            if department.aiis_function:
                return department.aiis_function
            department = department.parent_id
        return 'ga'

    @api.model_create_multi
    def create(self, vals_list):
        departments = super().create(vals_list)
        departments._aiis_ensure_analytic_account()
        return departments

    def write(self, vals):
        res = super().write(vals)
        if 'name' in vals:
            for department in self.filtered('aiis_analytic_account_id'):
                department.aiis_analytic_account_id.sudo().name = department.name
        return res

    def _aiis_ensure_analytic_account(self):
        plan = self.env.ref('aiis_finance.analytic_plan_departments', raise_if_not_found=False)
        if not plan:
            return
        for department in self.filtered(lambda d: not d.aiis_analytic_account_id):
            department.aiis_analytic_account_id = self.env['account.analytic.account'].sudo().create({
                'name': department.name,
                'plan_id': plan.id,
                'company_id': department.company_id.id,
            })

    def _aiis_analytic_distribution(self, distribution=None):
        """`distribution` with this department's analytic account added"""
        distribution = dict(distribution or {})
        if self.aiis_analytic_account_id:
            distribution.setdefault(str(self.aiis_analytic_account_id.id), 100.0)
        return distribution
