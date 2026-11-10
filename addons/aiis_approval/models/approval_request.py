from odoo import _, api, fields, models
from odoo.exceptions import UserError

APPROVAL_ACTIVITY = 'aiis_approval.mail_activity_approval'

# Levels on the reporting line, lowest first
RANK_STAFF, RANK_DEPARTMENT_HEAD, RANK_CEO, RANK_CHAIRMAN = 0, 1, 2, 3
END_RANKS = {'department_head': RANK_DEPARTMENT_HEAD, 'ceo': RANK_CEO}


class ApprovalRequest(models.Model):
    """One submission of a document for approval. The approver list (the
    lines) is generated from the template when it is submitted and does not
    change afterwards: a reorganisation only affects later submissions."""
    _name = 'aiis.approval.request'
    _description = 'Approval Request'
    _order = 'id desc'
    _rec_name = 'document_name'

    res_model = fields.Char(string='Document Model', required=True, index=True, readonly=True)
    res_id = fields.Many2oneReference(string='Document ID', model_field='res_model', required=True, index=True, readonly=True)
    document_name = fields.Char(string='Document', readonly=True)
    model_name = fields.Char(string='Document Type', compute='_compute_model_name')
    gate = fields.Char(readonly=True)
    template_id = fields.Many2one('aiis.approval.template', readonly=True, ondelete='set null')
    submitter_id = fields.Many2one('res.users', string='Submitted By', required=True, readonly=True)
    submit_date = fields.Datetime(string='Submitted On', required=True, readonly=True)
    amount = fields.Float(readonly=True, help='Amount in company currency the step conditions were checked against.')
    state = fields.Selection([
        ('pending', 'In Progress'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('withdrawn', 'Withdrawn'),
        ('cancelled', 'Cancelled'),
    ], required=True, default='pending', readonly=True)
    done_date = fields.Datetime(string='Closed On', readonly=True)
    line_ids = fields.One2many('aiis.approval.line', 'request_id', string='Approvers', readonly=True)
    # rejected and not submitted again yet: My Approvals › Returned to Me (#56)
    returned = fields.Boolean(string='Returned to Submitter', readonly=True, index=True, copy=False)
    rejection_note = fields.Char(string='Rejected By', compute='_compute_rejection_note', compute_sudo=True)
    rejection_reason = fields.Text(string='Reason', compute='_compute_rejection_note', compute_sudo=True)

    @api.depends('line_ids.state', 'line_ids.comment')
    def _compute_rejection_note(self):
        for request in self:
            line = request.line_ids.filtered(lambda l: l.state == 'rejected')[-1:]
            request.rejection_note = '%s (%s), %s' % (
                line.user_id.name, line.node_label or '',
                fields.Datetime.to_string(fields.Datetime.context_timestamp(request, line.decision_date))[:16]
                if line.decision_date else '') if line else False
            request.rejection_reason = line.comment

    @api.depends('res_model')
    def _compute_model_name(self):
        for request in self:
            request.model_name = self.env['ir.model']._get(request.res_model).name if request.res_model else False

    def _document(self):
        self.ensure_one()
        return self.env[self.res_model].browse(self.res_id)

    def action_open_document(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': self.res_model,
            'res_id': self.res_id,
            'view_mode': 'form',
            'target': 'current',
        }

    # ------------------------------------------------------------------
    # Building the approver list
    # ------------------------------------------------------------------

    @api.model
    def _expand_template(self, template, requester, amount):
        """Approver list for a submission: [(step, user, node, label)].
        Steps start at 1; a reporting-line node takes one step per level,
        nodes sharing a sequence number run side by side."""
        result = []
        step = 1
        nodes = template.node_ids.sorted(lambda n: (n.sequence, n.id))
        for sequence in sorted(set(nodes.mapped('sequence'))):
            width = 0
            for node in nodes.filtered(lambda n: n.sequence == sequence):
                if node.min_amount and not amount > node.min_amount:
                    continue
                users = self._expand_node(node, requester)
                for offset, user in enumerate(users):
                    result.append((step + offset, user, node, self._node_label(node, user, offset)))
                width = max(width, len(users))
            step += width
        return result

    @api.model
    def _node_label(self, node, user, offset):
        if node.node_type == 'hierarchy':
            return _("Reporting line, level %s", offset + 1)
        if node.node_type == 'department_head':
            return _("Head of %s", node.department_id.name)
        return node.position_id.name

    @api.model
    def _expand_node(self, node, requester):
        if node.node_type == 'position':
            return [node.position_id.sudo()._require_holder()]
        if node.node_type == 'department_head':
            user = node.department_id.sudo().manager_id.user_id
            if not user:
                raise UserError(_(
                    "The department %s has no head with a login, so there is no one to approve this.",
                    node.department_id.name))
            return [user]
        if not requester:
            raise UserError(_("This document has no requester (employee), so its reporting line is unknown."))
        return self._hierarchy_approvers(requester.sudo(), node.hierarchy_end)

    @api.model
    def _hierarchy_approvers(self, employee, end):
        """Every manager on the reporting line approves, up to the end level
        (design doc 2.2):

        - requester already at or above the end level: the next level up
          approves (a department head's own request goes to the CEO, the
          CEO's to the Chairman); the Chairman needs no approval at all;
        - managers without a login are skipped and the next one up approves;
        - a reporting line that runs out before the end level goes to the
          CEO (to the Chairman for the CEO's own requests).
        """
        Position = self.env['aiis.approval.position']
        ceo, chairman = Position._holder('ceo'), Position._holder('chairman')
        department_heads = set(self.env['hr.department'].sudo().search([('manager_id', '!=', False)]).manager_id.ids)

        def rank(emp):
            if emp.user_id and emp.user_id == chairman:
                return RANK_CHAIRMAN
            if emp.user_id and emp.user_id == ceo:
                return RANK_CEO
            return RANK_DEPARTMENT_HEAD if emp.id in department_heads else RANK_STAFF

        own_rank = rank(employee)
        if own_rank == RANK_CHAIRMAN:
            return []
        target = max(END_RANKS[end], own_rank + 1)
        approvers = []
        seen = {employee.id}
        manager = employee.parent_id
        while manager and manager.id not in seen:
            seen.add(manager.id)
            if manager.user_id.active:
                approvers.append(manager.user_id)
                if rank(manager) >= target:
                    return approvers
            manager = manager.parent_id
        fallback = ceo if target <= RANK_CEO else chairman
        if not fallback:
            raise UserError(_(
                "The reporting line of %(employee)s ends before reaching the %(level)s, and nobody "
                "holds the %(position)s position to take over. Ask an administrator to set it "
                "(Settings > Approvals > Positions).",
                employee=employee.name,
                level=_("CEO") if target <= RANK_CEO else _("Chairman"),
                position=_("CEO") if target <= RANK_CEO else _("Chairman")))
        if fallback not in approvers:
            approvers.append(fallback)
        return approvers

    @api.model
    def _create_for(self, document, template, requester, amount):
        """Create the request with its frozen approver list. The same person
        appearing more than once approves once: the earliest line counts,
        later ones are marked auto-approved."""
        request = self.sudo().create({
            'res_model': document._name,
            'res_id': document.id,
            'document_name': document.display_name,
            'gate': template.gate,
            'template_id': template.id,
            'submitter_id': self.env.uid,
            'submit_date': fields.Datetime.now(),
            'amount': amount,
        })
        seen = set()
        line_vals = []
        for step, user, node, label in self._expand_template(template, requester, amount):
            line_vals.append({
                'request_id': request.id,
                'step': step,
                'user_id': user.id,
                'node_id': node.id,
                'node_label': label,
                'state': 'auto' if user.id in seen else 'waiting',
            })
            seen.add(user.id)
        self.env['aiis.approval.line'].sudo().create(line_vals)
        return request

    # ------------------------------------------------------------------
    # Running it
    # ------------------------------------------------------------------

    def _advance(self):
        """Open the next step once nobody is pending any more. Returns True
        when the whole request is approved."""
        self.ensure_one()
        lines = self.sudo().line_ids
        if lines.filtered(lambda l: l.state == 'pending'):
            return False
        waiting = lines.filtered(lambda l: l.state == 'waiting')
        if not waiting:
            self.sudo().write({'state': 'approved', 'done_date': fields.Datetime.now()})
            return True
        next_step = min(waiting.mapped('step'))
        opened = waiting.filtered(lambda l: l.step == next_step)
        opened.write({'state': 'pending'})
        # sudo: whoever approved the previous step may only read the document
        document = self._document().sudo().with_context(mail_activity_quick_update=True)
        for line in opened:
            # quick_update: no "activity assigned" e-mail - the to-do shows
            # in the approver's activities and My Approvals anyway
            document.activity_schedule(
                APPROVAL_ACTIVITY, user_id=line.user_id.id,
                summary=_("Approval: %s", line.node_label))
        return False

    def _close(self, state):
        self.ensure_one()
        self.sudo().write({'state': state, 'done_date': fields.Datetime.now()})
        self.sudo().line_ids.filtered(lambda l: l.state in ('waiting', 'pending')).write({'state': 'cancelled'})
        self._document().sudo().activity_unlink([APPROVAL_ACTIVITY])

    def _approvers_summary(self):
        self.ensure_one()
        steps = {}
        for line in self.sudo().line_ids.filtered(lambda l: l.state != 'auto'):
            steps.setdefault(line.step, []).append(line.user_id.name)
        return ' → '.join(' + '.join(names) for _step, names in sorted(steps.items()))


class ApprovalLine(models.Model):
    """One approver on a request"""
    _name = 'aiis.approval.line'
    _description = 'Approval Line'
    _order = 'request_id desc, step, id'
    _rec_name = 'document_name'

    request_id = fields.Many2one('aiis.approval.request', required=True, ondelete='cascade', index=True)
    step = fields.Integer(required=True, help='Lines with the same step approve in parallel.')
    user_id = fields.Many2one('res.users', string='Approver', required=True, index=True)
    node_id = fields.Many2one('aiis.approval.template.node', string='Template Step', ondelete='set null')
    node_label = fields.Char(string='Approves As')
    state = fields.Selection([
        ('waiting', 'Waiting'),
        ('pending', 'To Approve'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('auto', 'Approved (same approver)'),
        ('reassigned', 'Reassigned'),
        ('cancelled', 'Cancelled'),
    ], required=True, default='waiting', index=True)
    decision_date = fields.Datetime(string='Decided On')
    comment = fields.Text()
    reassigned_to_id = fields.Many2one('aiis.approval.line', string='Reassigned To', readonly=True)

    res_model = fields.Char(related='request_id.res_model', store=True, index=True)
    res_id = fields.Many2oneReference(related='request_id.res_id', model_field='res_model', store=True, index=True)
    document_name = fields.Char(related='request_id.document_name', string='Document')
    model_name = fields.Char(related='request_id.model_name', string='Document Type')
    submitter_id = fields.Many2one(related='request_id.submitter_id', store=True)
    submit_date = fields.Datetime(related='request_id.submit_date', store=True)
    request_state = fields.Selection(related='request_id.state', string='Request Status')

    def _document(self):
        self.ensure_one()
        return self.request_id._document()

    def action_open_document(self):
        self.ensure_one()
        return self.request_id.action_open_document()

    def action_approve(self):
        for line in self:
            line._document()._approval_approve()
        return True

    def action_open_reject(self):
        self.ensure_one()
        return self._document().action_approval_open_reject()

    def action_open_reassign(self):
        self.ensure_one()
        if not self.env.user.has_group('base.group_system'):
            raise UserError(_("Only an administrator can reassign an approval."))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Reassign Approval'),
            'res_model': 'aiis.approval.reassign.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_line_id': self.id},
        }

    def _reassign(self, user):
        """Hand an open line to someone else (administrator only). The old
        line stays as a record of who it was meant for."""
        self.ensure_one()
        if self.state not in ('waiting', 'pending'):
            raise UserError(_("Only approvals that are still open can be reassigned."))
        new_line = self.sudo().copy({'user_id': user.id, 'state': self.state, 'comment': False, 'decision_date': False})
        self.sudo().write({'state': 'reassigned', 'reassigned_to_id': new_line.id,
                           'decision_date': fields.Datetime.now()})
        document = self._document()
        if new_line.state == 'pending':
            activities = document.activity_ids.filtered(
                lambda a: a.activity_type_id == self.env.ref(APPROVAL_ACTIVITY) and a.user_id == self.user_id)
            activities.unlink()
            document.with_context(mail_activity_quick_update=True).activity_schedule(
                APPROVAL_ACTIVITY, user_id=user.id, summary=_("Approval: %s", new_line.node_label))
        document.message_post(body=_(
            "Approval of %(old)s (%(label)s) reassigned to %(new)s by %(admin)s.",
            old=self.user_id.name, label=self.node_label, new=user.name, admin=self.env.user.name))
        return new_line
