from odoo import api, fields, models

BUYER_GROUP = 'aiis_procurement_request.group_procurement_buyer'


class ResCompany(models.Model):
    _inherit = 'res.company'

    aiis_procurement_department_id = fields.Many2one(
        'hr.department', string='Procurement Department',
        help='Its purchase users turn approved purchase requests into purchase orders. '
             'Empty: every purchase user.')

    def write(self, vals):
        res = super().write(vals)
        if 'aiis_procurement_department_id' in vals:
            self.env['res.users']._aiis_sync_procurement_buyers()
        return res


class ResUsers(models.Model):
    _inherit = 'res.users'

    @api.model
    def _aiis_sync_procurement_buyers(self):
        """Procurement Department (Buyer) = the purchase users working in a
        company's procurement department; every purchase user of a company
        that has none set (#48)"""
        group = self.env.ref(BUYER_GROUP, raise_if_not_found=False)
        if not group:
            return
        purchase_users = self.env['res.users'].sudo().with_context(active_test=False).search([
            ('share', '=', False), ('groups_id', 'in', self.env.ref('purchase.group_purchase_user').ids)])
        buyers = self.env['res.users']
        for company in self.env['res.company'].sudo().search([]):
            users = purchase_users.filtered(lambda u: company in u.company_ids)
            department = company.aiis_procurement_department_id
            if department:
                members = self.env['hr.employee'].sudo().with_context(active_test=False).search(
                    [('department_id', 'child_of', department.id), ('user_id', '!=', False)]).user_id
                users &= members
            buyers |= users
        group = group.sudo()
        if group.users != buyers:
            group.write({'users': [(6, 0, buyers.ids)]})

    @api.model_create_multi
    def create(self, vals_list):
        users = super().create(vals_list)
        self._aiis_sync_procurement_buyers()
        return users

    def write(self, vals):
        res = super().write(vals)
        if any(key == 'groups_id' or key.startswith(('in_group_', 'sel_groups_')) for key in vals):
            self._aiis_sync_procurement_buyers()
        return res


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    @api.model_create_multi
    def create(self, vals_list):
        employees = super().create(vals_list)
        self.env['res.users']._aiis_sync_procurement_buyers()
        return employees

    def write(self, vals):
        res = super().write(vals)
        if {'department_id', 'user_id', 'active'} & set(vals):
            self.env['res.users']._aiis_sync_procurement_buyers()
        return res
