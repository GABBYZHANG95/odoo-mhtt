from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import new_test_user, tagged
from odoo.tests.common import TransactionCase


class ProjectCase(TransactionCase):
    """Organisation, customer and helpers shared by the project tests"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.env = env

        def user(login, groups='base.group_user'):
            return new_test_user(env, login=login, groups=groups)

        cls.u_chairman = user('p_chairman', 'base.group_user,aiis_procurement_request.group_po_approver_chairman')
        cls.u_ceo = user('p_ceo', 'base.group_user,aiis_procurement_request.group_po_approver_ceo')
        cls.u_sales_head = user('p_sales_head', 'base.group_user,sales_team.group_sale_salesman')
        cls.u_sales = user('p_sales', 'base.group_user,sales_team.group_sale_salesman')
        cls.u_rd = user('p_rd')
        cls.u_buyer = user('p_buyer', 'base.group_user,purchase.group_purchase_user')
        cls.u_outsider = user('p_outsider')

        Employee = env['hr.employee']
        e_chairman = Employee.create({'name': 'Chairman', 'user_id': cls.u_chairman.id})
        e_ceo = Employee.create({'name': 'CEO', 'user_id': cls.u_ceo.id, 'parent_id': e_chairman.id})
        e_sales_head = Employee.create({'name': 'Sales Head', 'user_id': cls.u_sales_head.id, 'parent_id': e_ceo.id})
        e_sales = Employee.create({'name': 'Salesperson', 'user_id': cls.u_sales.id, 'parent_id': e_sales_head.id})
        cls.rd_department = env['hr.department'].create({'name': 'P Research & Development'})
        env.company.aiis_rd_department_id = cls.rd_department
        Employee.create({'name': 'R&D', 'user_id': cls.u_rd.id, 'parent_id': e_ceo.id,
                         'department_id': cls.rd_department.id})
        sales = env['hr.department'].create({'name': 'P Sales', 'manager_id': e_sales_head.id})
        (e_sales | e_sales_head).department_id = sales
        # Only the procurement department converts PRs into POs (#48); don't
        # depend on the department configured in the database being tested
        procurement = env['hr.department'].create({'name': 'P Procurement'})
        Employee.create({'name': 'Buyer', 'user_id': cls.u_buyer.id, 'parent_id': e_ceo.id,
                         'department_id': procurement.id})
        env.company.aiis_procurement_department_id = procurement
        ceo_department = env['hr.department'].create({'name': 'P CEO', 'manager_id': e_ceo.id})
        # the CEO heads the parent department, not Sales itself
        sales.parent_id = ceo_department
        env['hr.department'].create({'name': 'P Chairman', 'manager_id': e_chairman.id})

        env.ref('aiis_approval.position_ceo').user_id = cls.u_ceo
        # the CFO confirms the finance terms before the quotation (#61)
        cls.u_cfo = new_test_user(env, login='p_cfo', groups='base.group_user,aiis_project.group_project_cost')
        Employee.create({'name': 'CFO', 'user_id': cls.u_cfo.id, 'parent_id': e_ceo.id})
        env.ref('aiis_approval.position_cfo').user_id = cls.u_cfo
        env.ref('aiis_approval.position_chairman').user_id = cls.u_chairman

        cls.customer = env['res.partner'].create({'name': 'Saudi Telecom', 'aiis_customer_code': 'stc'})
        cls.usd = env.ref('base.USD')
        cls.today = fields.Date.context_today(env['aiis.project'])

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _new_project(self, customer=None):
        attachment = self.env['ir.attachment'].with_user(self.u_sales).create({'name': 'rfi.pdf', 'raw': b'info'})
        return self.env['aiis.project'].with_user(self.u_sales).create({
            'name': 'Smart City Pilot',
            'partner_id': (customer or self.customer).id,
            'member_ids': [(6, 0, [self.u_rd.id])],
            'info_attachment_ids': [(6, 0, [attachment.id])],
        })

    def _approve_all(self, project):
        while project.aiis_approval_state == 'pending':
            user = project.approval_request_id.line_ids.filtered(lambda l: l.state == 'pending')[:1].user_id
            project.with_user(user).action_approval_approve()

    def _upload_solution(self, project, name='design.pdf', user=None):
        user = user or self.u_rd
        attachment = self.env['ir.attachment'].with_user(user).create({
            'name': name, 'raw': b'design', 'res_model': 'aiis.project', 'res_id': project.id})
        project.with_user(user).write({'solution_attachment_ids': [(4, attachment.id)]})
        return attachment

    def _initiated(self):
        """Initiation approved; R&D has uploaded a solution document"""
        project = self._new_project()
        project.action_submit_initiation()
        self._approve_all(project)
        self._upload_solution(project)
        return project

    def _designed(self):
        project = self._initiated()
        project.with_user(self.u_rd).write({'bom_line_ids': [
            (0, 0, {'name': 'GPU Server', 'specification': '2x GPU', 'quantity': 2}),
            (0, 0, {'name': 'Installation', 'quantity': 1}),
        ]})
        project.with_user(self.u_rd).action_submit_solution()
        project.with_user(self.u_ceo).action_approval_approve()
        return project

    def _costed(self):
        project = self._designed()
        valid = self.today + timedelta(days=30)
        server, install = project.bom_line_ids.sorted('id')
        server.with_user(self.u_buyer).write({'unit_cost': 100.0, 'cost_currency_id': self.usd.id, 'quote_valid_until': valid})
        install.with_user(self.u_buyer).write({'unit_cost': 1000.0, 'quote_valid_until': valid})
        project.with_user(self.u_buyer).action_submit_costing()
        return project

    def _cfo_confirms(self, project):
        project.with_user(self.u_cfo).action_approval_approve()

    def _quoted(self):
        project = self._costed()
        project.with_user(self.u_ceo).write({'margin_percent': 25})
        project.with_user(self.u_ceo).action_approval_approve()
        self._cfo_confirms(project)
        return project


@tagged('post_install', '-at_install')
class TestProject(ProjectCase):
    """Project phase 1 (design doc 4.2, 4.3, 4.7, 4.8)."""

    # ------------------------------------------------------------------
    # Initiation
    # ------------------------------------------------------------------

    def test_initiation_chain_and_project_number(self):
        project = self._new_project()
        project.action_submit_initiation()
        approvers = project.approval_request_id.line_ids.sorted('step').user_id
        self.assertEqual(approvers, self.u_sales_head | self.u_ceo)
        self._approve_all(project)
        self.assertEqual(project.state, 'design')
        self.assertEqual(project.code, 'STC-%s-001' % self.today.year)
        self.assertEqual(project.analytic_account_id.code, project.code)
        second = self._new_project()
        second.action_submit_initiation()
        self._approve_all(second)
        self.assertEqual(second.code, 'STC-%s-002' % self.today.year)

    def test_initiation_needs_customer_code_and_information(self):
        nocode = self.env['res.partner'].create({'name': 'No Code Co'})
        with self.assertRaises(UserError):
            self._new_project(nocode).action_submit_initiation()
        project = self._new_project()
        project.info_attachment_ids = [(5,)]
        with self.assertRaises(UserError):
            project.action_submit_initiation()

    def test_no_bom_before_solution_design(self):
        project = self._new_project()
        with self.assertRaises(UserError):
            project.with_user(self.u_rd).write({'bom_line_ids': [(0, 0, {'name': 'Camera', 'quantity': 1})]})
        project.action_submit_initiation()
        with self.assertRaises(UserError):
            project.with_user(self.u_rd).write({'bom_line_ids': [(0, 0, {'name': 'Camera', 'quantity': 1})]})

    def test_initiation_rejected_back_to_draft(self):
        project = self._new_project()
        project.action_submit_initiation()
        project.with_user(self.u_sales_head)._approval_reject('Not our business')
        self.assertEqual(project.state, 'draft')
        self.assertFalse(project.code)

    # ------------------------------------------------------------------
    # Solution design / BOM
    # ------------------------------------------------------------------

    def test_solution_version_and_lock(self):
        project = self._initiated()
        project.with_user(self.u_rd).write({'bom_line_ids': [(0, 0, {'name': 'Camera', 'quantity': 10})]})
        project.with_user(self.u_rd).action_submit_solution()
        self.assertEqual(project.state, 'design_review')
        self.assertEqual(project.bom_version_id.name, 'v1')
        self.assertEqual(project.bom_version_id.line_ids.name, 'Camera')
        with self.assertRaises(UserError):
            project.bom_line_ids.with_user(self.u_rd).write({'quantity': 12})
        project.with_user(self.u_ceo).action_approval_approve()
        self.assertEqual(project.state, 'costing')
        self.assertEqual(project.bom_version_id.state, 'approved')

    def test_solution_rejected_back_to_design(self):
        project = self._initiated()
        project.with_user(self.u_rd).write({'bom_line_ids': [(0, 0, {'name': 'Camera', 'quantity': 10})]})
        project.with_user(self.u_rd).action_submit_solution()
        project.with_user(self.u_ceo)._approval_reject('Use the outdoor model')
        self.assertEqual(project.state, 'design')
        self.assertEqual(project.bom_version_id.state, 'rejected')
        project.with_user(self.u_rd).action_submit_solution()
        self.assertEqual(project.bom_version_id.name, 'v2')

    def test_change_after_approval_needs_new_quote(self):
        project = self._quoted()
        server = project.bom_line_ids.sorted('id')[0]
        self.assertFalse(server.needs_rfq)
        server.with_user(self.u_rd).write({'quantity': 3})
        self.assertTrue(server.changed_since_approval)
        self.assertTrue(server.needs_rfq)
        self.assertEqual(server.status, 'changed')
        self.assertEqual(project.bom_changed_count, 1)

    # ------------------------------------------------------------------
    # Costing, margin and quotation
    # ------------------------------------------------------------------

    def test_costing_needs_quotes(self):
        project = self._designed()
        with self.assertRaises(UserError):
            project.with_user(self.u_buyer).action_submit_costing()

    def test_margin_set_by_ceo_and_quotation_prices(self):
        project = self._costed()
        self.assertEqual(project.state, 'costing_review')
        with self.assertRaises(UserError):
            project.with_user(self.u_ceo).action_approval_approve()
        with self.assertRaises(UserError):
            project.with_user(self.u_buyer).write({'margin_percent': 20})
        project.with_user(self.u_ceo).write({'margin_percent': 20})
        project.with_user(self.u_ceo).action_approval_approve()
        # #61: then the CFO, before the salesperson gets the quotation
        self.assertEqual(project.state, 'costing_review')
        self.assertFalse(project.quotation_id)
        self.assertEqual(project._approval_pending_users(), self.u_cfo)
        self.assertEqual(project.sudo().cost_budget, project.sudo().total_cost)
        self.assertEqual(project.sudo().fx_rate_ids.currency_id, self.usd)
        with self.assertRaises(UserError):
            project.with_user(self.u_ceo).write({'margin_percent': 30})
        with self.assertRaises(UserError):
            project.with_user(self.u_ceo).write({'cost_budget': 1.0})
        budget = project.sudo().cost_budget
        project.with_user(self.u_cfo).write({'cost_budget': 0.0})
        with self.assertRaises(UserError):
            project.with_user(self.u_cfo).action_approval_approve()  # no budget
        project.with_user(self.u_cfo).write({'cost_budget': budget})
        # the CFO fixes the USD rate and the tax for the quotation
        project.with_user(self.u_cfo).fx_rate_ids.write({'rate': 4.0})
        tax = self.env['account.tax'].create({'name': 'P VAT 10', 'amount': 10, 'type_tax_use': 'sale'})
        project.with_user(self.u_cfo).write({'quote_tax_ids': [(6, 0, tax.ids)]})
        self._cfo_confirms(project)
        self.assertEqual(project.state, 'quoted')
        quotation = project.quotation_id
        self.assertEqual(quotation.order_line.tax_id, tax)
        # #63: the payment terms are agreed at the contract stage, not here
        self.assertFalse(project.payment_cycle)
        self.assertEqual(quotation.partner_id, self.customer)
        self.assertEqual(quotation.user_id, self.u_sales)
        self.assertEqual(quotation.aiis_project_id, project)
        company = self.env.company
        server_cost = 100.0 * 4.0
        self.assertAlmostEqual(project.sudo().quoted_cost, 2 * 400.0 + 1000.0, places=2)
        prices = quotation.order_line.sorted('id').mapped('price_unit')
        self.assertAlmostEqual(prices[0], company.currency_id.round(server_cost / 0.8), places=2)
        self.assertAlmostEqual(prices[1], 1250.0, places=2)
        # BOM lines without a product got one, reused later for purchasing
        self.assertTrue(all(project.bom_line_ids.mapped('product_id')))

    # ------------------------------------------------------------------
    # Customer decision
    # ------------------------------------------------------------------

    def test_customer_accepts(self):
        project = self._quoted()
        project.with_user(self.u_sales)._customer_accepted('STC-PO-7788')
        self.assertEqual(project.state, 'contract_bom')
        self.assertEqual(project.stage, 'contract')
        self.assertEqual(project.quotation_id.client_order_ref, 'STC-PO-7788')
        # #66: the decision is kept for the dashboard's win rate
        self.assertEqual((project.customer_decision, project.customer_decision_date), ('accepted', self.today))

    def test_customer_rejects_pause_and_restart(self):
        project = self._quoted()
        restart = self.today + timedelta(days=60)
        project.with_user(self.u_sales)._customer_rejected('pause', 'Budget next year', restart)
        self.assertEqual(project.status, 'paused')
        activity = project.activity_ids.filtered(lambda a: a.user_id == self.u_sales)
        self.assertEqual(activity.date_deadline, restart)
        with self.assertRaises(UserError):
            project.with_user(self.u_sales).action_open_customer_accept()
        project.with_user(self.u_sales).action_restart()
        self.assertEqual((project.status, project.state), ('active', 'design'))
        self.assertEqual(len(project.bom_line_ids), 2)

    def test_customer_rejects_close(self):
        project = self._quoted()
        project.with_user(self.u_sales)._customer_rejected('close', 'Went with a competitor')
        self.assertEqual(project.status, 'closed')
        self.assertEqual(project.customer_decision, 'rejected_closed')

    # ------------------------------------------------------------------
    # Visibility
    # ------------------------------------------------------------------

    def test_who_sees_the_project(self):
        project = self._initiated()
        Project = self.env['aiis.project']
        for user in (self.u_sales, self.u_rd, self.u_sales_head, self.u_buyer, self.u_ceo):
            self.assertTrue(Project.with_user(user).search([('id', '=', project.id)]), user.login)
        self.assertFalse(Project.with_user(self.u_outsider).search([('id', '=', project.id)]))
        # approvers open the form, project information included (uploaded by the salesperson)
        info = project.with_user(self.u_sales_head).info_attachment_ids
        self.assertTrue(info.read(['name']))
        self.assertEqual((info.res_model, info.res_id), ('aiis.project', project.id))

    def test_only_rd_maintains_bom(self):
        project = self._initiated()
        for user in (self.u_sales, self.u_buyer, self.u_sales_head):
            with self.assertRaises(UserError):
                project.with_user(user).write({'bom_line_ids': [(0, 0, {'name': 'Camera', 'quantity': 1})]})
        project.with_user(self.u_rd).write({'bom_line_ids': [(0, 0, {'name': 'Camera', 'quantity': 1})]})
        line = project.bom_line_ids
        with self.assertRaises(UserError):
            line.with_user(self.u_sales).write({'quantity': 4})
        with self.assertRaises(UserError):
            line.with_user(self.u_buyer).unlink()
        with self.assertRaises(UserError):
            project.with_user(self.u_sales).action_submit_solution()
        # Procurement still fills in the costs
        line.with_user(self.u_buyer).write({'unit_cost': 50.0})
        project.with_user(self.u_rd).action_submit_solution()
        self.assertEqual(project.state, 'design_review')

    def test_solution_documents_required_and_versioned(self):
        """#59: R&D uploads the design documents; each version keeps a copy"""
        project = self._initiated()
        project.with_user(self.u_rd).write({'bom_line_ids': [(0, 0, {'name': 'Camera', 'quantity': 10})]})
        first = project.solution_attachment_ids
        project.with_user(self.u_rd).write({'solution_attachment_ids': [(5,)]})
        with self.assertRaises(UserError):
            project.with_user(self.u_rd).action_submit_solution()
        project.with_user(self.u_rd).write({'solution_attachment_ids': [(4, first.id)]})
        project.with_user(self.u_rd).action_submit_solution()
        v1 = project.bom_version_id
        self.assertEqual(v1.attachment_ids.mapped('name'), ['design.pdf'])
        self.assertNotEqual(v1.attachment_ids, first)
        self.assertEqual((v1.attachment_ids.res_model, v1.attachment_ids.res_id), (v1._name, v1.id))
        # locked while the CEO reviews it
        with self.assertRaises(UserError):
            self._upload_solution(project, 'late.pdf')
        # the approvers and the team can open the documents
        for user in (self.u_ceo, self.u_sales, self.u_buyer):
            self.assertTrue(project.with_user(user).solution_attachment_ids.read(['datas']))
            self.assertTrue(v1.with_user(user).attachment_ids.read(['datas']))
        project.with_user(self.u_ceo)._approval_reject('More detail on the cabling')
        # replacing the documents leaves the submitted version untouched
        project.with_user(self.u_rd).write({'solution_attachment_ids': [(5,)]})
        self._upload_solution(project, 'design-v2.pdf')
        project.with_user(self.u_rd).action_submit_solution()
        self.assertEqual(project.bom_version_id.attachment_ids.mapped('name'), ['design-v2.pdf'])
        self.assertEqual(v1.attachment_ids.mapped('name'), ['design.pdf'])
        self.assertTrue(v1.attachment_ids.exists())

    def test_solution_documents_after_approval(self):
        """Like the BOM: R&D can still add documents while Procurement quotes;
        the change is logged and the approved version is untouched"""
        project = self._designed()
        self.assertEqual(project.state, 'costing')
        approved = project.bom_version_id
        self._upload_solution(project, 'addendum.pdf')
        self.assertEqual(approved.attachment_ids.mapped('name'), ['design.pdf'])
        self.assertIn('addendum.pdf', project.message_ids[:1].body)
        # locked again while the CEO approves the costing
        costed = self._costed()
        self.assertEqual(costed.state, 'costing_review')
        with self.assertRaises(UserError):
            self._upload_solution(costed, 'late.pdf')

    def test_only_rd_uploads_solution_documents(self):
        project = self._initiated()
        for user in (self.u_sales, self.u_buyer, self.u_ceo):
            with self.assertRaises(UserError):
                project.with_user(user).write({'solution_attachment_ids': [(5,)]})
        self.assertTrue(project.solution_attachment_ids)

    def test_only_sales_records_the_customer_decision(self):
        """#60: other project members don't decide for the customer; the head
        of the salesperson's own department can act for them, the CEO (head of
        the parent department) can't"""
        project = self._quoted()
        for user in (self.u_rd, self.u_buyer, self.u_ceo):
            self.assertFalse(project.with_user(user).is_salesperson, user.login)
            with self.assertRaises(UserError):
                project.with_user(user).action_open_customer_accept()
            with self.assertRaises(UserError):
                project.with_user(user)._customer_rejected('close', 'No')
        self.assertTrue(project.with_user(self.u_sales).is_salesperson)
        self.assertTrue(project.with_user(self.u_sales_head).is_salesperson)
        project.with_user(self.u_sales_head)._customer_accepted('STC-PO-1')
        self.assertEqual(project.state, 'contract_bom')

    def test_costs_hidden_from_rd_and_sales(self):
        project = self._costed()
        line = project.bom_line_ids[:1]
        for user in (self.u_rd, self.u_sales):
            with self.assertRaises(AccessError):
                line.with_user(user).read(['unit_cost'])
            with self.assertRaises(AccessError):
                project.with_user(user).read(['margin_percent'])
        self.assertTrue(line.with_user(self.u_buyer).read(['unit_cost'])[0]['unit_cost'])
