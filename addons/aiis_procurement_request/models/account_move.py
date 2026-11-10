from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare

# An RPC context cannot manufacture this identity. Used only while constructing
# the original bill, when Odoo creates/recomputes its invoice and tax lines.
_INITIAL_BILL = object()


def _changed(record, vals, names):
    for name in names & vals.keys():
        value = record[name]
        if hasattr(value, 'id'):
            value = value.id or False
        if value != vals[name]:
            return True
    return False


class AccountMove(models.Model):
    _inherit = 'account.move'

    aiis_purchase_order_id = fields.Many2one(
        'purchase.order', string='Approved Purchase Order', readonly=True, copy=False,
        index=True, ondelete='restrict')
    aiis_purchase_bill = fields.Boolean(compute='_compute_aiis_purchase_bill')

    @api.depends('aiis_purchase_order_id', 'invoice_line_ids.purchase_line_id')
    def _compute_aiis_purchase_bill(self):
        for bill in self:
            bill.aiis_purchase_bill = bool(bill._aiis_purchase_orders())

    def _aiis_purchase_orders(self):
        bills = self.sudo().filtered(lambda m: m.move_type == 'in_invoice')
        return bills.aiis_purchase_order_id | bills.invoice_line_ids.purchase_line_id.order_id

    def _aiis_terms_error(self):
        return UserError(_(
            'Purchase bill commercial terms must match the approved PO. '
            'Use Return to Procurement, revise the PO and obtain approval again.'))

    @api.model_create_multi
    def create(self, vals_list):
        # Construction privileges are for new bill lines, not references to
        # another bill's existing lines embedded in create commands.
        for vals in vals_list:
            for field_name in ('line_ids', 'invoice_line_ids'):
                for command in vals.get(field_name, []):
                    ids = (command[2] if command[0] == 6 else [command[1]]) if command[0] in (1, 2, 3, 4, 6) else []
                    referenced = self.env['account.move.line'].sudo().browse(ids)
                    for line in referenced:
                        if line.move_id._aiis_purchase_orders():
                            raise line.move_id._aiis_terms_error()
        bills = super(AccountMove, self.with_context(aiis_initial_bill=_INITIAL_BILL)).create(vals_list)
        bills = bills.with_env(self.env)
        for bill in bills.filtered(lambda m: m.move_type == 'in_invoice'):
            orders = bill._aiis_purchase_orders()
            if len(orders) == 1 and not bill.aiis_purchase_order_id:
                super(AccountMove, bill.sudo()).write({'aiis_purchase_order_id': orders.id})
            if orders:
                bill._check_aiis_purchase_terms()
        return bills

    def write(self, vals):
        if self.env.context.get('aiis_initial_bill') is not _INITIAL_BILL:
            commercial = {'partner_id', 'company_id', 'currency_id', 'move_type',
                          'invoice_payment_term_id', 'invoice_cash_rounding_id', 'aiis_purchase_order_id'}
            for bill in self.filtered(lambda m: m._aiis_purchase_orders()):
                if (_changed(bill, vals, commercial)
                        or {'tax_totals', 'amount_total'} & vals.keys()):
                    raise bill._aiis_terms_error()
                if bill.state == 'cancel' and vals.get('state', 'cancel') != 'cancel':
                    raise UserError(_('A cancelled purchase bill cannot be reused. Approve the revised PO to create a new bill.'))
            if vals.get('state') == 'posted':
                self._check_aiis_purchase_terms()
        return super().write(vals)

    def unlink(self):
        if self.filtered(lambda m: m._aiis_purchase_orders()):
            raise UserError(_('Keep purchase bills for the audit trail. Use Return to Procurement instead of deleting them.'))
        return super().unlink()

    def _check_aiis_purchase_terms(self):
        """Recheck on posting and payment, including historical/imported bills."""
        digits = self.env['decimal.precision'].precision_get('Product Price')
        for bill in self:
            orders = bill._aiis_purchase_orders()
            if not orders:
                continue
            if len(orders) != 1 or bill.move_type != 'in_invoice' or bill.state == 'cancel':
                raise bill._aiis_terms_error()
            order = orders.sudo()
            if (order.state not in ('purchase', 'done') or order.aiis_approval_state != 'approved'
                    or not order.aiis_approved_terms):
                raise UserError(_('The purchase order must be fully approved before confirming or paying its bill.'))
            if order.aiis_approved_terms != order._commercial_terms():
                raise bill._aiis_terms_error()
            if (bill.company_id != order.company_id or bill.currency_id != order.currency_id
                    or bill.partner_id.commercial_partner_id != order.partner_id.commercial_partner_id
                    or bill.invoice_payment_term_id != order.payment_term_id):
                raise bill._aiis_terms_error()
            lines = bill.invoice_line_ids.filtered(lambda l: l.display_type in (False, 'product'))
            expected = order.order_line.filtered(lambda l: not l.display_type and l.product_qty)
            if not all(lines.sudo().purchase_line_id) or set(lines.sudo().purchase_line_id.ids) != set(expected.ids):
                raise bill._aiis_terms_error()
            for po_line in expected.sudo():
                group = lines.sudo().filtered(lambda l: l.purchase_line_id == po_line)
                # One bill line per PO line. The only exception: fixed assets
                # tracked per unit (OCA asset profile "asset per item") are
                # split into lines of one unit each when the bill is created.
                if len(group) > 1 and not all(
                        'asset_profile_id' in l._fields and l.asset_profile_id.asset_product_item
                        and not float_compare(l.quantity, 1, precision_rounding=po_line.product_uom.rounding)
                        for l in group):
                    raise bill._aiis_terms_error()
                if float_compare(sum(group.mapped('quantity')), po_line.product_qty,
                                 precision_rounding=po_line.product_uom.rounding):
                    raise bill._aiis_terms_error()
            for line in lines.sudo():
                po_line = line.purchase_line_id
                if (line.product_id != po_line.product_id or line.product_uom_id != po_line.product_uom
                        or float_compare(line.price_unit, po_line.price_unit, precision_digits=digits)
                        or float_compare(line.discount, po_line.discount if 'discount' in po_line._fields else 0, precision_digits=digits)
                        or set(line.tax_ids.ids) != set(po_line.taxes_id.ids)
                        or bill.currency_id.compare_amounts(line.amount_currency, line.price_subtotal)):
                    raise bill._aiis_terms_error()
            if (bill.currency_id.compare_amounts(bill.amount_total, order.amount_total)
                    or bill.currency_id.compare_amounts(bill.amount_tax, order.amount_tax)):
                raise bill._aiis_terms_error()
            other_bills = order.order_line.invoice_lines.move_id.filtered(
                lambda m: m.id != bill.id and m.move_type == 'in_invoice' and m.state != 'cancel'
                and m.payment_state != 'reversed')
            if other_bills:
                raise UserError(_('This PO already has another active bill. Resolve the duplicate before confirming or paying.'))
        return True

    def _post(self, soft=True):
        self._check_aiis_purchase_terms()
        return super()._post(soft=soft)

    def action_register_payment(self):
        self._check_aiis_purchase_terms()
        return super().action_register_payment()

    def action_return_to_procurement(self):
        self.ensure_one()
        self.check_access_rights('write')
        self.check_access_rule('write')
        if self.env.user != self.env['aiis.approval.position']._require_finance_holder():
            raise UserError(_('Only the configured CFO can return a bill to Procurement.'))
        if self.state != 'draft' or len(self._aiis_purchase_orders()) != 1:
            raise UserError(_('Only a draft purchase bill can be returned to Procurement.'))
        return {'type': 'ir.actions.act_window', 'name': _('Return to Procurement'),
                'res_model': 'aiis.purchase.bill.return', 'view_mode': 'form',
                'views': [(False, 'form')], 'target': 'new', 'context': {'default_bill_id': self.id}}

    def _invoice_paid_hook(self):
        res = super()._invoice_paid_hook()
        orders = self.filtered(lambda m: m.move_type == 'in_invoice').line_ids.purchase_line_id.order_id
        orders._notify_paid()
        return res


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get('aiis_initial_bill') is not _INITIAL_BILL:
            for vals in vals_list:
                bill = self.env['account.move'].browse(vals.get('move_id'))
                if (vals.get('display_type', 'product') in (False, 'product') and bill
                        and bill._aiis_purchase_orders()):
                    raise bill._aiis_terms_error()
        return super().create(vals_list)

    def write(self, vals):
        if self.env.context.get('aiis_initial_bill') is not _INITIAL_BILL:
            commercial = {'product_id', 'product_uom_id', 'quantity', 'price_unit', 'discount',
                          'purchase_line_id', 'move_id', 'display_type'}
            for line in self:
                target = self.env['account.move'].browse(vals.get('move_id')) if vals.get('move_id') else line.move_id
                if ((line.move_id._aiis_purchase_orders() or target._aiis_purchase_orders())
                        and line.display_type in (False, 'product')):
                    if _changed(line, vals, commercial) or 'tax_ids' in vals:
                        raise line.move_id._aiis_terms_error()
        return super().write(vals)

    def unlink(self):
        if self.env.context.get('aiis_initial_bill') is not _INITIAL_BILL:
            for line in self.filtered(lambda l: l.display_type in (False, 'product')):
                if line.move_id._aiis_purchase_orders():
                    raise line.move_id._aiis_terms_error()
        return super().unlink()


class AccountPaymentRegister(models.TransientModel):
    _inherit = 'account.payment.register'

    def _create_payments(self):
        self.line_ids.move_id._check_aiis_purchase_terms()
        return super()._create_payments()
