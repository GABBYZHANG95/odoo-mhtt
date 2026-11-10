from datetime import date

from odoo.exceptions import AccessError
from odoo.tests import Form, new_test_user, tagged

from .test_finance import FinanceCase


@tagged('post_install', '-at_install')
class TestFinanceRegressions(FinanceCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.u_cfo = new_test_user(cls.env, login='f_cfo_ops', groups=(
            'base.group_user,account.group_account_manager,'
            'aiis_finance.group_finance_board,aiis_finance.group_finance_bank_operator'))

    def test_cfo_reconciles_supplier_payment_without_sales_rights(self):
        self.assertFalse(self.u_cfo.has_group('sales_team.group_sale_salesman'))
        bill = self._purchase(self.software, 1200, department=self.d_sales, taxes=[]).with_user(self.u_cfo)
        bill.action_post()
        payment = self.env['account.payment.register'].with_user(self.u_cfo).with_context(
            active_model='account.move', active_ids=bill.ids).create({
                'payment_date': date(self.year, 2, 5)})._create_payments()
        self.assertEqual(payment.payment_type, 'outbound')
        self.assertEqual(payment.partner_type, 'supplier')
        self.assertEqual(payment.reconciled_bill_ids, bill)
        board = self.env['aiis.finance.board'].with_user(self.u_cfo)
        params = {'year': self.year, 'cutoff': '%s-12-31' % self.year}
        before = board.get_cash_flow(params)['columns'][0]
        st = self.env['account.bank.statement.line'].with_user(self.u_cfo).create({
            'journal_id': payment.journal_id.id, 'date': date(self.year, 2, 6),
            'amount': -1200, 'payment_ref': 'Regression supplier payment', 'partner_id': self.vendor.id})
        outstanding = payment.move_id.line_ids.filtered(lambda l: l.account_id.reconcile and l.credit)
        with Form(st, view='account_reconcile_oca.bank_statement_line_form_reconcile_view') as form:
            # OCA can already suggest this payment. Selecting an existing
            # suggestion again toggles it off, just as it does in the UI.
            suggested = {line_id for line in form.reconcile_data_info.get('data', [])
                         for line_id in line.get('counterpart_line_ids', [])}
            if outstanding.id not in suggested:
                form.add_account_move_line_id = outstanding
        st.reconcile_bank_line()
        self.assertTrue(st.is_reconciled)
        after = board.get_cash_flow(params)['columns'][0]
        self.assertAlmostEqual(after['opex'] - before['opex'], -1200)
        self.assertAlmostEqual(after['other'], before['other'])

    def test_bank_writes_are_not_granted_to_board_or_billing_admins(self):
        billing_admin = new_test_user(self.env, login='f_billing_only',
                                      groups='base.group_user,account.group_account_manager')
        for user in (self.u_board, billing_admin, self.u_staff):
            self.assertFalse(self.env['account.bank.statement.line'].with_user(user).check_access_rights(
                'create', raise_exception=False), user.login)

    def test_board_reads_sales_tags_but_cannot_edit_them(self):
        tag = self.env['crm.tag'].create({'name': 'Finance regression'})
        self.assertEqual(tag.with_user(self.u_board).read(['name'])[0]['name'], 'Finance regression')
        self.env['sale.order'].with_user(self.u_board).get_views([(False, 'tree'), (False, 'form')])
        with self.assertRaises(AccessError):
            tag.with_user(self.u_board).write({'name': 'Forbidden'})

    def test_asset_post_without_personal_mailbox_preserves_author(self):
        self.u_cfo.email = False
        self.company.email = 'accounting@example.invalid'
        bill = self._purchase(self.laptop, 6000, department=self.d_sales).with_user(self.u_cfo)
        bill.action_post()
        assets = self.env['account.asset'].search([('account_move_line_ids', 'in', bill.invoice_line_ids.ids)])
        self.assertEqual(len(assets), 1)
        self.assertEqual((assets.method_time, assets.method_number, assets.method_period), ('year', 3, 'month'))
        message = bill.message_ids.filtered(lambda m: 'created the asset' in str(m.body))
        self.assertEqual(message.author_id, self.u_cfo.partner_id)
        self.assertIn('accounting@example.invalid', message.email_from)
        self.assertFalse(self.u_cfo.email, 'Posting must not invent a personal mailbox')

    def test_finance_groups_follow_the_positions(self):
        """A production database gets its people after the install: the
        board and bank groups follow whoever holds CFO / CEO / Chairman."""
        Position = self.env['aiis.approval.position']
        board = self.ref('aiis_finance.group_finance_board')
        bank = self.ref('aiis_finance.group_finance_bank_operator')
        cfo = new_test_user(self.env, login='f_new_cfo', groups='base.group_user')
        ceo = new_test_user(self.env, login='f_new_ceo', groups='base.group_user')
        old_cfo = Position.search([('code', '=', 'cfo')]).user_id
        Position.search([('code', '=', 'cfo')]).user_id = cfo
        Position.search([('code', '=', 'ceo')]).user_id = ceo
        self.assertIn(cfo, board.users)
        self.assertIn(ceo, board.users)
        self.assertEqual(bank.users, cfo)
        self.assertTrue(cfo.has_group('account.group_account_user'))
        self.assertNotIn(self.u_board, board.users, 'only the position holders')
        if old_cfo:
            self.assertNotIn(old_cfo, bank.users)
        Position.search([('code', '=', 'ceo')]).user_id = False
        self.assertNotIn(ceo, board.users)
        self.assertFalse(ceo.has_group('account.group_account_readonly'), 'board rights go with the position')
        self.assertTrue(ceo.has_group('base.group_user'))
        # a new CFO: the old one loses bank and accounting rights, unless another group grants them
        manager = new_test_user(self.env, login='f_cfo_mgr', groups='base.group_user,account.group_account_manager')
        Position.search([('code', '=', 'cfo')]).user_id = manager
        self.assertFalse(cfo.has_group('account.group_account_user'))
        self.assertFalse(cfo.has_group('aiis_finance.group_finance_board'))
        Position.search([('code', '=', 'cfo')]).user_id = cfo
        self.assertFalse(manager.has_group('account.group_account_user'))
        self.assertTrue(manager.has_group('account.group_account_invoice'), 'still implied by billing administrator')

    def test_rd_department_setting_sets_its_function(self):
        department = self.env['hr.department'].create({'name': 'F Labs'})
        self.company.aiis_rd_department_id = department
        self.assertEqual(department.aiis_function, 'rd')
        self.d_sales.aiis_function = 'sales'
        self.company.aiis_rd_department_id = self.d_sales
        self.assertEqual(self.d_sales.aiis_function, 'sales', 'a function the CFO set is kept')
