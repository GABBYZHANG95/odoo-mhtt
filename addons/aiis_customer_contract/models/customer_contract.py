from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import format_amount

# Contract states in which the linked quotation is allowed to be confirmed.
# 'converted' is the legacy (v1) state - those contracts were approved under
# the old flow, so their Sales Orders stay confirmable.
APPROVED_STATES = ('approved', 'confirmed', 'converted')


class CustomerContract(models.Model):
    """Internal "should we sign this" approval for a customer deal.

    Flow (v2): the quotation is prepared and sent to the customer first; once
    the customer sends back their PO, the salesperson starts a contract
    request from the quotation. The salesperson's manager approves it, and
    only then can the quotation be confirmed into a Sales Order - the
    contract is a gate in front of SO confirmation, it no longer creates the
    SO itself.

    The contract does not keep its own copy of the lines: it shows the
    quotation's lines and snapshots the quotation total at submission, so
    any later price change on the quotation forces a re-approval.

    Contracts created under v1 (contract -> generated SO) keep their own
    `line_ids` and stay in state 'converted'.
    """
    _name = 'customer.contract'
    _description = 'Customer Contract Request'
    _order = 'id desc'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(default='New', readonly=True, copy=False)
    sale_order_id = fields.Many2one(
        'sale.order', string='Quotation / Sales Order', readonly=True, copy=False, index=True)
    partner_id = fields.Many2one(
        'res.partner', string='Customer', required=True, tracking=True)
    salesperson_id = fields.Many2one(
        'hr.employee', string='Salesperson', required=True, tracking=True,
        default=lambda self: self.env.user.employee_id)
    department_id = fields.Many2one(
        'hr.department', string='Department', related='salesperson_id.department_id', store=True)
    manager_id = fields.Many2one(
        'hr.employee', string='Approver', related='salesperson_id.parent_id', store=True,
        help='Who needs to approve this request - the salesperson\'s manager '
             '(Employees > Reporting To). No separate approver setup needed.')
    contract_date = fields.Date(default=fields.Date.context_today, required=True)
    start_date = fields.Date(string='Service Start')
    end_date = fields.Date(string='Service End')
    currency_id = fields.Many2one(
        'res.currency', string='Currency', required=True,
        default=lambda self: self.env.company.currency_id,
        domain="[('name', 'in', ('USD', 'CNY', 'SAR'))]",
        help='Limited to USD, CNY (人民币) and SAR (Saudi Riyal) - the '
             'currencies this company actually deals in.')
    reason = fields.Text(string='Business Summary')
    customer_po_ref = fields.Char(
        string='Customer PO No.', related='sale_order_id.client_order_ref', readonly=False,
        help='The purchase order number on the PO the customer sent us. '
             'Stored on the quotation as its Customer Reference.')
    customer_po_file = fields.Binary(string='Customer PO File', attachment=True, copy=False)
    customer_po_filename = fields.Char(copy=False)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('confirmed', 'SO Confirmed'),
        ('converted', 'SO Created (legacy)'),
    ], default='draft', tracking=True, copy=False)
    rejection_reason = fields.Text(copy=False)
    sale_line_ids = fields.One2many(
        related='sale_order_id.order_line', string='Quotation Lines')
    line_ids = fields.One2many(
        'customer.contract.line', 'contract_id', copy=True,
        help='Only used by contracts created before v2; new contracts show '
             'the quotation lines instead.')
    estimated_total = fields.Monetary(
        string='Contract Amount', compute='_compute_estimated_total', store=True)
    approved_amount = fields.Monetary(
        string='Submitted Amount', readonly=True, copy=False, tracking=True,
        help='Quotation total at the time the contract was submitted. If the '
             'quotation total changes afterwards, the contract has to be '
             'approved again before the Sales Order can be confirmed.')
    amount_mismatch = fields.Boolean(compute='_compute_amount_mismatch')
    analytic_account_id = fields.Many2one(
        'account.analytic.account', string='Project / Analytic Account',
        help='Optional - tag this contract to a project so the eventual '
             'revenue shows up under it in the Finance reports.')
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)

    @api.depends('line_ids.subtotal', 'sale_order_id.amount_total')
    def _compute_estimated_total(self):
        for rec in self:
            if rec.line_ids:
                rec.estimated_total = sum(rec.line_ids.mapped('subtotal'))
            else:
                rec.estimated_total = rec.sale_order_id.amount_total

    @api.depends('approved_amount', 'state', 'currency_id',
                 'sale_order_id.amount_total', 'sale_order_id.currency_id')
    def _compute_amount_mismatch(self):
        for rec in self:
            rec.amount_mismatch = bool(
                rec.sale_order_id and rec.approved_amount
                and rec.state in ('submitted', 'approved', 'confirmed')
                and rec._quotation_changed())

    def _quotation_changed(self):
        """True if the quotation's currency or total no longer matches what
        was submitted for approval."""
        self.ensure_one()
        so = self.sale_order_id
        return (so.currency_id != self.currency_id
                or self.currency_id.compare_amounts(so.amount_total, self.approved_amount) != 0)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('customer.contract') or 'New'
        return super().create(vals_list)

    def action_submit(self):
        for rec in self:
            so = rec.sale_order_id
            if not so:
                raise UserError(_(
                    "A contract has to be started from its quotation: open the "
                    "quotation and click 'Request Contract Approval'."))
            if so.state not in ('draft', 'sent'):
                raise UserError(_("%s is no longer a quotation.", so.name))
            if not so.order_line:
                raise UserError(_("Add at least one line to %s before submitting.", so.name))
            if not rec.customer_po_ref:
                raise UserError(_("Enter the customer's PO number before submitting."))
            if not rec.customer_po_file:
                raise UserError(_("Attach the customer's PO before submitting."))
            if not rec.manager_id:
                raise UserError(_(
                    "%s has no manager set (Employees > Reporting To), so "
                    "there is no one to approve this request.", rec.salesperson_id.name))
            rec.write({
                'state': 'submitted',
                'partner_id': so.partner_id.id,
                'currency_id': so.currency_id.id,
                'approved_amount': so.amount_total,
            })

    def _check_can_approve(self):
        self.ensure_one()
        if self.env.user != self.manager_id.user_id and not self.env.user.has_group('sales_team.group_sale_manager'):
            raise UserError(_("Only %s (or a Sales Manager) can approve this request.", self.manager_id.name))

    def action_approve(self):
        for rec in self:
            rec._check_can_approve()
            if rec.amount_mismatch:
                raise UserError(_(
                    "The quotation was changed after this contract was submitted "
                    "(%(old)s → %(new)s). Reset the contract to draft and submit it again.",
                    old=format_amount(rec.env, rec.approved_amount, rec.currency_id),
                    new=format_amount(rec.env, rec.sale_order_id.amount_total, rec.sale_order_id.currency_id)))
            rec.state = 'approved'

    def action_reject(self):
        for rec in self:
            rec._check_can_approve()
            if not rec.rejection_reason:
                raise UserError(_("Please write a rejection reason first."))
            rec.state = 'rejected'

    def action_reset_to_draft(self):
        self.write({'state': 'draft', 'rejection_reason': False, 'approved_amount': 0.0})

    def action_view_sale_order(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'sale.order',
            'res_id': self.sale_order_id.id,
            'view_mode': 'form',
            'target': 'current',
        }


class CustomerContractLine(models.Model):
    """Lines of v1 contracts only - kept so their history stays readable."""
    _name = 'customer.contract.line'
    _description = 'Customer Contract Line'

    contract_id = fields.Many2one('customer.contract', required=True, ondelete='cascade')
    product_id = fields.Many2one('product.product', string='Product')
    description = fields.Char(string='Item / Service', required=True)
    quantity = fields.Float(default=1.0, required=True)
    uom_id = fields.Many2one(
        'uom.uom', string='Unit of Measure', required=True,
        default=lambda self: self.env.ref('uom.product_uom_unit', raise_if_not_found=False))
    unit_price = fields.Monetary(string='Unit Price')
    currency_id = fields.Many2one(related='contract_id.currency_id', store=True)
    subtotal = fields.Monetary(compute='_compute_subtotal', store=True)

    @api.depends('quantity', 'unit_price')
    def _compute_subtotal(self):
        for rec in self:
            rec.subtotal = rec.quantity * rec.unit_price
