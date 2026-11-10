from odoo.exceptions import AccessError
from odoo.tests import new_test_user, tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestAccess(TransactionCase):
    """Not in the flow, not visible (design doc 1.4, #50-#53)"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.env = env
        cls.u_head = new_test_user(env, login='a_head', groups='base.group_user')
        cls.u_seller = new_test_user(env, login='a_seller', groups='base.group_user')
        cls.u_other_seller = new_test_user(env, login='a_seller2', groups='base.group_user')
        cls.u_buyer = new_test_user(env, login='a_buyer', groups='base.group_user,purchase.group_purchase_user')
        cls.u_cfo = new_test_user(env, login='a_cfo', groups='base.group_user,aiis_sales_expense.group_sales_budget_manager')
        Employee = env['hr.employee']
        cls.e_head = Employee.create({'name': 'Sales Head', 'user_id': cls.u_head.id})
        cls.sales = env['hr.department'].create({'name': 'A Sales', 'aiis_function': 'sales', 'manager_id': cls.e_head.id})
        cls.team = env['hr.department'].create({'name': 'A Key Accounts', 'parent_id': cls.sales.id})
        cls.e_head.department_id = cls.sales
        cls.e_seller = Employee.create({'name': 'Seller', 'user_id': cls.u_seller.id, 'department_id': cls.team.id,
                                        'parent_id': cls.e_head.id})
        cls.e_other = Employee.create({'name': 'Seller 2', 'user_id': cls.u_other_seller.id,
                                       'department_id': cls.sales.id, 'parent_id': cls.e_head.id})
        cls.buying = env['hr.department'].create({'name': 'A Procurement'})
        Employee.create({'name': 'Buyer', 'user_id': cls.u_buyer.id, 'department_id': cls.buying.id})
        cls.customer = env['res.partner'].create({'name': 'A Customer'})

    def _contract(self, employee):
        return self.env['customer.contract'].create({
            'salesperson_id': employee.id, 'partner_id': self.customer.id})

    def test_sales_rights_follow_the_department(self):
        salesman = self.env.ref('sales_team.group_sale_salesman')
        all_docs = self.env.ref('sales_team.group_sale_salesman_all_leads')
        self.assertIn(salesman, self.u_seller.groups_id, 'sub-department of Sales')
        self.assertNotIn(all_docs, self.u_seller.groups_id)
        self.assertIn(all_docs, self.u_head.groups_id, 'the head sees all sales documents')
        self.assertNotIn(salesman, self.u_buyer.groups_id)
        self.e_seller.department_id = self.buying
        self.assertNotIn(salesman, self.u_seller.groups_id, 'leaving sales takes the rights away')

    def test_sales_app(self):
        root = self.env.ref('aiis_department_menu.menu_aiis_sales_root').id

        def sees(user):
            return root in self.env['ir.ui.menu'].with_user(user)._visible_menu_ids()

        self.assertTrue(sees(self.u_seller))
        self.assertTrue(sees(self.u_cfo))
        self.assertFalse(sees(self.u_buyer), 'procurement does not see Sales')

    def test_contracts_of_my_own(self):
        mine = self._contract(self.e_seller)
        theirs = self._contract(self.e_other)
        Contract = self.env['customer.contract']
        self.assertEqual(Contract.with_user(self.u_seller).search([('id', 'in', (mine | theirs).ids)]), mine)
        self.assertEqual(Contract.with_user(self.u_head).search([('id', 'in', (mine | theirs).ids)]), mine | theirs,
                         'the approver / department head')
        self.assertEqual(Contract.with_user(self.u_cfo).search([('id', 'in', (mine | theirs).ids)]), mine | theirs)
        self.assertFalse(Contract.with_user(self.u_buyer).search([('id', 'in', (mine | theirs).ids)]))
        with self.assertRaises(AccessError):
            theirs.with_user(self.u_seller).read(['name'])

    def test_procurement_sees_only_the_order_number(self):
        order = self.env['sale.order'].create({'partner_id': self.customer.id})
        request = self.env['procurement.request'].create({
            'employee_id': self.e_seller.id, 'sale_order_id': order.id,
            'line_ids': [(0, 0, {'description': 'Server', 'quantity': 1})]})
        as_buyer = request.with_user(self.u_buyer)
        self.assertEqual(as_buyer.sale_order_ref, order.name)
        with self.assertRaises(AccessError):
            order.with_user(self.u_buyer).read(['amount_total'])
