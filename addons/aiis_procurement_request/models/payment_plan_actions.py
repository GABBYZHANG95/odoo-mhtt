from odoo import _, api, fields, models
from odoo.exceptions import UserError

_CORE_STAGE_RECONCILE = object()


class PurchaseTermsFeedback(models.TransientModel):
    _name = 'aiis.purchase.terms.feedback'
    _description = 'CFO Supplier Terms Feedback'

    order_id = fields.Many2one('purchase.order', required=True, readonly=True)
    decision = fields.Selection([('changes', 'Request Changes'), ('clarification', 'Return for Clarification')], default='changes', required=True)
    reason = fields.Text(required=True)
    proposal = fields.Text(string='Suggested Terms for Negotiation')

    def action_return(self):
        self.ensure_one()
        order = self.order_id
        order.action_request_supplier_changes()
        if not (self.reason or '').strip() or (self.decision == 'changes' and not (self.proposal or '').strip()):
            raise UserError(_('Give a reason and, when requesting changes, the proposed terms to negotiate.'))
        message = '%s\n%s' % (self.reason.strip(), self.proposal or '')
        order._approval_reject(message)
        order._plan_internal_write({'aiis_terms_review_state': self.decision, 'aiis_cfo_feedback': self.reason.strip(),
                                    'aiis_cfo_proposal': self.proposal or False})
        return {'type': 'ir.actions.act_window_close'}


class PurchaseAcceptance(models.TransientModel):
    _name = 'aiis.purchase.acceptance'
    _description = 'Purchase Receipt Acceptance'

    order_id = fields.Many2one('purchase.order', required=True, readonly=True)
    note = fields.Text(required=True)
    evidence_ids = fields.Many2many('ir.attachment', string='Acceptance Evidence', required=True)

    def action_accept(self):
        self.ensure_one()
        order = self.order_id
        order._lock_stage_payment()
        order.action_confirm_purchase_acceptance()
        if not (self.note or '').strip() or not self.evidence_ids:
            raise UserError(_('Record the acceptance result and attach supporting evidence.'))
        evidence = self.env['ir.attachment']
        for attachment in self.evidence_ids:
            attachment.check_access_rights('read')
            attachment.check_access_rule('read')
            evidence |= (attachment if attachment.res_model == 'purchase.order' and attachment.res_id == order.id
                         else attachment.copy({'res_model': 'purchase.order', 'res_id': order.id}))
        order._plan_internal_write({'aiis_acceptance_date': order._business_today(),
            'aiis_acceptance_user_id': self.env.uid, 'aiis_acceptance_note': self.note.strip(),
            'aiis_acceptance_evidence_ids': [(6, 0, evidence.ids)]})
        order.sudo().message_post(body=_('Acceptance confirmed by %(user)s: %(note)s', user=self.env.user.name, note=self.note))
        order._sync_payment_work()
        return {'type': 'ir.actions.act_window_close'}


class AccountMovePaymentPlan(models.Model):
    _inherit = 'account.move'

    aiis_payment_stage_ids = fields.One2many(related='aiis_purchase_order_id.aiis_payment_stage_ids', readonly=True)
    aiis_available_payment = fields.Monetary(string='Currently Payable', compute='_compute_invoice_payment_limit', compute_sudo=False)
    aiis_payment_managed = fields.Boolean(related='aiis_purchase_order_id.aiis_payment_managed', readonly=True)

    @api.depends('aiis_purchase_order_id', 'amount_residual', 'state',
                 'aiis_purchase_order_id.aiis_full_receipt_date', 'aiis_purchase_order_id.aiis_acceptance_date')
    def _compute_invoice_payment_limit(self):
        for bill in self:
            order = bill.aiis_purchase_order_id.sudo()
            bill.aiis_available_payment = order._payment_limit() if order and order.aiis_payment_managed else 0

    def _post(self, soft=True):
        result = super()._post(soft=soft)
        self._aiis_purchase_orders()._sync_payment_work()
        return result

    def action_register_payment(self):
        for order in self._aiis_purchase_orders().filtered('aiis_payment_managed'):
            if not order._payment_limit():
                raise UserError(_('No payment stage is currently payable. Complete its milestone and wait until the allowed payment date.'))
        result = super().action_register_payment()
        managed = self._aiis_purchase_orders().filtered('aiis_payment_managed')
        if len(managed) == 1:
            result['context'] = dict(result.get('context', {}), default_payment_date=managed._business_today())
        return result


