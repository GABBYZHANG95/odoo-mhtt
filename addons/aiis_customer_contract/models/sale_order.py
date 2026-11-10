from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import format_amount

from .customer_contract import APPROVED_STATES


class SaleOrder(models.Model):
    """A quotation can only be confirmed once its customer contract has been
    approved (see customer.contract). One contract per quotation: a rejected
    contract is reset to draft and resubmitted rather than replaced."""
    _inherit = 'sale.order'

    contract_ids = fields.One2many('customer.contract', 'sale_order_id', string='Customer Contracts')
    contract_id = fields.Many2one(
        'customer.contract', string='Customer Contract', compute='_compute_contract_id')
    contract_state = fields.Selection(
        selection=lambda self: self.env['customer.contract']._fields['state'].selection,
        string='Contract Status', compute='_compute_contract_id')
    contract_ok = fields.Boolean(
        string='Contract Approved', compute='_compute_contract_id',
        help='An approved contract exists and the quotation total still '
             'matches the amount that was approved.')

    @api.depends('contract_ids.state', 'contract_ids.approved_amount', 'amount_total', 'currency_id')
    def _compute_contract_id(self):
        for order in self:
            contract = order.contract_ids[:1]
            order.contract_id = contract
            order.contract_state = contract.state
            order.contract_ok = bool(
                contract and contract.state in APPROVED_STATES
                and not (contract.approved_amount and contract._quotation_changed()))

    def action_request_contract(self):
        self.ensure_one()
        if self.contract_id:
            return self.action_view_contract()
        if self.state not in ('draft', 'sent'):
            raise UserError(_("Only quotations can be sent for contract approval."))
        if not self.order_line:
            raise UserError(_("Add at least one line to the quotation first."))
        salesperson = self.user_id.employee_id or self.env.user.employee_id
        if not salesperson:
            raise UserError(_(
                "%s is not linked to an employee, so there is no manager to "
                "approve the contract.", (self.user_id or self.env.user).name))
        self.env['customer.contract'].create({
            'sale_order_id': self.id,
            'partner_id': self.partner_id.id,
            'currency_id': self.currency_id.id,
            'salesperson_id': salesperson.id,
            'analytic_account_id': self.analytic_account_id.id if 'analytic_account_id' in self._fields else False,
        })
        return self.action_view_contract()

    def action_view_contract(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'customer.contract',
            'res_id': self.contract_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def _check_contract_approved(self):
        for order in self:
            contract = order.contract_id
            if not contract or contract.state not in APPROVED_STATES:
                raise UserError(_(
                    "%s cannot be confirmed yet: it needs an approved customer "
                    "contract first. Use 'Request Contract Approval' on the quotation.",
                    order.name))
            if contract.approved_amount and contract._quotation_changed():
                raise UserError(_(
                    "%(so)s was changed after its contract was approved "
                    "(%(old)s → %(new)s). Reset contract %(ct)s to draft and submit it again.",
                    so=order.name, ct=contract.name,
                    old=format_amount(self.env, contract.approved_amount, contract.currency_id),
                    new=format_amount(self.env, order.amount_total, order.currency_id)))

    def action_confirm(self):
        self._check_contract_approved()
        res = super().action_confirm()
        # The confirming user may not be allowed to edit the contract itself.
        self.contract_id.sudo().filtered(
            lambda c: c.state in ('approved', 'converted')).write({'state': 'confirmed'})
        return res
