from odoo import fields, models


class ProcurementRequest(models.Model):
    """Project purchase requests (design doc 4.5): raised by the project
    manager from the BOM, they skip approval (template "Project Purchase
    Request" has no steps) and go straight to Procurement."""
    _inherit = 'procurement.request'

    aiis_project_id = fields.Many2one('aiis.project', string='Project', readonly=True, index=True, copy=False)

    def _approval_gate(self):
        return 'project' if self.aiis_project_id else super()._approval_gate()

    def _default_new_product_category(self):
        if self.aiis_project_id:
            return self.env.ref('aiis_procurement_request.product_category_project_cost', raise_if_not_found=False)
        return super()._default_new_product_category()

    def _prepare_po_order_lines(self, new_product_categ=None):
        order_lines = super()._prepare_po_order_lines(new_product_categ=new_product_categ)
        for (_cmd, _id, vals), pr_line in zip(order_lines, self.line_ids):
            if pr_line.aiis_bom_line_id:
                vals['aiis_bom_line_id'] = pr_line.aiis_bom_line_id.id
        return order_lines


class ProcurementRequestLine(models.Model):
    _inherit = 'procurement.request.line'

    aiis_bom_line_id = fields.Many2one('aiis.project.bom.line', string='BOM Line', readonly=True, index=True)


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    aiis_bom_line_id = fields.Many2one('aiis.project.bom.line', string='BOM Line', readonly=True, index=True, copy=False)
