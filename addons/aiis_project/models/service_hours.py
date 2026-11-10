from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class ServiceHoursRequest(models.Model):
    """Maintenance service hours (design doc 4.6): an internal service
    engineer asks for an hours budget on a project before working; the
    reporting line up to the department head approves. Running out, they
    ask for an extension, approved the same way. Hours are only tracked,
    not costed (#31)."""
    _name = 'aiis.service.hours.request'
    _description = 'Service Hours Request'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'aiis.approval.mixin']
    _order = 'id desc'

    name = fields.Char(default='New', readonly=True, copy=False)
    project_id = fields.Many2one('aiis.project', required=True, index=True, tracking=True,
                                 domain=[('state', 'in', ('delivery', 'delivery_review', 'maintenance'))])
    employee_id = fields.Many2one(
        'hr.employee', string='Service Engineer', required=True, tracking=True,
        default=lambda self: self.env.user.employee_id)
    extends_id = fields.Many2one(
        'aiis.service.hours.request', string='Extends', readonly=True, index=True, ondelete='cascade',
        help='This request adds hours to an approved request that ran out.')
    extension_ids = fields.One2many('aiis.service.hours.request', 'extends_id', string='Extensions')
    hours = fields.Float(string='Hours Requested', required=True, tracking=True)
    date_from = fields.Date(string='From')
    date_to = fields.Date(string='To')
    description = fields.Text(string='Service Scope')
    state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('approved', 'Approved'),
    ], default='draft', required=True, tracking=True, copy=False)
    record_ids = fields.One2many('aiis.service.record', 'request_id', string='Service Records')
    hours_approved = fields.Float(string='Approved Hours', compute='_compute_hours', compute_sudo=True)
    hours_used = fields.Float(string='Used Hours', compute='_compute_hours', compute_sudo=True)
    hours_remaining = fields.Float(string='Remaining Hours', compute='_compute_hours', compute_sudo=True)

    @api.depends('state', 'hours', 'extension_ids.state', 'extension_ids.hours', 'record_ids.hours')
    def _compute_hours(self):
        for request in self:
            approved = sum(r.hours for r in request | request.extension_ids if r.state == 'approved')
            request.hours_approved = approved
            request.hours_used = sum(request.record_ids.mapped('hours'))
            request.hours_remaining = approved - request.hours_used

    @api.constrains('hours')
    def _check_hours(self):
        if any(request.hours <= 0 for request in self):
            raise ValidationError(_("Request a positive number of hours."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('aiis.service.hours.request') or 'New'
        return super().create(vals_list)

    def action_submit(self):
        for request in self:
            if request.state != 'draft':
                raise UserError(_("Only a draft request can be submitted."))
            if request.extends_id and request.extends_id.state != 'approved':
                raise UserError(_("An extension is only for an approved request."))
            request.state = 'submitted'
            request._approval_submit()

    def action_new_extension(self):
        self.ensure_one()
        if self.state != 'approved' or self.extends_id:
            raise UserError(_("Extensions are requested on an approved request."))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Request More Hours'),
            'res_model': self._name,
            'view_mode': 'form',
            'target': 'current',
            'context': {
                'default_extends_id': self.id,
                'default_project_id': self.project_id.id,
                'default_employee_id': self.employee_id.id,
                'default_description': self.description,
            },
        }

    def _approval_requester(self):
        return self.employee_id

    def _approval_on_approved(self):
        self.write({'state': 'approved'})
        for request in self.filtered('extends_id'):
            request.extends_id.message_post(body=_(
                "%(hours)s more hours approved (%(name)s).", hours=request.hours, name=request.name))

    def _approval_on_rejected(self):
        self.write({'state': 'draft'})

    def _approval_on_withdrawn(self):
        self.write({'state': 'draft'})


class ServiceRecord(models.Model):
    """Hours actually worked against an approved service hours request"""
    _name = 'aiis.service.record'
    _description = 'Service Record'
    _order = 'date desc, id desc'

    request_id = fields.Many2one(
        'aiis.service.hours.request', required=True, ondelete='cascade', index=True,
        domain=[('state', '=', 'approved'), ('extends_id', '=', False)])
    project_id = fields.Many2one(related='request_id.project_id', store=True)
    employee_id = fields.Many2one(related='request_id.employee_id', store=True)
    date = fields.Date(required=True, default=fields.Date.context_today)
    hours = fields.Float(required=True)
    description = fields.Char(string='Work Done', required=True)

    @api.constrains('hours', 'request_id')
    def _check_within_budget(self):
        for record in self:
            request = record.request_id.sudo()
            if record.hours <= 0:
                raise ValidationError(_("Record a positive number of hours."))
            if request.state != 'approved' or request.extends_id:
                raise ValidationError(_("Service hours are recorded on an approved request."))
            if request.hours_used > request.hours_approved:
                raise ValidationError(_(
                    "%(name)s has %(left)s approved hours left - request an extension for more.",
                    name=request.name, left=request.hours_approved - (request.hours_used - record.hours)))
