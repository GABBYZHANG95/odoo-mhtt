from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ProcurementRequest(models.Model):
    """Internal "what do we need to buy" request (PR) - deliberately kept
    separate from the actual Purchase Order (PO). A PR never names a
    vendor or a final price; it exists purely so an employee can ask for
    something and their reporting line can approve it before Procurement
    gets involved at all. Approval runs through the approval engine
    (aiis_approval): every manager up to the department head approves.
    Once approved, Procurement converts it into a real `purchase.order`
    (still just a draft RFQ) and takes it from there - picking the
    vendor, negotiating price, etc.

    The PO's own approval (CEO, plus the Chairman above 300,000 SAR) is in
    purchase_order.py. Once Procurement has received everything on the PO
    the request moves to Done and the requester is notified.
    """
    _name = 'procurement.request'
    _description = 'Procurement Request (PR)'
    _order = 'id desc'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'aiis.approval.mixin']

    name = fields.Char(default='New', readonly=True, copy=False)
    employee_id = fields.Many2one(
        'hr.employee', string='Requested By', required=True, tracking=True,
        default=lambda self: self.env.user.employee_id)
    department_id = fields.Many2one(
        'hr.department', string='Department', related='employee_id.department_id', store=True)
    manager_id = fields.Many2one(
        'hr.employee', string='Manager', related='employee_id.parent_id', store=True,
        help='The requester\'s direct manager (Employees > Reporting To).')
    request_date = fields.Date(default=fields.Date.context_today, required=True)
    needed_by_date = fields.Date(string='Needed By')
    currency_id = fields.Many2one(
        'res.currency', string='Currency', required=True,
        default=lambda self: self.env.company.currency_id,
        domain="[('name', 'in', ('USD', 'CNY', 'SAR'))]",
        help='Limited to USD, CNY (Chinese yuan) and SAR (Saudi Riyal) - the '
             'currencies this company actually deals in.')
    reason = fields.Text(string='Business Reason')
    state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('approved', 'Approved'),
        ('converted', 'Converted to PO'),
        ('done', 'Done'),
    ], default='draft', tracking=True, copy=False)
    line_ids = fields.One2many('procurement.request.line', 'request_id', copy=True)
    estimated_total = fields.Monetary(compute='_compute_estimated_total', store=True)
    analytic_account_id = fields.Many2one(
        'account.analytic.account', string='Project / Analytic Account',
        help='Optional - tag this request to a project so the eventual '
             'cost shows up under it in the Finance reports.')
    purchase_order_id = fields.Many2one('purchase.order', readonly=True, copy=False)
    can_create_po = fields.Boolean(compute='_compute_can_create_po')
    # Requesters can't read purchase orders (no Purchase rights), so the
    # PO's progress is mirrored here as superuser - status only, no prices
    po_approval_status = fields.Selection([
        ('draft', 'Being Prepared'),
        ('to_approve', 'Awaiting Approval'),
        ('approved', 'Approved'),
        ('cancelled', 'Cancelled'),
    ], string='PO Status', compute='_compute_po_progress', compute_sudo=True)
    payment_status = fields.Selection(
        related='purchase_order_id.aiis_payment_status', string='Payment Status', related_sudo=True)
    receipt_status = fields.Selection([
        ('pending', 'Not Received'),
        ('partial', 'Partially Received'),
        ('received', 'Received'),
    ], string='Goods Received', compute='_compute_po_progress', compute_sudo=True)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)

    @api.depends('line_ids.estimated_subtotal')
    def _compute_estimated_total(self):
        for rec in self:
            rec.estimated_total = sum(rec.line_ids.mapped('estimated_subtotal'))

    @api.depends('purchase_order_id.state', 'purchase_order_id.order_line.qty_received')
    def _compute_po_progress(self):
        po_states = {'draft': 'draft', 'sent': 'draft', 'to approve': 'to_approve',
                     'purchase': 'approved', 'done': 'approved', 'cancel': 'cancelled'}
        for rec in self:
            po = rec.purchase_order_id
            rec.po_approval_status = po_states.get(po.state, False)
            rec.receipt_status = po._get_receipt_status() if po.state in ('purchase', 'done') else False

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('procurement.request') or 'New'
        return super().create(vals_list)

    def action_submit(self):
        for rec in self:
            if not rec.line_ids:
                raise UserError(_("Add at least one line before submitting."))
            rec.state = 'submitted'
            rec._approval_submit()

    # Approval engine hooks

    def _approval_requester(self):
        return self.employee_id

    def _approval_on_approved(self):
        self.write({'state': 'approved'})
        self._schedule_procurement_activities()

    def _procurement_buyers(self):
        """Who converts this request into a PO (design doc 3.3, #48): the
        Procurement Department (Buyer) group, kept up to date by
        res.users._aiis_sync_procurement_buyers()"""
        self.ensure_one()
        return self.env.ref('aiis_procurement_request.group_procurement_buyer').sudo().users.filtered(
            lambda u: self.company_id in u.company_ids)

    @api.depends_context('uid')
    @api.depends('state', 'purchase_order_id')
    def _compute_can_create_po(self):
        for request in self:
            request.can_create_po = (request.state == 'approved' and not request.purchase_order_id
                                     and self.env.user in request._procurement_buyers())

    def _schedule_procurement_activities(self):
        """A shared purchasing task; conversion completes it for the team."""
        activity_type = self.env.ref('aiis_procurement_request.mail_activity_procurement')
        for request in self.sudo().filtered(lambda r: r.state == 'approved' and not r.purchase_order_id):
            buyers = request._procurement_buyers()
            tasks = request.activity_ids.filtered(lambda a: a.activity_type_id == activity_type)
            stale = tasks.filtered(lambda a: a.user_id not in buyers)
            assigned = (tasks - stale).user_id
            # tasks of people who no longer convert requests go away
            stale.unlink()
            for buyer in buyers - assigned:
                request.with_context(mail_activity_quick_update=True).activity_schedule(
                    'aiis_procurement_request.mail_activity_procurement', user_id=buyer.id,
                    summary=_("Create purchase order: %s", request.name),
                    note=_("This request is approved. Select a vendor and prepare the purchase order."))

    def _approval_on_rejected(self):
        # back to draft so the requester can change it and submit again
        self.write({'state': 'draft'})

    def _approval_on_withdrawn(self):
        self.write({'state': 'draft'})

    def action_mark_received(self):
        self.purchase_order_id.action_mark_received()
        return True

    def _mark_done(self):
        for rec in self:
            rec.state = 'done'
            rec.message_post(
                body=_("All items have been received - this request is complete."),
                partner_ids=rec.employee_id.user_id.partner_id.ids,
                subtype_xmlid='mail.mt_comment')

    def action_create_po(self):
        """A Purchase Order always needs a vendor at the database level
        (it's a required field in Odoo) - so unlike the PR, we can't create
        one "blank". Open a one-field wizard asking Procurement to pick the
        vendor first, then create the PO."""
        self.ensure_one()
        self._check_can_create_po()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'procurement.request.create.po.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_request_id': self.id},
        }

    def _check_can_create_po(self):
        self.ensure_one()
        if self.purchase_order_id:
            raise UserError(_("This request already has a Purchase Order."))
        approval = self.sudo().approval_request_id
        # Readonly UI fields are still writable over RPC. Require the
        # approval engine's protected record for this exact document.
        if (self.state != 'approved' or self.aiis_approval_state != 'approved'
                or approval.state != 'approved'
                or approval.res_model != self._name or approval.res_id != self.id):
            raise UserError(_("Only approved requests can be converted to a Purchase Order."))
        if self.env.user not in self._procurement_buyers():
            raise UserError(_("Purchase orders are created by the procurement department."))

    def _default_new_product_category(self):
        """Category for products created when this request becomes a PO.
        A plain employee request is for our own use; aiis_sale_procurement
        overrides this for requests raised from a Sales Order."""
        self.ensure_one()
        return self.env.ref('aiis_procurement_request.product_category_internal_expense', raise_if_not_found=False)

    def _prepare_po_order_lines(self, new_product_categ=None):
        """Odoo's purchase.order.line has a DB-level constraint requiring
        product_id, product_uom and date_planned on every real line - a PR
        line doesn't have to name a product (an employee shouldn't need to
        know whether one already exists in the catalog), so if none was
        picked, create a minimal one on the fly here rather than force
        that decision onto the requester."""
        self.ensure_one()
        planned = self.needed_by_date or (fields.Date.context_today(self) + relativedelta(days=7))
        order_lines = []
        for line in self.line_ids:
            product = line.product_id
            if product:
                # An existing product already has a fixed UoM category -
                # always use ITS purchase UoM for the PO line. Whatever the
                # PR line's own uom_id says is just how the employee
                # described the quantity; it can't override a real
                # product's UoM category without breaking the order line.
                line_uom = product.uom_po_id
            else:
                # No product picked - create one on the fly, in the SAME
                # unit of measure the PR line was filed in (meters, kg,
                # units, ...). Without this it defaults to "Units" and the
                # PO line fails with a UoM category mismatch the moment the
                # request used anything else (e.g. length, weight).
                line_uom = line.uom_id
                # sudo: creating products needs Purchase Administrator, but
                # any Purchase User converts requests
                product = self.env['product.product'].sudo().create({
                    'name': line.description,
                    'type': 'service',
                    'categ_id': (new_product_categ or self._default_new_product_category() or self.env.ref('product.product_category_all')).id,
                    'purchase_ok': True,
                    'sale_ok': False,
                    'uom_id': line_uom.id,
                    'uom_po_id': line_uom.id,
                })
            vals = {
                'product_id': product.id,
                'name': line.description,
                'product_qty': line.quantity,
                'product_uom': line_uom.id,
                'date_planned': planned,
                'analytic_distribution': (
                    {str(self.analytic_account_id.id): 100.0} if self.analytic_account_id else False),
            }
            # Employees don't see or fill the price - when it's blank,
            # leave price_unit out so Odoo computes it from the vendor's
            # price list instead of forcing 0
            if line.estimated_unit_price:
                vals['price_unit'] = line.estimated_unit_price
            order_lines.append((0, 0, vals))
        return order_lines


