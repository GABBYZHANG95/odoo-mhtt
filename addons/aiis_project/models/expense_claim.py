from markupsafe import Markup

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import format_amount

CLAIM_FIELDS = ('claim_line_ids', 'claim_receipt_ids')


class ExpenseClaimMixin(models.AbstractModel):
    """Claim an approved request's actual costs on the request itself (#65).

    The employee lists the actual expenses and uploads the receipts on the
    request, then Submit Claim builds the expense report linked to it and
    submits it to the reimbursement approval. One claim per request; going
    over the estimate is flagged, not blocked. The expense report made by
    hand in Expenses (picking the request) still works and counts as the
    claim.

    The request defines `employee_id`, `state` (draft / submitted /
    approved), `currency_id`, `company_id`, `expense_sheet_ids`, and
    `claim_line_ids` (hr.expense by the link field), and implements the
    `_claim_*` hooks below."""
    _name = 'aiis.expense.claim.mixin'
    _description = 'Expense Claim on the Request'

    # placeholders - the request defines its own
    currency_id = fields.Many2one('res.currency')
    company_id = fields.Many2one('res.company')

    claim_receipt_ids = fields.Many2many(
        'ir.attachment', string='Receipts', copy=False,
        help='Receipts of the actual expenses - they go on the expense report.')
    claim_sheet_id = fields.Many2one(
        'hr.expense.sheet', string='Claim', compute='_compute_claim', compute_sudo=True)
    claim_stage = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('approved', 'Approved'),
        ('claimed', 'Claim Submitted'),
        ('paid', 'Paid'),
    ], string='Status', compute='_compute_claim', compute_sudo=True)
    claim_total = fields.Monetary(
        string='Actual Amount', compute='_compute_claim', compute_sudo=True,
        help='Total of the actual expenses.')
    claim_over = fields.Boolean(compute='_compute_claim', compute_sudo=True)
    claim_pending_names = fields.Char(compute='_compute_claim', compute_sudo=True)
    claim_returned_reason = fields.Text(compute='_compute_claim', compute_sudo=True)
    claim_approval_html = fields.Html(compute='_compute_claim', compute_sudo=True, sanitize=False)
    claim_default_product_id = fields.Many2one('product.product', compute='_compute_claim_default_product')
    claim_editable = fields.Boolean(compute='_compute_claim_editable')

    # ------------------------------------------------------------------
    # Hooks for the request
    # ------------------------------------------------------------------

    def _claim_link_field(self):
        """Field on hr.expense and hr.expense.sheet linking to the request."""
        raise NotImplementedError

    def _claim_estimate(self):
        """The approved estimate, in the request's currency."""
        raise NotImplementedError

    def _claim_default_product(self):
        return self.env.ref('hr_expense.product_product_no_cost', raise_if_not_found=False)

    def _claim_sheet_name(self):
        return self.name

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    def _claim_amount_in_currency(self, amount_company):
        """An amount in company currency, in the request's currency."""
        self.ensure_one()
        company = self.company_id or self.env.company
        if not self.currency_id or self.currency_id == company.currency_id:
            return amount_company
        return company.currency_id._convert(amount_company, self.currency_id, company, fields.Date.context_today(self))

    @api.depends('state', 'expense_sheet_ids.state', 'expense_sheet_ids.aiis_approval_state',
                 'expense_sheet_ids.approval_request_id.line_ids.state', 'claim_line_ids.total_amount')
    def _compute_claim(self):
        for request in self:
            sheet = request.expense_sheet_ids.filtered(lambda s: s.state != 'cancel').sorted('id')[-1:]
            request.claim_sheet_id = sheet
            if request.state != 'approved':
                request.claim_stage = request.state
            elif sheet.state == 'done':
                request.claim_stage = 'paid'
            elif sheet.state in ('submit', 'approve', 'post'):
                request.claim_stage = 'claimed'
            else:
                request.claim_stage = 'approved'
            lines = request.claim_line_ids | sheet.expense_line_ids
            request.claim_total = request._claim_amount_in_currency(sum(lines.mapped('total_amount')))
            estimate = request._claim_estimate()
            currency = request.currency_id or request.env.company.currency_id
            # a note for the approvers: gone once they have decided
            deciding = sheet.state in (False, 'draft', 'submit')
            request.claim_over = deciding and bool(lines) and \
                currency.compare_amounts(request.claim_total, estimate) > 0
            request.claim_pending_names = sheet.approval_pending_names if sheet.state == 'submit' else False
            returned = sheet.state == 'draft' and sheet.aiis_approval_state == 'rejected'
            request.claim_returned_reason = (
                '%s: %s' % (sheet.approval_rejection_note or '', sheet.approval_rejection_reason or '')
                if returned else False)
            if sheet and sheet.approval_request_id:
                request.claim_approval_html = Markup('<h5 class="mt-3">%s</h5>%s') % (
                    _("Reimbursement - %s", sheet.name), sheet.approval_progress_html or '')
            else:
                request.claim_approval_html = False

    def _compute_claim_default_product(self):
        for request in self:
            request.claim_default_product_id = request._claim_default_product()

    def _claim_users(self):
        """The employee and the head of their own department (#60)."""
        self.ensure_one()
        employee = self.employee_id.sudo()
        return employee.user_id | employee.department_id.manager_id.user_id

    def _claim_can_edit(self):
        self.ensure_one()
        sheet = self.claim_sheet_id
        open_claim = self.state == 'approved' and (not sheet or sheet.state == 'draft')
        return open_claim and (self.env.su or self.env.user in self._claim_users()
                               or self.env.user.has_group('base.group_system'))

    @api.depends_context('uid')
    @api.depends('state', 'expense_sheet_ids.state')
    def _compute_claim_editable(self):
        for request in self:
            request.claim_editable = request._claim_can_edit()

    def _check_claim_editable(self):
        for request in self:
            if request.state != 'approved':
                raise UserError(_("%s is not approved yet - claim the costs once it is.", request.name))
            if not request._claim_can_edit():
                if request.claim_sheet_id.state not in (False, 'draft'):
                    raise UserError(_("The costs of %s are already claimed.", request.name))
                raise UserError(_("Only %s or the head of their department claims these costs.",
                                  request.employee_id.name))

    # ------------------------------------------------------------------
    # Editing the expenses and receipts
    # ------------------------------------------------------------------

    def write(self, vals):
        vals = dict(vals)
        claim_vals = {name: vals.pop(name) for name in CLAIM_FIELDS if name in vals}
        res = super().write(vals) if vals else True
        if claim_vals:
            self._check_claim_editable()
            # The department head may fill them in for their staff (#60);
            # Odoo only lets employees write their own expenses.
            for request in self:
                super(ExpenseClaimMixin, request.sudo()).write(request._claim_prepare_vals(claim_vals))
        return res

    def _claim_prepare_vals(self, claim_vals):
        self.ensure_one()
        claim_vals = dict(claim_vals)
        if 'claim_line_ids' in claim_vals:
            fixed = {'employee_id': self.employee_id.id, 'company_id': self.company_id.id or self.env.company.id}
            commands = []
            for command in claim_vals['claim_line_ids']:
                if command[0] == Command.CREATE:
                    command = (Command.CREATE, command[1], dict(command[2], **fixed))
                elif command[0] == Command.UPDATE:
                    command = (Command.UPDATE, command[1], dict(command[2], **fixed))
                commands.append(command)
            claim_vals['claim_line_ids'] = commands
        return claim_vals

    # ------------------------------------------------------------------
    # Submitting the claim
    # ------------------------------------------------------------------

    def action_submit_claim(self):
        Sheet = self.env['hr.expense.sheet'].sudo()
        for request in self:
            request._check_claim_editable()
            link = request._claim_link_field()
            sheet = request.claim_sheet_id.sudo()
            lines = request.sudo().claim_line_ids | sheet.expense_line_ids
            if not lines:
                raise UserError(_("Add the actual expenses before submitting the claim."))
            if not request.claim_receipt_ids:
                raise UserError(_("Attach the receipts before submitting the claim."))
            if not sheet:
                sheet = Sheet.create({
                    'name': request._claim_sheet_name(),
                    'employee_id': request.employee_id.id,
                    'company_id': request.company_id.id or self.env.company.id,
                    link: request.id,
                    'expense_line_ids': [Command.set(lines.ids)],
                })
            else:
                sheet.expense_line_ids = [Command.set(lines.ids)]
            on_sheet = set(sheet.attachment_ids.mapped('checksum'))
            for receipt in request.sudo().claim_receipt_ids:
                if receipt.checksum not in on_sheet:
                    receipt.with_context(sync_attachment=False).copy(
                        {'res_model': 'hr.expense.sheet', 'res_id': sheet.id})
                    on_sheet.add(receipt.checksum)
            sheet.action_submit_sheet()
            currency = request.currency_id or self.env.company.currency_id
            request.message_post(body=_("Claim submitted: %(sheet)s, %(amount)s.", sheet=sheet.name,
                                        amount=format_amount(self.env, request.claim_total, currency)))
        return True


