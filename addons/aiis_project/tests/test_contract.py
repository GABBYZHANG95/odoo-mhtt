from datetime import timedelta

from odoo.exceptions import UserError
from odoo.tests import new_test_user, tagged

from .test_project import ProjectCase


class ContractCase(ProjectCase):
    """The contract gate is CFO -> CEO (#63)"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        # an R&D head exists but no longer approves the contract
        cls.u_rd_head = new_test_user(env, login='p_rd_head', groups='base.group_user')
        ceo = env['hr.employee'].search([('user_id', '=', cls.u_ceo.id)])
        rd_head = env['hr.employee'].create({'name': 'R&D Head', 'user_id': cls.u_rd_head.id, 'parent_id': ceo.id})
        cls.rd_department.manager_id = rd_head

    def _accepted(self):
        project = self._quoted()
        project.with_user(self.u_sales)._customer_accepted('STC-PO-7788')
        return project

    def _fill_terms(self, project):
        project.with_user(self.u_sales).write({
            'commercial_terms': 'Delivery in 12 weeks, 1-year warranty',
            'payment_cycle': '30% on signing, 60% on delivery, 10% on acceptance',
            'payment_conditions': 'Invoices due in 30 days',
        })

    def _checked(self, project):
        """#62: R&D confirms the BOM, Procurement the prices"""
        project.with_user(self.u_rd).action_confirm_bom()
        project.with_user(self.u_buyer).action_confirm_prices()

    def _pending(self, project):
        return project.approval_request_id.line_ids.filtered(lambda l: l.state == 'pending').user_id

    def _delivered(self, maintenance_end=False):
        """A project in delivery, its project manager set"""
        project = self._accepted()
        self._checked(project)
        self._fill_terms(project)
        project.with_user(self.u_ceo).write({'project_manager_id': self.u_sales.id})
        if maintenance_end:
            project.with_user(self.u_sales).write({'maintenance_start': self.today, 'maintenance_end': maintenance_end})
        project.with_user(self.u_sales).action_submit_contract()
        self._approve_all(project)
        return project


