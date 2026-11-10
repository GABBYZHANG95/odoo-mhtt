from datetime import date, datetime, timedelta

from odoo.exceptions import AccessError
from odoo.tests import new_test_user, tagged

from odoo.addons.aiis_project.tests.test_project import ProjectCase

PAGES = ('overview', 'expenses', 'procurement', 'approvals', 'projects', 'maintenance')


@tagged('post_install', '-at_install')
class TestDashboard(ProjectCase):
    """Operations dashboard (design doc 6.9, #66)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Board = cls.env['aiis.operations.dashboard']
        cls.group = cls.env.ref('aiis_dashboard.group_operations_dashboard')

    def _page(self, page, user=None, **params):
        return self.Board.with_user(user or self.u_ceo).get_page(page, params)

    def _card(self, data, title):
        return next(card for card in data['cards'] if card['title'] == title)

    # ------------------------------------------------------------------
    # Access
    # ------------------------------------------------------------------

    def test_only_ceo_and_chairman(self):
        self.assertEqual(self.group.users, self.u_ceo | self.u_chairman)
        self.assertTrue(self._page('overview', self.u_chairman)['cards'])
        for user in (self.u_cfo, self.u_sales, self.u_sales_head):
            with self.assertRaises(AccessError):
                self._page('overview', user)

    def test_group_follows_the_positions(self):
        other = new_test_user(self.env, login='d_new_ceo', groups='base.group_user')
        self.env.ref('aiis_approval.position_ceo').user_id = other
        self.assertEqual(self.group.users, other | self.u_chairman)
        with self.assertRaises(AccessError):
            self._page('overview', self.u_ceo)

    # ------------------------------------------------------------------
    # Period
    # ------------------------------------------------------------------

    def test_every_page_and_period(self):
        for page in PAGES:
            for period in ('day', 'month', 'quarter', 'year'):
                data = self._page(page, period=period)
                self.assertTrue(data['cards'], (page, period))
                self.assertEqual(data['period']['kind'], period)
                if period == 'day':
                    self.assertFalse([b for b in data['blocks'] if b['type'] == 'chart'], 'no trend for one day')

    def test_like_for_like_comparison(self):
        p = self.Board._period({'period': 'month', 'date': '2026-11-08'})
        today = p['today']
        if today >= date(2026, 11, 30):
            self.assertEqual((p['prev_start'], p['prev_upto']), (date(2026, 10, 1), date(2026, 10, 30)))
        # a past month compares whole months; the shorter month caps the span
        p = self.Board._period({'period': 'month', 'date': '2026-03-15'})
        if today > date(2026, 3, 31):
            self.assertEqual((p['start'], p['upto']), (date(2026, 3, 1), date(2026, 3, 31)))
            self.assertEqual((p['prev_start'], p['prev_upto']), (date(2026, 2, 1), date(2026, 2, 28)))
        p = self.Board._period({'period': 'month'})
        self.assertEqual(p['upto'], today)
        self.assertEqual((p['prev_upto'] - p['prev_start']).days, min((today - p['start']).days,
                                                                     (p['start'] - p['prev_start']).days - 1))
        self.assertFalse(p['info']['next_date'])
        self.assertEqual(len(p['buckets']), (p['end'] - p['start']).days + 1)
        self.assertTrue(all(b['future'] for b in p['buckets'] if b['key'] > today.isoformat()))

    def test_quarter_buckets_are_months(self):
        p = self.Board._period({'period': 'quarter', 'date': '2026-05-20'})
        self.assertEqual(p['start'], date(2026, 4, 1))
        self.assertEqual([b['label'] for b in p['buckets']], ['Apr', 'May', 'Jun'])

    # ------------------------------------------------------------------
    # Figures
    # ------------------------------------------------------------------

    def _approved_claim(self, amount, day):
        employee = self.env['hr.employee'].search([('user_id', '=', self.u_sales.id)])
        product = self.env['product.product'].create({'name': 'Dinner', 'can_be_expensed': True, 'type': 'service'})
        expense = self.env['hr.expense'].create({
            'name': 'Dinner', 'employee_id': employee.id, 'product_id': product.id,
            'total_amount_currency': amount, 'date': day})
        sheet = self.env['hr.expense.sheet'].create({
            'name': 'Claim', 'employee_id': employee.id, 'expense_line_ids': [(6, 0, expense.ids)]})
        sheet.write({'approval_state': 'approve'})
        self.assertEqual(sheet.state, 'approve')
        return sheet

    def _amount(self, card):
        return float(card['value'].split()[0].replace(',', ''))

    def test_employee_expenses_follow_the_expense_date(self):
        before = self._amount(self._card(self._page('overview', period='day'), 'Employee Expenses'))
        before_expenses = self._amount(self._card(self._page('expenses', period='day'), 'Employee Expenses'))
        awaiting_before = self._amount(self._card(self._page('expenses', period='day'), 'Awaiting Payment'))
        self._approved_claim(1234, self.today)
        self._approved_claim(500, self.today - timedelta(days=400))
        data = self._page('overview', period='day')
        # other expenses already dated today in the database stay in the total
        self.assertEqual(self._amount(self._card(data, 'Employee Expenses')) - before, 1234)
        expenses = self._page('expenses', period='day')
        self.assertEqual(self._amount(self._card(expenses, 'Employee Expenses')) - before_expenses, 1234)
        awaiting = self._card(expenses, 'Awaiting Payment')
        self.assertEqual(awaiting['tag'], 'now')
        # approved but not posted yet: all of it is still to pay
        self.assertEqual(self._amount(awaiting) - awaiting_before, 1734)

    def test_customer_decisions_and_win_rate(self):
        won = self._quoted()
        won.with_user(self.u_sales)._customer_accepted('PO-1')
        lost = self._quoted()
        lost.with_user(self.u_sales)._customer_rejected('close', 'Price')
        data = self._page('projects', period='day')
        self.assertEqual(self._card(data, 'Win Rate')['value'], '50%')
        decisions = next(b for b in data['blocks'] if b['title'] == 'Customer decisions')
        self.assertEqual(len(decisions['rows']), 2)
        # filtered by salesperson
        data = self._page('projects', period='day', salesperson_id=str(self.u_ceo.id))
        self.assertEqual(self._card(data, 'Win Rate')['value'], '—')

    def test_documents_open_only_when_readable(self):
        project = self._initiated()
        board = self.Board.with_user(self.u_chairman).sudo()
        self.assertTrue(board._ref(project)['open'])  # the Chairman sees all projects (#50)
        claim = self._approved_claim(10, self.today)
        self.assertFalse(board._ref(claim)['open'])  # not his expense report

    def test_stage_intervals_from_the_tracking(self):
        project = self._new_project()
        start = datetime(2026, 1, 1, 8, 0)
        project.create_date = start
        changes = [
            (start + timedelta(days=1), 'Draft', 'Initiation Approval'),
            (start + timedelta(days=3), 'Initiation Approval', 'Solution Design'),
            (start + timedelta(days=10), 'Costing Approval', 'Quotation Sent'),
            (start + timedelta(days=20), 'Quotation Sent', 'Contract: BOM Check'),  # an old label
            (start + timedelta(days=30), 'Contract Approval', 'Delivery'),
        ]
        intervals = self.Board._stage_intervals(project, changes)
        self.assertEqual([i[0] for i in intervals], ['initiation', 'solution', 'contract', 'delivery'])
        self.assertEqual((intervals[1][2] - intervals[1][1]).days, 17)
        history = {project.id: changes}
        self.assertEqual(len(self.Board._project_events(history, 'contract', date(2026, 1, 1), date(2026, 2, 28))), 1)
        self.assertEqual(len(self.Board._project_events(history, 'quoted', date(2026, 1, 1), date(2026, 2, 28))), 1)
        self.assertEqual(len(self.Board._project_events(history, 'initiated', date(2026, 1, 1), date(2026, 2, 28))), 1)
