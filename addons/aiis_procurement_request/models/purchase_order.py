from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare


class PurchaseOrder(models.Model):
    """Replaces Odoo's own PO double validation (a single amount threshold,
    approvable by any Purchase Manager) with the approval engine
    (aiis_approval). Its template "Purchase Order" says who approves -
    by default the CEO for every PO, then the Chairman above 300,000 SAR
    (tax included, in company currency).

    Confirming an RFQ puts it in "To Approve" and submits it; once fully
    approved the order is confirmed. A rejection sends it back to draft,
    and changing the lines or currency during approval restarts it.

    Payment comes before receipt (design doc 3.2 step 6, #41): once
    approved, a draft vendor bill for the ordered quantities is prepared
    and the CFO gets a to-do to pay it; when it is paid, the buyer gets a
    to-do to follow up the shipment and record the receipt.
    """
    _name = 'purchase.order'
    _inherit = ['purchase.order', 'aiis.approval.mixin']

    aiis_approved_terms = fields.Json(readonly=True, copy=False)

    is_fully_received = fields.Boolean(compute='_compute_is_fully_received')
    aiis_payment_status = fields.Selection([
        ('none', 'Not Billed'),
        ('to_pay', 'To Pay'),
        ('partial', 'Partially Paid'),
        ('paid', 'Paid'),
    ], string='Payment', compute='_compute_aiis_payment_status', store=True, compute_sudo=True)

    @api.depends('order_line.invoice_lines.move_id.payment_state', 'order_line.invoice_lines.move_id.state')
    def _compute_aiis_payment_status(self):
        for order in self:
            bills = order.order_line.invoice_lines.move_id.filtered(
                lambda m: m.move_type == 'in_invoice' and m.state != 'cancel' and m.payment_state != 'reversed')
            states = set(bills.filtered(lambda m: m.state == 'posted').mapped('payment_state'))
            if not bills:
                order.aiis_payment_status = 'none'
            elif states and states <= {'paid', 'in_payment'} and all(m.state == 'posted' for m in bills):
                order.aiis_payment_status = 'paid'
            elif states & {'paid', 'in_payment', 'partial'}:
                order.aiis_payment_status = 'partial'
            else:
                order.aiis_payment_status = 'to_pay'

    @api.depends('order_line.qty_received', 'order_line.product_qty')
    def _compute_is_fully_received(self):
        for order in self:
            order.is_fully_received = order._get_receipt_status() == 'received'

    def _get_receipt_status(self):
        self.ensure_one()
        lines = self.order_line.filtered(lambda l: not l.display_type)
        if lines and all(float_compare(l.qty_received, l.product_qty, precision_rounding=l.product_uom.rounding) >= 0
                         for l in lines):
            return 'received'
        if any(lines.mapped('qty_received')):
            return 'partial'
        return 'pending'

    # ------------------------------------------------------------------
    # Approval
    # ------------------------------------------------------------------

    def _approval_requester(self):
        return self.user_id.employee_id

    def _approval_amount(self):
        company = self.company_id or self.env.company
        if self.currency_id and self.currency_id != company.currency_id:
            return self.currency_id._convert(
                self.amount_total, company.currency_id, company, self.date_order or fields.Date.today())
        return self.amount_total

    def _approval_restart_fields(self):
        # the approvers saw these numbers
        return ['order_line', 'currency_id']

    def _approval_allowed(self):
        self.ensure_one()
        return self.aiis_approval_state == 'approved'

    def button_confirm(self):
        to_confirm = self.filtered(lambda o: o.state in ('draft', 'sent'))
        res = super().button_confirm()
        to_confirm.filtered(lambda o: o.state == 'to approve')._approval_submit()
        to_confirm.sudo().activity_feedback(['aiis_procurement_request.mail_activity_revise_order'])
        return res

    def _approval_on_approved(self):
        self.button_approve()
        self._seal_approved_terms()
        self._prepare_prepayment_bill()
        self.sudo().activity_feedback(['aiis_procurement_request.mail_activity_revise_order'])

    def action_create_invoice(self):
        """Bills come from the approval only (_prepare_prepayment_bill), for
        the full approved PO. Odoo's Create Bill would add a second bill for
        received minus billed quantities - open the existing bill instead."""
        bills = self.order_line.invoice_lines.move_id.filtered(
            lambda m: m.move_type == 'in_invoice' and m.state != 'cancel' and m.payment_state != 'reversed')
        missing = self.filtered(lambda o: not (o.order_line.invoice_lines.move_id & bills))
        if missing:
            raise UserError(_("The vendor bill is prepared automatically when the purchase order is fully "
                              "approved. %s has no active bill.", ', '.join(missing.mapped('name'))))
        return self.action_view_invoice(bills)

    def _prepare_prepayment_bill(self):
        """Draft vendor bill for the ordered quantities, for the CFO to pay"""
        cfo = self.env['aiis.approval.position']._require_finance_holder()
        for order in self.sudo().filtered(lambda o: o.state in ('purchase', 'done')):
            if order.order_line.invoice_lines.move_id.filtered(
                    lambda m: m.move_type == 'in_invoice' and m.state != 'cancel' and m.payment_state != 'reversed'):
                continue
            order = order.with_company(order.company_id)
            vals = order._prepare_invoice()
            vals['aiis_purchase_order_id'] = order.id
            sequence = 10
            for line in order.order_line.filtered(lambda l: not l.display_type and l.product_qty):
                line_vals = line._prepare_account_move_line()
                line_vals.update({'quantity': line.product_qty, 'sequence': sequence})
                vals['invoice_line_ids'].append((0, 0, line_vals))
                sequence += 1
            if not vals['invoice_line_ids']:
                continue
            bill = self.env['account.move'].with_context(default_move_type='in_invoice').with_company(
                order.company_id).create(vals)
            bill.with_context(mail_activity_quick_update=True).activity_schedule(
                'aiis_procurement_request.mail_activity_pay_bill', user_id=cfo.id,
                summary=_("Pay %s", order.name),
                note=_("Approved purchase order - check the vendor bill, confirm it and register the payment."))
            order.message_post(body=_("Vendor bill prepared for payment by the CFO (%s).",
                                      cfo.name))

    def _notify_paid(self):
        """The bill is paid: the buyer follows up the shipment"""
        for order in self.sudo().filtered(lambda o: o.aiis_payment_status == 'paid'):
            order.message_post(body=_("Paid - follow up the shipment and record the receipt."))
            order.order_line.invoice_lines.move_id.filtered(
                lambda m: m.move_type == 'in_invoice' and m.payment_state in ('paid', 'in_payment')
            ).activity_feedback(['aiis_procurement_request.mail_activity_pay_bill'])
            receipt_type = self.env.ref('aiis_procurement_request.mail_activity_receive_order')
            if (not order.is_fully_received and order.user_id
                    and not order.activity_ids.filtered(lambda a: a.activity_type_id == receipt_type)):
                order.with_context(mail_activity_quick_update=True).activity_schedule(
                    'aiis_procurement_request.mail_activity_receive_order', user_id=order.user_id.id,
                    summary=_("Track shipment of %s", order.name),
                    note=_("Paid - follow up the shipment and record the received quantities."))

    def _approval_on_rejected(self):
        # back to procurement as a draft; the requester learns why (#54)
        for order in self:
            line = order.approval_request_id.line_ids.filtered(lambda l: l.state == 'rejected')[-1:]
            requests = self.env['procurement.request'].sudo().search([('purchase_order_id', '=', order.id)])
            for request in requests:
                request.message_post(
                    body=Markup('%s<br/>%s') % (
                        _("Purchase order %(order)s was rejected by %(user)s (%(label)s) and is back with "
                          "procurement to be revised:", order=order.name, user=line.user_id.name,
                          label=line.node_label), line.comment or ''),
                    partner_ids=request.employee_id.user_id.partner_id.ids,
                    subtype_xmlid='mail.mt_comment')
        self.button_draft()

    def _approval_on_withdrawn(self):
        self.button_draft()

    @api.model_create_multi
    def create(self, vals_list):
        if any('aiis_approved_terms' in vals for vals in vals_list):
            raise UserError(_("Approved terms are maintained by the approval workflow."))
        return super().create(vals_list)

    def _commercial_terms(self):
        self.ensure_one()
        return {
            'company': self.company_id.id, 'supplier': self.partner_id.commercial_partner_id.id,
            'currency': self.currency_id.id, 'payment_term': self.payment_term_id.id,
            'total': self.currency_id.round(self.amount_total),
            'lines': [[line.id, line.product_id.id, line.product_uom.id, line.product_qty,
                       line.price_unit, line.discount if 'discount' in line._fields else 0,
                       sorted(line.taxes_id.ids)]
                      for line in self.order_line.filtered(lambda l: not l.display_type and l.product_qty).sorted('id')],
        }

    def _seal_approved_terms(self):
        for order in self:
            super(PurchaseOrder, order.sudo()).write({'aiis_approved_terms': order._commercial_terms()})

    def _return_for_commercial_revision(self, reason):
        for order in self:
            order.button_draft()
            order.message_post(body=_("Returned by Finance for commercial revision: %s", reason))
            activity_type = self.env.ref('aiis_procurement_request.mail_activity_revise_order')
            buyers = self.env.ref('aiis_procurement_request.group_procurement_buyer').sudo().users.filtered(
                lambda u: u.active and order.company_id in u.company_ids)
            assignees = (order.user_id & buyers) or buyers
            if not assignees:
                raise UserError(_("Configure a Procurement buyer before returning this purchase order."))
            order.activity_ids.filtered(lambda a: a.activity_type_id == activity_type).unlink()
            for buyer in assignees:
                order.with_context(mail_activity_quick_update=True).activity_schedule(
                    'aiis_procurement_request.mail_activity_revise_order', user_id=buyer.id,
                    summary=_("Revise purchase order: %s", order.name), note=reason)

    def write(self, vals):
        if 'aiis_approved_terms' in vals:
            raise UserError(_("Approved terms are maintained by the approval workflow."))
        from .account_move import _changed
        terms = {'partner_id', 'company_id', 'currency_id', 'payment_term_id'}
        for order in self.filtered(lambda o: o.aiis_approved_terms or o.state in ('purchase', 'done')):
            if _changed(order, vals, terms):
                raise UserError(_("Approved PO commercial terms are locked. Return it to draft and obtain approval again."))
        return super().write(vals)

    def button_draft(self):
        bills = self.sudo().order_line.invoice_lines.move_id.filtered(
            lambda m: m.move_type == 'in_invoice' and m.state != 'cancel')
        if bills.filtered(lambda m: m.state == 'posted' and m.payment_state != 'reversed'):
            raise UserError(_("This PO has posted bills. Complete the accounting reversal/correction before revising the PO."))
        drafts = bills.filtered(lambda m: m.state == 'draft')
        drafts.activity_unlink(['aiis_procurement_request.mail_activity_pay_bill'])
        drafts.button_cancel()
        super(PurchaseOrder, self.sudo()).write({'aiis_approved_terms': False})
        # Whatever gets confirmed next is approved from scratch
        self._approval_cancel(_("the order was reset to draft"))
        self.filtered(lambda o: o.aiis_approval_state != 'none').sudo().write({'aiis_approval_state': 'none'})
        return super().button_draft()

    def button_cancel(self):
        self._approval_cancel(_("the order was cancelled"))
        return super().button_cancel()

    # ------------------------------------------------------------------
    # Receipt - no Inventory app, so "received" is the manual Received
    # quantity on the PO lines, entered by Procurement
    # ------------------------------------------------------------------

    def action_mark_received(self):
        for order in self:
            if order.state not in ('purchase', 'done'):
                raise UserError(_("Only approved purchase orders can be received."))
            for line in order.order_line.filtered(lambda l: not l.display_type and l.qty_received_method == 'manual'):
                line.qty_received = line.product_qty
        return True

    def _close_procurement_requests(self):
        requests = self.env['procurement.request'].sudo().search([
            ('purchase_order_id', 'in', self.ids), ('state', '=', 'converted')])
        requests.filtered(lambda r: r.purchase_order_id.is_fully_received)._mark_done()


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    def _guard_commercial_terms(self, vals=None):
        from .account_move import _changed
        terms = {'product_id', 'product_uom', 'product_qty', 'price_unit', 'order_id', 'display_type', 'discount'}
        for line in self.filtered(lambda l: l.order_id.aiis_approved_terms or l.order_id.state in ('purchase', 'done')):
            if vals is None or _changed(line, vals, terms & set(line._fields)) or 'taxes_id' in vals:
                raise UserError(_("Approved PO lines are locked. Return the PO to draft and obtain approval again."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            order = self.env['purchase.order'].browse(vals.get('order_id'))
            if order and (order.aiis_approved_terms or order.state in ('purchase', 'done')):
                raise UserError(_("Cannot add lines to an approved PO. Return it to draft and obtain approval again."))
        lines = super().create(vals_list)
        lines._restart_pending_commercial_approval()
        return lines

    def _restart_pending_commercial_approval(self):
        for order in self.order_id.filtered(lambda o: o.aiis_approval_state == 'pending'):
            order._approval_cancel(_("commercial lines changed during approval"))
            order._approval_submit()

    def unlink(self):
        self._guard_commercial_terms()
        orders = self.order_id
        result = super().unlink()
        for order in orders.filtered(lambda o: o.aiis_approval_state == 'pending'):
            order._approval_cancel(_("commercial lines changed during approval"))
            order._approval_submit()
        return result

    def write(self, vals):
        self._guard_commercial_terms(vals)
        res = super().write(vals)
        if {'product_id', 'product_uom', 'product_qty', 'price_unit', 'discount', 'taxes_id'} & vals.keys():
            self._restart_pending_commercial_approval()
        if 'qty_received' in vals or 'qty_received_manual' in vals:
            self.order_id._close_procurement_requests()
            self.order_id.filtered('is_fully_received').sudo().activity_feedback(
                ['aiis_procurement_request.mail_activity_receive_order'])
        return res
