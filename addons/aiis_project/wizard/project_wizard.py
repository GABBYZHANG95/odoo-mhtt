from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ProjectAcceptWizard(models.TransientModel):
    _name = 'aiis.project.accept.wizard'
    _description = 'Customer Accepted the Quotation'

    project_id = fields.Many2one('aiis.project', required=True)
    customer_po_ref = fields.Char(string='Customer PO Number', required=True, default=lambda self: self._default_po_ref())

    @api.model
    def _default_po_ref(self):
        # accepting a new quotation after a cost increase: usually the same PO, revised
        project = self.env['aiis.project'].browse(self.env.context.get('default_project_id'))
        return project.customer_po_ref or False

    def action_confirm(self):
        self.ensure_one()
        self.project_id._customer_accepted(self.customer_po_ref)
        return {'type': 'ir.actions.act_window_close'}


class ProjectRejectWizard(models.TransientModel):
    _name = 'aiis.project.reject.wizard'
    _description = 'Customer Rejected the Quotation'

    project_id = fields.Many2one('aiis.project', required=True)
    outcome = fields.Selection([
        ('pause', 'Pause - the customer may come back'),
        ('close', 'Close the project'),
    ], required=True, default='pause')
    restart_date = fields.Date(string='Expected Restart Date')
    reason = fields.Text(required=True)

    def action_confirm(self):
        self.ensure_one()
        self.project_id._customer_rejected(self.outcome, self.reason, self.restart_date)
        return {'type': 'ir.actions.act_window_close'}


class ProjectRenewWizard(models.TransientModel):
    _name = 'aiis.project.renew.wizard'
    _description = 'Renew Project Maintenance'

    project_id = fields.Many2one('aiis.project', required=True)
    current_end = fields.Date(related='project_id.maintenance_end', string='Current End')
    new_end = fields.Date(string='New End Date', required=True)

    def action_confirm(self):
        self.ensure_one()
        self.project_id._renew(self.new_end)
        return {'type': 'ir.actions.act_window_close'}


class ProjectPrWizard(models.TransientModel):
    """Project manager picks BOM lines and quantities for the next batch;
    the purchase request goes straight to Procurement."""
    _name = 'aiis.project.pr.wizard'
    _description = 'Create a Project Purchase Request'

    project_id = fields.Many2one('aiis.project', required=True)
    needed_by_date = fields.Date(string='Needed By')
    reason = fields.Text(string='Purpose', help='e.g. which delivery batch this is for')
    line_ids = fields.One2many('aiis.project.pr.wizard.line', 'wizard_id', string='BOM Lines',
                               compute='_compute_line_ids', store=True, readonly=False)

    @api.depends('project_id')
    def _compute_line_ids(self):
        for wizard in self:
            lines = wizard.project_id.sudo().bom_line_ids.filtered(lambda l: l.qty_remaining > 0)
            wizard.line_ids = [(5,)] + [(0, 0, {'bom_line_id': line.id, 'quantity': 0.0}) for line in lines]

    def action_confirm(self):
        self.ensure_one()
        project = self.project_id
        project._check_project_manager()
        picked = self.line_ids.filtered(lambda l: l.quantity > 0)
        if not picked:
            raise UserError(_("Enter a quantity on at least one BOM line."))
        for line in picked:
            if line.quantity > line.qty_remaining:
                raise UserError(_(
                    "%(item)s: only %(left)s left to request.", item=line.bom_line_id.name, left=line.qty_remaining))
        employee = self.env.user.employee_id
        if not employee:
            raise UserError(_("Your user has no employee record, so the request has no requester."))
        request = self.env['procurement.request'].create({
            'employee_id': employee.id,
            'aiis_project_id': project.id,
            'analytic_account_id': project.sudo().analytic_account_id.id,
            'needed_by_date': self.needed_by_date,
            'reason': self.reason or _("Project %s", project.display_name),
            'line_ids': [(0, 0, {
                'description': ' - '.join(filter(None, [line.bom_line_id.name, line.bom_line_id.specification])),
                'product_id': line.bom_line_id.product_id.id,
                'quantity': line.quantity,
                'uom_id': line.bom_line_id.uom_id.id,
                'aiis_bom_line_id': line.bom_line_id.id,
            }) for line in picked],
        })
        request.action_submit()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'procurement.request',
            'res_id': request.id,
            'view_mode': 'form',
            'target': 'current',
        }


class ProjectPrWizardLine(models.TransientModel):
    _name = 'aiis.project.pr.wizard.line'
    _description = 'Create a Project Purchase Request - Line'

    wizard_id = fields.Many2one('aiis.project.pr.wizard', required=True, ondelete='cascade')
    # not readonly: the form must send it back for the computed lines (a
    # readonly field is not saved and the line would have no BOM line)
    bom_line_id = fields.Many2one('aiis.project.bom.line', required=True)
    name = fields.Char(related='bom_line_id.name')
    specification = fields.Char(related='bom_line_id.specification')
    uom_id = fields.Many2one(related='bom_line_id.uom_id')
    qty_bom = fields.Float(related='bom_line_id.quantity', string='BOM Qty')
    qty_requested = fields.Float(related='bom_line_id.qty_requested')
    qty_remaining = fields.Float(related='bom_line_id.qty_remaining')
    quantity = fields.Float(string='Request Now', digits='Product Unit of Measure')
