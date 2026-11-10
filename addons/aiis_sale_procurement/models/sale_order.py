from odoo import _, api, fields, models
from odoo.exceptions import UserError

# PR states whose lines still "reserve" quantity on the SO line. Converted
# PRs are counted through their PO lines instead, rejected ones not at all.
OPEN_PR_STATES = ('draft', 'submitted', 'approved')


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    procurement_request_ids = fields.One2many(
        'procurement.request', 'sale_order_id', string='Purchase Requests')
    procurement_request_count = fields.Integer(compute='_compute_procurement_request_count')

    @api.depends('procurement_request_ids')
    def _compute_procurement_request_count(self):
        for order in self:
            order.procurement_request_count = len(order.procurement_request_ids)

    def _get_project_analytic_account(self):
        self.ensure_one()
        return self.contract_id.analytic_account_id or self.analytic_account_id

    def action_confirm(self):
        res = super().action_confirm()
        # Tag the revenue with the contract's project so it lines up with the
        # purchase cost in the analytic reports. Lines the salesperson already
        # tagged by hand are left alone.
        for order in self:
            analytic = order._get_project_analytic_account()
            if not analytic:
                continue
            if not order.analytic_account_id:
                order.analytic_account_id = analytic
            order.order_line.filtered(
                lambda l: not l.display_type and not l.analytic_distribution
            ).analytic_distribution = {str(analytic.id): 100.0}
        return res

    def action_create_procurement_request(self):
        self.ensure_one()
        if self.state != 'sale':
            raise UserError(_("Purchase requests can only be raised for confirmed Sales Orders."))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Create Purchase Request'),
            'res_model': 'sale.order.create.pr.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_sale_order_id': self.id},
        }

    def action_view_procurement_requests(self):
        self.ensure_one()
        action = {
            'type': 'ir.actions.act_window',
            'name': _('Purchase Requests'),
            'res_model': 'procurement.request',
            'domain': [('sale_order_id', '=', self.id)],
            'context': {'create': False},
        }
        if self.procurement_request_count == 1:
            action.update(view_mode='form', res_id=self.procurement_request_ids.id)
        else:
            action.update(view_mode='tree,form')
        return action


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    procurement_request_line_ids = fields.One2many('procurement.request.line', 'sale_line_id')
    purchase_covered_qty = fields.Float(
        string='Requested / Purchased', compute='_compute_purchase_covered_qty',
        digits='Product Unit of Measure',
        help='Quantity already covered by open purchase requests or by '
             'purchase orders (in this line\'s unit of measure).')

    @api.depends('procurement_request_line_ids.quantity', 'procurement_request_line_ids.request_id.state',
                 'purchase_line_ids.product_qty', 'purchase_line_ids.state')
    def _compute_purchase_covered_qty(self):
        # sudo: a salesperson may not be allowed to read requests raised by
        # colleagues, but they still count against what's left to purchase
        for line in self.sudo():
            qty = 0.0
            for pol in line.purchase_line_ids.filtered(lambda l: l.state != 'cancel'):
                qty += pol.product_uom._compute_quantity(
                    pol.product_qty, line.product_uom, raise_if_failure=False)
            for prl in line.procurement_request_line_ids.filtered(
                    lambda l: l.request_id.state in OPEN_PR_STATES):
                qty += prl.uom_id._compute_quantity(
                    prl.quantity, line.product_uom, raise_if_failure=False)
            line.purchase_covered_qty = qty
