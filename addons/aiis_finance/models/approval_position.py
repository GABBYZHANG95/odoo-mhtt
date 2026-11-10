from odoo import api, models


class ApprovalPosition(models.Model):
    """The finance groups follow the positions (design doc 6.8.6): the
    holders of CFO, CEO and Chairman are the finance board, the CFO alone
    does bank reconciliation. Kept in sync whenever a holder changes, not
    only on install - a production database gets its people after the
    modules are installed."""
    _inherit = 'aiis.approval.position'

    @api.model
    def _aiis_sync_finance_groups(self):
        for xmlid, codes in (('aiis_finance.group_finance_board', ('cfo', 'ceo', 'chairman')),
                             ('aiis_finance.group_finance_bank_operator', ('cfo',))):
            group = self.env.ref(xmlid, raise_if_not_found=False)
            if not group:
                continue
            holders = self.sudo().search([('code', 'in', codes), ('active', '=', True)]).user_id
            holders = holders.filtered(lambda u: u.active and not u.share)
            removed = group.sudo().users - holders
            if group.sudo().users != holders:
                group.sudo().users = [(6, 0, holders.ids)]
            if removed:
                self._aiis_drop_implied(group, removed)

    @api.model
    def _aiis_drop_implied(self, group, users):
        """Leaving a group keeps the groups it implied (Odoo only adds them),
        so a former CFO would keep accounting rights. Take them away too;
        Odoo puts back any that the user's other groups still imply. The
        user type (internal user) is never touched."""
        user_types = self.env.ref('base.module_category_user_type')
        implied = group.sudo().trans_implied_ids.filtered(lambda g: g.category_id != user_types)
        if implied:
            users.sudo().write({'groups_id': [(3, g.id) for g in implied]})

    @api.model_create_multi
    def create(self, vals_list):
        positions = super().create(vals_list)
        self._aiis_sync_finance_groups()
        return positions

    def write(self, vals):
        result = super().write(vals)
        if {'user_id', 'active', 'code'} & vals.keys():
            self._aiis_sync_finance_groups()
        return result
