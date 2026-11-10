from datetime import timedelta

from odoo.exceptions import UserError, ValidationError
from odoo.tests import Form, tagged

from .test_contract import ContractCase


@tagged('post_install', '-at_install')
class TestDelivery(ContractCase):
    """Project phase 3, delivery and maintenance (design doc 4.5, 4.6)."""

    def _pr_wizard(self, project, quantities):
        """Through the form, as the project manager uses it"""
        action = project.with_user(self.u_sales).action_open_pr_wizard()
        form = Form(self.env['aiis.project.pr.wizard'].with_user(self.u_sales).with_context(action['context']))
        for index in range(len(form.line_ids)):
            with form.line_ids.edit(index) as line:
                line.quantity = quantities.get(line.name, 0.0)
        return form.save()

    # ------------------------------------------------------------------
    # Project purchase requests and BOM progress
    # ------------------------------------------------------------------

    def test_project_pr_from_bom_skips_approval(self):
        project = self._delivered()
        self.assertEqual(project.state, 'delivery')
        with self.assertRaises(UserError):
            self._pr_wizard(project, {'GPU Server': 5}).action_confirm()
        action = self._pr_wizard(project, {'GPU Server': 1}).action_confirm()
        request = self.env['procurement.request'].browse(action['res_id'])
        self.assertEqual(request.state, 'approved')
        # #64: the request says Procurement is next, by name
        self.assertIn('Prepare Purchase Order', str(request.workflow_hint))
        self.assertIn(self.u_buyer.name, str(request.workflow_hint))
        self.assertFalse(request.approval_request_id.line_ids)
        self.assertEqual(request.aiis_project_id, project)
        self.assertEqual(request.analytic_account_id, project.analytic_account_id)
        server = project.bom_line_ids.sorted('id')[0]
        self.assertEqual((server.qty_requested, server.qty_remaining), (1, 1))
        # Procurement converts it; the PO line keeps the BOM link
        wizard = self.env['procurement.request.create.po.wizard'].with_user(self.u_buyer).create({
            'request_id': request.id, 'partner_id': self.customer.id})
        wizard.action_confirm()
        po = request.purchase_order_id
        self.assertEqual(po.order_line.aiis_bom_line_id, server)
        # #61: over the CFO's cost budget the PO is flagged, not blocked
        po.order_line.price_unit = 10 ** 7
        self.assertIn('Over the cost budget', po.aiis_budget_warning)
        po.order_line.price_unit = 100
        self.assertFalse(po.aiis_budget_warning)
        po.order_line.taxes_id = [(5,)]
        # #58: the supplier's agreement first goes to the CFO, then the CEO
        evidence = self.env['ir.attachment'].create({
            'name': 'agreement.pdf', 'raw': b'agreed', 'res_model': 'purchase.order', 'res_id': po.id})
        po.with_user(self.u_buyer).write({
            'aiis_settlement_mode': 'credit', 'aiis_supplier_offer': 'Pay 30 days after receipt',
            'aiis_supplier_evidence_ids': [(6, 0, evidence.ids)],
            'aiis_supplier_confirmed': True})
        po.with_user(self.u_buyer).button_confirm()
        po.with_user(self.u_cfo).action_confirm_supplier_terms()
        po.with_user(self.u_ceo).action_approval_approve()
        self.assertEqual(server.qty_ordered, 1)
        self.assertAlmostEqual(project.sudo().budget_committed, 100.0, places=2)
        po.order_line.qty_received = 1
        self.assertEqual(server.qty_received, 1)
        self.assertEqual(request.state, 'done')

    def test_only_project_manager_raises_prs(self):
        project = self._delivered()
        with self.assertRaises(UserError):
            project.with_user(self.u_rd).action_open_pr_wizard()

    # ------------------------------------------------------------------
    # Delivery acceptance, maintenance, closing
    # ------------------------------------------------------------------

    def test_delivery_without_maintenance_closes(self):
        project = self._delivered()
        project.with_user(self.u_sales).action_request_delivery_acceptance()
        self.assertEqual(project.approval_request_id.line_ids.user_id, self.u_ceo)
        project.with_user(self.u_ceo).action_approval_approve()
        self.assertEqual((project.state, project.status, project.stage), ('done', 'closed', 'delivery'))

    def test_delivery_rejected_stays_in_delivery(self):
        project = self._delivered()
        project.with_user(self.u_sales).action_request_delivery_acceptance()
        project.with_user(self.u_ceo)._approval_reject('Site acceptance report missing')
        self.assertEqual(project.state, 'delivery')

    def test_maintenance_renew_and_close(self):
        end = self.today + timedelta(days=365)
        project = self._delivered(maintenance_end=end)
        project.with_user(self.u_sales).action_request_delivery_acceptance()
        project.with_user(self.u_ceo).action_approval_approve()
        self.assertEqual((project.state, project.stage), ('maintenance', 'maintenance'))
        self.assertTrue(project.activity_ids.filtered(lambda a: a.date_deadline == end))
        with self.assertRaises(UserError):
            project.with_user(self.u_sales).action_open_renew()
        project.with_user(self.u_ceo)._renew(end + timedelta(days=365))
        self.assertEqual(project.maintenance_end, end + timedelta(days=365))
        with self.assertRaises(UserError):
            project.with_user(self.u_ceo)._renew(end)
        project.with_user(self.u_sales).action_request_closing()
        project.with_user(self.u_ceo).action_approval_approve()
        self.assertEqual((project.state, project.status, project.stage), ('done', 'closed', 'maintenance'))

    # ------------------------------------------------------------------
    # Service hours
    # ------------------------------------------------------------------

    def test_service_hours_budget_and_extension(self):
        project = self._delivered(maintenance_end=self.today + timedelta(days=365))
        Request = self.env['aiis.service.hours.request']
        request = Request.with_user(self.u_rd).create({'project_id': project.id, 'hours': 10, 'description': 'Q1 visits'})
        request.action_submit()
        # R&D staff reports to the CEO directly here: the CEO is the next level up
        self.assertEqual(request.approval_request_id.line_ids.user_id, self.u_ceo)
        request.with_user(self.u_ceo).action_approval_approve()
        self.assertEqual(request.hours_approved, 10)
        request.with_user(self.u_rd).write({'record_ids': [(0, 0, {'hours': 8, 'description': 'Visit 1'})]})
        with self.assertRaises(ValidationError):
            request.with_user(self.u_rd).write({'record_ids': [(0, 0, {'hours': 3, 'description': 'Visit 2'})]})
        action = request.with_user(self.u_rd).action_new_extension()
        with Form(Request.with_user(self.u_rd).with_context(action['context'])) as form:
            form.hours = 5
        extension = form.save()
        extension.action_submit()
        extension.with_user(self.u_ceo).action_approval_approve()
        self.assertEqual((request.hours_approved, request.hours_remaining), (15, 7))
        request.with_user(self.u_rd).write({'record_ids': [(0, 0, {'hours': 3, 'description': 'Visit 2'})]})
        self.assertEqual(request.hours_used, 11)

    # ------------------------------------------------------------------
    # Travel request and reimbursement
    # ------------------------------------------------------------------

    def _travel(self, project, user=None):
        user = user or self.u_sales
        travel = self.env['aiis.travel.request'].with_user(user).create({
            'project_id': project.id, 'destination': 'Riyadh', 'purpose': 'Site installation',
            'date_from': self.today, 'date_to': self.today + timedelta(days=3), 'budget': 5000,
        })
        travel.action_submit()
        return travel

    def test_travel_request_and_reimbursement(self):
        project = self._delivered()
        travel = self._travel(project)
        # salesperson -> sales head -> CEO
        self.assertEqual(travel.approval_request_id.line_ids.sorted('step').user_id, self.u_sales_head | self.u_ceo)
        self._approve_all(travel)
        self.assertEqual(travel.state, 'approved')
        employee = self.env['hr.employee'].search([('user_id', '=', self.u_sales.id)])
        product = self.env['product.product'].search([('can_be_expensed', '=', True)], limit=1) or \
            self.env['product.product'].create({'name': 'Travel', 'can_be_expensed': True, 'type': 'service'})
        expense = self.env['hr.expense'].with_user(self.u_sales).create({
            'name': 'Hotel', 'employee_id': employee.id, 'product_id': product.id, 'total_amount_currency': 1200,
        })
        sheet = self.env['hr.expense.sheet'].with_user(self.u_sales).create({
            'name': 'Riyadh trip', 'employee_id': employee.id, 'expense_line_ids': [(6, 0, expense.ids)],
            'aiis_travel_request_id': travel.id,
        })
        sheet.action_submit_sheet()
        self.assertEqual(sheet.state, 'submit')
        self.assertEqual(expense.analytic_distribution, {str(project.analytic_account_id.id): 100.0})
        with self.assertRaises(UserError):
            sheet.with_user(self.u_ceo).action_approve_expense_sheets()
        # the sales head sees and approves it although not an expense approver
        sheet.with_user(self.u_sales_head).action_approval_approve()
        sheet.with_user(self.u_ceo).action_approval_approve()
        self.assertEqual(sheet.state, 'approve')
        self.assertEqual(travel.amount_claimed, 1200)

    def test_reimbursement_rejected_back_to_draft(self):
        project = self._delivered()
        travel = self._travel(project)
        self._approve_all(travel)
        employee = self.env['hr.employee'].search([('user_id', '=', self.u_sales.id)])
        product = self.env['product.product'].create({'name': 'Taxi', 'can_be_expensed': True, 'type': 'service'})
        expense = self.env['hr.expense'].with_user(self.u_sales).create({
            'name': 'Taxi', 'employee_id': employee.id, 'product_id': product.id, 'total_amount_currency': 80,
        })
        sheet = self.env['hr.expense.sheet'].with_user(self.u_sales).create({
            'name': 'Taxi', 'employee_id': employee.id, 'expense_line_ids': [(6, 0, expense.ids)],
            'aiis_travel_request_id': travel.id,
        })
        sheet.action_submit_sheet()
        sheet.with_user(self.u_sales_head)._approval_reject('Receipt missing')
        self.assertEqual(sheet.state, 'draft')

    def test_travel_claim_on_request(self):
        """#65: the traveller claims on the travel request; one claim, over
        budget flagged, costs on the project."""
        project = self._delivered()
        travel = self._travel(project)
        self._approve_all(travel)
        self.assertEqual(travel.claim_stage, 'approved')
        product = self.env['product.product'].create({'name': 'Hotel', 'can_be_expensed': True, 'type': 'service'})
        travel.with_user(self.u_sales).write({'claim_line_ids': [
            (0, 0, {'name': 'Hotel', 'product_id': product.id, 'total_amount_currency': 4000}),
            (0, 0, {'name': 'Flight', 'product_id': product.id, 'total_amount_currency': 1500}),
        ]})
        # the CEO approved the trip and reads the lines
        self.assertEqual(sorted(travel.with_user(self.u_ceo).claim_line_ids.mapped('total_amount')), [1500, 4000])
        with self.assertRaisesRegex(UserError, 'receipts'):
            travel.with_user(self.u_sales).action_submit_claim()
        receipt = self.env['ir.attachment'].with_user(self.u_sales).create({
            'name': 'hotel.pdf', 'raw': b'hotel', 'res_model': travel._name, 'res_id': travel.id})
        travel.with_user(self.u_sales).write({'claim_receipt_ids': [(4, receipt.id)]})
        self.assertTrue(travel.claim_over)  # 5500 over the budget of 5000
        travel.with_user(self.u_sales).action_submit_claim()
        sheet = travel.claim_sheet_id
        self.assertEqual(sheet.aiis_travel_request_id, travel)
        self.assertEqual(sheet.state, 'submit')
        self.assertTrue(sheet.aiis_claim_over)
        self.assertEqual(travel.claim_stage, 'claimed')
        self.assertEqual(sheet.expense_line_ids.mapped('analytic_distribution'),
                         [{str(project.analytic_account_id.id): 100.0}] * 2)
        with self.assertRaises(ValidationError):
            self.env['hr.expense.sheet'].with_user(self.u_sales).create({
                'name': 'Again', 'employee_id': sheet.employee_id.id, 'aiis_travel_request_id': travel.id})
        self._approve_all(sheet)
        self.assertEqual(sheet.state, 'approve')
        self.assertEqual(travel.amount_claimed, 5500)
