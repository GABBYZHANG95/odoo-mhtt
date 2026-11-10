from odoo import api, fields, models


class ProcurementRequestCreatePoWizard(models.TransientModel):
    """A Purchase Order requires a vendor at creation time (it's a
    mandatory field in Odoo) - a PR deliberately does not have one yet, so
    this one-field wizard is where Procurement picks it before the actual
    purchase.order record gets created."""
    _name = 'procurement.request.create.po.wizard'
    _description = 'Pick a Vendor to Create the Purchase Order'

    request_id = fields.Many2one('procurement.request', required=True)
    partner_id = fields.Many2one(
        'res.partner', string='Vendor', required=True,
        domain=[('supplier_rank', '>', 0)])
    has_new_products = fields.Boolean(compute='_compute_has_new_products')
    categ_id = fields.Many2one(
        'product.category', string='Category for New Products',
        compute='_compute_categ_id', store=True, readonly=False,
        help='Lines that don\'t name an existing product get a new product '
             'in this category - it decides whether Finance books the '
             'purchase as Project Cost or Internal Expense.')

    @api.depends('request_id')
    def _compute_has_new_products(self):
        for wizard in self:
            wizard.has_new_products = bool(wizard.request_id.line_ids.filtered(lambda l: not l.product_id))

    @api.depends('request_id')
    def _compute_categ_id(self):
        for wizard in self:
            wizard.categ_id = wizard.request_id and wizard.request_id._default_new_product_category()

    def action_confirm(self):
        self.ensure_one()
        request = self.request_id
        request.check_access_rights('write')
        request.check_access_rule('write')
        # Serialize conversion so two open wizards cannot create two POs.
        self.env.cr.execute('SELECT id FROM procurement_request WHERE id = %s FOR UPDATE', [request.id])
        request.invalidate_recordset(['state', 'purchase_order_id', 'aiis_approval_state', 'approval_request_id'])
        request._check_can_create_po()
        po = self.env['purchase.order'].create({
            'partner_id': self.partner_id.id,
            'currency_id': request.currency_id.id,
            'origin': request.name,
            'order_line': request._prepare_po_order_lines(new_product_categ=self.categ_id),
        })
        request.write({'state': 'converted', 'purchase_order_id': po.id})
        request.sudo().activity_feedback(['aiis_procurement_request.mail_activity_procurement'])
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.order',
            'res_id': po.id,
            'view_mode': 'form',
            'target': 'current',
        }