class ProcurementRequestLine(models.Model):
    _name = 'procurement.request.line'
    _description = 'Procurement Request Line'

    request_id = fields.Many2one('procurement.request', required=True, ondelete='cascade')
    product_id = fields.Many2one(
        'product.product', string='Product',
        help='Optional - link to an existing product if there is one. If '
             'not, just describe what is needed; Procurement creates the '
             'product record when converting the request to a PO.')
    description = fields.Char(string='Item / Specification', required=True)
    quantity = fields.Float(default=1.0, required=True)
    uom_id = fields.Many2one(
        'uom.uom', string='Unit of Measure', required=True,
        default=lambda self: self.env.ref('uom.product_uom_unit', raise_if_not_found=False))
    estimated_unit_price = fields.Monetary(
        string='Est. Unit Price',
        help='Filled by Procurement - hidden from the requester, who only '
             'describes what is needed.')
    currency_id = fields.Many2one(related='request_id.currency_id', store=True)
    estimated_subtotal = fields.Monetary(compute='_compute_estimated_subtotal', store=True)

    @api.depends('quantity', 'estimated_unit_price')
    def _compute_estimated_subtotal(self):
        for rec in self:
            rec.estimated_subtotal = rec.quantity * rec.estimated_unit_price

    @api.onchange('product_id')
    def _onchange_product_id(self):
        for rec in self:
            if rec.product_id:
                # keep a spec the employee already typed
                rec.description = rec.description or rec.product_id.display_name
                rec.uom_id = rec.product_id.uom_po_id
                rec.estimated_unit_price = rec.product_id.standard_price
