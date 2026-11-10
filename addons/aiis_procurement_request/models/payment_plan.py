from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare

# Only server-side compatibility fixtures may create legacy orders.
_LEGACY_PAYMENT_FLOW = object()
PLAN_FIELDS = {'aiis_settlement_mode', 'aiis_payment_stage_ids', 'aiis_supplier_offer',
               'aiis_supplier_evidence_ids', 'aiis_supplier_confirmed', 'payment_term_id'}
INTERNAL_FIELDS = {'aiis_payment_managed', 'aiis_terms_review_state', 'aiis_terms_submitted',
                   'aiis_terms_confirmed', 'aiis_terms_review_request_id', 'aiis_cfo_proposal',
                   'aiis_cfo_feedback', 'aiis_final_approval_date', 'aiis_full_receipt_date',
                   'aiis_acceptance_date', 'aiis_acceptance_user_id', 'aiis_acceptance_note',
                   'aiis_acceptance_evidence_ids'}


class PurchaseOrderPaymentPlan(models.Model):
    _inherit = 'purchase.order'

    aiis_payment_managed = fields.Boolean(default=True, readonly=True, copy=False)
    aiis_settlement_mode = fields.Selection([
        ('prepaid', 'Full Prepayment'), ('credit', 'Pay after Receipt'), ('staged', 'Staged Payments'),
    ], default='prepaid', required=True, string='Settlement Method')
    aiis_supplier_offer = fields.Text(string='Supplier Confirmed Terms')
    aiis_supplier_evidence_ids = fields.Many2many('ir.attachment', 'aiis_po_supplier_evidence_rel',
                                                 string='Supplier Confirmation Evidence')
    aiis_supplier_confirmed = fields.Boolean(string='Supplier Has Agreed to These Terms', copy=False)
    aiis_payment_stage_ids = fields.One2many('aiis.purchase.payment.stage', 'order_id', string='Payment Plan', copy=True)
    aiis_terms_review_state = fields.Selection([
        ('draft', 'Not Submitted'), ('waiting', 'CFO Review'), ('confirmed', 'CFO Confirmed'),
        ('changes', 'Negotiate with Supplier'), ('clarification', 'Clarification Required'),
    ], default='draft', readonly=True, copy=False, string='Payment Terms Review')
    aiis_terms_submitted = fields.Json(readonly=True, copy=False)
    aiis_terms_confirmed = fields.Json(readonly=True, copy=False)
    aiis_terms_review_request_id = fields.Many2one('aiis.approval.request', readonly=True, copy=False)
    aiis_cfo_proposal = fields.Text(string='CFO Proposed Changes', readonly=True, copy=False)
    aiis_cfo_feedback = fields.Text(string='CFO Feedback', readonly=True, copy=False)
    aiis_final_approval_date = fields.Date(string='Final Approval Date', readonly=True, copy=False)
    aiis_full_receipt_date = fields.Date(string='Full Receipt Date', readonly=True, copy=False)
    aiis_acceptance_date = fields.Date(string='Acceptance Date', readonly=True, copy=False)
    aiis_acceptance_user_id = fields.Many2one('res.users', string='Accepted By', readonly=True, copy=False)
    aiis_acceptance_note = fields.Text(string='Acceptance Result', readonly=True, copy=False)
    aiis_acceptance_evidence_ids = fields.Many2many('ir.attachment', 'aiis_po_acceptance_evidence_rel', string='Acceptance Evidence', readonly=True, copy=False)
    aiis_can_review_terms = fields.Boolean(compute='_compute_payment_controls')
    aiis_can_receive = fields.Boolean(compute='_compute_payment_controls')
    aiis_can_accept = fields.Boolean(compute='_compute_payment_controls')
    aiis_available_payment = fields.Monetary(string='Currently Payable', compute='_compute_payment_controls')

    def _business_today(self):
        self.ensure_one()
        return fields.Date.context_today(self.with_context(tz=self.company_id.partner_id.tz or 'Asia/Riyadh'))

    def _plan_internal_write(self, vals):
        return super(PurchaseOrderPaymentPlan, self.sudo()).write(vals)

    def _assert_plan_editable(self):
        for order in self:
            if not self.env.su and (self.env.user == self.env['aiis.approval.position']._holder('cfo')
                                    or not self.env.user.has_group('aiis_procurement_request.group_procurement_buyer')):
                raise UserError(_('Only Procurement may maintain the supplier agreement and payment plan.'))
            if order.aiis_approved_terms or order.aiis_approval_state == 'pending':
                raise UserError(_('Withdraw or return the PO before changing supplier terms or the payment plan.'))

    @api.model
    def _default_payment_stages(self, mode):
        if mode == 'staged':
            return [(0, 0, {'sequence': 10, 'name': 'Advance', 'percentage': 30, 'trigger': 'approval'}),
                    (0, 0, {'sequence': 20, 'name': 'Balance after receipt', 'percentage': 70, 'trigger': 'receipt'})]
        return [(0, 0, {'sequence': 10, 'name': 'Payment', 'percentage': 100,
                        'trigger': 'receipt' if mode == 'credit' else 'approval',
                        'days': 30 if mode == 'credit' else 0})]

    @api.onchange('aiis_settlement_mode')
    def _onchange_settlement_mode(self):
        if self.aiis_payment_managed and not self.aiis_approved_terms and self.aiis_approval_state != 'pending':
            self.aiis_payment_stage_ids = [(5, 0, 0)] + self._default_payment_stages(self.aiis_settlement_mode)
            self.aiis_supplier_confirmed = False

    @api.model_create_multi
    def create(self, vals_list):
        compat = self.env.context.get('aiis_legacy_payment_flow') is _LEGACY_PAYMENT_FLOW
        for vals in vals_list:
            forbidden = INTERNAL_FIELDS & vals.keys()
            if forbidden - {'aiis_payment_managed'}:
                raise UserError(_('Review and milestone fields are maintained by the workflow.'))
            managed = vals.get('aiis_payment_managed', self.env.context.get('default_aiis_payment_managed', True))
            if not managed and not compat:
                raise UserError(_('New purchase orders must use the payment review workflow.'))
            if managed and not vals.get('aiis_payment_stage_ids'):
                vals['aiis_payment_stage_ids'] = self._default_payment_stages(vals.get('aiis_settlement_mode', 'prepaid'))
        return super().create(vals_list)

    def write(self, vals):
        if self and any(not isinstance(order.id, int) for order in self):
            return models.Model.write(self, vals)
        if INTERNAL_FIELDS & vals.keys():
            raise UserError(_('Review and milestone fields are maintained by the workflow.'))
        managed = self.filtered('aiis_payment_managed')
        commercial = PLAN_FIELDS | {'partner_id', 'currency_id', 'company_id'}
        if commercial & vals.keys() and managed:
            managed._assert_plan_editable()
            if 'aiis_supplier_confirmed' not in vals:
                vals = dict(vals, aiis_supplier_confirmed=False)
        if 'aiis_settlement_mode' in vals and 'aiis_payment_stage_ids' not in vals and managed:
            vals = dict(vals, aiis_payment_stage_ids=[(5, 0, 0)] + self._default_payment_stages(vals['aiis_settlement_mode']))
        result = super().write(vals)
        if managed and vals.get('aiis_supplier_confirmed'):
            managed._plan_internal_write({'aiis_supplier_confirmed': True})
        return result

    def _terms_fingerprint(self):
        self.ensure_one()
        return {
            'commercial': super(PurchaseOrderPaymentPlan, self)._commercial_terms(),
            'method': self.aiis_settlement_mode, 'supplier_terms': self.aiis_supplier_offer or '',
            'supplier_confirmed': self.aiis_supplier_confirmed,
            'evidence': [[a.id, a.checksum] for a in self.aiis_supplier_evidence_ids.sorted('id')],
            'stages': [[s.sequence, s.name, s.percentage, s.trigger, s.days]
                       for s in self.aiis_payment_stage_ids.sorted('sequence')],
        }

    def _commercial_terms(self):
        terms = super()._commercial_terms()
        if self.aiis_payment_managed:
            terms['settlement'] = self._terms_fingerprint()
        return terms

    def _validate_supplier_plan(self):
        self.ensure_one()
        if not self.aiis_supplier_confirmed or not (self.aiis_supplier_offer or '').strip() or not self.aiis_supplier_evidence_ids:
            raise UserError(_('Record the supplier agreement, attach confirmation evidence and confirm supplier acceptance before submitting.'))
        stages = self.aiis_payment_stage_ids.sorted('sequence')
        if not stages or float_compare(sum(stages.mapped('percentage')), 100, precision_digits=4):
            raise UserError(_('Payment stage percentages must total 100%.'))
        if len(set(stages.mapped('sequence'))) != len(stages):
            raise UserError(_('Each payment stage must have a different sequence.'))
        if any(amount < 0 or cumulative > self.amount_total + self.currency_id.rounding / 2
               for stage, amount, cumulative in self._stage_amounts()):
            raise UserError(_('Adjust the stage percentages so currency rounding does not exceed the total.'))
        ranks = {'approval': 0, 'receipt': 1, 'acceptance': 2}
        order = [ranks[s.trigger] for s in stages]
        if order != sorted(order):
            raise UserError(_('Payment milestones must follow approval, receipt, then acceptance.'))
        if self.aiis_settlement_mode == 'prepaid' and any(s.trigger != 'approval' for s in stages):
            raise UserError(_('Full prepayment stages must be triggered by PO approval.'))
        if self.aiis_settlement_mode == 'credit' and any(s.trigger == 'approval' for s in stages):
            raise UserError(_('Pay after receipt cannot contain an advance payment.'))

    def _approval_gate(self):
        if self.aiis_payment_managed and self.aiis_terms_review_state == 'waiting':
            return 'payment_terms'
        return super()._approval_gate()

    def _approval_submit(self):
        for order in self.filtered('aiis_payment_managed'):
            self.env['aiis.approval.position']._require_finance_holder()
            order._validate_supplier_plan()
            if order.aiis_terms_confirmed != order._terms_fingerprint():
                order._plan_internal_write({'aiis_terms_review_state': 'waiting',
                                             'aiis_terms_submitted': order._terms_fingerprint()})
        return super()._approval_submit()

    def button_confirm(self):
        managed = self.filtered('aiis_payment_managed')
        legacy = self - managed
        result = super(PurchaseOrderPaymentPlan, legacy).button_confirm() if legacy else True
        for order in managed:
            if order.aiis_approval_state == 'pending':
                raise UserError(_('This PO is already waiting for approval.'))
            if order.state not in ('draft', 'sent'):
                continue
            order._assert_plan_editable()
            order._plan_internal_write({'aiis_terms_confirmed': False})
            order._approval_submit()
            order.sudo().activity_feedback(['aiis_procurement_request.mail_activity_revise_order'])
        return result

    def _approval_approve(self, comment=False):
        for order in self.filtered('aiis_payment_managed'):
            if order._approval_gate() == 'payment_terms':
                if self.env.user != self.env['aiis.approval.position']._require_finance_holder():
                    raise UserError(_('Only the configured CFO can confirm supplier terms.'))
                if order.aiis_terms_submitted != order._terms_fingerprint():
                    raise UserError(_('The supplier agreement changed. Withdraw and submit the updated agreement again.'))
        return super()._approval_approve(comment=comment)

    def _approval_on_approved(self):
        for order in self:
            if order.aiis_payment_managed and order.approval_request_id.gate == 'payment_terms':
                order._plan_internal_write({'aiis_terms_review_state': 'confirmed',
                    'aiis_terms_confirmed': order._terms_fingerprint(),
                    'aiis_terms_review_request_id': order.approval_request_id.id, 'aiis_approval_state': 'none'})
                submitter = order.aiis_terms_review_request_id.submitter_id
                super(PurchaseOrderPaymentPlan, order.with_user(submitter).sudo()).button_confirm()
            else:
                if order.aiis_payment_managed:
                    if order.aiis_terms_confirmed != order._terms_fingerprint():
                        raise UserError(_('CFO must confirm the current agreement before final approval.'))
                    order._plan_internal_write({'aiis_final_approval_date': order._business_today()})
                super(PurchaseOrderPaymentPlan, order)._approval_on_approved()
                order._sync_payment_work()

    def button_draft(self):
        result = super().button_draft()
        compat = self.env.context.get('aiis_legacy_payment_flow') is _LEGACY_PAYMENT_FLOW
        for order in self:
            vals = {'aiis_terms_review_state': 'draft', 'aiis_terms_confirmed': False,
                    'aiis_supplier_confirmed': False, 'aiis_final_approval_date': False,
                    'aiis_acceptance_date': False, 'aiis_acceptance_user_id': False,
                    'aiis_acceptance_evidence_ids': [(5, 0, 0)], 'aiis_acceptance_note': False}
            if not compat and not order.aiis_payment_managed:
                vals['aiis_payment_managed'] = True
                if not order.aiis_payment_stage_ids:
                    vals['aiis_payment_stage_ids'] = order._default_payment_stages('prepaid')
            order._plan_internal_write(vals)
        return result

    def _approval_on_rejected(self):
        reviewing = self.filtered(lambda o: o.aiis_payment_managed and o.approval_request_id.gate == 'payment_terms')
        result = super()._approval_on_rejected()
        for order in reviewing:
            reason = order.approval_request_id.rejection_reason
            order._plan_internal_write({'aiis_terms_review_state': 'changes', 'aiis_cfo_feedback': reason})
        return result

    def action_confirm_supplier_terms(self):
        self.ensure_one()
        if self._approval_gate() != 'payment_terms':
            raise UserError(_('This PO is not waiting for CFO terms review.'))
        return self._approval_approve()

    def action_request_supplier_changes(self):
        self.ensure_one()
        self._approval_my_line()
        if self._approval_gate() != 'payment_terms' or self.env.user != self.env['aiis.approval.position']._require_finance_holder():
            raise UserError(_('Only the assigned CFO can request supplier changes.'))
        return {'type': 'ir.actions.act_window', 'res_model': 'aiis.purchase.terms.feedback',
                'name': _('Supplier Terms Feedback'), 'views': [(False, 'form')], 'view_mode': 'form',
                'target': 'new', 'context': {'default_order_id': self.id}}

    def _settlement_bills(self):
        return self.sudo().order_line.invoice_lines.move_id.filtered(
            lambda b: b.move_type == 'in_invoice' and b.state != 'cancel' and b.payment_state != 'reversed')

    def _settled_amount(self):
        self.ensure_one()
        return sum(b.amount_total - b.amount_residual for b in self._settlement_bills() if b.state == 'posted')

    def _stage_amounts(self, stages=None):
        self.ensure_one()
        result, allocated = [], 0
        stages = self.aiis_payment_stage_ids.sorted('sequence') if stages is None else sorted(stages, key=lambda s: s.sequence)
        for stage in stages:
            amount = (self.amount_total - allocated if stage == stages[-1]
                      else self.currency_id.round(self.amount_total * stage.percentage / 100))
            allocated += amount
            result.append((stage, amount, allocated))
        return result

    def _next_payment_stage(self):
        self.ensure_one()
        paid = self._settled_amount()
        for stage, amount, cumulative in self._stage_amounts():
            if self.currency_id.compare_amounts(cumulative, paid) > 0:
                return stage, max(0, cumulative - paid)
        return self.env['aiis.purchase.payment.stage'], 0

    def _lock_stage_payment(self):
        # A row-version change serializes competing payments/receipt changes.
        # Odoo retries a conflicting repeatable-read RPC against fresh balances.
        records = self.filtered('aiis_payment_managed').sorted('id')
        if records:
            self.env.cr.execute('UPDATE purchase_order SET write_date=write_date WHERE id IN %s', [tuple(records.ids)])
            self.env['account.move'].invalidate_model(['amount_residual', 'payment_state'])
        return True

    def _payment_limit(self):
        self.ensure_one()
        if not self.aiis_payment_managed:
            return sum(b.amount_residual for b in self._settlement_bills())
        if self.state not in ('purchase', 'done') or self.aiis_approval_state != 'approved':
            return 0
        stage, remaining = self._next_payment_stage()
        if stage and stage.trigger == 'acceptance' and not self.aiis_acceptance_evidence_ids:
            return 0
        return remaining if stage and stage.due_date and stage.due_date <= self._business_today() else 0

    def _prepayment_complete(self):
        self.ensure_one()
        required = sum(amount for stage, amount, cumulative in self._stage_amounts() if stage.trigger == 'approval')
        return self.currency_id.compare_amounts(self._settled_amount(), required) >= 0

    @api.depends_context('uid')
    @api.depends('aiis_payment_managed', 'aiis_terms_review_state', 'approval_request_id.line_ids.state',
                 'order_line.invoice_lines.move_id.amount_residual', 'aiis_full_receipt_date', 'aiis_acceptance_date')
    def _compute_payment_controls(self):
        for order in self:
            order.aiis_can_review_terms = order.aiis_payment_managed and order._approval_gate() == 'payment_terms' and order.approval_can_act
            buyer = (self.env.user.has_group('aiis_procurement_request.group_procurement_buyer')
                     and self.env.user != self.env['aiis.approval.position']._holder('cfo'))
            order.aiis_can_receive = not order.aiis_payment_managed or (
                buyer and order.state in ('purchase', 'done') and order.aiis_approval_state == 'approved'
                and order._prepayment_complete())
            order.aiis_can_accept = (buyer and order.aiis_payment_managed and order.state in ('purchase', 'done')
                                    and order.aiis_approval_state == 'approved' and order.is_fully_received
                                    and not order.aiis_acceptance_date
                                    and any(s.trigger == 'acceptance' for s in order.aiis_payment_stage_ids))
            order.aiis_available_payment = order._payment_limit() if order.aiis_payment_managed else 0

    def _sync_payment_work(self):
        for order in self.sudo().filtered(lambda o: o.aiis_payment_managed and o.state in ('purchase', 'done')):
            received = order.is_fully_received
            if bool(order.aiis_full_receipt_date) != bool(received):
                order._plan_internal_write({'aiis_full_receipt_date': order._business_today() if received else False,
                                            'aiis_acceptance_date': False, 'aiis_acceptance_user_id': False})
            stage, remaining = order._next_payment_stage()
            bills = order._settlement_bills()
            closed = order.order_line.invoice_lines.move_id.filtered(
                lambda b: b.move_type == 'in_invoice' and (b.state == 'cancel' or b.payment_state == 'reversed'))
            closed.activity_unlink(['aiis_procurement_request.mail_activity_pay_bill'])
            cfo = self.env['aiis.approval.position']._require_finance_holder()
            pay_type = self.env.ref('aiis_procurement_request.mail_activity_pay_bill')
            for bill in bills:
                tasks = bill.activity_ids.filtered(lambda a: a.activity_type_id == pay_type)
                if bill.state == 'draft' or (stage and stage.due_date):
                    vals = {'user_id': cfo.id, 'date_deadline': stage.due_date if stage and stage.due_date else order._business_today(),
                            'summary': _('Review bill: %s', order.name) if bill.state == 'draft' else _('Pay stage: %s', stage.name),
                            'note': _('Payment is limited to the approved stage. Review the payment plan before registering payment.')}
                    if tasks:
                        tasks.with_context(mail_activity_quick_update=True).write(vals)
                    else:
                        bill.with_context(mail_activity_quick_update=True).activity_schedule(
                            'aiis_procurement_request.mail_activity_pay_bill', **vals)
                else:
                    tasks.unlink()
            buyer = order.user_id
            for xmlid, needed, summary in [
                ('mail_activity_receive_order', not received and order._prepayment_complete(), _('Track shipment and receive %s', order.name)),
                ('mail_activity_accept_order', received and not order.aiis_acceptance_date
                 and any(s.trigger == 'acceptance' for s in order.aiis_payment_stage_ids), _('Confirm acceptance of %s', order.name)),
            ]:
                activity_type = self.env.ref('aiis_procurement_request.' + xmlid)
                tasks = order.activity_ids.filtered(lambda a: a.activity_type_id == activity_type)
                if needed and buyer:
                    if not tasks:
                        order.with_context(mail_activity_quick_update=True).activity_schedule(
                            'aiis_procurement_request.' + xmlid, user_id=buyer.id, summary=summary)
                elif tasks:
                    tasks.action_feedback()

    def _prepare_prepayment_bill(self):
        result = super()._prepare_prepayment_bill()
        self._sync_payment_work()
        return result

    def _notify_paid(self):
        legacy = self.filtered(lambda o: not o.aiis_payment_managed)
        if legacy:
            super(PurchaseOrderPaymentPlan, legacy)._notify_paid()
        self._sync_payment_work()

    def action_confirm_purchase_acceptance(self):
        self.ensure_one()
        if (self.env.user == self.env['aiis.approval.position']._holder('cfo')
                or not self.env.user.has_group('aiis_procurement_request.group_procurement_buyer')):
            raise UserError(_('Procurement must confirm receipt and acceptance.'))
        if not self.aiis_can_accept:
            raise UserError(_('Complete full receipt before confirming the required acceptance.'))
        return {'type': 'ir.actions.act_window', 'res_model': 'aiis.purchase.acceptance', 'view_mode': 'form',
                'views': [(False, 'form')], 'target': 'new', 'name': _('Confirm Purchase Acceptance'),
                'context': {'default_order_id': self.id}}


