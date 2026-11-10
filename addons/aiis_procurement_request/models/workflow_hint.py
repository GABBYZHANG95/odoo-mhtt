from markupsafe import Markup

from odoo import _, fields, models


def _step(text, detail=None):
    return Markup('<b>%s</b>') % text + (Markup(' - %s') % detail if detail else Markup())


class PurchaseOrderWorkflowHint(models.Model):
    """Header hint (#64): the current step and who has to do what - from the
    same open tasks as the To do list, on the PO and its vendor bills."""
    _inherit = 'purchase.order'

    workflow_hint = fields.Html(compute='_compute_workflow_hint', compute_sudo=True, sanitize=False)

    def _compute_workflow_hint(self):
        Todo = self.env['aiis.todo']
        for order in self:
            order.workflow_hint = order._workflow_hint_text(Todo)

    def _workflow_hint_text(self, Todo=None):
        self.ensure_one()
        Todo = Todo or self.env['aiis.todo']
        order = self.sudo()
        if not order.id:
            return False
        if order.state == 'cancel':
            return _step(_("Cancelled"))
        bills = order.order_line.invoice_lines.move_id.filtered(lambda m: m.move_type == 'in_invoice')
        tasks = Todo._workflow_tasks_hint([order, bills])
        if order.aiis_approval_state == 'pending':
            later = order.approval_request_id.line_ids.filtered(lambda l: l.state == 'waiting').user_id
            step = _("Supplier terms review") if order.approval_request_id.gate == 'payment_terms' else _("Approval")
            text = _step(step, Markup('%s %s') % (_("waiting for"), tasks or '-'))
            if later:
                text += Markup(' · %s %s') % (_("then:"), ', '.join(later.mapped('name')))
            return text
        if order.state in ('draft', 'sent', 'to approve'):
            if tasks:
                return _step(_("Request for quotation"), Markup('%s %s') % (_("waiting for"), tasks))
            return _step(_("Request for quotation"), _(
                "Procurement (%s) records the supplier terms and evidence, then submits for approval",
                order.user_id.name or _("buyer")))
        # approved purchase order: payment stages, receipt, acceptance
        received = order.is_fully_received
        paid = order.aiis_payment_status == 'paid'
        if tasks:
            return _step(_("Approved"), Markup('%s %s') % (_("waiting for"), tasks))
        if received and paid:
            return _step(_("Completed"), _("paid and fully received"))
        if not received:
            return _step(_("Approved"), _("no open task - waiting for the supplier's shipment or the next payment date"))
        return _step(_("Received"), _("no open task - waiting for the next payment date"))


class ProcurementRequestWorkflowHint(models.Model):
    _inherit = 'procurement.request'

    workflow_hint = fields.Html(compute='_compute_workflow_hint', compute_sudo=True, sanitize=False)

    def _compute_workflow_hint(self):
        Todo = self.env['aiis.todo']
        for request in self:
            request.workflow_hint = request._workflow_hint_text(Todo)

    def _workflow_hint_text(self, Todo):
        self.ensure_one()
        request = self.sudo()
        if not request.id:
            return False
        requester = request.employee_id.name or _("the requester")
        tasks = Todo._workflow_tasks_hint([request])
        if request.state == 'draft':
            if request.aiis_approval_state == 'rejected':
                return _step(_("Returned"), _("%s revises the request and submits it again", requester))
            return _step(_("Draft"), _("%s submits the request", requester))
        if request.state == 'submitted':
            later = request.approval_request_id.line_ids.filtered(lambda l: l.state == 'waiting').user_id
            text = _step(_("Approval"), Markup('%s %s') % (_("waiting for"), tasks or '-'))
            if later:
                text += Markup(' · %s %s') % (_("then:"), ', '.join(later.mapped('name')))
            return text
        if request.state == 'approved':
            return _step(_("Approved"), Markup('%s %s') % (
                _("waiting for"), tasks or _("Procurement to prepare the purchase order")))
        if request.state == 'converted' and request.purchase_order_id:
            order = request.purchase_order_id
            return Markup('%s %s: %s') % (_("Purchase order"), order.name, order._workflow_hint_text(Todo))
        if request.state == 'done':
            return _step(_("Done"), _("the purchase order is fully received"))
        return False


class AccountMoveWorkflowHint(models.Model):
    """Purchase bill header hint (#64): who does what before the CFO can pay,
    following the approved payment plan (#58)."""
    _inherit = 'account.move'

    workflow_hint = fields.Html(compute='_compute_workflow_hint', compute_sudo=True, sanitize=False)

    def _compute_workflow_hint(self):
        Todo = self.env['aiis.todo']
        for bill in self:
            bill.workflow_hint = bill._workflow_hint_text(Todo) if bill.id else False

    def _workflow_hint_text(self, Todo):
        self.ensure_one()
        bill = self.sudo()
        orders = bill._aiis_purchase_orders()
        if len(orders) != 1:
            return False
        order = orders
        if bill.state == 'cancel':
            return _step(_("Cancelled"), _("returned to Procurement; the revised PO gets a new bill"))
        if bill.payment_state in ('paid', 'in_payment'):
            return _step(_("Paid"), order._workflow_hint_text(Todo))
        tasks = Todo._workflow_tasks_hint([bill, order])
        currency = bill.currency_id
        if bill.state == 'draft':
            return _step(_("Draft bill"), Markup('%s %s') % (
                _("the CFO checks it against the approved PO and confirms it; waiting for"), tasks or '-'))
        if not order.aiis_payment_managed:
            return _step(_("To pay"), Markup('%s %s') % (_("waiting for"), tasks or '-'))
        payable = order._payment_limit()
        stage, __ = order._next_payment_stage()
        if currency.compare_amounts(payable, 0) > 0:
            return _step(_("Payable now: %s", currency.format(payable)), Markup('%s %s') % (
                _("the CFO registers the payment; waiting for"), tasks or '-'))
        if not stage:
            return _step(_("Nothing left to pay"), tasks or False)
        milestone = dict(stage._fields['trigger']._description_selection(self.env)).get(stage.trigger)
        if stage.due_date:
            text = _("next stage %(stage)s (%(amount)s) can be paid from %(date)s",
                     stage=stage.name, amount=currency.format(stage.amount),
                     date=fields.Date.to_string(stage.due_date))
        else:
            who = {'receipt': _("Procurement (%s) records the full receipt", order.user_id.name or _("buyer")),
                   'acceptance': _("Procurement (%s) confirms the acceptance", order.user_id.name or _("buyer"))}
            text = _("next stage %(stage)s (%(amount)s) waits for: %(milestone)s%(days)s - %(who)s",
                     stage=stage.name, amount=currency.format(stage.amount), milestone=milestone,
                     days=_(" + %s days", stage.days) if stage.days else '',
                     who=who.get(stage.trigger, _("the final PO approval")))
        result = _step(_("Not payable yet"), text)
        if tasks:
            result += Markup(' · %s %s') % (_("open tasks:"), tasks)
        return result
