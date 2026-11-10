from odoo import api, models

# Context flag set while the Employees side panel computes department counters.
OWN_MEMBERS_CTX = 'aiis_department_own_members'


class DepartmentPanelOwnMembersMixin(models.AbstractModel):
    """Employees side panel: a department shows only its own members.

    By default Odoo adds every sub-department's headcount to its parent
    (so "Chairman" showed the whole company). The matching client-side
    change - clicking a department lists only its own people instead of
    `child_of` - is in static/src/search_model_patch.js.
    """
    _name = 'aiis.department.panel.own.members.mixin'
    _description = 'Department side panel: own members only'

    @api.model
    def search_panel_select_range(self, field_name, **kwargs):
        if field_name == 'department_id':
            self = self.with_context(**{OWN_MEMBERS_CTX: True})
        return super().search_panel_select_range(field_name, **kwargs)

    @api.model
    def _search_panel_global_counters(self, values_range, parent_name):
        if self.env.context.get(OWN_MEMBERS_CTX):
            return  # keep the local (own members) counts
        return super()._search_panel_global_counters(values_range, parent_name)


class HrEmployee(models.Model):
    _name = 'hr.employee'
    _inherit = ['hr.employee', 'aiis.department.panel.own.members.mixin']


class HrEmployeePublic(models.Model):
    _name = 'hr.employee.public'
    _inherit = ['hr.employee.public', 'aiis.department.panel.own.members.mixin']
