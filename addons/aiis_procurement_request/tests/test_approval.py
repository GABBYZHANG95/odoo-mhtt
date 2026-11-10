from odoo import fields
from ..models.payment_plan import _LEGACY_PAYMENT_FLOW
from odoo.tools.safe_eval import safe_eval
from odoo.exceptions import AccessError, UserError
from odoo.tests import new_test_user, tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestApproval(TransactionCase):
    """PR and PO approval through the approval engine (design doc 2.2-2.4).

    Organisation used here:

        Chairman
          CEO
            Sales head ── Team lead ── Sales staff
            R&D head (no login) ── R&D staff
        Loner (no manager)
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env(context=dict(cls.env.context, tracking_disable=True, default_aiis_payment_managed=False,
                                   aiis_legacy_payment_flow=_LEGACY_PAYMENT_FLOW))
        cls.env = env
        # This fixture starts with all purchase users eligible, regardless of
        # the procurement department configured in the database being tested.
        env.company.aiis_procurement_department_id = False

        def user(login, groups='base.group_user'):
            return new_test_user(env, login=login, groups=groups)

        cls.u_chairman = user('t_chairman', 'base.group_user,aiis_procurement_request.group_po_approver_chairman')
        cls.u_ceo = user('t_ceo', 'base.group_user,aiis_procurement_request.group_po_approver_ceo')
        cls.u_sales_head = user('t_sales_head')
        cls.u_lead = user('t_lead')
        cls.u_staff = user('t_staff')
        cls.u_rd_staff = user('t_rd_staff')
        cls.u_loner = user('t_loner')
        cls.u_buyer = user('t_buyer', 'base.group_user,purchase.group_purchase_user')
        cls.u_cfo = user('t_default_cfo', 'base.group_user,account.group_account_invoice')

        Employee = env['hr.employee']
        cls.e_chairman = Employee.create({'name': 'Chairman', 'user_id': cls.u_chairman.id})
        cls.e_ceo = Employee.create({'name': 'CEO', 'user_id': cls.u_ceo.id, 'parent_id': cls.e_chairman.id})
        cls.e_sales_head = Employee.create({'name': 'Sales Head', 'user_id': cls.u_sales_head.id, 'parent_id': cls.e_ceo.id})
        cls.e_lead = Employee.create({'name': 'Team Lead', 'user_id': cls.u_lead.id, 'parent_id': cls.e_sales_head.id})
        cls.e_staff = Employee.create({'name': 'Sales Staff', 'user_id': cls.u_staff.id, 'parent_id': cls.e_lead.id})
        cls.e_rd_head = Employee.create({'name': 'R&D Head', 'parent_id': cls.e_ceo.id})
        cls.e_rd_staff = Employee.create({'name': 'R&D Staff', 'user_id': cls.u_rd_staff.id, 'parent_id': cls.e_rd_head.id})
        cls.e_loner = Employee.create({'name': 'Loner', 'user_id': cls.u_loner.id})
        Employee.create({'name': 'Buyer', 'user_id': cls.u_buyer.id, 'parent_id': cls.e_ceo.id})

        Department = env['hr.department']
        Department.create({'name': 'T Chairman', 'manager_id': cls.e_chairman.id})
        Department.create({'name': 'T CEO', 'manager_id': cls.e_ceo.id})
        Department.create({'name': 'T Sales', 'manager_id': cls.e_sales_head.id})
        Department.create({'name': 'T R&D', 'manager_id': cls.e_rd_head.id})

        env.ref('aiis_approval.position_ceo').user_id = cls.u_ceo
        env.ref('aiis_approval.position_chairman').user_id = cls.u_chairman
        env.ref('aiis_approval.position_cfo').user_id = cls.u_cfo

        cls.vendor = env['res.partner'].create({'name': 'T Vendor'})
        cls.product = env['product.product'].create({'name': 'T Server', 'purchase_ok': True, 'type': 'service'})

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _pr(self, user, employee):
        pr = self.env['procurement.request'].with_user(user).create({
            'employee_id': employee.id,
            'line_ids': [(0, 0, {'description': 'Laptop', 'quantity': 1})],
        })
        pr.action_submit()
        return pr

    def _approvers(self, record, states=('waiting', 'pending')):
        lines = record.approval_request_id.line_ids.filtered(lambda l: l.state in states).sorted('step')
        return lines.mapped('user_id')

    def _po(self, amount):
        po = self.env['purchase.order'].with_user(self.u_buyer).create({
            'partner_id': self.vendor.id,
            'currency_id': self.env.company.currency_id.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id, 'product_qty': 1, 'price_unit': amount, 'taxes_id': [(6, 0, [])],
            })],
        })
        po.button_confirm()
        return po

    # ------------------------------------------------------------------
    # Reporting line (PR, up to the department head)
    # ------------------------------------------------------------------

    def test_staff_every_level_up_to_department_head(self):
        pr = self._pr(self.u_staff, self.e_staff)
        self.assertEqual(pr.state, 'submitted')
        self.assertEqual(self._approvers(pr), self.u_lead | self.u_sales_head)
        self.assertEqual(pr.approval_pending_names, self.u_lead.name)
        pr.with_user(self.u_lead).action_approval_approve()
        self.assertEqual(self._approvers(pr, ('pending',)), self.u_sales_head)
        pr.with_user(self.u_sales_head).action_approval_approve()
        self.assertEqual(pr.state, 'approved')
        self.assertEqual(pr.aiis_approval_state, 'approved')

    def test_po_wizard_rejects_unapproved_request(self):
        pr = self._pr(self.u_staff, self.e_staff)
        wizard = self.env['procurement.request.create.po.wizard'].with_user(self.u_buyer).create({
            'request_id': pr.id, 'partner_id': self.vendor.id})
        for state in ('draft', 'submitted', 'approved'):
            # A state flag alone is insufficient while approval is pending.
            pr.sudo().state = state
            with self.assertRaises(UserError):
                wizard.action_confirm()
            self.assertFalse(pr.purchase_order_id)
        # Neither forged status flags nor another PR's approval are proof.
        pr.sudo().write({'state': 'approved', 'aiis_approval_state': 'approved'})
        with self.assertRaises(UserError):
            wizard.action_confirm()
        other = self._pr(self.u_chairman, self.e_chairman)
        pr.sudo().approval_request_id = other.approval_request_id
        with self.assertRaises(UserError):
            wizard.action_confirm()

    def test_conversion_completes_team_tasks_and_cannot_be_repeated(self):
        pr = self._pr(self.u_staff, self.e_staff)
        pr.with_user(self.u_lead).action_approval_approve()
        pr.with_user(self.u_sales_head).action_approval_approve()
        activity_type = self.env.ref('aiis_procurement_request.mail_activity_procurement')
        tasks = pr.sudo().activity_ids.filtered(lambda a: a.activity_type_id == activity_type)
        self.assertIn(self.u_buyer, tasks.user_id)
        pr._schedule_procurement_activities()
        self.assertEqual(pr.sudo().activity_ids.filtered(lambda a: a.activity_type_id == activity_type), tasks)
        Wizard = self.env['procurement.request.create.po.wizard'].with_user(self.u_buyer)
        first = Wizard.create({'request_id': pr.id, 'partner_id': self.vendor.id})
        stale = Wizard.create({'request_id': pr.id, 'partner_id': self.vendor.id})
        first.action_confirm()
        order = pr.purchase_order_id
        self.assertTrue(order)
        self.assertFalse(pr.sudo().activity_ids.filtered(lambda a: a.activity_type_id == activity_type))
        with self.assertRaises(UserError):
            stale.action_confirm()
        self.assertEqual(pr.purchase_order_id, order)

    def test_department_head_own_request_goes_to_ceo(self):
        pr = self._pr(self.u_sales_head, self.e_sales_head)
        self.assertEqual(self._approvers(pr), self.u_ceo)

    def test_ceo_own_request_goes_to_chairman(self):
        pr = self._pr(self.u_ceo, self.e_ceo)
        self.assertEqual(self._approvers(pr), self.u_chairman)

    def test_chairman_needs_no_approval(self):
        pr = self._pr(self.u_chairman, self.e_chairman)
        self.assertEqual(pr.state, 'approved')
        self.assertFalse(pr.approval_request_id.line_ids)

    def test_manager_without_login_is_skipped(self):
        pr = self._pr(self.u_rd_staff, self.e_rd_staff)
        self.assertEqual(self._approvers(pr), self.u_ceo)

    def test_no_manager_goes_to_ceo(self):
        pr = self._pr(self.u_loner, self.e_loner)
        self.assertEqual(self._approvers(pr), self.u_ceo)

    def test_list_is_frozen_at_submission(self):
        pr = self._pr(self.u_staff, self.e_staff)
        self.e_staff.parent_id = self.e_sales_head
        self.assertEqual(self._approvers(pr), self.u_lead | self.u_sales_head)

    def test_only_the_current_approver_can_approve(self):
        pr = self._pr(self.u_staff, self.e_staff)
        with self.assertRaises(UserError):
            pr.with_user(self.u_sales_head).action_approval_approve()
        with self.assertRaises(UserError):
            pr.with_user(self.u_ceo).action_approval_approve()

    def test_reject_needs_reason_and_returns_to_draft(self):
        pr = self._pr(self.u_staff, self.e_staff)
        with self.assertRaises(UserError):
            pr.with_user(self.u_lead)._approval_reject('  ')
        first = pr.approval_request_id
        pr.with_user(self.u_lead)._approval_reject('Too expensive')
        self.assertEqual(pr.state, 'draft')
        self.assertEqual(pr.aiis_approval_state, 'rejected')
        self.assertEqual(first.state, 'rejected')
        self.assertEqual(first.line_ids.filtered(lambda l: l.user_id == self.u_sales_head).state, 'cancelled')
        # submitting again builds a new list
        pr.with_user(self.u_staff).action_submit()
        self.assertNotEqual(pr.approval_request_id, first)
        self.assertEqual(self._approvers(pr, ('pending',)), self.u_lead)

    def test_withdraw(self):
        pr = self._pr(self.u_staff, self.e_staff)
        with self.assertRaises(UserError):
            pr.with_user(self.u_lead).action_approval_withdraw()
        pr.with_user(self.u_staff).action_approval_withdraw()
        self.assertEqual(pr.state, 'draft')
        self.assertEqual(pr.approval_request_id.state, 'withdrawn')

    def test_same_person_approves_once(self):
        self.env['aiis.approval.template.node'].create({
            'template_id': self.env.ref('aiis_procurement_request.approval_template_procurement_request').id,
            'sequence': 20, 'node_type': 'position', 'position_id': self.env.ref('aiis_approval.position_ceo').id,
        })
        pr = self._pr(self.u_sales_head, self.e_sales_head)
        lines = pr.approval_request_id.line_ids
        self.assertEqual(lines.mapped('state'), ['pending', 'auto'])
        pr.with_user(self.u_ceo).action_approval_approve()
        self.assertEqual(pr.state, 'approved')

    def test_my_approvals_and_visibility(self):
        pr = self._pr(self.u_staff, self.e_staff)
        mine = self.env['aiis.approval.line'].with_user(self.u_lead).search(
            [('user_id', '=', self.u_lead.id), ('state', '=', 'pending')])
        self.assertEqual(mine.res_id, pr.id)
        # the department head two levels up can open the request
        self.assertTrue(self.env['procurement.request'].with_user(self.u_sales_head).search([('id', '=', pr.id)]))
        self.assertTrue(self.env['procurement.request'].with_user(self.u_lead).search([('is_my_approval', '=', True)]))
        self.assertFalse(self.env['procurement.request'].with_user(self.u_loner).search([('id', '=', pr.id)]))

    def test_reassign(self):
        pr = self._pr(self.u_staff, self.e_staff)
        line = pr.approval_request_id.line_ids.filtered(lambda l: l.state == 'pending')
        line._reassign(self.u_loner)
        self.assertEqual(line.state, 'reassigned')
        pr.with_user(self.u_loner).action_approval_approve()
        self.assertEqual(self._approvers(pr, ('pending',)), self.u_sales_head)

    # ------------------------------------------------------------------
    # Purchase orders (CEO, Chairman above 300,000 SAR)
    # ------------------------------------------------------------------

    def test_po_ceo_only(self):
        po = self._po(1000)
        self.assertEqual(po.state, 'to approve')
        self.assertEqual(self._approvers(po), self.u_ceo)
        with self.assertRaises(UserError):
            po.with_user(self.u_chairman).action_approval_approve()
        po.with_user(self.u_ceo).action_approval_approve()
        self.assertIn(po.state, ('purchase', 'done'))

    def test_po_exactly_at_threshold_needs_ceo_only(self):
        po = self._po(300000)
        self.assertEqual(self._approvers(po), self.u_ceo)

    def test_po_above_threshold_ceo_then_chairman(self):
        po = self._po(300001)
        self.assertEqual(self._approvers(po), self.u_ceo | self.u_chairman)
        po.with_user(self.u_ceo).action_approval_approve()
        self.assertEqual(po.state, 'to approve')
        self.assertEqual(self._approvers(po, ('pending',)), self.u_chairman)
        po.with_user(self.u_chairman).action_approval_approve()
        self.assertIn(po.state, ('purchase', 'done'))

    def test_po_change_during_approval_restarts(self):
        po = self._po(400000)
        po.with_user(self.u_ceo).action_approval_approve()
        first = po.approval_request_id
        po.with_user(self.u_buyer).write({'order_line': [(1, po.order_line.id, {'price_unit': 410000})]})
        self.assertEqual(first.state, 'cancelled')
        self.assertNotEqual(po.approval_request_id, first)
        self.assertEqual(self._approvers(po, ('pending',)), self.u_ceo)

    def test_po_reject_returns_to_draft(self):
        po = self._po(1000)
        po.with_user(self.u_ceo)._approval_reject('Wrong vendor')
        self.assertEqual(po.state, 'draft')
        po.with_user(self.u_buyer).button_confirm()
        self.assertEqual(po.state, 'to approve')
        self.assertEqual(self._approvers(po, ('pending',)), self.u_ceo)

    def test_purchase_manager_cannot_bypass(self):
        self.u_buyer.groups_id |= self.env.ref('purchase.group_purchase_manager')
        po = self._po(1000)
        self.assertEqual(po.state, 'to approve')
        po.with_user(self.u_buyer).button_approve()
        self.assertEqual(po.state, 'to approve')

    # ------------------------------------------------------------------
    # CFO payment before receipt (design doc 3.2 step 6, #41)
    # ------------------------------------------------------------------

    def test_po_approved_prepares_bill_for_cfo(self):
        cfo = new_test_user(self.env, login='t_cfo', groups='base.group_user,account.group_account_invoice')
        self.env.ref('aiis_approval.position_cfo').user_id = cfo
        po = self._po(1000)
        po.with_user(self.u_ceo).action_approval_approve()
        bill = po.order_line.invoice_lines.move_id
        self.assertEqual(len(bill), 1)
        self.assertEqual(bill.state, 'draft')
        self.assertEqual(bill.invoice_line_ids.quantity, 1)
        self.assertEqual(po.aiis_payment_status, 'to_pay')
        self.assertTrue(bill.activity_ids.filtered(lambda a: a.user_id == cfo))
        # To do includes existing draft bills and retains them until paid.
        def bill_tasks():
            return self.env['aiis.todo'].with_user(cfo).search([
                ('kind', '=', 'bill'), ('res_model', '=', 'account.move'), ('res_id', '=', bill.id)])
        self.assertEqual(len(bill_tasks()), 1)
        task = bill_tasks()
        self.assertEqual(task.action_open_document()['res_id'], bill.id)
        self.assertFalse(self.env['aiis.todo'].with_user(self.u_staff).search([('id', '=', task.id)]))
        with self.assertRaises(AccessError):
            task.with_user(self.u_staff).action_open_document()
        # the CFO confirms the bill and pays it
        bill = bill.with_user(cfo)
        bill.invoice_date = fields.Date.today()
        bill.action_post()
        self.env['account.payment.register'].with_user(cfo).with_context(
            active_model='account.move', active_ids=bill.ids).create({'amount': 400})._create_payments()
        self.assertEqual(po.aiis_payment_status, 'partial')
        self.assertEqual(len(bill_tasks()), 1)
        self.env['account.payment.register'].with_user(cfo).with_context(
            active_model='account.move', active_ids=bill.ids).create({})._create_payments()
        self.assertEqual(po.aiis_payment_status, 'paid')
        self.assertFalse(bill_tasks())
        receipt_domain = [('kind', '=', 'receipt'), ('res_id', '=', po.id)]
        self.assertEqual(len(self.env['aiis.todo'].with_user(self.u_buyer).search(receipt_domain)), 1)
        po.with_user(self.u_buyer).action_mark_received()
        self.assertFalse(self.env['aiis.todo'].with_user(self.u_buyer).search(receipt_domain))
        self.assertFalse(bill.activity_ids.filtered(
            lambda a: a.activity_type_id == self.env.ref('aiis_procurement_request.mail_activity_pay_bill')))
        self.assertFalse(po.activity_ids.filtered(lambda a: a.user_id == self.u_buyer and 'shipment' in (a.summary or '')))

    def test_only_procurement_department_converts(self):
        """#48: the CEO has purchase rights but is not a buyer"""
        self.u_ceo.groups_id = [(4, self.env.ref('purchase.group_purchase_user').id)]
        pr = self._pr(self.u_staff, self.e_staff)
        pr.with_user(self.u_lead).action_approval_approve()
        pr.with_user(self.u_sales_head).action_approval_approve()
        # no procurement department yet: every purchase user, the CEO too
        self.assertIn(self.u_ceo, pr.sudo().activity_ids.user_id)
        buyer_group = self.env.ref('aiis_procurement_request.group_procurement_buyer')
        self.assertIn(self.u_ceo, buyer_group.users)
        department = self.env['hr.department'].create({'name': 'T Procurement'})
        self.u_buyer.employee_id.department_id = department
        self.env.company.aiis_procurement_department_id = department
        self.assertEqual(buyer_group.users.filtered(lambda u: u.login.startswith('t_')), self.u_buyer,
                         'only the procurement department converts requests')
        pr._schedule_procurement_activities()  # what the upgrade does
        activity_type = self.env.ref('aiis_procurement_request.mail_activity_procurement')
        tasks = pr.sudo().activity_ids.filtered(lambda a: a.activity_type_id == activity_type)
        self.assertEqual(tasks.user_id, self.u_buyer)
        self.assertTrue(pr.with_user(self.u_buyer).can_create_po)
        self.assertFalse(pr.with_user(self.u_ceo).can_create_po)
        with self.assertRaises(UserError):
            pr.with_user(self.u_ceo).action_create_po()

    def test_requester_reference_price(self):
        """#49: the requester may give a reference price; it starts the PO price"""
        pr = self.env['procurement.request'].with_user(self.u_staff).create({
            'employee_id': self.e_staff.id,
            'line_ids': [(0, 0, {'description': 'AI tool, 3 months', 'quantity': 3, 'estimated_unit_price': 400})],
        })
        self.assertEqual(pr.with_user(self.u_staff).estimated_total, 1200)
        pr.action_submit()
        self.assertEqual(pr.with_user(self.u_lead).estimated_total, 1200, 'the approver sees it')
        pr.with_user(self.u_lead).action_approval_approve()
        pr.with_user(self.u_sales_head).action_approval_approve()
        self.env['procurement.request.create.po.wizard'].with_user(self.u_buyer).create(
            {'request_id': pr.id, 'partner_id': self.vendor.id}).action_confirm()
        self.assertEqual(pr.sudo().purchase_order_id.order_line.price_unit, 400)

    def _rejection_tasks(self, record):
        activity_type = self.env.ref('aiis_approval.mail_activity_rejected')
        return record.sudo().activity_ids.filtered(lambda a: a.activity_type_id == activity_type)

    def test_rejection_gives_the_submitter_a_task(self):
        """#55: the submitter learns why, and resubmitting closes the task"""
        pr = self._pr(self.u_staff, self.e_staff)
        pr.with_user(self.u_lead)._approval_reject('Get a cheaper model')
        task = self._rejection_tasks(pr)
        self.assertEqual(task.user_id, self.u_staff)
        self.assertIn('Get a cheaper model', task.note)
        pr.with_user(self.u_staff).action_submit()
        self.assertFalse(self._rejection_tasks(pr))

    def test_rejected_po_back_to_procurement_and_requester_told(self):
        """#54: the PO goes back to procurement, the PR says why"""
        pr = self._pr(self.u_staff, self.e_staff)
        pr.with_user(self.u_lead).action_approval_approve()
        pr.with_user(self.u_sales_head).action_approval_approve()
        self.env['procurement.request.create.po.wizard'].with_user(self.u_buyer).create(
            {'request_id': pr.id, 'partner_id': self.vendor.id}).action_confirm()
        po = pr.sudo().purchase_order_id
        po.order_line.price_unit = 900
        po.with_user(self.u_buyer).button_confirm()
        po.with_user(self.u_ceo)._approval_reject('Order half a year only')
        self.assertEqual(po.state, 'draft')
        self.assertEqual(self._rejection_tasks(po).user_id, self.u_buyer)
        message = pr.sudo().message_ids.filtered(lambda m: 'Order half a year only' in (m.body or ''))
        self.assertIn(self.u_staff.partner_id, message.partner_ids)
        po.with_user(self.u_buyer).button_confirm()
        self.assertFalse(self._rejection_tasks(po))

    def test_returned_to_me(self):
        """#56: a rejection is on the submitter's Returned to Me list until
        it is submitted again or cancelled"""
        def returned(user):
            return self.env['aiis.approval.request'].with_user(user).search(
                [('submitter_id', '=', user.id), ('returned', '=', True)])
        pr = self._pr(self.u_staff, self.e_staff)
        pr.with_user(self.u_lead)._approval_reject('Too expensive')
        self.assertEqual(returned(self.u_staff).mapped('res_id'), pr.ids)
        self.assertEqual(returned(self.u_staff).rejection_reason, 'Too expensive')
        self.assertIn(self.u_lead.name, pr.with_user(self.u_staff).approval_rejection_note)
        pr.with_user(self.u_staff).action_submit()
        self.assertFalse(returned(self.u_staff))
        pr.with_user(self.u_lead).action_approval_approve()
        pr.with_user(self.u_sales_head).action_approval_approve()
        self.env['procurement.request.create.po.wizard'].with_user(self.u_buyer).create(
            {'request_id': pr.id, 'partner_id': self.vendor.id}).action_confirm()
        po = pr.sudo().purchase_order_id
        po.order_line.price_unit = 900
        po.with_user(self.u_buyer).button_confirm()
        po.with_user(self.u_ceo)._approval_reject('Order half a year only')
        self.assertEqual(returned(self.u_buyer).mapped('res_id'), po.ids)
        po.with_user(self.u_buyer).button_cancel()
        self.assertFalse(returned(self.u_buyer))
        self.assertFalse(self._rejection_tasks(po))

    def _todos(self, user, document, kind):
        return self.env['aiis.todo'].with_user(user).search([
            ('res_model', '=', document._name), ('res_id', '=', document.id), ('kind', '=', kind)])

    def test_todo_approval_revision_and_conversion(self):
        pr = self._pr(self.u_staff, self.e_staff)
        self.assertEqual(len(self._todos(self.u_lead, pr, 'approval')), 1)
        self.assertFalse(self._todos(self.u_sales_head, pr, 'approval'))
        task = self._todos(self.u_lead, pr, 'approval')
        self.assertEqual(task.document_name, pr.name)
        self.assertIn(task, self.env['aiis.todo'].with_user(self.u_lead).search([('document_name', 'ilike', pr.name)]))
        self.assertNotIn(task, self.env['aiis.todo'].with_user(self.u_lead).search(
            [('document_name', 'ilike', 'no such document')]), 'the name filter is applied')
        self.assertEqual(task.action_open_document()['res_id'], pr.id)
        pr.with_user(self.u_lead)._approval_reject('Change specifications')
        self.assertFalse(self._todos(self.u_lead, pr, 'approval'))
        revision = self._todos(self.u_staff, pr, 'returned')
        self.assertEqual(len(revision), 1)
        self.assertEqual(revision.note, 'Change specifications')
        pr.with_user(self.u_staff).action_submit()
        self.assertFalse(self._todos(self.u_staff, pr, 'returned'))
        pr.with_user(self.u_lead).action_approval_approve()
        self.assertFalse(self._todos(self.u_lead, pr, 'approval'))
        self.assertEqual(len(self._todos(self.u_sales_head, pr, 'approval')), 1)
        pr.with_user(self.u_sales_head).action_approval_approve()
        self.assertEqual(len(self._todos(self.u_buyer, pr, 'convert')), 1)
        self.env['procurement.request.create.po.wizard'].with_user(self.u_buyer).create(
            {'request_id': pr.id, 'partner_id': self.vendor.id}).action_confirm()
        self.assertFalse(self._todos(self.u_buyer, pr, 'convert'))

    def test_todo_reassignment_and_withdrawal(self):
        pr = self._pr(self.u_staff, self.e_staff)
        line = pr.approval_request_id.line_ids.filtered(lambda l: l.state == 'pending')
        line._reassign(self.u_loner)
        self.assertFalse(self._todos(self.u_lead, pr, 'approval'))
        self.assertEqual(len(self._todos(self.u_loner, pr, 'approval')), 1)
        pr.with_user(self.u_staff).action_approval_withdraw()
        self.assertFalse(self._todos(self.u_loner, pr, 'approval'))

    def test_todo_ignores_reminders_and_checks_document_access(self):
        pr = self._pr(self.u_staff, self.e_staff)
        pr.sudo().activity_schedule('mail.mail_activity_data_todo', user_id=self.u_loner.id,
                                    summary='Read this later')
        self.assertFalse(self.env['aiis.todo'].with_user(self.u_loner).search([]))
        task = self._todos(self.u_lead, pr, 'approval')
        # An assigned task must not grant read access to another user's row.
        with self.assertRaises(AccessError):
            task.with_user(self.u_loner).read(['res_model', 'res_id', 'document_name'])
        root = self.env.ref('aiis_approval.menu_aiis_my_approvals')
        self.assertEqual(root.action, self.env.ref('aiis_approval.action_aiis_todo'))
        self.assertFalse(self.env['ir.ui.menu'].search([('parent_id', '=', root.id)]))

    def test_todo_deleted_documents_leave_no_tasks(self):
        pr = self._pr(self.u_staff, self.e_staff)
        request = pr.approval_request_id
        self.assertTrue(self._todos(self.u_lead, pr, 'approval'))
        pr.sudo().unlink()
        self.assertEqual(request.state, 'cancelled')
        self.assertFalse(self.env['aiis.todo'].with_user(self.u_lead).search([
            ('res_model', '=', 'procurement.request'), ('res_id', '=', pr.id)]))
        returned = self._pr(self.u_staff, self.e_staff)
        returned.with_user(self.u_lead)._approval_reject('Revise this')
        request = returned.approval_request_id
        self.assertTrue(self._todos(self.u_staff, returned, 'returned'))
        returned.sudo().unlink()
        self.assertFalse(request.returned)
        self.assertEqual(request.state, 'cancelled')

    def test_finance_requires_configured_cfo_without_fallback(self):
        self.env.ref('aiis_approval.position_cfo').user_id = False
        po = self._po(1000)
        with self.assertRaisesRegex(UserError, 'Configure an active internal user as CFO'), self.cr.savepoint():
            po.with_user(self.u_ceo).action_approval_approve()
        self.assertEqual(po.state, 'to approve')
        self.assertFalse(po.order_line.invoice_lines.move_id)
        self.assertEqual(self._approvers(po, ('pending',)), self.u_ceo)

    def test_finance_rejects_archived_cfo(self):
        po = self._po(1000)
        self.u_cfo.active = False
        with self.assertRaisesRegex(UserError, 'Configure an active internal user as CFO'), self.cr.savepoint():
            po.with_user(self.u_ceo).action_approval_approve()
        self.assertEqual(po.state, 'to approve')
        self.assertFalse(po.order_line.invoice_lines.move_id)

    def test_finance_wrong_assignee_and_cfo_replacement(self):
        # Accounting access alone does not make the CEO a finance task owner.
        self.u_ceo.groups_id = [(4, self.env.ref('account.group_account_invoice').id)]
        po = self._po(1000)
        po.with_user(self.u_ceo).action_approval_approve()
        bill = po.order_line.invoice_lines.move_id
        activity_type = self.env.ref('aiis_procurement_request.mail_activity_pay_bill')
        tasks = bill.activity_ids.filtered(lambda a: a.activity_type_id == activity_type)
        tasks.write({'user_id': self.u_ceo.id})
        self.assertFalse(self._todos(self.u_ceo, bill, 'bill'))
        self.env.ref('aiis_approval.position_cfo')._sync_finance_tasks()
        self.assertEqual(tasks.user_id, self.u_cfo)
        self.assertEqual(len(self._todos(self.u_cfo, bill, 'bill')), 1)
        replacement = new_test_user(self.env, login='t_replacement_cfo',
                                    groups='base.group_user,account.group_account_invoice')
        self.env.ref('aiis_approval.position_cfo').user_id = replacement
        self.assertEqual(tasks.user_id, replacement)
        self.assertFalse(self._todos(self.u_cfo, bill, 'bill'))
        self.assertEqual(len(self._todos(replacement, bill, 'bill')), 1)
        self.assertFalse(self._todos(self.u_ceo, bill, 'bill'))

    def test_legacy_bill_link_opens_personal_inbox(self):
        action = self.env.ref('aiis_procurement_request.action_vendor_bills_to_process')
        self.assertEqual(action.res_model, 'aiis.todo')
        self.assertEqual(action.view_id, self.env.ref('aiis_approval.view_aiis_todo_tree'))
        domain = safe_eval(action.domain, {'uid': self.u_ceo.id})
        self.assertIn(('user_id', '=', self.u_ceo.id), domain)
        tasks = self.env[action.res_model].with_user(self.u_ceo).search(domain)
        self.assertFalse(tasks.filtered(lambda t: t.kind in ('bill', 'expense')))
        self.assertTrue(all(t.user_id == self.u_ceo for t in tasks))

    def _approved_purchase_bill(self, amount=1000):
        po = self._po(amount)
        po.with_user(self.u_ceo).action_approval_approve()
        return po, po.order_line.invoice_lines.move_id.with_user(self.u_cfo)

    def test_bill_lines_split_only_for_assets_per_unit(self):
        """One bill line per PO line: a second line for the same PO line is
        refused unless it is a fixed asset tracked per unit (aiis_finance)."""
        po, bill = self._approved_purchase_bill()
        with self.assertRaises(UserError), self.cr.savepoint():
            line = bill.invoice_line_ids
            line.with_context(check_move_validity=False, aiis_initial_bill=True).write({'quantity': line.quantity / 2})
            line.with_context(check_move_validity=False, aiis_initial_bill=True).copy({'move_id': bill.id})
            bill._check_aiis_purchase_terms()

    def test_purchase_bill_commercial_fields_locked(self):
        po, bill = self._approved_purchase_bill()
        line = bill.invoice_line_ids
        tax = self.env['account.tax'].create({'name': 'Bill guard tax', 'amount': 5, 'type_tax_use': 'purchase'})
        other_product = self.env['product.product'].create({'name': 'Other service', 'type': 'service'})
        for vals in [
            {'price_unit': 1500}, {'quantity': 2}, {'discount': 10},
            {'tax_ids': [(6, 0, tax.ids)]}, {'purchase_line_id': False},
            {'product_id': other_product.id}, {'display_type': 'line_note'},
            {'product_uom_id': self.env.ref('uom.product_uom_dozen').id},
        ]:
            with self.subTest(vals=vals), self.assertRaises(UserError), self.cr.savepoint():
                line.with_context(aiis_initial_bill=True).write(vals)
        other_vendor = self.env['res.partner'].create({'name': 'Other vendor'})
        for vals in [{'partner_id': other_vendor.id}, {'aiis_purchase_order_id': False},
                     {'currency_id': self.env.ref('base.EUR').id}, {'move_type': 'in_refund'},
                     {'tax_totals': {}}]:
            with self.subTest(vals=vals), self.assertRaises(UserError), self.cr.savepoint():
                bill.write(vals)
        with self.assertRaises(UserError), self.cr.savepoint():
            line.unlink()
        with self.assertRaises(UserError), self.cr.savepoint():
            bill.write({'invoice_line_ids': [(0, 0, {'name': 'Extra charge', 'quantity': 1, 'price_unit': 50})]})
        with self.assertRaises(UserError), self.cr.savepoint():
            bill.unlink()
        self.assertEqual(line.price_unit, 1000)
        self.assertEqual(bill.aiis_purchase_order_id, po)

    def test_purchase_bill_financial_fields_and_partial_payment(self):
        po, bill = self._approved_purchase_bill()
        bill.write({'invoice_date': fields.Date.today(), 'date': fields.Date.today(), 'ref': 'Supplier invoice 001'})
        accounts = self.env['account.account'].search([
            ('company_id', '=', self.env.company.id), ('account_type', '=', 'expense'),
            ('id', '!=', bill.invoice_line_ids.account_id.id)], limit=1)
        if accounts:
            bill.invoice_line_ids.write({'account_id': accounts.id})
        bill.action_post()
        for amount in [400, 600]:
            self.env['account.payment.register'].with_user(self.u_cfo).with_context(
                active_model='account.move', active_ids=bill.ids).create({'amount': amount})._create_payments()
        self.assertEqual(po.aiis_payment_status, 'paid')
        self.assertEqual(bill.ref, 'Supplier invoice 001')

    def test_purchase_bill_posting_and_payment_recheck_imported_difference(self):
        po, bill = self._approved_purchase_bill()
        line = bill.invoice_line_ids
        self.env.flush_all()
        # Simulate inconsistent historical/imported data below ORM guards.
        self.cr.execute('UPDATE account_move_line SET price_unit=1500 WHERE id=%s', [line.id])
        self.env.invalidate_all()
        with self.assertRaises(UserError), self.cr.savepoint():
            bill.action_post()
        self.cr.execute('UPDATE account_move_line SET price_unit=1000 WHERE id=%s', [line.id])
        self.env.invalidate_all()
        bill.invoice_date = fields.Date.today()
        bill.action_post()
        wizard = self.env['account.payment.register'].with_user(self.u_cfo).with_context(
            active_model='account.move', active_ids=bill.ids).create({})
        self.env.flush_all()
        self.cr.execute('UPDATE account_move_line SET quantity=2 WHERE id=%s', [line.id])
        self.env.invalidate_all()
        with self.assertRaises(UserError), self.cr.savepoint():
            bill.action_register_payment()
        with self.assertRaises(UserError), self.cr.savepoint():
            wizard._create_payments()
        self.assertEqual(po.aiis_payment_status, 'to_pay')

    def test_purchase_bill_return_reapprove_and_replace(self):
        po, old_bill = self._approved_purchase_bill()
        with self.assertRaises(UserError), self.cr.savepoint():
            self.env['aiis.purchase.bill.return'].with_user(self.u_cfo).create(
                {'bill_id': old_bill.id, 'reason': '  '}).action_return()
        self.env['aiis.purchase.bill.return'].with_user(self.u_cfo).create({
            'bill_id': old_bill.id, 'reason': 'Supplier quoted a revised price',
        }).action_return()
        self.assertEqual(old_bill.state, 'cancel')
        self.assertEqual(po.state, 'draft')
        self.assertFalse(po.aiis_approved_terms)
        self.assertFalse(self._todos(self.u_cfo, old_bill, 'bill'))
        self.assertEqual(len(self._todos(self.u_buyer, po, 'purchase_revision')), 1)
        with self.assertRaises(UserError), self.cr.savepoint():
            old_bill.button_draft()
        po.with_user(self.u_buyer).order_line.price_unit = 300001
        po.with_user(self.u_buyer).button_confirm()
        po.with_user(self.u_ceo).action_approval_approve()
        self.assertEqual(po.state, 'to approve')
        self.assertFalse(po.order_line.invoice_lines.move_id.filtered(lambda m: m.state != 'cancel'))
        po.with_user(self.u_chairman).action_approval_approve()
        new_bill = po.order_line.invoice_lines.move_id.filtered(lambda m: m.state != 'cancel')
        self.assertEqual(len(new_bill), 1)
        self.assertNotEqual(old_bill, new_bill)
        self.assertEqual(new_bill.amount_total, 300001)
        self.assertEqual(len(self._todos(self.u_cfo, new_bill, 'bill')), 1)
        self.assertFalse(self._todos(self.u_buyer, po, 'purchase_revision'))
        new_bill.with_user(self.u_cfo).invoice_date = fields.Date.today()
        new_bill.with_user(self.u_cfo).action_post()

    def test_purchase_bill_posted_cannot_return_or_reset_po(self):
        po, bill = self._approved_purchase_bill()
        bill.invoice_date = fields.Date.today()
        bill.action_post()
        with self.assertRaises(UserError), self.cr.savepoint():
            bill.action_return_to_procurement()
        with self.assertRaises(UserError), self.cr.savepoint():
            po.with_user(self.u_buyer).button_draft()
        with self.assertRaises(UserError), self.cr.savepoint():
            self.env['aiis.purchase.bill.return'].with_user(self.u_ceo).create({
                'bill_id': bill.id, 'reason': 'Unapproved return'}).action_return()
        self.assertEqual(po.state, 'purchase')
        self.assertEqual(bill.state, 'posted')

    def test_approved_po_terms_and_snapshot_cannot_be_forged(self):
        po, bill = self._approved_purchase_bill()
        for vals in [{'price_unit': 1500}, {'product_qty': 2}, {'taxes_id': [(5, 0, 0)]}]:
            with self.subTest(vals=vals), self.assertRaises(UserError), self.cr.savepoint():
                po.with_user(self.u_buyer).order_line.write(vals)
        with self.assertRaises(UserError), self.cr.savepoint():
            po.with_user(self.u_buyer).write({'currency_id': self.env.ref('base.EUR').id})
        with self.assertRaises(UserError), self.cr.savepoint():
            po.write({'aiis_approved_terms': {}})
        # Changing state alone does not unlock the approved commercial terms.
        po.write({'state': 'draft'})
        with self.assertRaises(UserError), self.cr.savepoint():
            po.order_line.price_unit = 1500
        with self.assertRaises(UserError), self.cr.savepoint():
            bill._check_aiis_purchase_terms()

    def test_pending_po_direct_line_change_restarts_approval(self):
        po = self._po(1000)
        previous = po.approval_request_id
        po.with_user(self.u_buyer).order_line.price_unit = 300001
        self.assertNotEqual(previous, po.approval_request_id)
        self.assertEqual(previous.state, 'cancelled')
        self.assertEqual(self._approvers(po), self.u_ceo | self.u_chairman)

    def test_purchase_bill_taxed_quantities_and_no_duplicate_bill(self):
        po = self._po(400)
        tax = self.env['account.tax'].create({'name': 'PO VAT 15%', 'amount': 15, 'type_tax_use': 'purchase'})
        po.with_user(self.u_buyer).order_line.write({'product_qty': 3, 'taxes_id': [(6, 0, tax.ids)]})
        po.with_user(self.u_ceo).action_approval_approve()
        bill = po.order_line.invoice_lines.move_id.with_user(self.u_cfo)
        self.assertEqual(bill.amount_total, 1380)
        bill.invoice_date = fields.Date.today()
        bill.action_post()
        self.assertEqual(bill.amount_total, po.amount_total)
        with self.assertRaises(UserError), self.cr.savepoint():
            bill.copy()

    def test_purchase_bill_standard_reversal_allows_reapproval(self):
        po, bill = self._approved_purchase_bill()
        bill.invoice_date = fields.Date.today()
        bill.action_post()
        refund = bill.with_user(self.u_cfo)._reverse_moves(
            [{'invoice_date': fields.Date.today(), 'date': fields.Date.today()}], cancel=True)
        self.assertEqual(refund.move_type, 'in_refund')
        self.assertEqual(refund.state, 'posted')
        self.assertEqual(bill.payment_state, 'reversed')
        po.with_user(self.u_buyer).button_draft()
        po.with_user(self.u_buyer).order_line.price_unit = 1200
        po.with_user(self.u_buyer).button_confirm()
        po.with_user(self.u_ceo).action_approval_approve()
        new_bill = po.order_line.invoice_lines.move_id.filtered(
            lambda m: m.move_type == 'in_invoice' and m.state == 'draft')
        self.assertEqual(len(new_bill), 1)
        new_bill.with_user(self.u_cfo).invoice_date = fields.Date.today()
        new_bill.with_user(self.u_cfo).action_post()
        self.assertEqual(new_bill.amount_total, 1200)
        self.assertEqual(bill.state, 'posted', 'the original posted bill remains in the audit trail')

    def test_purchase_bill_nested_create_cannot_modify_existing_lines(self):
        po, bill = self._approved_purchase_bill()
        line = bill.invoice_line_ids
        with self.assertRaises(UserError), self.cr.savepoint():
            self.env['account.move'].with_user(self.u_cfo).create({
                'move_type': 'in_invoice', 'partner_id': self.vendor.id,
                'invoice_line_ids': [(1, line.id, {'price_unit': 1500})],
            })
        self.assertEqual(line.price_unit, 1000)
