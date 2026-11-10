from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare


class SaleOrderCreatePrWizard(models.TransientModel):
    """Pick which lines of a confirmed Sales Order need to be bought in, and
    how much of each, then raise one Purchase Request (PR) for them. Lines
    that are already fully covered by earlier PRs / POs are not offered."""
    _name = 'sale.order.create.pr.wizard'
    _description = 'Create Purchase Request from Sales Order'

    sale_order_id = fields.Many2one('sale.order', required=True, readonly=True)
    needed_by_date = fields.Date(string='Needed By')
    reason = fields.Text(string='Business Reason')
    line_ids = fields.One2many('sale.order.create.pr.wizard.line', 'wizard_id')

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        order = self.env['sale.order'].browse(res.get('sale_order_id') or self.env.context.get('default_sale_order_id'))
        if not order:
            return res
        contract = order.contract_id
        res['needed_by_date'] = contract.start_date or (fields.Date.context_today(self) + relativedelta(days=7))
        res['reason'] = _("For %(so)s - %(customer)s", so=order.name, customer=order.partner_id.display_name)
        if contract:
            res['reason'] += _(" (contract %s)", contract.name)
        lines = []
        for sol in order.order_line.filtered(lambda l: not l.display_type and l.product_id):
            remaining = sol.product_uom_qty - sol.purchase_covered_qty
            if float_compare(remaining, 0.0, precision_rounding=sol.product_uom.rounding) <= 0:
                continue
            lines.append((0, 0, {
                'sale_line_id': sol.id,
                'selected': sol.product_id.purchase_ok,
                'description': sol.name.split('\n')[0][:200] if sol.name else sol.product_id.display_name,
                'quantity': remaining,
                'estimated_unit_price': sol.product_id.standard_price,
            }))
        res['line_ids'] = lines
        return res

    def action_confirm(self):
        self.ensure_one()
        order = self.sale_order_id
        if order.state != 'sale':
            raise UserError(_("Purchase requests can only be raised for confirmed Sales Orders."))
        lines = self.line_ids.filtered('selected')
        if not lines:
            raise UserError(_("Tick at least one line to purchase."))
        for line in lines:
            if float_compare(line.quantity, 0.0, precision_rounding=line.uom_id.rounding) <= 0:
                raise UserError(_("The quantity to purchase for '%s' must be positive.", line.description))
            if float_compare(line.quantity, line.remaining_qty, precision_rounding=line.uom_id.rounding) > 0:
                raise UserError(_(
                    "'%(item)s': only %(remaining)s %(uom)s left to purchase (sold %(sold)s, "
                    "already requested / purchased %(covered)s).",
                    item=line.description, remaining=line.remaining_qty, uom=line.uom_id.name,
                    sold=line.sold_qty, covered=line.covered_qty))
        employee = self.env.user.employee_id
        if not employee:
            raise UserError(_("Your user is not linked to an employee, so the request has no approver."))
        contract = order.contract_id
        analytic = order._get_project_analytic_account()
        request = self.env['procurement.request'].create({
            'employee_id': employee.id,
            'sale_order_id': order.id,
            'customer_contract_id': contract.id,
            'needed_by_date': self.needed_by_date,
            'reason': self.reason,
            'analytic_account_id': analytic.id,
            'line_ids': [(0, 0, {
                'sale_line_id': line.sale_line_id.id,
                'product_id': line.product_id.id,
                'description': line.description,
                'quantity': line.quantity,
                'uom_id': line.uom_id.id,
                'estimated_unit_price': line.estimated_unit_price,
            }) for line in lines],
        })
        order.message_post(body=_("Purchase request %s created.", request._get_html_link()))
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'procurement.request',
            'res_id': request.id,
            'view_mode': 'form',
            'target': 'current',
        }


class SaleOrderCreatePrWizardLine(models.TransientModel):
    _name = 'sale.order.create.pr.wizard.line'
    _description = 'Create Purchase Request from Sales Order - Line'

    wizard_id = fields.Many2one('sale.order.create.pr.wizard', required=True, ondelete='cascade')
    selected = fields.Boolean(string='Purchase')
    sale_line_id = fields.Many2one('sale.order.line', required=True, readonly=True)
    product_id = fields.Many2one(related='sale_line_id.product_id')
    uom_id = fields.Many2one(related='sale_line_id.product_uom', string='Unit')
    description = fields.Char(string='Item / Specification', required=True)
    sold_qty = fields.Float(related='sale_line_id.product_uom_qty', string='Sold')
    covered_qty = fields.Float(related='sale_line_id.purchase_covered_qty', string='Already Requested')
    remaining_qty = fields.Float(compute='_compute_remaining_qty', string='Remaining')
    quantity = fields.Float(string='Qty to Purchase', digits='Product Unit of Measure')
    currency_id = fields.Many2one(related='sale_line_id.company_id.currency_id')
    estimated_unit_price = fields.Monetary(string='Est. Unit Cost')

    @api.depends('sold_qty', 'covered_qty')
    def _compute_remaining_qty(self):
        for line in self:
            line.remaining_qty = line.sold_qty - line.covered_qty
