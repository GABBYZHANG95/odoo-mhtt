from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .project_bom import COST_GROUPS

STAGES = [
    ('initiation', 'Initiation'),
    ('solution', 'Solution Design'),
    ('contract', 'Contract'),
    ('delivery', 'Delivery'),
    ('maintenance', 'Maintenance'),
]
STATE_STAGE = {
    'draft': 'initiation', 'initiation': 'initiation',
    'design': 'solution', 'design_review': 'solution', 'costing': 'solution',
    'costing_review': 'solution', 'quoted': 'solution',
    'contract_bom': 'contract', 'contract_requote': 'contract', 'renegotiate': 'contract',
    'contract_terms': 'contract', 'contract_review': 'contract',
    'delivery': 'delivery', 'delivery_review': 'delivery',
    'maintenance': 'maintenance', 'closing_review': 'maintenance',
}
# Which approval template (gate) a state waits on
STATE_GATE = {
    'initiation': 'initiation', 'design_review': 'solution', 'costing_review': 'costing',
    'contract_review': 'contract', 'delivery_review': 'delivery', 'closing_review': 'closing',
}
# Where a rejected or withdrawn gate sends the project back to
GATE_BACK_STATE = {
    'initiation': 'draft', 'solution': 'design', 'costing': 'costing', 'contract': 'contract_terms',
    'delivery': 'delivery', 'closing': 'maintenance',
}
# R&D can change the solution documents like the BOM lines: from solution
# design up to the contract terms, except while a gate is under approval (#59)
# Contract terms the salesperson fills in (#60); the project manager may
# also set the maintenance period
SALES_TERM_FIELDS = {'commercial_terms', 'payment_cycle', 'payment_conditions'}
MAINTENANCE_FIELDS = {'maintenance_start', 'maintenance_end'}
# What the CFO confirms before the quotation (#61)
FINANCE_FIELDS = {'quote_tax_ids', 'fx_rate_ids', 'cost_budget'}
SOLUTION_DOC_STATES = ('design', 'costing', 'quoted', 'contract_bom', 'contract_requote', 'renegotiate')