class PurchasePaymentStage(models.Model):
    _name = 'aiis.purchase.payment.stage'
    _description = 'Purchase Payment Stage'
    _order = 'sequence, id'

    order_id = fields.Many2one('purchase.order', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10, required=True)
    name = fields.Char(string='Stage', required=True)
    percentage = fields.Float(string='Percentage', required=True, digits=(12, 4))
    trigger = fields.Selection([('approval', 'Final PO Approval'), ('receipt', 'Full Receipt'), ('acceptance', 'Acceptance Passed')], string='Milestone', required=True, default='approval')
    days = fields.Integer(string='Days after Milestone', default=0)
    currency_id = fields.Many2one(related='order_id.currency_id')
    amount = fields.Monetary(compute='_compute_amounts', store=True)
    cumulative_amount = fields.Monetary(compute='_compute_amounts', store=True)
    due_date = fields.Date(string='Payment Allowed From', compute='_compute_due_date', store=True)
    state = fields.Selection([('blocked', 'Waiting for Milestone'), ('scheduled', 'Scheduled'), ('ready', 'Payable'), ('paid', 'Settled')], string='Status', compute='_compute_state')

    @api.constrains('percentage', 'days')
    def _validate_values(self):
        for stage in self:
            if stage.percentage <= 0 or stage.percentage > 100 or stage.days < 0:
                raise UserError(_('Stage percentage must be greater than 0 and at most 100; days cannot be negative.'))

    @api.depends('order_id.amount_total', 'order_id.aiis_payment_stage_ids.percentage', 'order_id.aiis_payment_stage_ids.sequence')
    def _compute_amounts(self):
        for stage in self:
            order = stage.order_id
            stages = list(order.aiis_payment_stage_ids)
            if not isinstance(stage.id, int):
                stages = [s for s in stages if s.id != stage.id
                          and not (stage._origin.id and s._origin.id == stage._origin.id)] + [stage]
            allocation = next(((amount, cumulative) for candidate, amount, cumulative in order._stage_amounts(stages)
                               if candidate == stage), (0, 0))
            stage.amount, stage.cumulative_amount = allocation

    @api.depends('trigger', 'days', 'order_id.aiis_final_approval_date', 'order_id.aiis_full_receipt_date', 'order_id.aiis_acceptance_date')
    def _compute_due_date(self):
        for stage in self:
            date = {'approval': stage.order_id.aiis_final_approval_date, 'receipt': stage.order_id.aiis_full_receipt_date,
                    'acceptance': stage.order_id.aiis_acceptance_date}.get(stage.trigger)
            stage.due_date = date + timedelta(days=stage.days) if date else False

    def _compute_state(self):
        for stage in self:
            next_stage, remaining = stage.order_id._next_payment_stage()
            if not stage.order_id.aiis_final_approval_date:
                stage.state = 'blocked'
            elif stage.order_id.currency_id.compare_amounts(stage.cumulative_amount, stage.order_id._settled_amount()) <= 0:
                stage.state = 'paid'
            elif not stage.due_date or next_stage != stage:
                stage.state = 'blocked'
            elif stage.due_date > stage.order_id._business_today():
                stage.state = 'scheduled'
            else:
                stage.state = 'ready'

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if {'amount', 'cumulative_amount', 'due_date', 'state'} & vals.keys():
                raise UserError(_('Payment progress is maintained by the workflow.'))
            self.env['purchase.order'].browse(vals.get('order_id'))._assert_plan_editable()
        return super().create(vals_list)

    def write(self, vals):
        if self and any(not isinstance(stage.id, int) for stage in self):
            return models.Model.write(self, vals)
        if {'amount', 'cumulative_amount', 'due_date', 'state'} & vals.keys():
            raise UserError(_('Payment progress is maintained by the workflow.'))
        self.order_id._assert_plan_editable()
        if vals.get('order_id'):
            self.env['purchase.order'].browse(vals['order_id'])._assert_plan_editable()
        result = super().write(vals)
        self.order_id._plan_internal_write({'aiis_supplier_confirmed': False})
        return result

    def unlink(self):
        self.order_id._assert_plan_editable()
        return super().unlink()


