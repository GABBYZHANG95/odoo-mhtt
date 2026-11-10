from odoo import _, api, fields, models
from odoo.exceptions import UserError


class SaleOrder(models.Model):
    """A project's sales order is confirmed by the project contract gate
    (design doc 4.4, #40) instead of a customer contract; other sales keep
    the customer contract approval."""
    _inherit = 'sale.order'

    aiis_project_id = fields.Many2one('aiis.project', string='Project', readonly=True, copy=False, index=True)

    @api.depends('aiis_project_id.contract_approved')
    def _compute_contract_id(self):
        super()._compute_contract_id()
        for order in self.filtered('aiis_project_id'):
            order.contract_ok = order.aiis_project_id.sudo().contract_approved

    def _check_contract_approved(self):
        project_orders = self.filtered('aiis_project_id')
        for order in project_orders:
            if not order.aiis_project_id.sudo().contract_approved:
                raise UserError(_(
                    "%(so)s belongs to project %(project)s: it is confirmed automatically once the "
                    "project contract is approved.", so=order.name, project=order.aiis_project_id.sudo().display_name))
        return super(SaleOrder, self - project_orders)._check_contract_approved()
