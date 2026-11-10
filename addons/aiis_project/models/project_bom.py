from odoo import _, api, fields, models
from odoo.exceptions import UserError

# Costs, vendors and margin: Procurement (CEO / Chairman groups imply it)
# and the CFO only - R&D and Sales never see them (design doc 4.8)
COST_GROUPS = 'purchase.group_purchase_user,aiis_project.group_project_cost'

# What R&D maintains; changing any of these after the solution was
# approved flags the line and asks Procurement for a new quote
TECHNICAL_FIELDS = {'name', 'specification', 'quantity', 'uom_id', 'product_id'}
COST_FIELDS = {'vendor_id', 'unit_cost', 'cost_currency_id', 'quote_valid_until'}
# The approvers are looking at this BOM / these costs (or the contract is
# being finalised on them) - no edits meanwhile
LOCKED_STATES = ('design_review', 'costing_review', 'contract_terms', 'contract_review')


class ProjectBomLine(models.Model):
    """One line of a project's bill of materials. The BOM is a living list:
    R&D can change it at any time; each submission for approval saves a
    version (aiis.project.bom.version)."""
    _name = 'aiis.project.bom.line'
    _description = 'Project BOM Line'
    _order = 'project_id, sequence, id'

    project_id = fields.Many2one('aiis.project', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    name = fields.Char(string='Item', required=True)
    specification = fields.Char(string='Specification / Model')
    quantity = fields.Float(default=1.0, required=True, digits='Product Unit of Measure')
    uom_id = fields.Many2one(
        'uom.uom', string='Unit', required=True,
        default=lambda self: self.env.ref('uom.product_uom_unit', raise_if_not_found=False))
    product_id = fields.Many2one(
        'product.product', string='Product',
        help='Optional - an existing product. Lines without one get a product '
             'when the quotation is generated.')

    vendor_id = fields.Many2one('res.partner', string='Vendor', groups=COST_GROUPS)
    unit_cost = fields.Monetary(currency_field='cost_currency_id', groups=COST_GROUPS)
    cost_currency_id = fields.Many2one(
        'res.currency', string='Cost Currency', groups=COST_GROUPS,
        default=lambda self: self.env.company.currency_id)
    quote_valid_until = fields.Date(groups=COST_GROUPS)
    company_currency_id = fields.Many2one(related='project_id.company_currency_id')
    line_cost = fields.Monetary(
        string='Line Cost', currency_field='company_currency_id', compute='_compute_line_cost',
        groups=COST_GROUPS, help='Quantity x unit cost, converted to company currency.')
    quote_expired = fields.Boolean(compute='_compute_quote_status', groups=COST_GROUPS)

    pr_line_ids = fields.One2many('procurement.request.line', 'aiis_bom_line_id', string='Purchase Request Lines')
    po_line_ids = fields.One2many('purchase.order.line', 'aiis_bom_line_id', string='Purchase Order Lines')
    qty_requested = fields.Float(
        string='Requested', compute='_compute_progress', compute_sudo=True, digits='Product Unit of Measure')
    qty_ordered = fields.Float(
        string='On PO', compute='_compute_progress', compute_sudo=True, digits='Product Unit of Measure',
        help='On approved purchase orders.')
    qty_received = fields.Float(
        string='Received', compute='_compute_progress', compute_sudo=True, digits='Product Unit of Measure')
    qty_remaining = fields.Float(
        string='Remaining', compute='_compute_progress', compute_sudo=True, digits='Product Unit of Measure',
        help='Still to request: quantity - requested.')

    changed_since_approval = fields.Boolean(readonly=True, copy=False)
    needs_rfq = fields.Boolean(string='Needs Quote', default=True, readonly=True, copy=False)
    status = fields.Selection([
        ('needs_rfq', 'Needs Quote'),
        ('changed', 'Changed - Needs Quote'),
        ('expired', 'Quote Expired'),
        ('quoted', 'Quoted'),
    ], compute='_compute_status', compute_sudo=True)

    @api.depends('quantity', 'unit_cost', 'cost_currency_id', 'project_id.company_id')
    def _compute_line_cost(self):
        today = fields.Date.context_today(self)
        for line in self:
            company = line.project_id.company_id or self.env.company
            cost = line.quantity * line.unit_cost
            currency = line.cost_currency_id or company.currency_id
            line.line_cost = currency._convert(cost, company.currency_id, company, today) if cost else 0.0

    @api.depends('quote_valid_until')
    def _compute_quote_status(self):
        today = fields.Date.context_today(self)
        for line in self:
            line.quote_expired = bool(line.quote_valid_until) and line.quote_valid_until < today

    @api.depends('needs_rfq', 'changed_since_approval', 'quote_valid_until')
    def _compute_status(self):
        today = fields.Date.context_today(self)
        for line in self:
            if line.needs_rfq:
                line.status = 'changed' if line.changed_since_approval else 'needs_rfq'
            elif line.quote_valid_until and line.quote_valid_until < today:
                line.status = 'expired'
            else:
                line.status = 'quoted'

    @api.depends('quantity', 'pr_line_ids.quantity', 'pr_line_ids.request_id.state',
                 'po_line_ids.product_qty', 'po_line_ids.qty_received', 'po_line_ids.order_id.state')
    def _compute_progress(self):
        for line in self:
            requested = sum(line.pr_line_ids.filtered(lambda l: l.request_id.state != 'draft').mapped('quantity'))
            approved = line.po_line_ids.filtered(lambda l: l.order_id.state in ('purchase', 'done'))
            line.qty_requested = requested
            line.qty_ordered = sum(approved.mapped('product_qty'))
            line.qty_received = sum(approved.mapped('qty_received'))
            line.qty_remaining = max(line.quantity - requested, 0.0)

    @api.onchange('product_id')
    def _onchange_product_id(self):
        for line in self:
            if line.product_id:
                line.name = line.name or line.product_id.display_name
                line.uom_id = line.product_id.uom_id

    def _check_rd(self, projects):
        """Only R&D adds, changes or removes BOM items (#43)"""
        if self.env.su or self.env.context.get('aiis_bom_flags') or self.env.user.has_group('base.group_system'):
            return
        for company in projects.company_id or self.env.company:
            if not company._aiis_is_rd(self.env.user):
                raise UserError(_("Only R&D maintains the BOM items. Procurement fills in the costs."))

    def _check_unlocked(self, projects):
        if projects.filtered(lambda p: p.state in ('draft', 'initiation')):
            raise UserError(_("The BOM is built during solution design, once the initiation is approved."))
        if not self.env.context.get('aiis_bom_flags') and projects.filtered(lambda p: p.state in LOCKED_STATES):
            raise UserError(_("The BOM can't be changed while it is waiting for approval. Withdraw the approval first."))

    @api.model_create_multi
    def create(self, vals_list):
        projects = self.env['aiis.project'].browse({v.get('project_id') for v in vals_list if v.get('project_id')})
        self._check_unlocked(projects)
        self._check_rd(projects)
        lines = super().create(vals_list)
        projects._reset_contract_check(bom=True, prices=True)
        flagged = lines.with_context(aiis_bom_flags=True)
        flagged.filtered(lambda l: l.project_id.bom_approved).write({'changed_since_approval': True})
        flagged.filtered(lambda l: l.sudo().unit_cost > 0).write({'needs_rfq': False})
        return lines

    def write(self, vals):
        self._check_unlocked(self.project_id)
        if TECHNICAL_FIELDS & set(vals):
            self._check_rd(self.project_id)
        res = super().write(vals)
        if self.env.context.get('aiis_bom_flags'):
            return res
        # #62: a change after the contract check confirmations voids them
        if TECHNICAL_FIELDS & set(vals):
            self.project_id._reset_contract_check(bom=True, prices=True)
        elif COST_FIELDS & set(vals):
            self.project_id._reset_contract_check(prices=True)
        flagged = self.with_context(aiis_bom_flags=True)
        if TECHNICAL_FIELDS & set(vals):
            flagged.write({'needs_rfq': True})
            flagged.filtered(lambda l: l.project_id.bom_approved).write({'changed_since_approval': True})
        elif COST_FIELDS & set(vals):
            # Procurement entered or updated the quote
            flagged.filtered(lambda l: l.sudo().unit_cost > 0).write({'needs_rfq': False})
        return res

    def unlink(self):
        self._check_unlocked(self.project_id)
        self._check_rd(self.project_id)
        projects = self.project_id
        res = super().unlink()
        projects._reset_contract_check(bom=True, prices=True)
        return res


class ProjectBomVersion(models.Model):
    """Snapshot of the BOM taken when it was submitted for solution approval.
    The approval is for this version; later edits show up as changed lines."""
    _name = 'aiis.project.bom.version'
    _description = 'Project BOM Version'
    _order = 'project_id, number desc'

    project_id = fields.Many2one('aiis.project', required=True, ondelete='cascade', index=True)
    number = fields.Integer(required=True)
    name = fields.Char(compute='_compute_name')
    submit_date = fields.Datetime(string='Submitted On', default=fields.Datetime.now)
    submitted_by_id = fields.Many2one('res.users', string='Submitted By', default=lambda self: self.env.user)
    state = fields.Selection([
        ('submitted', 'Submitted'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('superseded', 'Superseded'),
        ('confirmed', 'Confirmed for Contract'),
    ], default='submitted', required=True)
    line_ids = fields.One2many('aiis.project.bom.version.line', 'version_id', string='Lines')
    attachment_ids = fields.Many2many(
        'ir.attachment', 'aiis_project_bom_version_attachment_rel', 'version_id', 'attachment_id',
        string='Solution Documents', readonly=True,
        help='Copy of the solution documents when this version was saved (#59).')
    attachment_count = fields.Integer(string='Documents', compute='_compute_attachment_count')

    @api.depends('attachment_ids')
    def _compute_attachment_count(self):
        for version in self:
            version.attachment_count = len(version.attachment_ids)

    @api.depends('number')
    def _compute_name(self):
        for version in self:
            version.name = 'v%s' % version.number


class ProjectBomVersionLine(models.Model):
    _name = 'aiis.project.bom.version.line'
    _description = 'Project BOM Version Line'
    _order = 'version_id, sequence, id'

    version_id = fields.Many2one('aiis.project.bom.version', required=True, ondelete='cascade')
    sequence = fields.Integer()
    name = fields.Char(string='Item', required=True)
    specification = fields.Char(string='Specification / Model')
    quantity = fields.Float(digits='Product Unit of Measure')
    uom_id = fields.Many2one('uom.uom', string='Unit')
    product_id = fields.Many2one('product.product', string='Product')
