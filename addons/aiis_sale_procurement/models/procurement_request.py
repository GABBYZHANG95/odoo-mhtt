from odoo import api, fields, models


class ProcurementRequest(models.Model):
    """A PR can be raised for a confirmed Sales Order (back-to-back
    purchasing). It still goes through the normal manager approval; the
    link only carries the SO / contract / project through to the PO."""
    _inherit = 'procurement.request'

    sale_order_id = fields.Many2one(
        'sale.order', string='Sales Order', readonly=True, copy=False, index=True, tracking=True)
    customer_id = fields.Many2one(related='sale_order_id.partner_id', string='Customer')
    customer_contract_id = fields.Many2one(
        'customer.contract', string='Customer Contract', readonly=True, copy=False)
    # Procurement sees the numbers only, not the sales documents (design doc 1.4, #51)
    sale_order_ref = fields.Char(related='sale_order_id.name', string='Sales Order No.', store=True)
    customer_contract_ref = fields.Char(related='customer_contract_id.name', string='Customer Contract No.', store=True)

    def _default_new_product_category(self):
        if self.sale_order_id:
            return self.env.ref('aiis_procurement_request.product_category_project_cost', raise_if_not_found=False)
        return super()._default_new_product_category()

    def _prepare_po_order_lines(self, new_product_categ=None):
        """Link each PO line back to the SO line it was requested for, using
        sale_purchase's own `sale_line_id` - that is what drives the native
        Purchase / Sales smart buttons on the SO and the PO."""
        order_lines = super()._prepare_po_order_lines(new_product_categ=new_product_categ)
        for (_cmd, _id, vals), pr_line in zip(order_lines, self.line_ids):
            if pr_line.sale_line_id:
                vals['sale_line_id'] = pr_line.sale_line_id.id
        return order_lines


class ProcurementRequestLine(models.Model):
    _inherit = 'procurement.request.line'

    sale_line_id = fields.Many2one(
        'sale.order.line', string='Sales Order Line', readonly=True, copy=False, index=True)


class ProcurementRequestCreatePoWizard(models.TransientModel):
    _inherit = 'procurement.request.create.po.wizard'

    def action_confirm(self):
        res = super().action_confirm()
        request = self.request_id
        if request.sale_order_id and request.purchase_order_id:
            # sudo: Procurement may have no Sales rights, the SO number is all we read
            request.purchase_order_id.origin = '%s, %s' % (request.name, request.sudo().sale_order_id.name)
        return res