class HrExpense(models.Model):
    _inherit = 'hr.expense'

    aiis_travel_request_id = fields.Many2one(
        'aiis.travel.request', string='Travel Request', index=True, copy=False, ondelete='set null')

    @api.constrains('aiis_travel_request_id', 'employee_id')
    def _check_aiis_travel_request(self):
        for expense in self.filtered('aiis_travel_request_id'):
            if expense.aiis_travel_request_id.sudo().employee_id != expense.employee_id:
                raise ValidationError(_("The travel request belongs to another employee."))


class HrExpenseSheetClaim(models.Model):
    """The claimed request on the report: the expenses carry the link too,
    so they show on the request, and only one claim per request."""
    _inherit = 'hr.expense.sheet'

    aiis_claim_estimate = fields.Monetary(
        string='Estimate', compute='_compute_aiis_claim', currency_field='aiis_claim_currency_id', compute_sudo=True,
        help='What the linked request estimated.')
    aiis_claim_currency_id = fields.Many2one('res.currency', compute='_compute_aiis_claim', compute_sudo=True)
    aiis_claim_over = fields.Boolean(compute='_compute_aiis_claim', compute_sudo=True)
    aiis_on_engine = fields.Boolean(
        compute='_compute_aiis_claim', compute_sudo=True,
        help="Claims a request: approved through the approval engine, not by Odoo's expense manager.")

    def _aiis_claim_links(self):
        """Link fields of the requests an expense report can claim."""
        return ['aiis_travel_request_id']

    def _aiis_claim_request(self):
        self.ensure_one()
        for name in self._aiis_claim_links():
            if self[name]:
                return self[name]
        return None

    @api.depends('total_amount', 'state', 'aiis_travel_request_id')
    def _compute_aiis_claim(self):
        for sheet in self:
            request = sheet._aiis_claim_request()
            sheet.aiis_on_engine = bool(request)
            if not request:
                sheet.aiis_claim_estimate = 0.0
                sheet.aiis_claim_currency_id = sheet.currency_id
                sheet.aiis_claim_over = False
                continue
            currency = request.currency_id or sheet.currency_id
            estimate = request._claim_estimate()
            sheet.aiis_claim_estimate = estimate
            sheet.aiis_claim_currency_id = currency
            sheet.aiis_claim_over = sheet.state in ('draft', 'submit') and currency.compare_amounts(
                request._claim_amount_in_currency(sheet.total_amount), estimate) > 0

    @api.constrains('aiis_travel_request_id', 'state')
    def _check_one_claim_travel(self):
        self._aiis_check_one_claim()

    def _aiis_check_one_claim(self):
        for sheet in self.filtered(lambda s: s.state != 'cancel'):
            for name in sheet._aiis_claim_links():
                if not sheet[name]:
                    continue
                others = self.sudo().search_count([
                    (name, '=', sheet[name].id), ('state', '!=', 'cancel'), ('id', '!=', sheet.id)])
                if others:
                    raise ValidationError(_("%s already has an expense report - each request is claimed once.",
                                            sheet[name].sudo().name))

    def _aiis_claim_tag_lines(self):
        for sheet in self:
            for name in sheet._aiis_claim_links():
                untagged = sheet.expense_line_ids.filtered(lambda e: e[name] != sheet[name])
                if sheet[name] and untagged:
                    untagged.sudo().write({name: sheet[name].id})

    @api.model_create_multi
    def create(self, vals_list):
        sheets = super().create(vals_list)
        sheets._aiis_claim_tag_lines()
        return sheets

    def write(self, vals):
        res = super().write(vals)
        if 'expense_line_ids' in vals or set(vals) & set(self._aiis_claim_links()):
            self._aiis_claim_tag_lines()
        return res
