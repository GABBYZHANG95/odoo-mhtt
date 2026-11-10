from datetime import date

from odoo.exceptions import UserError, ValidationError
from odoo.tests import Form, new_test_user, tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestSalesExpense(TransactionCase):
    """Sales trips, entertainment and gifts (design doc 5, #44)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.env = env

        def user(login, groups='base.group_user'):
            return new_test_user(env, login=login, groups=groups)

        cls.u_chairman = user('s_chairman')
        cls.u_ceo = user('s_ceo')
        cls.u_cfo = user('s_cfo', 'base.group_user,aiis_sales_expense.group_sales_budget_manager')
        cls.u_head = user('s_head')
        cls.u_sales = user('s_sales')
        cls.u_other = user('s_other')

        Employee = env['hr.employee']
        chairman = Employee.create({'name': 'Chairman', 'user_id': cls.u_chairman.id})
        ceo = Employee.create({'name': 'CEO', 'user_id': cls.u_ceo.id, 'parent_id': chairman.id})
        Employee.create({'name': 'CFO', 'user_id': cls.u_cfo.id, 'parent_id': ceo.id})
        head = Employee.create({'name': 'Sales Head', 'user_id': cls.u_head.id, 'parent_id': ceo.id})
        cls.department = env['hr.department'].create({'name': 'S Sales', 'manager_id': head.id})
        head.department_id = cls.department
        cls.e_sales = Employee.create({'name': 'Salesperson', 'user_id': cls.u_sales.id, 'parent_id': head.id,
                                       'department_id': cls.department.id, 'work_email': 's@example.com'})
        Employee.create({'name': 'Other', 'user_id': cls.u_other.id, 'parent_id': head.id,
                         'department_id': cls.department.id})
        env['hr.department'].create({'name': 'S CEO', 'manager_id': ceo.id})
        env['hr.department'].create({'name': 'S Chairman', 'manager_id': chairman.id})
        env.ref('aiis_approval.position_ceo').user_id = cls.u_ceo
        env.ref('aiis_approval.position_chairman').user_id = cls.u_chairman
        env.ref('aiis_approval.position_cfo').user_id = cls.u_cfo

        cls.customer = env['res.partner'].create({'name': 'Customer Co'})
        cls.year = date.today().year
        env['aiis.sales.budget'].create({
            'department_id': cls.department.id, 'date_from': date(cls.year, 1, 1),
            'date_to': date(cls.year, 12, 31), 'amount': 10000,
        })
        cls.product = env['product.product'].create({'name': 'Client dinner', 'can_be_expensed': True, 'type': 'service'})

    def _request(self, amount, kind='entertainment', user=None, **extra):
        user = user or self.u_sales
        vals = {'expense_type': kind, 'partner_id': self.customer.id, 'purpose': 'Dinner with the CIO',
                'amount': amount, 'date_from': date(self.year, 6, 1)}
        vals.update(extra)
        request = self.env['aiis.sales.expense.request'].with_user(user).create(vals)
        request.action_submit()
        return request

    def _approve_all(self, record):
        while record.aiis_approval_state == 'pending':
            approver = record.approval_request_id.line_ids.filtered(lambda l: l.state == 'pending')[:1].user_id
            record.with_user(approver).action_approval_approve()

    def _reimbursement(self, request, amount):
        expense = self.env['hr.expense'].with_user(self.u_sales).create({
            'name': 'Dinner', 'employee_id': self.e_sales.id, 'product_id': self.product.id,
            'total_amount_currency': amount,
        })
        sheet = self.env['hr.expense.sheet'].with_user(self.u_sales).create({
            'name': 'Dinner claim', 'employee_id': self.e_sales.id, 'expense_line_ids': [(6, 0, expense.ids)],
            'aiis_sales_request_id': request.id,
        })
        sheet.action_submit_sheet()
        return sheet

    def test_request_chain_up_to_ceo(self):
        request = self._request(500)
        approvers = request.approval_request_id.line_ids.sorted('step').user_id
        self.assertEqual(approvers, self.u_head | self.u_ceo)
        self._approve_all(request)
        self.assertEqual(request.state, 'approved')

    def test_trip_needs_destination_and_dates(self):
        with self.assertRaises(ValidationError):
            self._request(2000, kind='trip')
        request = self._request(2000, kind='trip', destination='Jeddah', date_to=date(self.year, 6, 3))
        self.assertEqual(request.state, 'submitted')

    def test_budget_box_for_approvers_only(self):
        first = self._request(3000)
        self._approve_all(first)
        request = self._request(8000)
        box = request.with_user(self.u_head)
        self.assertTrue(box.show_budget)
        self.assertEqual((box.budget_amount, box.budget_used, box.budget_this), (10000, 3000, 8000))
        self.assertEqual(box.budget_remaining, -1000)
        self.assertTrue(box.budget_over)
        self.assertTrue(request.with_user(self.u_cfo).show_budget)
        self.assertFalse(request.with_user(self.u_sales).show_budget)

    def test_reimbursement_ceo_then_cfo_replaces_estimate(self):
        request = self._request(3000)
        self._approve_all(request)
        sheet = self._reimbursement(request, 2500)
        self.assertEqual(sheet.state, 'submit')
        approvers = sheet.approval_request_id.line_ids.sorted('step').user_id
        self.assertEqual(approvers, self.u_head | self.u_ceo | self.u_cfo)
        with self.assertRaises(UserError):
            sheet.with_user(self.u_head).action_approve_expense_sheets()
        self._approve_all(sheet)
        self.assertEqual(sheet.state, 'approve')
        self.assertTrue(sheet.activity_ids.filtered(lambda a: a.user_id == self.u_cfo))
        # the budget now counts what was reimbursed, not the estimate
        self.assertEqual(request.budget_consumption, 2500)
        other = self._request(100)
        self.assertEqual(other.with_user(self.u_cfo).budget_used, 2500)

    def test_cfo_rejects_back_to_employee(self):
        request = self._request(800)
        self._approve_all(request)
        sheet = self._reimbursement(request, 800)
        sheet.with_user(self.u_head).action_approval_approve()
        sheet.with_user(self.u_ceo).action_approval_approve()
        sheet.with_user(self.u_cfo)._approval_reject('Receipt is not readable')
        self.assertEqual(sheet.state, 'draft')

    def test_request_of_another_employee_cannot_be_claimed(self):
        request = self._request(500, user=self.u_other)
        self._approve_all(request)
        with self.assertRaises(ValidationError):
            self.env['hr.expense.sheet'].create({
                'name': 'Wrong', 'employee_id': self.e_sales.id, 'aiis_sales_request_id': request.id})

    def test_visibility(self):
        request = self._request(500)
        Request = self.env['aiis.sales.expense.request']
        self.assertTrue(Request.with_user(self.u_head).search([('id', '=', request.id)]))
        self.assertTrue(Request.with_user(self.u_cfo).search([('id', '=', request.id)]))
        self.assertFalse(Request.with_user(self.u_other).search([('id', '=', request.id)]))

    def test_todo_expense_post_and_pay(self):
        self.u_cfo.groups_id = [
            (4, self.env.ref('account.group_account_manager').id),
            (4, self.env.ref('hr_expense.group_hr_expense_manager').id)]
        request = self._request(500)
        self._approve_all(request)
        sheet = self._reimbursement(request, 500)
        self._approve_all(sheet)
        Todo = self.env['aiis.todo'].with_user(self.u_cfo)
        domain = [('kind', '=', 'expense'), ('res_id', '=', sheet.id)]
        self.assertEqual(len(Todo.search(domain)), 1)
        self.assertEqual(Todo.search(domain).action_open_document()['res_id'], sheet.id)
        sheet.with_user(self.u_cfo).action_sheet_move_create()
        self.assertEqual(len(Todo.search(domain)), 1)
        self.env['account.payment.register'].with_user(self.u_cfo).with_context(
            active_model='account.move', active_ids=sheet.account_move_ids.ids).create({})._create_payments()
        self.assertFalse(Todo.search(domain))
        self.assertEqual(request.claim_stage, 'paid')

    def test_finance_expense_requires_cfo_and_excludes_ceo(self):
        request = self._request(500)
        self._approve_all(request)
        sheet = self._reimbursement(request, 500)
        sheet.with_user(self.u_head).action_approval_approve()
        sheet.with_user(self.u_ceo).action_approval_approve()
        position = self.env.ref('aiis_approval.position_cfo')
        position.user_id = False
        with self.assertRaisesRegex(UserError, 'Configure an active internal user as CFO'), self.cr.savepoint():
            sheet.with_user(self.u_cfo).action_approval_approve()
        self.assertEqual(sheet.state, 'submit')
        position.user_id = self.u_cfo
        sheet.with_user(self.u_cfo).action_approval_approve()
        activity_type = self.env.ref('aiis_sales_expense.mail_activity_pay_expense')
        activities = sheet.sudo().activity_ids.filtered(lambda a: a.activity_type_id == activity_type)
        self.assertEqual(activities.user_id, self.u_cfo)
        activities.write({'user_id': self.u_ceo.id})
        domain = [('kind', '=', 'expense'), ('res_id', '=', sheet.id)]
        self.assertFalse(self.env['aiis.todo'].with_user(self.u_ceo).search(domain))
        position._sync_finance_tasks()
        self.assertEqual(activities.user_id, self.u_cfo)
        self.assertEqual(len(self.env['aiis.todo'].with_user(self.u_cfo).search(domain)), 1)

    # ------------------------------------------------------------------
    # Claim on the request (#65)
    # ------------------------------------------------------------------

    def _add_lines(self, request, amounts, user=None):
        request.with_user(user or self.u_sales).write({'claim_line_ids': [
            (0, 0, {'name': 'Dinner %s' % i, 'product_id': self.product.id, 'total_amount_currency': amount})
            for i, amount in enumerate(amounts)]})

    def _add_receipt(self, request, user=None):
        user = user or self.u_sales
        receipt = self.env['ir.attachment'].with_user(user).create({
            'name': 'receipt.pdf', 'raw': ('receipt %s' % request.id).encode(), 'res_model': request._name, 'res_id': request.id})
        request.with_user(user).write({'claim_receipt_ids': [(4, receipt.id)]})

    def test_claim_on_request(self):
        request = self._request(3000)
        self.assertEqual(request.claim_stage, 'submitted')
        self._approve_all(request)
        self.assertEqual(request.claim_stage, 'approved')
        self.assertTrue(request.with_user(self.u_sales).claim_editable)
        self.assertEqual(request.claim_default_product_id, self.env.ref('hr_expense.expense_product_meal'))
        self._add_lines(request, [1200, 1500])
        self._add_receipt(request)
        self.assertEqual(request.claim_total, 2700)
        self.assertFalse(request.claim_over)
        request.with_user(self.u_sales).action_submit_claim()

        sheet = request.claim_sheet_id
        self.assertEqual(sheet.aiis_sales_request_id, request)
        self.assertEqual(sheet.employee_id, self.e_sales)
        self.assertEqual(sheet.expense_line_ids, request.claim_line_ids)
        self.assertEqual(sheet.state, 'submit')
        self.assertTrue(sheet.attachment_ids)
        self.assertEqual(sheet.approval_request_id.line_ids.sorted('step').user_id,
                         self.u_head | self.u_ceo | self.u_cfo)
        self.assertEqual(request.claim_stage, 'claimed')
        self.assertEqual(request.claim_pending_names, self.u_head.name)
        self.assertTrue(sheet.aiis_on_engine)
        sheet.with_user(self.u_head).action_approval_approve()
        self.assertEqual(sheet.approval_pending_names, self.u_ceo.name)
        self.assertEqual(request.claim_pending_names, self.u_ceo.name)
        self.assertIn('Reimbursement', request.claim_approval_html)
        # claimed once: no more changes, no second report
        self.assertFalse(request.with_user(self.u_sales).claim_editable)
        with self.assertRaises(UserError):
            self._add_lines(request, [100])
        with self.assertRaises(UserError):
            request.with_user(self.u_sales).action_submit_claim()
        with self.assertRaises(ValidationError):
            self.env['hr.expense.sheet'].with_user(self.u_sales).create({
                'name': 'Second', 'employee_id': self.e_sales.id, 'aiis_sales_request_id': request.id})
        self._approve_all(sheet)
        self.assertEqual(request.amount_reimbursed, 2700)

    def test_claim_over_estimate_is_flagged_not_blocked(self):
        request = self._request(1000)
        self._approve_all(request)
        self._add_lines(request, [1500])
        self._add_receipt(request)
        self.assertTrue(request.claim_over)
        request.with_user(self.u_sales).action_submit_claim()
        self.assertEqual(request.claim_sheet_id.state, 'submit')
        self.assertTrue(request.claim_sheet_id.aiis_claim_over)
        self.assertEqual(request.claim_sheet_id.aiis_claim_estimate, 1000)
        self.assertTrue(request.claim_over)
        # a note for the approvers only: gone once they have decided
        self._approve_all(request.claim_sheet_id)
        self.assertFalse(request.claim_sheet_id.aiis_claim_over)
        self.assertFalse(request.claim_over)

    def test_claim_needs_approval_lines_and_receipts(self):
        request = self._request(500)
        with self.assertRaises(UserError):
            self._add_lines(request, [100])
        self._approve_all(request)
        with self.assertRaisesRegex(UserError, 'actual expenses'):
            request.with_user(self.u_sales).action_submit_claim()
        self._add_lines(request, [400])
        with self.assertRaisesRegex(UserError, 'receipts'):
            request.with_user(self.u_sales).action_submit_claim()

    def test_claim_returned_and_resubmitted(self):
        request = self._request(800)
        self._approve_all(request)
        self._add_lines(request, [800])
        self._add_receipt(request)
        request.with_user(self.u_sales).action_submit_claim()
        sheet = request.claim_sheet_id
        sheet.with_user(self.u_head)._approval_reject('Receipt is not readable')
        self.assertEqual(sheet.state, 'draft')
        self.assertEqual(request.claim_stage, 'approved')
        self.assertIn('Receipt is not readable', request.claim_returned_reason)
        self.assertTrue(request.with_user(self.u_sales).claim_editable)
        line = request.claim_line_ids
        request.with_user(self.u_sales).write({'claim_line_ids': [(1, line.id, {'total_amount_currency': 750})]})
        self._add_receipt(request)
        request.with_user(self.u_sales).action_submit_claim()
        self.assertEqual(request.claim_sheet_id, sheet)
        self.assertEqual(sheet.state, 'submit')
        self.assertEqual(sheet.total_amount, 750)
        self.assertFalse(request.claim_returned_reason)

    def test_department_head_claims_for_staff(self):
        request = self._request(600)
        self._approve_all(request)
        self.assertTrue(request.with_user(self.u_head).claim_editable)
        self._add_lines(request, [600], user=self.u_head)
        self._add_receipt(request, user=self.u_head)
        self.assertEqual(request.claim_line_ids.employee_id, self.e_sales)
        # the CEO approved the request and reads the lines, but does not claim
        self.assertEqual(request.with_user(self.u_ceo).claim_line_ids.mapped('total_amount'), [600])
        self.assertFalse(request.with_user(self.u_ceo).claim_editable)
        with self.assertRaises(UserError):
            self._add_lines(request, [10], user=self.u_ceo)
        request.with_user(self.u_head).action_submit_claim()
        self.assertEqual(request.claim_sheet_id.employee_id, self.e_sales)
        self.assertEqual(request.claim_sheet_id.state, 'submit')

    def test_report_made_in_expenses_counts_as_the_claim(self):
        request = self._request(3000)
        self._approve_all(request)
        sheet = self._reimbursement(request, 2500)
        self.assertEqual(request.claim_sheet_id, sheet)
        self.assertEqual(request.claim_line_ids, sheet.expense_line_ids)
        self.assertEqual(request.claim_stage, 'claimed')

    def test_claim_lines_entered_in_the_form(self):
        """The Actual Expenses lines are entered in the request form."""
        request = self._request(3000)
        self._approve_all(request)
        with Form(request.with_user(self.u_sales)) as form:
            with form.claim_line_ids.new() as line:
                line.name = 'Dinner night 1'
                self.assertEqual(line.product_id, self.env.ref('hr_expense.expense_product_meal'))
                line.product_id = self.product
                line.total_amount_currency = 600
        self.assertEqual(request.claim_line_ids.total_amount, 600)
        self.assertEqual(request.claim_line_ids.employee_id, self.e_sales)
