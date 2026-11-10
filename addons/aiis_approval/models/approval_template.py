from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ApprovalTemplate(models.Model):
    """Which approval rules a kind of document follows. One active template
    per document type and gate (a gate is one decision point on a document
    that has several, e.g. the project gates; most documents have none)."""
    _name = 'aiis.approval.template'
    _description = 'Approval Template'
    _order = 'res_model, gate, id'

    name = fields.Char(required=True, translate=True)
    model_id = fields.Many2one('ir.model', string='Document Type', required=True, ondelete='cascade')
    res_model = fields.Char(related='model_id.model', store=True, index=True)
    gate = fields.Char(help='Only for documents with several approval gates (e.g. project stages).')
    active = fields.Boolean(default=True)
    node_ids = fields.One2many('aiis.approval.template.node', 'template_id', string='Approval Steps', copy=True)
    note = fields.Text()

    @api.constrains('res_model', 'gate', 'active')
    def _check_one_active(self):
        for template in self.filtered('active'):
            others = self.search_count([
                ('id', '!=', template.id), ('res_model', '=', template.res_model),
                ('gate', '=', template.gate or False), ('active', '=', True)])
            if others:
                raise ValidationError(_(
                    "There is already an active approval template for %s. Archive it first.",
                    template.model_id.name))

    @api.model
    def _for_document(self, res_model, gate=False):
        return self.sudo().search(
            [('res_model', '=', res_model), ('gate', '=', gate or False)], limit=1)


class ApprovalTemplateNode(models.Model):
    """One step of a template. Steps run in sequence order; steps that share
    a sequence number run in parallel and all must approve."""
    _name = 'aiis.approval.template.node'
    _description = 'Approval Template Step'
    _order = 'sequence, id'

    template_id = fields.Many2one('aiis.approval.template', required=True, ondelete='cascade')
    sequence = fields.Integer(default=10, help='Steps with the same number run in parallel.')
    node_type = fields.Selection([
        ('hierarchy', 'Reporting Line'),
        ('department_head', 'Department Head'),
        ('position', 'Position'),
    ], string='Approved By', required=True, default='hierarchy')
    hierarchy_end = fields.Selection([
        ('department_head', 'Up to Department Head'),
        ('ceo', 'Up to CEO'),
    ], string='Up To', default='department_head',
        help='Every manager on the requester\'s reporting line approves, up to this level.')
    department_id = fields.Many2one('hr.department', help='Its head approves.')
    position_id = fields.Many2one('aiis.approval.position', string='Position')
    min_amount = fields.Float(
        string='Only Above Amount',
        help='Leave 0 to always apply. Otherwise this step only applies when the '
             'document amount (in company currency) is above this value.')
    name = fields.Char(compute='_compute_name')

    @api.depends('node_type', 'hierarchy_end', 'department_id', 'position_id', 'min_amount')
    def _compute_name(self):
        end_labels = dict(self._fields['hierarchy_end'].selection)
        for node in self:
            if node.node_type == 'hierarchy':
                label = _("Reporting line (%s)", end_labels.get(node.hierarchy_end, '').lower())
            elif node.node_type == 'department_head':
                label = _("Head of %s", node.department_id.name or '?')
            else:
                label = node.position_id.name or '?'
            if node.min_amount:
                label = _("%(step)s, above %(amount)s", step=label, amount='{:,.0f}'.format(node.min_amount))
            node.name = label

    @api.constrains('node_type', 'hierarchy_end', 'department_id', 'position_id')
    def _check_parameters(self):
        for node in self:
            if node.node_type == 'hierarchy' and not node.hierarchy_end:
                raise ValidationError(_("A reporting-line step needs the level it goes up to."))
            if node.node_type == 'department_head' and not node.department_id:
                raise ValidationError(_("A department-head step needs a department."))
            if node.node_type == 'position' and not node.position_id:
                raise ValidationError(_("A position step needs a position."))
