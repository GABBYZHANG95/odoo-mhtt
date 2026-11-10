from datetime import date, timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import Form, new_test_user, tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class FinanceCase(TransactionCase):
    """Booking rules, personnel costs and the finance board (design doc 6.3-6.8)"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.env = env
        cls.company = env.company
        cls.ref = env.ref

        # Paying and posting purchase bills needs a CFO (finance tasks go to
        # them); a fresh database has nobody in the position yet. Set before
        # the board user below: holder changes resync the finance groups.
        cfo_position = env['aiis.approval.position'].search([('code', '=', 'cfo')], limit=1)
        if not cfo_position.user_id:
            cfo_position.user_id = new_test_user(env, login='f_cfo_holder', groups=(
                'base.group_user,account.group_account_manager'))
        cls.u_board = new_test_user(env, login='f_board', groups='base.group_user,aiis_finance.group_finance_board')
        cls.u_hr = new_test_user(env, login='f_hr', groups='base.group_user,hr.group_hr_user')
        cls.u_staff = new_test_user(env, login='f_staff', groups='base.group_user')

        Department = env['hr.department']
        cls.d_admin = Department.create({'name': 'F Finance'})
        cls.d_rd = Department.create({'name': 'F R&D', 'aiis_function': 'rd'})
        cls.d_rd_team = Department.create({'name': 'F R&D Team', 'parent_id': cls.d_rd.id})
        cls.d_sales = Department.create({'name': 'F Sales', 'aiis_function': 'sales'})
        Employee = env['hr.employee']
        cls.e_admin = Employee.create({'name': 'Accountant', 'department_id': cls.d_admin.id})
        cls.e_rd = Employee.create({'name': 'Developer', 'department_id': cls.d_rd_team.id, 'user_id': cls.u_staff.id})
        cls.e_sales = Employee.create({'name': 'Seller', 'department_id': cls.d_sales.id,
                                       'work_contact_id': env['res.partner'].create({'name': 'Seller'}).id})

        cls.vendor = env['res.partner'].create({'name': 'F Vendor'})
        cls.customer = env['res.partner'].create({'name': 'F Customer', 'aiis_customer_code': 'fcu'})
        cls.software = env['product.product'].create({
            'name': 'IDE licence', 'type': 'service', 'categ_id': cls.ref('aiis_finance.categ_nature_software').id})
        cls.laptop = env['product.product'].create({
            'name': 'Laptop', 'type': 'consu',
            'categ_id': cls.ref('aiis_procurement_request.product_category_internal_it_equipment').id})
        cls.hardware = env['product.product'].create({
            'name': 'Switch', 'type': 'consu', 'sale_ok': True,
            'categ_id': cls.ref('aiis_procurement_request.product_category_project_hardware').id})
        cls.maintenance = env['product.product'].create({
            'name': 'Annual maintenance', 'type': 'service', 'sale_ok': True,
            'categ_id': cls.ref('aiis_finance.categ_maintenance_services').id})
        cls.year = fields.Date.today().year
        cls.pricelist = env['product.pricelist'].create({'name': 'F SAR', 'currency_id': cls.company.currency_id.id})

    def account(self, name):
        return self.ref('aiis_finance.account_%s' % name)

    def _purchase(self, product, price, department=None, project=None, qty=1, taxes=None):
        """A purchase order raised from a purchase request, and its bill"""
        order = self.env['purchase.order'].create({
            'partner_id': self.vendor.id,
            'order_line': [(0, 0, {'product_id': product.id, 'product_qty': qty, 'price_unit': price})],
        })
        if department or project:
            employee = (department and self.env['hr.employee'].search(
                [('department_id', '=', department.id)], limit=1)) or self.e_admin
            self.env['procurement.request'].sudo().create({
                'employee_id': employee.id,
                'department_id': department.id if department else False,
                'reason': 'test',
                'purchase_order_id': order.id,
                'aiis_project_id': project.id if project else False,
                'line_ids': [(0, 0, {'description': product.name, 'quantity': qty})],
            })
        # A bill needs a fully approved PO (#57); the approval flow itself is
        # tested in aiis_procurement_request, here the PO is simply marked approved.
        if taxes is not None:
            order.order_line.taxes_id = [(6, 0, taxes)]
        order.sudo().write({'state': 'purchase', 'aiis_approval_state': 'approved'})
        order._plan_internal_write({'aiis_final_approval_date': date(self.year, 1, 10)})
        order._seal_approved_terms()
        line = order.order_line
        bill = self.env['account.move'].create({
            'move_type': 'in_invoice',
            'partner_id': self.vendor.id,
            'invoice_date': date(self.year, 1, 15),
            'invoice_line_ids': [(0, 0, dict(line._prepare_account_move_line(), quantity=qty))],
        })
        return bill

    def _project(self):
        project = self.env['aiis.project'].sudo().create({
            'name': 'F Project', 'partner_id': self.customer.id, 'salesperson_id': self.env.user.id})
        project.analytic_account_id = self.env['account.analytic.account'].create({
            'name': 'F Project', 'plan_id': self.ref('aiis_project.analytic_plan_projects').id})
        return project

    def _entry(self, day, lines):
        """Posted journal entry: [(account, debit, credit, extra)]"""
        move = self.env['account.move'].create({
            'move_type': 'entry',
            'date': day,
            'journal_id': self.env['account.journal'].search(
                [('type', '=', 'general'), ('company_id', '=', self.company.id)], limit=1).id,
            'line_ids': [(0, 0, dict({'account_id': account.id, 'debit': debit, 'credit': credit, 'name': '/'}, **extra))
                         for account, debit, credit, extra in lines],
        })
        move.action_post()
        return move


class TestBooking(FinanceCase):

    def test_report_lines(self):
        Account = self.env['account.account']
        expense = Account.create({'code': '9900001', 'name': 'X expense', 'account_type': 'expense'})
        self.assertEqual(expense.aiis_report_line, 'ga_other')
        bank = Account.create({'code': '9900002', 'name': 'X bank', 'account_type': 'asset_current'})
        self.assertEqual(bank.aiis_report_line, 'other_receivable')
        with self.assertRaises(ValidationError):
            expense.aiis_report_line = 'bank'
        self.assertEqual(self.account('rd_software').aiis_report_line, 'rd_software')

    def test_internal_purchase_by_department_function(self):
        cases = [(self.d_rd_team, 'rd_software'), (self.d_sales, 'sales_crm'), (self.d_admin, 'ga_it')]
        for department, account in cases:
            bill = self._purchase(self.software, 1200, department=department)
            line = bill.invoice_line_ids
            self.assertEqual(line.account_id, self.account(account), department.name)
            self.assertIn(str(department.aiis_analytic_account_id.id), line.analytic_distribution)

    def test_equipment_threshold(self):
        cheap = self._purchase(self.laptop, 4999, department=self.d_rd)
        self.assertEqual(cheap.invoice_line_ids.account_id, self.account('ga_other'),
                         'equipment below the threshold is G&A whatever the department')
        dear = self._purchase(self.laptop, 6000, department=self.d_rd, qty=2)
        line = dear.invoice_line_ids
        self.assertEqual(line.account_id, self.account('asset_computers'))
        self.assertEqual(line.asset_profile_id, self.ref('aiis_finance.asset_profile_computers'))
        dear.action_post()
        assets = self.env['account.asset'].search([('account_move_line_ids', 'in', line.ids)])
        self.assertEqual(len(assets), 2, 'one asset per unit')
        self.assertEqual(assets[0].method_number, 3)

    def test_project_purchase_is_cost_of_revenue(self):
        project = self._project()
        bill = self._purchase(self.software, 1000, project=project)
        self.assertEqual(bill.invoice_line_ids.account_id, self.account('project_cost'))

    def test_expenses(self):
        product = self.ref('hr_expense.expense_product_travel_accommodation')
        # a plain trip of an R&D engineer: R&D other
        expense = self.env['hr.expense'].create({
            'name': 'Conference', 'employee_id': self.e_rd.id, 'product_id': product.id,
            'total_amount_currency': 800})
        self.assertEqual(expense.account_id, self.account('rd_other'))
        # a sales trip: Sales travel and entertainment
        request = self.env['aiis.sales.expense.request'].create({
            'employee_id': self.e_sales.id, 'expense_type': 'entertainment', 'partner_id': self.customer.id,
            'purpose': 'Dinner', 'amount': 500})
        request.sudo().state = 'approved'
        sales_expense = self.env['hr.expense'].create({
            'name': 'Dinner', 'employee_id': self.e_sales.id,
            'product_id': self.ref('hr_expense.expense_product_meal').id, 'total_amount_currency': 450})
        self.env['hr.expense.sheet'].create({
            'name': 'Dinner', 'employee_id': self.e_sales.id, 'aiis_sales_request_id': request.id,
            'expense_line_ids': [(6, 0, sales_expense.ids)]})
        self.assertEqual(sales_expense.account_id, self.account('sales_travel'))
        vals = expense._prepare_move_lines_vals()
        self.assertIn(str(self.d_rd_team.aiis_analytic_account_id.id), vals['analytic_distribution'])

    def test_department_analytic_and_function(self):
        self.assertEqual(self.d_rd_team._aiis_get_function(), 'rd', 'inherited from the parent')
        self.assertEqual(self.d_admin._aiis_get_function(), 'ga')
        self.assertEqual(self.d_rd.aiis_analytic_account_id.plan_id, self.ref('aiis_finance.analytic_plan_departments'))
        self.d_rd.name = 'F Research'
        self.assertEqual(self.d_rd.aiis_analytic_account_id.name, 'F Research')

    def test_maintenance_revenue_is_spread(self):
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': self.customer.id, 'invoice_date': date(self.year, 1, 1),
            'invoice_line_ids': [(0, 0, {'product_id': self.maintenance.id, 'quantity': 1, 'price_unit': 12000,
                                         'tax_ids': [(5, 0, 0)]})],
        })
        self.assertEqual(invoice.invoice_line_ids.account_id, self.account('maintenance_revenue'))
        invoice.action_post()
        line = invoice.invoice_line_ids
        self.assertEqual(line.account_id, self.account('deferred_maintenance'))
        self.assertEqual(line.spread_id.period_number, 12)


class TestPersonnelCost(FinanceCase):

    def test_post_and_cancel(self):
        Cost = self.env['aiis.personnel.cost'].with_user(self.u_hr)
        cost = Cost.create({'employee_id': self.e_rd.id, 'type_id': self.ref('aiis_finance.personnel_type_iqama').id,
                            'amount': 650, 'date': date(self.year, 2, 10),
                            'partner_id': self.vendor.id})
        cost.action_post()
        debit = cost.move_id.line_ids.filtered('debit')
        self.assertEqual(debit.account_id, self.account('rd_personnel'))
        self.assertIn(str(self.d_rd_team.aiis_analytic_account_id.id), debit.analytic_distribution)
        credit = cost.move_id.line_ids.filtered('credit')
        self.assertEqual((credit.account_id, credit.partner_id), (self.account('payroll_payable'), self.vendor))
        with self.assertRaises(UserError):
            cost.amount = 700
        cost.action_cancel()
        self.assertEqual((cost.state, cost.move_id.state), ('cancel', 'cancel'))

    def test_employees_cannot_see(self):
        with self.assertRaises(AccessError):
            self.env['aiis.personnel.cost'].with_user(self.u_staff).search([])

    def test_monthly_salary(self):
        contract = self.env['hr.contract'].create({
            'name': 'Seller contract', 'employee_id': self.e_sales.id, 'wage': 9000,
            'date_start': date(self.year, 1, 1), 'state': 'open'})
        move = contract._aiis_post_payroll_month(date(self.year, 1, 1))
        cost = self.env['aiis.personnel.cost'].search([('contract_id', '=', contract.id)])
        self.assertEqual((cost.amount, cost.state, cost.date), (9000, 'posted', date(self.year, 1, 31)))
        self.assertEqual(move.line_ids.filtered('debit').account_id, self.account('sales_personnel'))
        self.assertEqual(contract._aiis_post_payroll_month(date(self.year, 1, 1)), move, 'once a month')


class TestFinanceBoard(FinanceCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.board = cls.env['aiis.finance.board'].with_user(cls.u_board)
        cls.params = {'year': cls.year, 'cutoff': fields.Date.to_string(date(cls.year, 12, 31))}

    def _order_and_invoice(self, amount, invoiced):
        order = self.env['sale.order'].create({
            'partner_id': self.customer.id,
            'pricelist_id': self.pricelist.id,
            'date_order': fields.Datetime.to_datetime(date(self.year, 1, 10)),
            'order_line': [(0, 0, {'product_id': self.hardware.id, 'product_uom_qty': 1, 'price_unit': amount,
                                   'tax_id': [(5, 0, 0)]})],
        })
        # confirmed (the customer-contract gate is tested in its own module)
        order.write({'state': 'sale', 'date_order': fields.Datetime.to_datetime(date(self.year, 1, 10))})
        invoice = order._create_invoices()
        invoice.invoice_line_ids.price_unit = invoiced
        invoice.invoice_date = date(self.year, 1, 20)
        invoice.action_post()
        return order, invoice

    def _mis_values(self, report):
        wizard = self.env['aiis.finance.report.wizard'].with_user(self.u_board).create({
            'report': report, 'fiscal_year': self.year, 'cutoff_date': date(self.year, 12, 31)})
        action = wizard.action_open()
        instance = self.env['mis.report.instance'].browse(action['res_id'])
        matrix = instance._compute_matrix()
        return {row.kpi.name: [cell.val for cell in row.iter_cells()]
                for row in matrix.iter_rows() if not row.account_id}

    def test_overview(self):
        order, invoice = self._order_and_invoice(10000, 4000)
        self._entry(date(self.year, 1, 25), [
            (self.account('project_cost'), 1500, 0, {}),
            (self.ref('aiis_finance.account_accrued'), 0, 1500, {}),
        ])
        data = self.board.get_overview(dict(self.params, period='q1'))
        kpis = data['kpis']
        self.assertEqual(kpis['order_value'], 10000)
        self.assertEqual(kpis['revenue'], 4000)
        self.assertEqual(kpis['gross_profit'], 2500)
        self.assertAlmostEqual(kpis['gross_margin'], 62.5)
        self.assertEqual(kpis['backlog'], 6000)
        self.assertEqual(data['quarters'][0]['revenue'], 4000)
        action = self.board.open_drill('revenue', dict(self.params, period='q1'))
        self.assertEqual(self.env['account.move.line'].search(action['domain']), invoice.invoice_line_ids)

    def test_future_quarters_are_empty(self):
        data = self.board.get_overview(dict(self.params, cutoff=fields.Date.to_string(date(self.year, 4, 30))))
        self.assertEqual([q['future'] for q in data['quarters']], [False, False, True, True])

    def test_board_only(self):
        with self.assertRaises(AccessError):
            self.env['aiis.finance.board'].with_user(self.u_staff).get_overview(self.params)

    def test_profit_loss_and_balance_sheet(self):
        before_pl = self._mis_values('pl')
        before_bs = self._mis_values('bs')
        self._order_and_invoice(5000, 5000)
        self._entry(date(self.year, 2, 1), [
            (self.account('project_cost'), 2000, 0, {}),
            (self.account('rd_software'), 300, 0, {}),
            (self.account('zakat'), 100, 0, {}),
            (self.ref('aiis_finance.account_accrued'), 0, 2400, {}),
        ])
        pl = self._mis_values('pl')
        q1, full = 0, 4
        self.assertEqual(pl['revenue'][q1] - (before_pl['revenue'][q1] or 0), 5000)
        self.assertEqual(pl['gross_profit'][full] - (before_pl['gross_profit'][full] or 0), 3000)
        self.assertEqual(pl['rd'][q1] - (before_pl['rd'][q1] or 0), 300)
        self.assertEqual(pl['net_profit'][full] - (before_pl['net_profit'][full] or 0), 2600)
        self.assertAlmostEqual(pl['gross_margin'][full], pl['gross_profit'][full] / pl['revenue'][full])
        bs = self._mis_values('bs')
        self.assertEqual(bs['check'][q1] or 0.0, 0.0)
        self.assertEqual(bs['current_year_profit'][q1] - (before_bs['current_year_profit'][q1] or 0), 2600)

    def test_cash_flow_and_checks(self):
        before = self.board.get_cash_flow(self.params)['columns'][0]
        _order, invoice = self._order_and_invoice(3000, 3000)
        payment = self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=invoice.ids).create(
            {'payment_date': date(self.year, 2, 5)})._create_payments()
        bank = self.env['account.journal'].search([('type', '=', 'bank'), ('company_id', '=', self.company.id)], limit=1)
        st_line = self.env['account.bank.statement.line'].create({
            'journal_id': bank.id, 'date': date(self.year, 2, 6), 'amount': 3000, 'payment_ref': 'F Customer',
            'partner_id': self.customer.id})
        outstanding = payment.move_id.line_ids.filtered(lambda l: l.account_id.reconcile and l.debit)
        with Form(st_line, view='account_reconcile_oca.bank_statement_line_form_reconcile_view') as form:
            form.add_account_move_line_id = outstanding
        st_line.reconcile_bank_line()
        self.assertTrue(st_line.is_reconciled)
        cash = self.board.get_cash_flow(self.params)
        q1 = cash['columns'][0]
        self.assertEqual(q1['receipts'] - before['receipts'], 3000)
        self.assertEqual(q1['net'] - before['net'], 3000)
        self.assertEqual(q1['closing'] - q1['opening'], q1['net'])
        self.assertEqual(q1['other'], before['other'])
        checks = self.board.get_checks(self.params)['checks']
        aging = checks[0]
        self.assertTrue(aging['ok'], aging)