class PurchaseOrderLinePaymentPlan(models.Model):
    _inherit = 'purchase.order.line'

    @api.model_create_multi
    def create(self, vals_list):
        orders = self.env['purchase.order'].browse([v['order_id'] for v in vals_list if v.get('order_id')])
        orders.filtered('aiis_payment_managed')._assert_plan_editable()
        return super().create(vals_list)

    def unlink(self):
        self.order_id.filtered('aiis_payment_managed')._assert_plan_editable()
        return super().unlink()

    def write(self, vals):
        if self and any(not isinstance(line.id, int) for line in self):
            return models.Model.write(self, vals)
        managed = self.order_id.filtered('aiis_payment_managed')
        if vals.get('order_id'):
            self.env['purchase.order'].browse(vals['order_id']).filtered('aiis_payment_managed')._assert_plan_editable()
        if {'product_id', 'product_qty', 'product_uom', 'price_unit', 'discount', 'taxes_id'} & vals.keys():
            managed._assert_plan_editable()
        if {'qty_received', 'qty_received_manual'} & vals.keys() and managed:
            managed._lock_stage_payment()
            if any(order.state not in ('purchase', 'done') or order.aiis_approval_state != 'approved' for order in managed):
                raise UserError(_('Complete PO approval before recording receipt.'))
            if not self.env.su and (self.env.user == self.env['aiis.approval.position']._holder('cfo')
                                    or not self.env.user.has_group('aiis_procurement_request.group_procurement_buyer')):
                raise UserError(_('Only Procurement may record receipt.'))
            if any(not order._prepayment_complete() for order in managed):
                raise UserError(_('The approved advance payment must be settled before recording receipt.'))
        result = super().write(vals)
        if {'product_id', 'product_qty', 'product_uom', 'price_unit', 'discount', 'taxes_id'} & vals.keys():
            managed._plan_internal_write({'aiis_supplier_confirmed': False})
        managed._sync_payment_work()
        return result
