import base64
from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import new_test_user, tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestPaymentPlan(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.env.company.aiis_procurement_department_id = False
        cls.buyer = new_test_user(cls.env, login='plan_buyer', groups='base.group_user,purchase.group_purchase_user')
        cls.ceo = new_test_user(cls.env, login='plan_ceo', groups='base.group_user,aiis_procurement_request.group_po_approver_ceo')
        cls.chairman = new_test_user(cls.env, login='plan_chairman', groups='base.group_user,aiis_procurement_request.group_po_approver_chairman')
        cls.cfo = new_test_user(cls.env, login='plan_cfo', groups='base.group_user,account.group_account_manager,purchase.group_purchase_user')
        ceo_emp = cls.env['hr.employee'].create({'name': 'Plan CEO', 'user_id': cls.ceo.id})
        cls.env['hr.employee'].create({'name': 'Plan Buyer', 'user_id': cls.buyer.id, 'parent_id': ceo_emp.id})
        for code, user in [('ceo', cls.ceo), ('chairman', cls.chairman), ('cfo', cls.cfo)]:
            cls.env.ref('aiis_approval.position_' + code).user_id = user
        cls.vendor = cls.env['res.partner'].create({'name': 'Payment Plan Vendor'})
        cls.product = cls.env['product.product'].create({'name': 'Plan Service', 'type': 'service', 'purchase_ok': True})

    def _new(self, mode='staged', amount=1000, stages=None):
        vals = {'partner_id': self.vendor.id, 'aiis_settlement_mode': mode,
                'order_line': [(0, 0, {'product_id': self.product.id, 'product_qty': 1,
                                       'price_unit': amount, 'taxes_id': [(6, 0, [])]})]}
        if stages:
            vals['aiis_payment_stage_ids'] = [(0, 0, dict(s, sequence=(i + 1) * 10)) for i, s in enumerate(stages)]
        order = self.env['purchase.order'].with_user(self.buyer).create(vals)
        self._evidence(order)
        return order

    def _evidence(self, order, text='Supplier accepts 30% advance and 70% after delivery'):
        evidence = self.env['ir.attachment'].create({'name': 'Vendor agreement.txt', 'type': 'binary',
            'datas': base64.b64encode(text.encode()), 'res_model': 'purchase.order', 'res_id': order.id})
        order.write({'aiis_supplier_offer': text, 'aiis_supplier_evidence_ids': [(6, 0, evidence.ids)],
                     'aiis_supplier_confirmed': True})
        return evidence

    def _approve(self, order):
        order.button_confirm()
        order.with_user(self.cfo).action_confirm_supplier_terms()
        order.with_user(self.ceo).action_approval_approve()
        if order.amount_total > 300000:
            order.with_user(self.chairman).action_approval_approve()
        bill = order.order_line.invoice_lines.move_id.filtered(lambda b: b.state != 'cancel')
        bill = bill.with_user(self.cfo)
        bill.invoice_date = order._business_today()
        bill.action_post()
        return bill

    def _pay(self, bill, amount=None, date=None):
        vals = {} if amount is None else {'amount': amount}
        if date:
            vals['payment_date'] = date
        wizard = self.env['account.payment.register'].with_user(self.cfo).with_context(
            active_model='account.move', active_ids=bill.ids).create(vals)
        wizard._create_payments()
        return wizard

    def _todo(self, user, record, kind):
        return self.env['aiis.todo'].with_user(user).search([
            ('kind', '=', kind), ('res_model', '=', record._name), ('res_id', '=', record.id)])

    def test_cfo_before_ceo_and_audit(self):
        order = self._new()
        order.button_confirm()
        self.assertEqual(order.state, 'draft')
        self.assertEqual(order.approval_request_id.gate, 'payment_terms')
        self.assertEqual(len(self._todo(self.cfo, order, 'approval')), 1)
        self.assertFalse(self._todo(self.ceo, order, 'approval'))
        self.assertFalse(order.order_line.invoice_lines)
        with self.assertRaises(UserError), self.cr.savepoint():
            order.with_user(self.ceo).action_approval_approve()
        order.with_user(self.cfo).action_confirm_supplier_terms()
        self.assertEqual(order.aiis_terms_review_request_id.state, 'approved')
        self.assertEqual(order.aiis_terms_review_state, 'confirmed')
        self.assertEqual(order.state, 'to approve')
        self.assertEqual(len(self._todo(self.ceo, order, 'approval')), 1)
        order.with_user(self.ceo).action_approval_approve()
        self.assertEqual(order.state, 'purchase')
        self.assertTrue(order.order_line.invoice_lines)

    def test_changes_and_clarification_require_supplier_confirmation(self):
        order = self._new()
        order.button_confirm()
        original = order.aiis_supplier_offer
        wizard = self.env['aiis.purchase.terms.feedback'].with_user(self.cfo).create({
            'order_id': order.id, 'decision': 'changes', 'reason': 'Reduce cash exposure',
            'proposal': 'Negotiate 20% advance and 80% after receipt'})
        wizard.action_return()
        self.assertEqual(order.state, 'draft')
        self.assertEqual(order.aiis_supplier_offer, original)
        self.assertIn('20%', order.aiis_cfo_proposal)
        self.assertFalse(order.aiis_supplier_confirmed)
        self.assertEqual(len(self._todo(self.buyer, order, 'returned')), 1)
        with self.assertRaises(UserError), self.cr.savepoint():
            order.button_confirm()
        self._evidence(order, 'Supplier confirms revised agreement')
        order.button_confirm()
        self.env['aiis.purchase.terms.feedback'].with_user(self.cfo).create({
            'order_id': order.id, 'decision': 'clarification', 'reason': 'Attach delivery details',
        }).action_return()
        self.assertEqual(order.aiis_terms_review_state, 'clarification')

    def test_30_70_partial_payment_receipt_and_cap(self):
        order = self._new()
        bill = self._approve(order)
        self.assertEqual(order._payment_limit(), 300)
        self.assertEqual(self.env['account.payment.register'].with_user(self.cfo).with_context(
            active_model='account.move', active_ids=bill.ids).create({}).amount, 300)
        with self.assertRaises(UserError), self.cr.savepoint():
            self._pay(bill, 1000)
        with self.assertRaises(UserError), self.cr.savepoint():
            order.action_mark_received()
        self._pay(bill, 100)
        self.assertEqual(order._payment_limit(), 200)
        self.assertFalse(self._todo(self.buyer, order, 'receipt'))
        self._pay(bill, 200)
        self.assertEqual(order._payment_limit(), 0)
        self.assertFalse(self._todo(self.cfo, bill, 'bill'))
        self.assertEqual(len(self._todo(self.buyer, order, 'receipt')), 1)
        with self.assertRaises(UserError), self.cr.savepoint():
            self._pay(bill, 700)
        order.action_mark_received()
        self.assertEqual(order._payment_limit(), 700)
        self.assertEqual(len(self._todo(self.cfo, bill, 'bill')), 1)
        self._pay(bill)
        self.assertEqual(order.aiis_payment_status, 'paid')
        self.assertFalse(self._todo(self.cfo, bill, 'bill'))

    def test_acceptance_requires_receipt_evidence_and_buyer(self):
        order = self._new(stages=[
            {'name': 'Advance', 'percentage': 30, 'trigger': 'approval'},
            {'name': 'Acceptance balance', 'percentage': 70, 'trigger': 'acceptance'},
        ])
        bill = self._approve(order)
        self._pay(bill)
        with self.assertRaises(UserError), self.cr.savepoint():
            order.action_confirm_purchase_acceptance()
        order.action_mark_received()
        self.assertEqual(order._payment_limit(), 0)
        self.assertEqual(len(self._todo(self.buyer, order, 'purchase_acceptance')), 1)
        with self.assertRaises(UserError), self.cr.savepoint():
            order.with_user(self.cfo).action_confirm_purchase_acceptance()
        with self.assertRaises(UserError), self.cr.savepoint():
            order.with_user(self.cfo).write({'aiis_acceptance_date': order._business_today()})
        evidence = self._evidence_attachment(order)
        self.env['aiis.purchase.acceptance'].with_user(self.buyer).create({
            'order_id': order.id, 'note': 'Inspected and accepted', 'evidence_ids': [(6, 0, evidence.ids)],
        }).action_accept()
        self.assertEqual(order._payment_limit(), 700)
        self._pay(bill)
        self.assertEqual(order.aiis_payment_status, 'paid')

    def _evidence_attachment(self, order):
        return self.env['ir.attachment'].create({'name': 'Acceptance.txt', 'type': 'binary',
            'datas': base64.b64encode(b'Acceptance passed'), 'res_model': 'purchase.order', 'res_id': order.id})

    def test_credit_waits_for_full_receipt_and_date(self):
        order = self._new(mode='credit')
        bill = self._approve(order)
        self.assertEqual(order._payment_limit(), 0)
        self.assertEqual(len(self._todo(self.buyer, order, 'receipt')), 1)
        order.order_line.qty_received = 0.5
        self.assertEqual(order._payment_limit(), 0)
        order.action_mark_received()
        self.assertTrue(order.aiis_payment_stage_ids.due_date)
        self.assertEqual(order._payment_limit(), 0)
        self.assertFalse(self._todo(self.cfo, bill, 'bill'))
        with self.assertRaises(UserError), self.cr.savepoint():
            self._pay(bill)
        order._plan_internal_write({'aiis_full_receipt_date': order._business_today() - timedelta(days=30)})
        self.assertEqual(order._payment_limit(), 1000)
        self.assertEqual(len(self._todo(self.cfo, bill, 'bill')), 1, 'date-based inbox refresh needs no cron')
        self._pay(bill)
        self.assertEqual(order.aiis_payment_status, 'paid')

    def test_cfo_cannot_overwrite_supplier_terms_or_stage(self):
        order = self._new()
        with self.assertRaises(UserError), self.cr.savepoint():
            order.with_user(self.cfo).write({'aiis_supplier_offer': 'Unilateral new terms'})
        with self.assertRaises(UserError), self.cr.savepoint():
            order.aiis_payment_stage_ids[:1].with_user(self.cfo).write({'percentage': 10})
        with self.assertRaises(UserError), self.cr.savepoint():
            order.write({'aiis_terms_review_state': 'confirmed'})
        with self.assertRaises(UserError), self.cr.savepoint():
            order.write({'aiis_payment_managed': False})
        with self.assertRaises(UserError), self.cr.savepoint():
            order.aiis_payment_stage_ids[:1].write({'cumulative_amount': 100000})

    def test_changed_conditions_restart_cfo_and_chairman_still_required(self):
        order = self._new(amount=300001)
        order.button_confirm()
        order.with_user(self.cfo).action_confirm_supplier_terms()
        with self.assertRaises(UserError), self.cr.savepoint():
            order.order_line.price_unit = 400000
        order.action_approval_withdraw()
        order.order_line.price_unit = 400000
        self._evidence(order, 'Supplier reconfirms updated price')
        order.button_confirm()
        self.assertEqual(order.approval_request_id.gate, 'payment_terms')
        order.with_user(self.cfo).action_confirm_supplier_terms()
        order.with_user(self.ceo).action_approval_approve()
        self.assertEqual(order.state, 'to approve')
        self.assertFalse(order.order_line.invoice_lines)
        order.with_user(self.chairman).action_approval_approve()
        self.assertEqual(order.state, 'purchase')

    def test_direct_payment_reconciliation_cannot_skip_stage(self):
        order = self._new()
        bill = self._approve(order)
        payment = self.env['account.payment'].with_user(self.cfo).create({
            'payment_type': 'outbound', 'partner_type': 'supplier', 'partner_id': self.vendor.id,
            'amount': 1000, 'currency_id': bill.currency_id.id,
            'journal_id': self.env['account.journal'].search([('type', '=', 'bank'), ('company_id', '=', order.company_id.id)], limit=1).id,
        })
        payment.action_post()
        payable = bill.line_ids.filtered(lambda l: l.account_id.account_type == 'liability_payable')
        counterpart = payment.move_id.line_ids.filtered(lambda l: l.account_id == payable.account_id)
        with self.assertRaises(UserError), self.cr.savepoint():
            (payable | counterpart).reconcile()
        self.assertEqual(bill.amount_residual, 1000)

    def test_percentages_rounding_and_missing_supplier_evidence(self):
        order = self._new(amount=1000.01)
        self.assertEqual(sum(a for s, a, c in order._stage_amounts()), 1000.01)
        order.aiis_payment_stage_ids[:1].write({'percentage': 20})
        self._evidence(order)
        with self.assertRaises(UserError), self.cr.savepoint():
            order.button_confirm()
        order.aiis_payment_stage_ids[:1].write({'percentage': 30})
        order.write({'aiis_supplier_confirmed': True, 'aiis_supplier_evidence_ids': [(5, 0, 0)]})
        with self.assertRaises(UserError), self.cr.savepoint():
            order.button_confirm()

    def test_full_prepayment_and_receipt_role(self):
        order = self._new(mode='prepaid')
        bill = self._approve(order)
        self.assertEqual(order._payment_limit(), 1000)
        self._pay(bill)
        with self.assertRaises(UserError), self.cr.savepoint():
            order.with_user(self.cfo).action_mark_received()
        order.action_mark_received()
        self.assertTrue(order.is_fully_received)
        self.assertFalse(self._todo(self.buyer, order, 'receipt'))

    def test_batch_and_backdated_payments_rejected(self):
        first = self._new()
        bill = self._approve(first)
        other = self._new()
        second = self._approve(other)
        wizard = self.env['account.payment.register'].with_user(self.cfo).with_context(
            active_model='account.move', active_ids=(bill | second).ids).create({})
        with self.assertRaises(UserError), self.cr.savepoint():
            wizard._create_payments()
        with self.assertRaises(UserError), self.cr.savepoint():
            self._pay(bill, 300, date=first._business_today() - timedelta(days=1))

    def test_evidence_change_invalidates_pending_review_and_approved_payment(self):
        order = self._new()
        order.button_confirm()
        attachment = order.aiis_supplier_evidence_ids
        attachment.sudo().write({'datas': base64.b64encode(b'Altered vendor agreement')})
        with self.assertRaises(UserError), self.cr.savepoint():
            order.with_user(self.cfo).action_confirm_supplier_terms()
        order.action_approval_withdraw()
        self._evidence(order, 'Supplier reconfirmed agreement')
        bill = self._approve(order)
        order.aiis_supplier_evidence_ids.sudo().write({'datas': base64.b64encode(b'Changed after approval')})
        with self.assertRaises(UserError), self.cr.savepoint():
            self._pay(bill)

    def test_pending_line_create_unlink_and_writeoff_do_not_bypass(self):
        order = self._new()
        order.button_confirm()
        with self.assertRaises(UserError), self.cr.savepoint():
            self.env['purchase.order.line'].with_user(self.cfo).create({
                'order_id': order.id, 'product_id': self.product.id, 'product_qty': 1, 'price_unit': 10})
        with self.assertRaises(UserError), self.cr.savepoint():
            order.order_line.with_user(self.cfo).unlink()
        with self.assertRaises(UserError), self.cr.savepoint():
            order.order_line.qty_received = 1
        order.with_user(self.cfo).action_confirm_supplier_terms()
        order.with_user(self.ceo).action_approval_approve()
        bill = order.order_line.invoice_lines.move_id.with_user(self.cfo)
        bill.invoice_date = order._business_today()
        bill.action_post()
        account = self.env['account.account'].search([
            ('company_id', '=', order.company_id.id), ('account_type', '=', 'expense')], limit=1)
        wizard = self.env['account.payment.register'].with_user(self.cfo).with_context(
            active_model='account.move', active_ids=bill.ids).create({
                'amount': 300, 'payment_difference_handling': 'reconcile', 'writeoff_account_id': account.id})
        with self.assertRaises(UserError), self.cr.savepoint():
            wizard._create_payments()
        self.assertEqual(bill.amount_residual, 1000)

    def test_onchange_preview_does_not_change_persisted_plan(self):
        order = self._new()
        stage = order.aiis_payment_stage_ids.sorted('sequence')[0]
        preview = stage.new({'order_id': order.id, 'name': stage.name, 'sequence': stage.sequence,
            'percentage': 20, 'trigger': 'approval', 'days': 0}, origin=stage)
        preview._compute_amounts()
        self.assertEqual(preview.amount, 200)
        self.assertEqual(stage.percentage, 30)
        self.assertEqual(stage.amount, 300)
        self.assertTrue(order.aiis_supplier_confirmed)
        preview.write({'amount': 200, 'cumulative_amount': 200})
        self.assertEqual(stage.amount, 300)

    def test_invoice_form_reads_payment_limit_and_stage_progress(self):
        order = self._new()
        order.button_confirm()
        order.with_user(self.cfo).action_confirm_supplier_terms()
        order.with_user(self.ceo).action_approval_approve()
        bill = order.order_line.invoice_lines.move_id.with_user(self.cfo)
        specification = {'aiis_available_payment': {}, 'aiis_payment_managed': {},
            'amount_residual': {}, 'currency_id': {'fields': {'display_name': {}}},
            'aiis_payment_stage_ids': {'fields': {'name': {}, 'amount': {}, 'state': {}, 'due_date': {}}}}
        data = bill.web_read(specification)[0]
        self.assertEqual(data['aiis_available_payment'], 300)
        self.assertEqual(len(data['aiis_payment_stage_ids']), 2)
        bill.invoice_date = order._business_today()
        bill.action_post()
        self.assertEqual(bill.web_read(specification)[0]['aiis_available_payment'], 300)

    def test_missing_cfo_stops_submission(self):
        order = self._new()
        self.env.ref('aiis_approval.position_cfo').user_id = False
        with self.assertRaises(UserError), self.cr.savepoint():
            order.button_confirm()
        self.assertEqual(order.aiis_approval_state, 'none')

    def test_workflow_hint_follows_the_tasks(self):
        """#64: the header says who has to do what, from the To do tasks"""
        order = self._new(mode='prepaid')
        self.assertIn('records the supplier terms', str(order.workflow_hint))
        order.button_confirm()
        order.invalidate_recordset(['workflow_hint'])
        self.assertIn('Supplier terms review', str(order.workflow_hint))
        self.assertIn(self.cfo.name, str(order.workflow_hint))
        order.with_user(self.cfo).action_confirm_supplier_terms()
        order.invalidate_recordset(['workflow_hint'])
        self.assertIn(self.ceo.name, str(order.workflow_hint))
        order.with_user(self.ceo).action_approval_approve()
        order.invalidate_recordset(['workflow_hint'])
        hint = str(order.workflow_hint)
        self.assertIn('Confirm Bill and Pay', hint)
        self.assertIn(self.cfo.name, hint)
        bill = order.invoice_ids
        self.assertIn('Draft bill', str(bill.workflow_hint))

    def test_bill_hint_waits_for_receipt(self):
        """#64: pay after receipt + 30 days - the bill says who unlocks it"""
        order = self._new(mode='credit', stages=[{'name': 'Payment', 'percentage': 100, 'trigger': 'receipt', 'days': 30}])
        bill = self._approve(order)
        hint = str(bill.workflow_hint)
        self.assertIn('Not payable yet', hint)
        self.assertIn('Full Receipt', hint)
        self.assertIn('30 days', hint)
        order.order_line.with_user(self.buyer).write({'qty_received': 1})
        bill.invalidate_recordset(['workflow_hint'])
        self.assertIn('can be paid from', str(bill.workflow_hint))

    def test_create_bill_opens_the_approved_bill(self):
        """Odoo's Create Bill must not add a second (negative) bill next to
        the one prepared on approval - it opens that bill instead"""
        order = self._new(mode='prepaid')
        with self.assertRaises(UserError), self.cr.savepoint():
            order.action_create_invoice()
        order.button_confirm()
        order.with_user(self.cfo).action_confirm_supplier_terms()
        order.with_user(self.ceo).action_approval_approve()
        bill = order.invoice_ids
        self.assertEqual(len(bill), 1)
        for user in (self.ceo, self.cfo):
            action = order.with_user(user).action_create_invoice()
            self.assertEqual(action.get('res_id'), bill.id)
        self.assertEqual(order.invoice_ids, bill)

    def test_foreign_bill_currency_payment_keeps_stage_cap(self):
        currency = self.env.ref('base.USD')
        if currency == self.env.company.currency_id:
            currency = self.env.ref('base.EUR')
        currency.active = True
        today = fields.Date.today()
        for date, rate in [(today - timedelta(days=1), 0.25), (today, 0.2)]:
            existing = self.env['res.currency.rate'].search([
                ('currency_id', '=', currency.id), ('company_id', '=', self.env.company.id), ('name', '=', date)])
            if existing:
                existing.rate = rate
            else:
                self.env['res.currency.rate'].create({'currency_id': currency.id, 'company_id': self.env.company.id, 'name': date, 'rate': rate})
        order = self._new()
        order.write({'currency_id': currency.id})
        self._evidence(order, 'Supplier confirms USD settlement with 30% advance')
        order.button_confirm()
        order.with_user(self.cfo).action_confirm_supplier_terms()
        order.with_user(self.ceo).action_approval_approve()
        bill = order.order_line.invoice_lines.move_id.with_user(self.cfo)
        bill.write({'invoice_date': today - timedelta(days=1), 'date': today - timedelta(days=1)})
        bill.action_post()
        self._pay(bill)
        self.assertEqual(bill.amount_residual, 700)
        self.assertEqual(order._payment_limit(), 0)
        order.action_mark_received()
        self._pay(bill)
        self.assertEqual(bill.amount_residual, 0)
