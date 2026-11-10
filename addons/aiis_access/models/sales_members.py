from odoo import api, models

MEMBER_GROUP = 'aiis_access.group_sales_member'
HEAD_GROUP = 'aiis_access.group_sales_head'
# what membership brings, taken back when someone leaves sales
MEMBER_RIGHTS = ('sales_team.group_sale_salesman',)
HEAD_RIGHTS = ('sales_team.group_sale_salesman_all_leads',)


class ResUsers(models.Model):
    _inherit = 'res.users'

    @api.model
    def _aiis_sync_sales_members(self):
        """Sales rights follow the department (design doc 1.4, #52): the
        employees of a Sales department are sales members, its head a sales
        head. Who left sales loses what the membership gave, unless a sales
        manager."""
        member_group = self.env.ref(MEMBER_GROUP, raise_if_not_found=False)
        head_group = self.env.ref(HEAD_GROUP, raise_if_not_found=False)
        if not member_group or not head_group:
            return
        member_group, head_group = member_group.sudo(), head_group.sudo()
        departments = self.env['hr.department'].sudo().search([])
        sales_departments = departments.filtered(lambda d: d._aiis_get_function() == 'sales')
        employees = self.env['hr.employee'].sudo().search(
            [('department_id', 'in', sales_departments.ids), ('user_id', '!=', False)])
        members = employees.user_id.filtered(lambda u: not u.share)
        heads = sales_departments.manager_id.user_id.filtered(lambda u: not u.share)
        members |= heads
        manager_group = self.env.ref('sales_team.group_sale_manager')

        def take_back(users, xmlids):
            groups = [self.env.ref(xmlid) for xmlid in xmlids]
            for user in users.filtered(lambda u: manager_group not in u.groups_id):
                user.sudo().write({'groups_id': [(3, group.id) for group in groups]})

        old_members, old_heads = member_group.users, head_group.users
        if old_heads != heads:
            head_group.write({'users': [(6, 0, heads.ids)]})
            take_back(old_heads - heads, HEAD_RIGHTS)
        member_group.write({'users': [(6, 0, members.ids)]})
        take_back(old_members - members, MEMBER_RIGHTS + HEAD_RIGHTS)


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    @api.model_create_multi
    def create(self, vals_list):
        employees = super().create(vals_list)
        self.env['res.users']._aiis_sync_sales_members()
        return employees

    def write(self, vals):
        res = super().write(vals)
        if {'department_id', 'user_id', 'active'} & set(vals):
            self.env['res.users']._aiis_sync_sales_members()
        return res


class HrDepartment(models.Model):
    _inherit = 'hr.department'

    @api.model_create_multi
    def create(self, vals_list):
        departments = super().create(vals_list)
        self.env['res.users']._aiis_sync_sales_members()
        return departments

    def write(self, vals):
        res = super().write(vals)
        if {'manager_id', 'aiis_function', 'parent_id', 'active'} & set(vals):
            self.env['res.users']._aiis_sync_sales_members()
        return res
