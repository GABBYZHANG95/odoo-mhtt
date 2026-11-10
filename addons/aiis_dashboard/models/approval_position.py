from odoo import api, models


class ApprovalPosition(models.Model):
    """The operations dashboard follows the CEO and Chairman positions
    (design doc 6.9, #66): whoever holds them is in the group, nobody else."""
    _inherit = 'aiis.approval.position'

    @api.model
    def _aiis_sync_dashboard_group(self):
        group = self.env.ref('aiis_dashboard.group_operations_dashboard', raise_if_not_found=False)
        if not group:
            return
        holders = self.sudo().search([('code', 'in', ('ceo', 'chairman')), ('active', '=', True)]).user_id
        holders = holders.filtered(lambda u: u.active and not u.share)
        if group.sudo().users != holders:
            group.sudo().users = [(6, 0, holders.ids)]

    @api.model_create_multi
    def create(self, vals_list):
        positions = super().create(vals_list)
        self._aiis_sync_dashboard_group()
        return positions

    def write(self, vals):
        result = super().write(vals)
        if {'user_id', 'active', 'code'} & vals.keys():
            self._aiis_sync_dashboard_group()
        return result
