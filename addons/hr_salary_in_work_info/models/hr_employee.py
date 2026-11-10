from odoo import fields, models


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    current_wage = fields.Monetary(
        string="Wage",
        related="contract_id.wage",
        currency_field="currency_id",
        groups="hr_contract.group_hr_contract_manager",
        help="Current wage from the employee's active contract.",
    )