@tagged('post_install', '-at_install')
class TestContract(ContractCase):
    """Project phase 2, the contract (design doc 4.4, decisions #39 and #40)."""

    def test_only_sales_fills_and_submits_the_contract(self):
        """#60: contract terms and submission are the salesperson's"""
        project = self._accepted()
        self._checked(project)
        self.assertEqual(project.state, 'contract_terms')
        with self.assertRaises(UserError):
            project.with_user(self.u_rd).write({'commercial_terms': 'Anything'})
        with self.assertRaises(UserError):
            project.with_user(self.u_buyer).write({'maintenance_end': self.today})
        self._fill_terms(project)
        project.with_user(self.u_ceo).write({'project_manager_id': self.u_sales.id})
        with self.assertRaises(UserError):
            project.with_user(self.u_rd).action_submit_contract()
        project.with_user(self.u_sales_head).action_submit_contract()
        self.assertEqual(project.state, 'contract_review')

    def test_contract_check_in_parallel(self):
        """#62: either order; a later BOM change voids the confirmations"""
        project = self._accepted()
        project.with_user(self.u_buyer).action_confirm_prices()
        self.assertEqual(project.contract_prices_confirmed_by, self.u_buyer)
        self.assertEqual(project.state, 'contract_bom')
        with self.assertRaises(UserError):
            project.with_user(self.u_sales).action_confirm_prices()
        with self.assertRaises(UserError):
            project.with_user(self.u_buyer).write({'contract_bom_confirmed_by': self.u_rd.id})
        # R&D updates an outdated line: Procurement has to confirm again
        server = project.bom_line_ids.sorted('id')[0]
        server.with_user(self.u_rd).write({'specification': '2x GPU, new model'})
        self.assertFalse(project.contract_prices_confirmed_by)
        project.with_user(self.u_rd).action_confirm_bom()
        self.assertIn('confirmed by', str(project.workflow_hint))
        self.assertEqual(project.state, 'contract_bom')
        server.with_user(self.u_buyer).write({'unit_cost': 100.0})
        project.with_user(self.u_buyer).action_confirm_prices()
        self.assertEqual(project.state, 'contract_terms')

    def test_confirm_bom_saves_solution_documents(self):
        """#59: R&D may update the documents when confirming the BOM; the
        confirmation is kept as a version with its own copy"""
        project = self._accepted()
        approved = project.bom_version_id
        self._upload_solution(project, 'design-final.pdf')
        project.with_user(self.u_rd).action_confirm_bom()
        confirmed = project.bom_version_id
        self.assertNotEqual(confirmed, approved)
        self.assertEqual(confirmed.state, 'confirmed')
        self.assertEqual(approved.state, 'approved')
        self.assertEqual(sorted(confirmed.attachment_ids.mapped('name')), ['design-final.pdf', 'design.pdf'])
        self.assertEqual(approved.attachment_ids.mapped('name'), ['design.pdf'])
        # locked again after the check
        with self.assertRaises(UserError):
            self._upload_solution(project, 'later.pdf')

    def test_same_cost_contract_to_delivery(self):
        project = self._accepted()
        self.assertEqual(project.state, 'contract_bom')
        price = project.quotation_id.amount_untaxed
        project.with_user(self.u_rd).action_confirm_bom()
        # #62: Procurement confirms the prices too, even when nothing changed
        self.assertEqual(project.state, 'contract_bom')
        self.assertIn('waiting to confirm the prices', str(project.workflow_hint))
        project.with_user(self.u_buyer).action_confirm_prices()
        self.assertEqual(project.state, 'contract_terms')
        with self.assertRaises(UserError):
            project.with_user(self.u_sales).action_submit_contract()
        self._fill_terms(project)
        project.with_user(self.u_sales).action_submit_contract()
        self.assertEqual(project.state, 'contract_review')
        # #63: the CFO, then the CEO - no R&D head
        self.assertEqual(self._pending(project), self.u_cfo)
        self.assertNotIn(self.u_rd_head, project.approval_request_id.line_ids.user_id)
        project.with_user(self.u_cfo).action_approval_approve()
        self.assertEqual(self._pending(project), self.u_ceo)
        project.with_user(self.u_ceo).write({'project_manager_id': self.u_sales.id})
        project.with_user(self.u_ceo).action_approval_approve()
        self.assertEqual((project.state, project.stage), ('delivery', 'delivery'))
        order = project.quotation_id
        self.assertEqual(order.state, 'sale')
        self.assertEqual(order.client_order_ref, 'STC-PO-7788')
        self.assertEqual(order.amount_untaxed, price)

    def test_contract_rejected_back_to_sales(self):
        project = self._accepted()
        self._checked(project)
        self._fill_terms(project)
        project.with_user(self.u_sales).action_submit_contract()
        project.with_user(self.u_cfo)._approval_reject('Payment cycle too long')
        self.assertEqual(project.state, 'contract_terms')
        project.with_user(self.u_sales).write({'payment_cycle': '50% on signing, 50% on delivery'})
        project.with_user(self.u_sales).action_submit_contract()
        self.assertEqual(project.state, 'contract_review')

    def test_ceo_assigns_project_manager(self):
        project = self._new_project()
        # the salesperson suggests one on the draft
        project.with_user(self.u_sales).write({'project_manager_id': self.u_rd.id})
        project.action_submit_initiation()
        with self.assertRaises(UserError):
            project.with_user(self.u_sales).write({'project_manager_id': self.u_sales.id})
        project.with_user(self.u_ceo).write({'project_manager_id': self.u_sales.id})
        self.assertEqual(project.project_manager_id, self.u_sales)

    def test_contract_needs_project_manager(self):
        project = self._accepted()
        self._checked(project)
        self._fill_terms(project)
        project.with_user(self.u_sales).action_submit_contract()
        project.with_user(self.u_cfo).action_approval_approve()
        with self.assertRaises(UserError):
            project.with_user(self.u_ceo).action_approval_approve()
        project.with_user(self.u_ceo).write({'project_manager_id': self.u_sales.id})
        project.with_user(self.u_ceo).action_approval_approve()
        self.assertEqual(project.state, 'delivery')

    def test_bom_locked_while_terms_are_finalised(self):
        project = self._accepted()
        self._checked(project)
        with self.assertRaises(UserError):
            project.bom_line_ids[:1].with_user(self.u_rd).write({'quantity': 5})

    def test_expired_quote_requoted_lower_cost_keeps_price(self):
        project = self._accepted()
        price = project.quotation_id.amount_untaxed
        install = project.bom_line_ids.sorted('id')[1]
        install.sudo().quote_valid_until = self.today - timedelta(days=1)
        project.with_user(self.u_rd).action_confirm_bom()
        self.assertEqual(project.state, 'contract_bom')
        with self.assertRaises(UserError):
            project.with_user(self.u_buyer).action_confirm_prices()
        install.with_user(self.u_buyer).write({'unit_cost': 900.0, 'quote_valid_until': self.today + timedelta(days=30)})
        project.with_user(self.u_buyer).action_confirm_prices()
        self.assertEqual(project.state, 'contract_terms')
        self.assertEqual(project.quotation_id.amount_untaxed, price)
        self.assertGreater(project.sudo().actual_margin, 25)

    def test_cost_increase_needs_new_quotation(self):
        project = self._accepted()
        first_quotation = project.quotation_id
        server = project.bom_line_ids.sorted('id')[0]
        server.with_user(self.u_rd).write({'quantity': 3})
        project.with_user(self.u_rd).action_confirm_bom()
        with self.assertRaises(UserError):
            project.with_user(self.u_buyer).action_confirm_prices()  # the changed line needs a quote
        server.with_user(self.u_buyer).write({'unit_cost': 100.0})
        project.with_user(self.u_buyer).action_confirm_prices()
        self.assertEqual(project.state, 'renegotiate')
        # a CEO rejection of the new price goes back to the salesperson
        project.with_user(self.u_sales).action_requote()
        self.assertEqual(project.state, 'costing_review')
        project.with_user(self.u_ceo)._approval_reject('Talk to the customer again')
        self.assertEqual(project.state, 'renegotiate')
        project.with_user(self.u_sales).action_requote()
        project.with_user(self.u_ceo).action_approval_approve()
        self._cfo_confirms(project)
        self.assertEqual(project.state, 'quoted')
        self.assertNotEqual(project.quotation_id, first_quotation)
        self.assertEqual(first_quotation.state, 'cancel')
        self.assertEqual(project.quotation_id.order_line.sorted('id')[0].product_uom_qty, 3)
        action = project.with_user(self.u_sales).action_open_customer_accept()
        wizard = self.env['aiis.project.accept.wizard'].with_user(self.u_sales).with_context(action['context']).create({})
        self.assertEqual(wizard.customer_po_ref, 'STC-PO-7788')
        wizard.action_confirm()
        self.assertEqual(project.state, 'contract_bom')

    def test_project_order_confirmed_only_by_project_contract(self):
        project = self._accepted()
        with self.assertRaises(UserError):
            project.quotation_id.with_user(self.u_sales).action_confirm()
        # sales outside projects still need a customer contract
        other = self.env['sale.order'].with_user(self.u_sales).create({
            'partner_id': self.customer.id,
            'order_line': [(0, 0, {'product_id': project.bom_line_ids[:1].product_id.id, 'product_uom_qty': 1})],
        })
        with self.assertRaises(UserError):
            other.action_confirm()