class PaymentRegisterPlan(models.TransientModel):
    _inherit = 'account.payment.register'

    @api.depends('can_edit_wizard', 'source_amount', 'source_amount_currency', 'source_currency_id', 'company_id', 'currency_id', 'payment_date')
    def _compute_amount(self):
        super()._compute_amount()
        for wizard in self:
            bills = wizard.line_ids.move_id.filtered(lambda m: m.move_type == 'in_invoice' and m.aiis_payment_managed)
            if len(bills) == 1 and wizard.currency_id == bills.currency_id:
                wizard.amount = min(bills.amount_residual, bills.aiis_purchase_order_id._payment_limit())

    def _create_payments(self):
        bills = self.line_ids.move_id
        managed = bills.filtered(lambda m: m.move_type == 'in_invoice' and m.aiis_payment_managed)
        if managed:
            if len(bills) != 1 or len(managed) != 1 or not self.can_edit_wizard:
                raise UserError(_('Register staged purchase payments one bill at a time.'))
            bill = managed
            bill._check_aiis_purchase_terms()
            if self.currency_id != bill.currency_id:
                raise UserError(_('Use the bill currency when registering a staged purchase payment.'))
            order = bill.aiis_purchase_order_id.sudo()
            order._lock_stage_payment()
            stage, remaining = order._next_payment_stage()
            limit = order._payment_limit()
            if not limit or not stage or self.payment_date < stage.due_date:
                raise UserError(_('This payment stage is not yet payable. Check its milestone and allowed payment date.'))
            if self.amount <= 0 or bill.currency_id.compare_amounts(self.amount, limit) > 0:
                raise UserError(_('The payment exceeds the remaining approved amount for the current stage.'))
        result = super()._create_payments()
        managed._aiis_purchase_orders()._sync_payment_work()
        return result


class MoveLinePlanSettlement(models.Model):
    _inherit = 'account.move.line'

    def reconcile(self):
        if self.env.context.get('aiis_core_stage_reconcile') is _CORE_STAGE_RECONCILE:
            return super().reconcile()
        payable = self.filtered(lambda l: l.account_id.account_type == 'liability_payable')
        bills = payable.move_id.filtered(lambda m: m.move_type == 'in_invoice' and m.aiis_payment_managed)
        if bills:
            if len(bills) != 1:
                raise UserError(_('Reconcile staged purchase bills separately.'))
            bill = bills
            bill.aiis_purchase_order_id.sudo()._lock_stage_payment()
            bill._check_aiis_purchase_terms()
            counterparts = payable - payable.filtered(lambda l: l.move_id == bill)
            # Standard full credit-note reversal remains an accounting correction.
            refunds = counterparts.move_id
            is_reversal = bool(refunds) and all(m.move_type == 'in_refund' and m.reversed_entry_id == bill
                                               and m.currency_id == bill.currency_id
                                               and bill.currency_id.compare_amounts(abs(m.amount_total), bill.amount_total) == 0
                                               for m in refunds)
            if not is_reversal:
                if any(m.move_type == 'in_refund' or not (m.payment_id or m.statement_line_id) for m in refunds):
                    raise UserError(_('Staged purchase balances require a payment or bank transaction; use a full reversal for commercial corrections.'))
                if any(l.currency_id != bill.currency_id for l in counterparts):
                    raise UserError(_('Settle a staged purchase bill in its own currency.'))
                order = bill.aiis_purchase_order_id.sudo()
                stage, remaining = order._next_payment_stage()
                amount = min(bill.amount_residual, sum(max(0, l.amount_residual_currency) for l in counterparts))
                limit = order._payment_limit()
                if not limit or not stage or any(l.date < stage.due_date for l in counterparts):
                    raise UserError(_('The required payment milestone or allowed date has not been reached.'))
                if bill.currency_id.compare_amounts(amount, limit) > 0:
                    raise UserError(_('Reconciliation would exceed the approved payment stage.'))
        # Only the validated outer settlement may delegate generated FX/rounding
        # reconciliations to Odoo; a JSON context cannot forge this identity.
        result = super(MoveLinePlanSettlement, self.with_context(
            aiis_core_stage_reconcile=_CORE_STAGE_RECONCILE)).reconcile()
        bills._aiis_purchase_orders()._sync_payment_work()
        return result