class Project(models.Model):
    """A customer project from opportunity to closing (design doc 4).
    Phase 1: initiation, solution design (BOM), costing and quotation, and
    the customer's decision. Phase 2: the contract - R&D confirms the BOM,
    Procurement re-quotes what changed or expired, a cost increase sends
    the project back to the customer for a new quotation, then the sales
    terms go through the contract gate and the sales order is confirmed.
    Each gate is approved through the approval engine with its own
    template (gate code)."""
    _name = 'aiis.project'
    _description = 'Project'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'aiis.approval.mixin']
    _order = 'id desc'

    name = fields.Char(string='Project Name', required=True, tracking=True)
    code = fields.Char(string='Project Number', readonly=True, copy=False, index=True, tracking=True)
    partner_id = fields.Many2one('res.partner', string='Customer', required=True, tracking=True)
    customer_code = fields.Char(related='partner_id.aiis_customer_code', string='Customer Code')
    salesperson_id = fields.Many2one(
        'res.users', string='Salesperson', required=True, tracking=True,
        default=lambda self: self.env.user, domain=[('share', '=', False)])
    project_manager_id = fields.Many2one(
        'res.users', string='Project Manager', tracking=True, domain=[('share', '=', False)])
    member_ids = fields.Many2many(
        'res.users', 'aiis_project_member_rel', 'project_id', 'user_id', string='Project Members',
        domain=[('share', '=', False)],
        help='People working on the project (e.g. R&D for the BOM) - they can see it.')
    opportunity = fields.Text(string='Opportunity Description')
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    company_currency_id = fields.Many2one(related='company_id.currency_id', string='Company Currency')
    currency_id = fields.Many2one(
        'res.currency', string='Currency', required=True, default=lambda self: self.env.company.currency_id)
    expected_amount = fields.Monetary(string='Expected Amount')
    expected_date = fields.Date(string='Expected Date')
    info_attachment_ids = fields.Many2many(
        'ir.attachment', 'aiis_project_info_attachment_rel', 'project_id', 'attachment_id',
        string='Project Information', help='Documents describing the opportunity - required to submit.')
    solution_attachment_ids = fields.Many2many(
        'ir.attachment', 'aiis_project_solution_attachment_rel', 'project_id', 'attachment_id',
        string='Solution Documents', copy=False,
        help='Design documents from R&D (drawings, design description, slides) - required to submit '
             'the solution. Each BOM version keeps a copy of them (#59).')

    state = fields.Selection([
        ('draft', 'Draft'),
        ('initiation', 'Initiation Approval'),
        ('design', 'Solution Design'),
        ('design_review', 'Solution Approval'),
        ('costing', 'Costing'),
        ('costing_review', 'Costing Approval'),
        ('quoted', 'Quotation Sent'),
        ('contract_bom', 'Contract: BOM & Price Check'),
        ('contract_requote', 'Contract: Re-quote'),
        ('renegotiate', 'Contract: Cost Increase'),
        ('contract_terms', 'Contract: Terms'),
        ('contract_review', 'Contract Approval'),
        ('delivery', 'Delivery'),
        ('delivery_review', 'Delivery Acceptance'),
        ('maintenance', 'Maintenance'),
        ('closing_review', 'Closing Approval'),
        ('done', 'Completed'),
    ], default='draft', required=True, tracking=True, copy=False)
    stage = fields.Selection(STAGES, compute='_compute_stage', store=True)
    status = fields.Selection([
        ('active', 'In Progress'),
        ('paused', 'Paused'),
        ('closed', 'Closed'),
    ], default='active', required=True, tracking=True, copy=False)
    restart_date = fields.Date(copy=False, tracking=True)
    close_reason = fields.Text(copy=False)
    analytic_account_id = fields.Many2one('account.analytic.account', string='Analytic Account', readonly=True, copy=False)

    bom_line_ids = fields.One2many('aiis.project.bom.line', 'project_id', string='BOM', copy=True)
    bom_version_ids = fields.One2many('aiis.project.bom.version', 'project_id', string='BOM Versions', readonly=True)
    bom_version_id = fields.Many2one(
        'aiis.project.bom.version', compute='_compute_bom_version', compute_sudo=True, string='Current Version')
    bom_approved = fields.Boolean(compute='_compute_bom_version', compute_sudo=True)
    bom_changed_count = fields.Integer(compute='_compute_bom_counts', compute_sudo=True)
    bom_needs_rfq_count = fields.Integer(compute='_compute_bom_counts', compute_sudo=True)

    total_cost = fields.Monetary(
        currency_field='company_currency_id', compute='_compute_costing', groups=COST_GROUPS)
    margin_percent = fields.Float(
        string='Margin (%)', groups=COST_GROUPS, tracking=True, copy=False,
        help='Gross margin set by the CEO: price = cost / (1 - margin).')
    quote_amount = fields.Monetary(
        string='Quotation Amount', currency_field='company_currency_id', compute='_compute_costing',
        groups=COST_GROUPS)
    can_set_margin = fields.Boolean(compute='_compute_can_set_margin')
    can_review_finance = fields.Boolean(compute='_compute_can_set_margin')

    # Finance review before the quotation (#61): the CFO's step after the CEO
    quote_tax_ids = fields.Many2many(
        'account.tax', 'aiis_project_quote_tax_rel', 'project_id', 'tax_id', string='Quotation Taxes',
        copy=False, domain=[('type_tax_use', '=', 'sale')], help='Applied to every quotation line.')
    fx_rate_ids = fields.One2many(
        'aiis.project.fx.rate', 'project_id', string='Exchange Rates', copy=False, groups=COST_GROUPS)
    cost_budget = fields.Monetary(
        string='Cost Budget', currency_field='company_currency_id', copy=False, tracking=True, groups=COST_GROUPS,
        help='Confirmed by the CFO; project purchase orders over it are flagged, not blocked.')
    budget_committed = fields.Monetary(
        string='Committed', currency_field='company_currency_id', compute='_compute_budget', groups=COST_GROUPS,
        help='Approved project purchase orders, untaxed.')
    budget_pending = fields.Monetary(
        string='Awaiting Approval', currency_field='company_currency_id', compute='_compute_budget',
        groups=COST_GROUPS, help='Project purchase orders not approved yet, untaxed.')
    budget_remaining = fields.Monetary(
        string='Remaining', currency_field='company_currency_id', compute='_compute_budget', groups=COST_GROUPS,
        help='Budget - committed - awaiting approval.')

    # Contract check (#62): R&D confirms the BOM and Procurement the prices, in parallel
    contract_bom_confirmed_by = fields.Many2one('res.users', string='BOM Confirmed By', readonly=True, copy=False)
    contract_prices_confirmed_by = fields.Many2one('res.users', string='Prices Confirmed By', readonly=True, copy=False)
    workflow_hint = fields.Html(compute='_compute_workflow_hint', sanitize=False,
                                help='Current step, who it waits for and who is done (#62).')
    quotation_id = fields.Many2one('sale.order', string='Quotation', readonly=True, copy=False)
    customer_po_ref = fields.Char(string='Customer PO Number', copy=False, tracking=True)

    quoted_cost = fields.Monetary(
        string='Cost When Quoted', currency_field='company_currency_id', readonly=True, copy=False,
        groups=COST_GROUPS, help='Total cost the current quotation was priced on.')
    quotation_amount = fields.Monetary(
        related='quotation_id.amount_untaxed', string='Quotation (untaxed)', currency_field='company_currency_id')
    actual_margin = fields.Float(
        string='Margin at Current Cost (%)', compute='_compute_actual_margin', groups=COST_GROUPS,
        help='(Quotation - current cost) / quotation, untaxed.')
    requote_from_contract = fields.Boolean(copy=False, readonly=True)
    commercial_terms = fields.Text(copy=False, tracking=True)
    payment_cycle = fields.Text(
        string='Payment Cycle', copy=False, tracking=True,
        help='When the customer pays, e.g. 30% on signing, 60% on delivery, 10% on acceptance.')
    payment_conditions = fields.Text(
        string='Payment Conditions', copy=False, tracking=True,
        help='Conditions attached to the payments, e.g. invoice due in 30 days, retention, guarantees.')
    contract_approved = fields.Boolean(readonly=True, copy=False)
    # the customer's last decision on the quotation (#66, for the dashboard's win rate)
    customer_decision = fields.Selection([
        ('accepted', 'Accepted'),
        ('rejected_closed', 'Rejected - Closed'),
        ('rejected_paused', 'Rejected - Paused'),
    ], readonly=True, copy=False, tracking=True)
    customer_decision_date = fields.Date(readonly=True, copy=False)
    maintenance_start = fields.Date(copy=False, tracking=True)
    maintenance_end = fields.Date(
        copy=False, tracking=True,
        help='Leave empty when the contract has no maintenance: the project then closes after delivery.')

    procurement_request_ids = fields.One2many('procurement.request', 'aiis_project_id', string='Purchase Requests')
    procurement_request_count = fields.Integer(compute='_compute_procurement_request_count', compute_sudo=True)
    service_request_ids = fields.One2many(
        'aiis.service.hours.request', 'project_id', string='Service Hours',
        domain=[('extends_id', '=', False)])
    travel_request_ids = fields.One2many('aiis.travel.request', 'project_id', string='Travel Requests')
    is_project_manager = fields.Boolean(compute='_compute_is_project_manager')
    is_ceo = fields.Boolean(compute='_compute_is_project_manager')
    is_rd = fields.Boolean(compute='_compute_is_project_manager', help='R&D maintains the BOM (#43).')
    is_salesperson = fields.Boolean(
        compute='_compute_is_project_manager',
        help="The project's salesperson, the head of their department (#60) or an administrator.")
    can_assign_manager = fields.Boolean(
        compute='_compute_is_project_manager',
        help='The CEO assigns the project manager (#42); the salesperson can suggest one on the draft.')

    _sql_constraints = [
        ('code_unique', 'unique(code)', 'This project number is already used.'),
    ]

    @api.depends('code', 'name')
    def _compute_display_name(self):
        for project in self:
            project.display_name = '%s - %s' % (project.code, project.name) if project.code else project.name

    @api.depends('state', 'maintenance_end')
    def _compute_stage(self):
        for project in self:
            if project.state == 'done':
                project.stage = 'maintenance' if project.maintenance_end else 'delivery'
            else:
                project.stage = STATE_STAGE.get(project.state, 'initiation')

    def _compute_procurement_request_count(self):
        for project in self:
            project.procurement_request_count = len(project.procurement_request_ids)

    @api.depends_context('uid')
    @api.depends('project_manager_id', 'salesperson_id', 'state')
    def _compute_is_project_manager(self):
        ceo = self.env['aiis.approval.position']._holder('ceo')
        is_admin = self.env.user.has_group('base.group_system')
        for project in self:
            project.is_project_manager = project.project_manager_id == self.env.user or is_admin
            project.is_ceo = self.env.user == ceo
            project.is_rd = is_admin or (project.company_id or self.env.company)._aiis_is_rd(self.env.user)
            project.is_salesperson = is_admin or self.env.user in project._sales_users()
            project.can_assign_manager = (
                project.is_ceo or is_admin
                or (project.state == 'draft' and project.salesperson_id == self.env.user))

    @api.depends('bom_version_ids.state')
    def _compute_bom_version(self):
        for project in self:
            project.bom_version_id = project.bom_version_ids.sorted('number', reverse=True)[:1]
            project.bom_approved = any(v.state in ('approved', 'superseded') for v in project.bom_version_ids)

    @api.depends('bom_line_ids.changed_since_approval', 'bom_line_ids.needs_rfq')
    def _compute_bom_counts(self):
        for project in self:
            project.bom_changed_count = len(project.bom_line_ids.filtered('changed_since_approval'))
            project.bom_needs_rfq_count = len(project.bom_line_ids.filtered('needs_rfq'))

    @api.depends('bom_line_ids.line_cost', 'margin_percent')
    def _compute_costing(self):
        for project in self:
            project.total_cost = sum(project.bom_line_ids.mapped('line_cost'))
            margin = project.margin_percent / 100.0
            project.quote_amount = project.total_cost / (1 - margin) if 0 <= margin < 1 else 0.0

    @api.depends('total_cost', 'quotation_id.amount_untaxed')
    def _compute_actual_margin(self):
        for project in self:
            amount = project.quotation_id.amount_untaxed
            project.actual_margin = (amount - project.total_cost) / amount * 100 if amount else 0.0

    @api.depends_context('uid')
    @api.depends('state', 'approval_request_id.line_ids.state')
    def _compute_can_set_margin(self):
        """The costing gate is CEO (margin) then CFO (finance, #61): each edits
        their part only while it is their turn"""
        Position = self.env['aiis.approval.position']
        ceo, cfo, user = Position._holder('ceo'), Position._holder('cfo'), self.env.user
        for project in self:
            pending = project._approval_pending_users() if project.state == 'costing_review' else self.env['res.users']
            project.can_set_margin = bool(user == ceo and user in pending)
            project.can_review_finance = bool(user == cfo and user in pending)

    def _rd_users(self):
        self.ensure_one()
        company = self.company_id or self.env.company
        return self.sudo().member_ids.filtered(lambda u: company._aiis_is_rd(u))

    def _compute_workflow_hint(self):
        """#62: what the project waits for, by whom, and who is done"""
        def names(users, default):
            return ', '.join(users.mapped('name')) or default

        def done(user):
            return Markup('<span class="text-success">✓ %s</span>') % _("confirmed by %s", user.name)

        def waiting(text):
            return Markup('<span class="fw-bold">%s</span>') % text

        for project in self:
            p = project.sudo()
            sales = p.salesperson_id.name
            pm = p.project_manager_id.name or _("the project manager")
            rd = names(p._rd_users(), _("R&D"))
            hint = False
            if p.status == 'closed':
                hint = Markup('<b>%s</b>') % _("Closed.")
            elif p.status == 'paused':
                hint = Markup('<b>%s</b> %s') % (_("Paused."), _("The salesperson (%s) restarts it.", sales))
            elif p.aiis_approval_state == 'pending':
                lines = p.approval_request_id.line_ids
                pending = lines.filtered(lambda l: l.state == 'pending').user_id
                later = lines.filtered(lambda l: l.state == 'waiting').user_id
                step = dict(self._fields['state']._description_selection(self.env)).get(p.state)
                hint = Markup('<b>%s</b> - %s %s') % (step, _("waiting for:"), waiting(names(pending, '-')))
                if p.state == 'costing_review' and p.approval_request_id.gate == 'costing':
                    role = _("(CEO sets the margin)") if pending == self.env['aiis.approval.position']._holder('ceo') else _("(CFO confirms the cost budget, taxes and exchange rates)")
                    hint += Markup(' %s') % role
                if later:
                    hint += Markup(' · %s %s') % (_("then:"), names(later, ''))
            elif p.state == 'draft':
                hint = Markup('<b>%s</b> - %s') % (_("Initiation"), waiting(_(
                    "the salesperson (%s) attaches the project information and submits the initiation", sales)))
            elif p.state == 'design':
                hint = Markup('<b>%s</b> - %s') % (_("Solution design"), waiting(_(
                    "R&D (%s) builds the BOM, uploads the solution documents and submits the solution", rd)))
            elif p.state == 'costing':
                hint = Markup('<b>%s</b> - %s') % (_("Costing"), waiting(_(
                    "Procurement quotes every BOM line, then submits the costing")))
            elif p.state == 'quoted':
                hint = Markup('<b>%s</b> - %s') % (_("Quotation sent"), waiting(_(
                    "the salesperson (%s) records the customer's decision", sales)))
            elif p.state == 'contract_bom':
                bom = done(p.contract_bom_confirmed_by) if p.contract_bom_confirmed_by else waiting(_("waiting to confirm the BOM"))
                prices = done(p.contract_prices_confirmed_by) if p.contract_prices_confirmed_by else waiting(_("waiting to confirm the prices"))
                hint = Markup('<b>%s</b> - %s (%s): %s · %s: %s') % (
                    _("Contract check, in parallel"), _("R&D"), rd, bom, _("Procurement"), prices)
            elif p.state == 'contract_requote':
                hint = Markup('<b>%s</b> - %s') % (_("Contract check"), waiting(_(
                    "Procurement quotes the changed and expired lines again, then submits the new quotes")))
            elif p.state == 'renegotiate':
                hint = Markup('<b>%s</b> - %s') % (_("Costs went up"), waiting(_(
                    "the salesperson (%s) discusses the price with the customer, then clicks Re-quote", sales)))
            elif p.state == 'contract_terms':
                hint = Markup('<b>%s</b> - %s') % (_("Contract terms"), waiting(_(
                    "the salesperson (%s) fills in the Contract tab and submits the contract", sales)))
            elif p.state == 'delivery':
                hint = Markup('<b>%s</b> - %s') % (_("Delivery"), waiting(_(
                    "%s raises purchase requests from the BOM and requests the delivery acceptance", pm)))
            elif p.state == 'maintenance':
                hint = Markup('<b>%s</b> - %s') % (_("Maintenance until %s", p.maintenance_end or '-'), waiting(_(
                    "%s requests closing at the end; the CEO can renew", pm)))
            elif p.state == 'done':
                hint = Markup('<b>%s</b>') % _("Completed.")
            project.workflow_hint = hint

    def _approval_pending_users(self):
        self.ensure_one()
        return self.approval_request_id.sudo().line_ids.filtered(lambda l: l.state == 'pending').user_id

    def _to_company_currency(self, amount, currency):
        """At the rate the CFO confirmed, else today's rate"""
        self.ensure_one()
        company = self.company_id
        if not currency or currency == company.currency_id:
            return amount
        rate = self.sudo().fx_rate_ids.filtered(lambda r: r.currency_id == currency)[:1]
        if rate:
            return amount * rate.rate
        return currency._convert(amount, company.currency_id, company, fields.Date.context_today(self))

    def _budget_po_lines(self):
        self.ensure_one()
        return self.env['purchase.order.line'].sudo().search([
            ('aiis_bom_line_id.project_id', '=', self.id), ('order_id.state', '!=', 'cancel')])

    def _budget_amount(self, lines):
        company = self.company_id
        today = fields.Date.context_today(self)
        return sum(line.currency_id._convert(line.price_subtotal, company.currency_id, company,
                                             line.order_id.date_order or today) for line in lines)

    def _compute_budget(self):
        for project in self:
            lines = project._budget_po_lines() if project.id else self.env['purchase.order.line']
            approved = lines.filtered(lambda l: l.order_id.state in ('purchase', 'done'))
            project.budget_committed = project._budget_amount(approved)
            project.budget_pending = project._budget_amount(lines - approved)
            project.budget_remaining = project.cost_budget - project.budget_committed - project.budget_pending

    # ------------------------------------------------------------------
    # Approval engine hooks
    # ------------------------------------------------------------------

    def _approval_gate(self):
        return STATE_GATE.get(self.state, False)

    def _approval_requester(self):
        return self.salesperson_id.employee_id

    def _approval_on_approved(self):
        gate = self.approval_request_id.gate
        if gate == 'initiation':
            self._assign_code()
            self.write({'state': 'design'})
        elif gate == 'solution':
            self.bom_version_id.write({'state': 'approved'})
            (self.bom_version_ids - self.bom_version_id).filtered(
                lambda v: v.state == 'approved').write({'state': 'superseded'})
            self.bom_line_ids.with_context(aiis_bom_flags=True).write({'changed_since_approval': False})
            self.write({'state': 'costing'})
        elif gate == 'costing':
            self._create_quotation()
            self.write({'state': 'quoted', 'requote_from_contract': False})
        elif gate == 'contract':
            self._contract_approved()
        elif gate == 'delivery':
            self._delivery_accepted()
        elif gate == 'closing':
            self._close_completed(_("Maintenance finished - project closed."))

    def _approval_on_rejected(self):
        gate = self.approval_request_id.gate
        if gate == 'solution':
            self.bom_version_id.write({'state': 'rejected'})
        back = GATE_BACK_STATE[gate]
        if gate == 'costing' and self.requote_from_contract:
            back = 'renegotiate'
        self.write({'state': back})

    def _approval_on_withdrawn(self):
        self._approval_on_rejected()

    def _approval_approve(self, comment=False):
        for project in self:
            gate = project.approval_request_id.gate
            if gate == 'costing' and project.can_set_margin and not 0 < project.sudo().margin_percent < 100:
                raise UserError(_("Set the margin (between 0 and 100%) before approving the costing."))
            if gate == 'costing' and project.can_review_finance:
                project._check_finance_review()
            if gate == 'contract' and not project.project_manager_id and project._approval_is_last_step():
                raise UserError(_("Assign the project manager before approving the contract - "
                                  "they run the delivery."))
        res = super()._approval_approve(comment)
        cfo = self.env['aiis.approval.position']._holder('cfo')
        for project in self.filtered(lambda p: p.state == 'costing_review'):
            if cfo and cfo in project._approval_pending_users():
                project._prepare_finance_review()
        return res

    def _prepare_finance_review(self):
        """CEO approved the margin: defaults for the CFO (#61) - budget at the
        quoted cost, the company's sales tax, today's rates for foreign costs"""
        for project in self.sudo():
            company = project.company_id
            today = fields.Date.context_today(project)
            currencies = project.bom_line_ids.cost_currency_id - company.currency_id
            commands = [(2, r.id) for r in project.fx_rate_ids]
            commands += [(0, 0, {'currency_id': c.id, 'rate': c._convert(1.0, company.currency_id, company, today, round=False)})
                         for c in currencies]
            vals = {'fx_rate_ids': commands, 'cost_budget': project.total_cost}
            if not project.quote_tax_ids and company.account_sale_tax_id:
                vals['quote_tax_ids'] = [(6, 0, company.account_sale_tax_id.ids)]
            project.write(vals)

    def _check_finance_review(self):
        for project in self.sudo():
            if not project.cost_budget > 0:
                raise UserError(_("Confirm the cost budget on the Finance tab."))
            unrated = project.bom_line_ids.cost_currency_id - project.company_id.currency_id \
                - project.fx_rate_ids.currency_id
            if unrated:
                raise UserError(_("Set the exchange rate for: %s", ', '.join(unrated.mapped('name'))))

    def _approval_is_last_step(self):
        """Is the current user's approval the last one still open?"""
        self.ensure_one()
        open_lines = self.approval_request_id.sudo().line_ids.filtered(lambda l: l.state in ('waiting', 'pending'))
        return not (open_lines - open_lines.filtered(lambda l: l.user_id == self.env.user and l.state == 'pending'))

    def write(self, vals):
        if 'project_manager_id' in vals and not self.env.su:
            cannot = self.filtered(lambda p: not p.can_assign_manager)
            if cannot:
                raise UserError(_("The CEO assigns the project manager (the salesperson can suggest one "
                                  "while the project is a draft)."))
        if {'contract_bom_confirmed_by', 'contract_prices_confirmed_by'} & vals.keys() and not self.env.su:
            raise UserError(_("Use Confirm BOM / Confirm Prices."))
        if 'margin_percent' in vals and not self.env.su:
            if self.filtered(lambda p: not p.can_set_margin):
                raise UserError(_("Only the CEO sets the margin, while approving the costing."))
        if FINANCE_FIELDS & vals.keys() and not self.env.su:
            if self.filtered(lambda p: not p.can_review_finance):
                raise UserError(_("Only the CFO confirms the finance terms, after the CEO approved the margin."))
        if SALES_TERM_FIELDS & vals.keys():
            self._check_salesperson()
        if MAINTENANCE_FIELDS & vals.keys() and not self.env.su:
            if self.filtered(lambda p: not (p.is_salesperson or p.is_project_manager)):
                raise UserError(_("The salesperson or the project manager sets the maintenance period."))
        if 'solution_attachment_ids' in vals and not self.env.su:
            for project in self:
                if not project.is_rd:
                    raise UserError(_("Only R&D uploads the solution documents."))
                if (project.status != 'active' or project.state not in SOLUTION_DOC_STATES
                        or (project.state == 'contract_bom' and project.contract_bom_confirmed_by)):
                    raise UserError(_("Solution documents can't be changed at this point (under approval, "
                                      "after the contract terms, or the project is paused or closed)."))
        documents_before = {p.id: p.sudo().solution_attachment_ids for p in self} \
            if 'solution_attachment_ids' in vals else {}
        res = super().write(vals)
        for project in self.filtered(lambda p: p.id in documents_before and p.bom_approved):
            project._log_solution_document_changes(documents_before[project.id])
        if {'info_attachment_ids', 'solution_attachment_ids'} & vals.keys():
            self._attach_project_information()
        return res

    @api.model_create_multi
    def create(self, vals_list):
        projects = super().create(vals_list)
        projects._attach_project_information()
        return projects

    def _log_solution_document_changes(self, before):
        """After the solution was approved, document changes are visible in the chatter"""
        after = self.sudo().solution_attachment_ids
        added, removed = (after - before).mapped('name'), (before - after).mapped('name')
        if added or removed:
            parts = ([_("added %s", ', '.join(added))] if added else []) + \
                    ([_("removed %s", ', '.join(removed))] if removed else [])
            self.message_post(body=_("Solution documents changed after approval: %s.", '; '.join(parts)))

    def _attach_project_information(self):
        """Uploaded project information belongs to the project, so whoever
        can see the project can open it - an attachment with no document is
        only readable by whoever uploaded it."""
        for project in self:
            attachments = project.sudo().info_attachment_ids | project.sudo().solution_attachment_ids
            loose = attachments.filtered(
                lambda a: not a.res_model or (a.res_model == self._name and not a.res_id))
            loose.write({'res_model': self._name, 'res_id': project.id})

    # ------------------------------------------------------------------
    # Steps
    # ------------------------------------------------------------------

    def _check_active(self):
        for project in self:
            if project.status != 'active':
                raise UserError(_("%s is paused or closed.", project.display_name))

    def action_submit_initiation(self):
        self._check_active()
        self._check_salesperson()
        for project in self:
            if project.state != 'draft':
                raise UserError(_("Only a draft project can be submitted for initiation."))
            if not project.partner_id.aiis_customer_code:
                raise UserError(_(
                    "Set the customer code of %s first (customer form, Sales & Purchase tab) - "
                    "it is part of the project number.", project.partner_id.display_name))
            if not project.info_attachment_ids:
                raise UserError(_("Attach the project information before submitting."))
            project.state = 'initiation'
            project._approval_submit()

    def _check_solution_documents(self):
        for project in self:
            if not project.solution_attachment_ids:
                raise UserError(_("Upload the solution documents (BOM tab) before submitting."))

    def _save_bom_version(self, state='submitted'):
        """Snapshot the BOM lines and a copy of the solution documents: the
        version stays as it was approved even if the project's documents change."""
        self.ensure_one()
        number = max(self.bom_version_ids.mapped('number') or [0]) + 1
        version = self.env['aiis.project.bom.version'].sudo().create({
            'project_id': self.id,
            'number': number,
            'state': state,
            'line_ids': [(0, 0, {
                'sequence': line.sequence, 'name': line.name, 'specification': line.specification,
                'quantity': line.quantity, 'uom_id': line.uom_id.id, 'product_id': line.product_id.id,
            }) for line in self.bom_line_ids],
        })
        copies = self.env['ir.attachment']
        for attachment in self.sudo().solution_attachment_ids:
            copies |= attachment.copy({'res_model': version._name, 'res_id': version.id})
        version.attachment_ids = copies
        return version

    def _sales_users(self):
        """The salesperson and the head of their own department, who can act
        for their staff (#60). Not the heads of parent departments."""
        self.ensure_one()
        department = self.salesperson_id.sudo().employee_id.department_id
        return self.salesperson_id | department.manager_id.user_id

    def _check_salesperson(self):
        if self.env.su:
            return
        for project in self:
            if not project.is_salesperson:
                raise UserError(_("Only the salesperson of %s (or their department head) does this.",
                                  project.display_name))

    def _check_rd(self):
        for project in self:
            if not project.is_rd:
                raise UserError(_("Only R&D submits the solution and confirms the BOM."))

    def action_submit_solution(self):
        self._check_active()
        self._check_rd()
        for project in self:
            if project.state != 'design':
                raise UserError(_("The solution can only be submitted during solution design."))
            if not project.bom_line_ids:
                raise UserError(_("Add the BOM lines before submitting the solution."))
            project._check_solution_documents()
            project._save_bom_version()
            project.state = 'design_review'
            project._approval_submit()

    def action_submit_costing(self):
        self._check_active()
        if not self.env.user.has_group('purchase.group_purchase_user'):
            raise UserError(_("Only Procurement submits the costing."))
        today = fields.Date.context_today(self)
        for project in self:
            if project.state != 'costing':
                raise UserError(_("The costing can only be submitted during costing."))
            missing = project.bom_line_ids.filtered(lambda l: l.needs_rfq or not l.unit_cost > 0)
            if missing:
                raise UserError(_("These BOM lines still need a quote: %s", ', '.join(missing.mapped('name'))))
            expired = project.bom_line_ids.filtered(lambda l: l.quote_valid_until and l.quote_valid_until < today)
            if expired:
                raise UserError(_("These quotes have expired: %s", ', '.join(expired.mapped('name'))))
            project.state = 'costing_review'
            project._approval_submit()

    def _assign_code(self):
        """Customer code + year + sequence per customer per year: STC-2026-001"""
        for project in self:
            prefix = '%s-%s-' % (project.partner_id.aiis_customer_code, fields.Date.context_today(project).year)
            used = self.sudo().search([('code', '=like', prefix + '%')]).mapped('code')
            numbers = [int(code[len(prefix):]) for code in used if code[len(prefix):].isdigit()]
            project.code = '%s%03d' % (prefix, max(numbers or [0]) + 1)
            project.analytic_account_id = self.env['account.analytic.account'].sudo().create({
                'name': '%s %s' % (project.code, project.name),
                'code': project.code,
                'plan_id': self.env.ref('aiis_project.analytic_plan_projects').id,
                'partner_id': project.partner_id.id,
                'company_id': project.company_id.id,
            })
            project.message_post(body=_("Initiation approved - project number %s.", project.code))

    def _create_quotation(self):
        """Quotation from the BOM: one line per BOM line, priced at cost
        (converted to company currency) / (1 - margin)."""
        for project in self.sudo():
            margin = project.margin_percent / 100.0
            company = project.company_id
            today = fields.Date.context_today(project)
            order_lines = []
            quoted_cost = 0.0
            for line in project.bom_line_ids:
                product = line.product_id or project._create_bom_product(line)
                unit_cost = project._to_company_currency(line.unit_cost, line.cost_currency_id)
                quoted_cost += unit_cost * line.quantity
                taxes = project.quote_tax_ids or product.taxes_id.filtered(lambda t: t.company_id == company)
                order_lines.append((0, 0, {
                    'product_id': product.id,
                    'name': ' - '.join(filter(None, [line.name, line.specification])),
                    'product_uom_qty': line.quantity,
                    'product_uom': line.uom_id.id if line.uom_id.category_id == product.uom_id.category_id else product.uom_id.id,
                    'price_unit': company.currency_id.round(unit_cost / (1 - margin)),
                    'tax_id': [(6, 0, taxes.ids)],
                }))
            if project.quotation_id.state in ('draft', 'sent'):
                # superseded by the new quotation
                project.quotation_id._action_cancel()
            quotation = self.env['sale.order'].sudo().create({
                'partner_id': project.partner_id.id,
                'user_id': project.salesperson_id.id,
                'company_id': company.id,
                'currency_id': company.currency_id.id,
                'origin': project.code,
                'aiis_project_id': project.id,
                'analytic_account_id': project.analytic_account_id.id,
                'order_line': order_lines,
            })
            project.write({'quotation_id': quotation.id, 'quoted_cost': quoted_cost})
            project.message_post(
                body=Markup(_("Costing approved at a %(margin)s%% margin - quotation %(quotation)s is ready for the customer.")) % {
                    'margin': '{:g}'.format(project.margin_percent), 'quotation': quotation.name},
                partner_ids=project.salesperson_id.partner_id.ids,
                subtype_xmlid='mail.mt_comment')

    def _create_bom_product(self, line):
        product = self.env['product.product'].sudo().create({
            'name': ' - '.join(filter(None, [line.name, line.specification])),
            'type': 'service',
            'categ_id': (self.env.ref('aiis_procurement_request.product_category_project_cost', raise_if_not_found=False)
                         or self.env.ref('product.product_category_all')).id,
            'sale_ok': True,
            'purchase_ok': True,
            'uom_id': line.uom_id.id,
            'uom_po_id': line.uom_id.id,
            'taxes_id': [(6, 0, [])],
        })
        line.with_context(aiis_bom_flags=True).product_id = product
        return product

    # ------------------------------------------------------------------
    # Customer decision, pause and restart
    # ------------------------------------------------------------------

    def _open_wizard(self, model, name):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': name,
            'res_model': model,
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_project_id': self.id},
        }

    def action_open_customer_accept(self):
        self._check_active()
        self._check_salesperson()
        return self._open_wizard('aiis.project.accept.wizard', _('Customer Accepted'))

    def action_open_customer_reject(self):
        self._check_active()
        self._check_salesperson()
        return self._open_wizard('aiis.project.reject.wizard', _('Customer Rejected'))

    def _customer_accepted(self, customer_po_ref):
        self.ensure_one()
        self._check_salesperson()
        if self.state != 'quoted':
            raise UserError(_("The customer decides once the quotation has been sent."))
        self.write({'state': 'contract_bom', 'customer_po_ref': customer_po_ref})
        self.sudo().write({'customer_decision': 'accepted',
                           'customer_decision_date': fields.Date.context_today(self)})
        self.sudo().write({'contract_bom_confirmed_by': False, 'contract_prices_confirmed_by': False})
        if self.quotation_id:
            self.quotation_id.sudo().client_order_ref = customer_po_ref
        self.message_post(body=_(
            "Customer accepted - customer PO %s. On to the contract stage: R&D confirms the BOM and "
            "Procurement confirms the prices.",
            customer_po_ref))

    def _customer_rejected(self, outcome, reason, restart_date=False):
        self.ensure_one()
        self._check_salesperson()
        if self.state != 'quoted':
            raise UserError(_("The customer decides once the quotation has been sent."))
        if outcome == 'pause':
            if not restart_date:
                raise UserError(_("Set the expected restart date."))
        self.sudo().write({'customer_decision': 'rejected_paused' if outcome == 'pause' else 'rejected_closed',
                           'customer_decision_date': fields.Date.context_today(self)})
        if outcome == 'pause':
            self.write({'status': 'paused', 'restart_date': restart_date, 'close_reason': reason})
            self.activity_schedule(
                'mail.mail_activity_data_todo', date_deadline=restart_date, user_id=self.salesperson_id.id,
                summary=_("Restart %s?", self.display_name))
            self.message_post(body=_("Customer rejected the quotation - paused until %(date)s: %(reason)s",
                                     date=restart_date, reason=reason))
        else:
            self.write({'status': 'closed', 'close_reason': reason})
            self.message_post(body=_("Customer rejected the quotation - project closed: %s", reason))

    # ------------------------------------------------------------------
    # Contract (design doc 4.4)
    # ------------------------------------------------------------------

    def _lines_to_requote(self):
        self.ensure_one()
        today = fields.Date.context_today(self)
        return self.sudo().bom_line_ids.filtered(
            lambda l: l.needs_rfq or not l.unit_cost > 0 or (l.quote_valid_until and l.quote_valid_until < today))

    def action_confirm_bom(self):
        """R&D: the BOM is current (changing lines before this flags them
        for a new quote)"""
        self._check_active()
        self._check_rd()
        for project in self:
            if project.state != 'contract_bom':
                raise UserError(_("The BOM is confirmed at the start of the contract stage."))
            if not project.bom_line_ids:
                raise UserError(_("The BOM is empty."))
            if project.contract_bom_confirmed_by:
                raise UserError(_("The BOM is already confirmed (by %s).", project.contract_bom_confirmed_by.name))
            project._check_solution_documents()
            project._save_bom_version('confirmed')
            changed = project.bom_line_ids.filtered('changed_since_approval')
            project.sudo().contract_bom_confirmed_by = self.env.user
            project.message_post(body=_("BOM confirmed by %(user)s (%(count)s line(s) changed since the solution was approved).",
                                        user=self.env.user.name, count=len(changed)))
            project._contract_check_done()

    def action_confirm_prices(self):
        """Procurement, in parallel with R&D (#62): every price checked;
        expired and changed lines quoted again first"""
        self._check_active()
        if not self.env.user.has_group('purchase.group_purchase_user'):
            raise UserError(_("Only Procurement confirms the prices."))
        for project in self:
            if project.state != 'contract_bom':
                raise UserError(_("The prices are confirmed at the start of the contract stage."))
            if project.contract_prices_confirmed_by:
                raise UserError(_("The prices are already confirmed (by %s).", project.contract_prices_confirmed_by.name))
            missing = project._lines_to_requote()
            if missing:
                raise UserError(_("Quote these BOM lines again first (changed, expired or without a cost): %s",
                                  ', '.join(missing.mapped('name'))))
            project.sudo().contract_prices_confirmed_by = self.env.user
            project.message_post(body=_("Prices confirmed by %s.", self.env.user.name))
            project._contract_check_done()

    def _contract_check_done(self):
        """Both R&D and Procurement confirmed: compare the costs"""
        for project in self.filtered(lambda p: p.state == 'contract_bom'):
            if project.contract_bom_confirmed_by and project.contract_prices_confirmed_by:
                project._check_cost_change()

    def _reset_contract_check(self, bom=False, prices=False):
        """A BOM or price change after a confirmation voids it (#62)"""
        for project in self.sudo().filtered(lambda p: p.state == 'contract_bom'):
            vals, names = {}, []
            if bom and project.contract_bom_confirmed_by:
                vals['contract_bom_confirmed_by'] = False
                names.append(_("BOM (R&D)"))
            if prices and project.contract_prices_confirmed_by:
                vals['contract_prices_confirmed_by'] = False
                names.append(_("prices (Procurement)"))
            if vals:
                project.write(vals)
                project.message_post(body=_("BOM lines changed - confirm again: %s.", ', '.join(names)))

    def action_submit_requote(self):
        """Procurement: expired and changed lines have new quotes"""
        self._check_active()
        if not self.env.user.has_group('purchase.group_purchase_user'):
            raise UserError(_("Only Procurement submits the new quotes."))
        for project in self:
            if project.state != 'contract_requote':
                raise UserError(_("Nothing to re-quote at this point."))
            missing = project._lines_to_requote()
            if missing:
                raise UserError(_("These BOM lines still need a valid quote: %s", ', '.join(missing.mapped('name'))))
            project._check_cost_change()

    def _check_cost_change(self):
        """Cost the same or lower: the quoted price stands. Higher: the
        salesperson has to talk to the customer and get a new quotation."""
        for project in self.sudo():
            currency = project.company_currency_id
            if currency.compare_amounts(project.total_cost, project.quoted_cost) > 0:
                project.state = 'renegotiate'
                project.message_post(
                    body=_("The costs went up since the quotation. Discuss the price with the customer, "
                           "then ask for a new quotation (Re-quote)."),
                    partner_ids=project.salesperson_id.partner_id.ids, subtype_xmlid='mail.mt_comment')
            else:
                project.state = 'contract_terms'
                project.message_post(
                    body=_("The quoted price stands. The salesperson fills in the contract terms and submits the contract."),
                    partner_ids=project.salesperson_id.partner_id.ids, subtype_xmlid='mail.mt_comment')

    def action_requote(self):
        """Salesperson, after talking to the customer: price it again"""
        self._check_active()
        self._check_salesperson()
        for project in self:
            if project.state != 'renegotiate':
                raise UserError(_("A new quotation is only needed when the costs went up."))
            project.write({'state': 'costing_review', 'requote_from_contract': True})
            project._approval_submit()

    def action_submit_contract(self):
        self._check_active()
        self._check_salesperson()
        for project in self:
            if project.state != 'contract_terms':
                raise UserError(_("The contract is submitted once the terms are filled in."))
            missing = [project._fields[f].string for f in ('commercial_terms', 'payment_cycle', 'payment_conditions')
                       if not (project[f] or '').strip()]
            if missing:
                raise UserError(_("Fill in: %s", ', '.join(missing)))
            quotation = project.sudo().quotation_id
            if not quotation or quotation.state not in ('draft', 'sent'):
                raise UserError(_("The project has no open quotation to turn into the sales order."))
            project.state = 'contract_review'
            project._approval_submit()

    def _contract_approved(self):
        """The project contract replaces the customer contract: confirm the
        sales order and move on to delivery."""
        self.write({'contract_approved': True, 'state': 'delivery'})
        order = self.quotation_id.sudo()
        if order.state in ('draft', 'sent'):
            order.client_order_ref = order.client_order_ref or self.customer_po_ref
            order.action_confirm()
        self.message_post(
            body=_("Contract approved - sales order %s confirmed. On to delivery.", order.name),
            partner_ids=self.salesperson_id.partner_id.ids, subtype_xmlid='mail.mt_comment')

    # ------------------------------------------------------------------
    # Delivery and maintenance (design doc 4.5, 4.6)
    # ------------------------------------------------------------------

    def _check_project_manager(self):
        for project in self:
            if not project.is_project_manager:
                raise UserError(_("Only the project manager of %s can do this.", project.display_name))

    def action_open_pr_wizard(self):
        self._check_active()
        self._check_project_manager()
        if self.state not in ('delivery', 'delivery_review', 'maintenance'):
            raise UserError(_("Purchase requests are raised during delivery and maintenance."))
        return self._open_wizard('aiis.project.pr.wizard', _('Create Purchase Request'))

    def action_view_procurement_requests(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Purchase Requests'),
            'res_model': 'procurement.request',
            'view_mode': 'tree,form',
            'domain': [('aiis_project_id', '=', self.id)],
            'context': {'create': False},
        }

    def action_request_delivery_acceptance(self):
        self._check_active()
        self._check_project_manager()
        for project in self:
            if project.state != 'delivery':
                raise UserError(_("Delivery acceptance is requested during delivery."))
            if project.maintenance_end and project.maintenance_start and project.maintenance_end < project.maintenance_start:
                raise UserError(_("The maintenance end date is before its start date."))
            project.state = 'delivery_review'
            project._approval_submit()

    def _delivery_accepted(self):
        if self.maintenance_end:
            self.write({'state': 'maintenance'})
            self.activity_schedule(
                'mail.mail_activity_data_todo', date_deadline=self.maintenance_end,
                user_id=(self.project_manager_id or self.salesperson_id).id,
                summary=_("Maintenance of %s ends - request closing or ask the CEO to renew", self.display_name))
            self.message_post(body=_("Delivery accepted by the CEO - maintenance until %s.", self.maintenance_end))
        else:
            self._close_completed(_("Delivery accepted by the CEO - no maintenance, project closed."))

    def _close_completed(self, message):
        self.write({'state': 'done', 'status': 'closed'})
        self.message_post(body=message)

    def action_request_closing(self):
        self._check_active()
        self._check_project_manager()
        for project in self:
            if project.state != 'maintenance':
                raise UserError(_("Closing is requested at the end of maintenance."))
            project.state = 'closing_review'
            project._approval_submit()

    def action_open_renew(self):
        self._check_active()
        if not self.is_ceo or self.state != 'maintenance':
            raise UserError(_("Only the CEO renews the maintenance, during maintenance."))
        return self._open_wizard('aiis.project.renew.wizard', _('Renew Maintenance'))

    def _renew(self, new_end):
        self.ensure_one()
        if not self.is_ceo:
            raise UserError(_("Only the CEO renews the maintenance."))
        if self.maintenance_end and new_end <= self.maintenance_end:
            raise UserError(_("The new end date must be after the current one (%s).", self.maintenance_end))
        old = self.maintenance_end
        self.sudo().write({'maintenance_end': new_end})
        self.activity_schedule(
            'mail.mail_activity_data_todo', date_deadline=new_end,
            user_id=(self.project_manager_id or self.salesperson_id).id,
            summary=_("Maintenance of %s ends - request closing or ask the CEO to renew", self.display_name))
        self.message_post(body=_("Maintenance renewed by the CEO: %(old)s → %(new)s.", old=old, new=new_end))

    def action_restart(self):
        self._check_salesperson()
        for project in self:
            if project.status != 'paused':
                raise UserError(_("Only a paused project can be restarted."))
            # back to solution design, keeping the BOM and costs to update
            project.write({'status': 'active', 'state': 'design', 'restart_date': False})
            project.message_post(body=_("Project restarted - back to solution design."))

    def action_open_quotation(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'sale.order',
            'res_id': self.quotation_id.id,
            'view_mode': 'form',
            'target': 'current',
        }
