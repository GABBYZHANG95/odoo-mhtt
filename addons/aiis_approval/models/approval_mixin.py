from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .approval_request import APPROVAL_ACTIVITY

REJECTED_ACTIVITY = 'aiis_approval.mail_activity_rejected'


class ApprovalMixin(models.AbstractModel):
    """Plug a document into the approval engine.

    The document tells the engine three things by overriding:

    - `_approval_requester()`: the employee whose reporting line applies;
    - `_approval_amount()`: amount in company currency for step conditions;
    - `_approval_on_approved()` / `_approval_on_rejected()` /
      `_approval_on_withdrawn()`: what happens to the document then.

    and calls `_approval_submit()` from its own submit action. Fields listed
    in `_approval_restart_fields()` restart a running approval when they
    change. The form shows the approve / reject / withdraw buttons and the
    `approval_progress_html` field; see the procurement request views.
    """
    _name = 'aiis.approval.mixin'
    _description = 'Approval Mixin'

    approval_request_id = fields.Many2one(
        'aiis.approval.request', string='Current Approval', copy=False, readonly=True, index=True)
    aiis_approval_state = fields.Selection([
        ('none', 'Not Submitted'),
        ('pending', 'Waiting for Approval'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ], string='Approval Status', default='none', copy=False, readonly=True, index=True)
    approval_pending_names = fields.Char(
        string='Waiting For', compute='_compute_approval_display', compute_sudo=True)
    approval_progress_html = fields.Html(
        string='Approval Progress', compute='_compute_approval_display', compute_sudo=True, sanitize=False)
    approval_rejection_note = fields.Char(related='approval_request_id.rejection_note', string='Rejected By')
    approval_rejection_reason = fields.Text(related='approval_request_id.rejection_reason', string='Rejection Reason')
    approval_can_act = fields.Boolean(compute='_compute_approval_can_act')
    approval_can_withdraw = fields.Boolean(compute='_compute_approval_can_act')
    is_my_approval = fields.Boolean(
        string='Waiting for My Approval', compute='_compute_approval_can_act', search='_search_is_my_approval')
    approval_user_ids = fields.Many2many(
        'res.users', string='Approvers', compute='_compute_approval_user_ids', search='_search_approval_user_ids',
        compute_sudo=True, help='Everyone on an approver list of this document - used for access rules.')

    # ------------------------------------------------------------------
    # Hooks for the document
    # ------------------------------------------------------------------

    def _approval_gate(self):
        return False

    def _approval_requester(self):
        return self.env['hr.employee']

    def _approval_amount(self):
        return 0.0

    def _approval_restart_fields(self):
        return []

    def _approval_on_approved(self):
        pass

    def _approval_on_rejected(self):
        pass

    def _approval_on_withdrawn(self):
        pass

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    def _approval_lines(self, states=None):
        self.ensure_one()
        lines = self.approval_request_id.sudo().line_ids
        return lines.filtered(lambda l: l.state in states) if states else lines

    @api.depends('approval_request_id.line_ids.state', 'approval_request_id.state')
    def _compute_approval_display(self):
        state_labels = dict(self.env['aiis.approval.line']._fields['state']._description_selection(self.env))
        badge = {'pending': 'text-bg-warning', 'approved': 'text-bg-success', 'auto': 'text-bg-success',
                 'rejected': 'text-bg-danger', 'waiting': 'text-bg-light', 'reassigned': 'text-bg-secondary',
                 'cancelled': 'text-bg-secondary'}
        for record in self:
            request = record.approval_request_id.sudo()
            pending = record._approval_lines(['pending']) if request else self.env['aiis.approval.line']
            record.approval_pending_names = ', '.join(pending.user_id.mapped('name')) or False
            if not request:
                record.approval_progress_html = Markup('<p class="text-muted">%s</p>') % _("Not submitted for approval yet.")
                continue
            rows = Markup()
            for line in request.line_ids.sorted(lambda l: (l.step, l.id)):
                rows += Markup(
                    '<tr><td>%s</td><td>%s</td><td>%s</td>'
                    '<td><span class="badge rounded-pill %s">%s</span></td><td>%s</td><td>%s</td></tr>'
                ) % (
                    line.step, line.user_id.name, line.node_label or '',
                    badge.get(line.state, ''), state_labels.get(line.state, line.state),
                    fields.Datetime.to_string(fields.Datetime.context_timestamp(record, line.decision_date))[:16]
                    if line.decision_date else '',
                    line.comment or '',
                )
            header = _("Submitted by %(user)s on %(date)s", user=request.submitter_id.name,
                       date=fields.Datetime.to_string(fields.Datetime.context_timestamp(record, request.submit_date))[:16])
            record.approval_progress_html = Markup(
                '<p class="text-muted mb-2">%s</p>'
                '<table class="table table-sm o_aiis_approval_progress"><thead><tr>'
                '<th>%s</th><th>%s</th><th>%s</th><th>%s</th><th>%s</th><th>%s</th>'
                '</tr></thead><tbody>%s</tbody></table>'
            ) % (escape(header), _("Step"), _("Approver"), _("Approves As"), _("Status"), _("Date"),
                 _("Comment"), rows)

    @api.depends_context('uid')
    @api.depends('approval_request_id.line_ids.state', 'approval_request_id.submitter_id')
    def _compute_approval_can_act(self):
        is_admin = self.env.user.has_group('base.group_system')
        for record in self:
            request = record.approval_request_id.sudo()
            open_request = request.state == 'pending'
            mine = open_request and bool(record._approval_lines(['pending']).filtered(lambda l: l.user_id == self.env.user))
            record.approval_can_act = mine
            record.is_my_approval = mine
            record.approval_can_withdraw = open_request and (request.submitter_id == self.env.user or is_admin)

    def _search_is_my_approval(self, operator, value):
        if operator not in ('=', '!=') or not isinstance(value, bool):
            raise UserError(_("Unsupported search on 'Waiting for My Approval'."))
        lines = self.env['aiis.approval.line'].sudo().search([
            ('res_model', '=', self._name), ('user_id', '=', self.env.uid),
            ('state', '=', 'pending'), ('request_id.state', '=', 'pending')])
        domain = [('id', 'in', lines.mapped('res_id'))]
        return domain if (operator == '=') == value else ['!'] + domain

    @api.depends('approval_request_id.line_ids.user_id')
    def _compute_approval_user_ids(self):
        lines = self.env['aiis.approval.line'].sudo().search([('res_model', '=', self._name), ('res_id', 'in', self.ids)])
        for record in self:
            record.approval_user_ids = lines.filtered(lambda l: l.res_id == record.id).user_id

    def _search_approval_user_ids(self, operator, value):
        if operator not in ('in', '='):
            raise UserError(_("Unsupported search on approvers."))
        users = value if isinstance(value, (list, tuple)) else [value]
        lines = self.env['aiis.approval.line'].sudo().search([('res_model', '=', self._name), ('user_id', 'in', users)])
        return [('id', 'in', lines.mapped('res_id'))]

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _approval_submit(self):
        """Start an approval: generate and freeze the approver list. With
        nobody to ask (e.g. the Chairman's own request) it is approved at once."""
        for record in self:
            if record.aiis_approval_state == 'pending':
                raise UserError(_("%s is already waiting for approval.", record.display_name))
            template = self.env['aiis.approval.template']._for_document(record._name, record._approval_gate())
            if not template:
                raise UserError(_(
                    "No approval template is set up for %s. Ask an administrator (Settings > Approvals > Templates).",
                    self.env['ir.model']._get(record._name).name))
            # submitted again: the rejection is dealt with
            record.approval_request_id.sudo().returned = False
            record.sudo().activity_ids.filtered(
                lambda a: a.activity_type_id == self.env.ref(REJECTED_ACTIVITY)).action_feedback(
                feedback=_("Submitted again."))
            request = self.env['aiis.approval.request']._create_for(
                record, template, record._approval_requester(), record._approval_amount())
            record.sudo().write({'approval_request_id': request.id, 'aiis_approval_state': 'pending'})
            if not request.line_ids:
                record.sudo().message_post(body=_("Submitted - no approval needed."))
            else:
                record.sudo().message_post(body=_("Submitted for approval: %s", request._approvers_summary()))
            if request._advance():
                record._approval_finish()

    def _approval_finish(self):
        self.ensure_one()
        self.sudo().write({'aiis_approval_state': 'approved'})
        if self.approval_request_id.line_ids:
            self.sudo().message_post(body=_("Fully approved."))
        self.sudo()._approval_on_approved()

    def _approval_my_line(self):
        self.ensure_one()
        request = self.approval_request_id.sudo()
        line = request.state == 'pending' and self._approval_lines(['pending']).filtered(
            lambda l: l.user_id == self.env.user)[:1]
        if not line:
            raise UserError(_("%s is not waiting for your approval.", self.display_name))
        return line

    def _approval_approve(self, comment=False):
        for record in self:
            line = record._approval_my_line()
            line.write({'state': 'approved', 'decision_date': fields.Datetime.now(), 'comment': comment})
            # sudo: an approver may only have read access to the document;
            # the message and the done activity are still in their name
            record.sudo().activity_feedback([APPROVAL_ACTIVITY], user_id=self.env.uid)
            body = _("Approved by %(user)s (%(label)s).", user=self.env.user.name, label=line.node_label)
            if comment:
                body = Markup('%s<br/>%s') % (body, comment)
            record.sudo().message_post(body=body)
            if record.approval_request_id._advance():
                record._approval_finish()
        return True

    def action_approval_approve(self):
        return self._approval_approve()

    def action_approval_open_reject(self):
        self.ensure_one()
        self._approval_my_line()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Reject'),
            'res_model': 'aiis.approval.reject.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_res_model': self._name, 'default_res_id': self.id},
        }

    def _approval_reject(self, reason):
        if not (reason or '').strip():
            raise UserError(_("Please give a reason for the rejection."))
        for record in self:
            line = record._approval_my_line()
            line.write({'state': 'rejected', 'decision_date': fields.Datetime.now(), 'comment': reason})
            record.approval_request_id._close('rejected')
            record.approval_request_id.sudo().returned = True
            record.sudo().write({'aiis_approval_state': 'rejected'})
            record.sudo().message_post(body=Markup('%s<br/>%s') % (
                _("Rejected by %(user)s (%(label)s):", user=self.env.user.name, label=line.node_label), reason))
            record.sudo()._approval_on_rejected()
            record._approval_notify_rejected(line, reason)
        return True

    def _approval_notify_rejected(self, line, reason):
        """The submitter gets a to-do with the reason (#55)"""
        self.ensure_one()
        submitter = line.request_id.sudo().submitter_id
        if not submitter.active or submitter.share:
            return
        self.sudo().with_context(mail_activity_quick_update=True).activity_schedule(
            REJECTED_ACTIVITY, user_id=submitter.id,
            summary=_("Rejected: %s", self.display_name),
            note=Markup('%s<br/>%s') % (
                _("Rejected by %(user)s (%(label)s):", user=self.env.user.name, label=line.node_label), reason))

    def action_approval_withdraw(self):
        for record in self:
            request = record.approval_request_id.sudo()
            if request.state != 'pending':
                raise UserError(_("%s is not waiting for approval.", record.display_name))
            if request.submitter_id != self.env.user and not self.env.user.has_group('base.group_system'):
                raise UserError(_("Only %s, who submitted it, can withdraw it.", request.submitter_id.name))
            request._close('withdrawn')
            record.sudo().write({'aiis_approval_state': 'none'})
            record.sudo().message_post(body=_("Approval withdrawn by %s.", self.env.user.name))
            record.sudo()._approval_on_withdrawn()
        return True

    def _approval_cancel(self, reason):
        """Drop a running approval without calling the document hooks, e.g.
        because the document itself was reset or cancelled."""
        for record in self.filtered(lambda r: r.aiis_approval_state == 'pending'):
            record.approval_request_id._close('cancelled')
            record.sudo().write({'aiis_approval_state': 'none'})
            record.sudo().message_post(body=_("Approval cancelled: %s", reason))

    def write(self, vals):
        if vals.get('state') in ('cancel', 'cancelled'):
            # a cancelled document is not coming back: off Returned to Me
            returned = self.approval_request_id.sudo().filtered('returned')
            returned.returned = False
            self.sudo().filtered(lambda r: r.approval_request_id in returned).activity_unlink([REJECTED_ACTIVITY])
        restart = self.env['aiis.approval.mixin']
        if not self.env.context.get('aiis_approval_no_restart'):
            watched = set(self._approval_restart_fields()) & set(vals)
            if watched:
                restart = self.filtered(lambda r: r.aiis_approval_state == 'pending')
        res = super().write(vals)
        for record in restart:
            record._approval_cancel(_("changed while waiting for approval"))
            record.with_context(aiis_approval_no_restart=True)._approval_submit()
        return res

    def unlink(self):
        # A deleted document must not leave an approval or revision in To do.
        for request in self.sudo().approval_request_id.filtered(
                lambda r: r.state == 'pending' or r.returned):
            request._close('cancelled')
            request.returned = False
        return super().unlink()
