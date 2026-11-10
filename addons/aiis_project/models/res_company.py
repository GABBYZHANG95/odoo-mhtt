from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    aiis_rd_department_id = fields.Many2one(
        'hr.department', string='R&D Department',
        help='Its employees (sub-departments and head included) maintain project BOMs.')

    def _aiis_is_rd(self, user):
        """Does the user belong to R&D (design doc 4.8, #43)?"""
        self.ensure_one()
        department = self.sudo().aiis_rd_department_id
        if not department:
            return False
        employees = self.env['hr.employee'].sudo().search([('user_id', '=', user.id)])
        return bool(employees.filtered(lambda e: e.department_id and e.department_id.parent_path.startswith(
            department.parent_path))) or department.manager_id.user_id == user
