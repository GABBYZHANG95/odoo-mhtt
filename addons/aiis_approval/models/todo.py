from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, MissingError


class Todo(models.Model):
    """Live projection of workflow tasks, never a second workflow state.

    Business modules extend _todo_queries. Stable IDs reserve the last digit
    for the source: approval line=0, returned request=1, mail activity=2.
    Only explicit business activity types belong here, not arbitrary reminders.
    """
    _name = 'aiis.todo'
    _description = 'My To do'
    _auto = False
    _log_access = False
    _rec_name = 'document_name'
    _order = 'assigned_on desc, id desc'
    _depends = {
        'aiis.approval.line': ['request_id', 'user_id', 'state'],
        'aiis.approval.request': ['res_model', 'res_id', 'submitter_id', 'submit_date', 'done_date', 'state', 'returned'],
        'aiis.approval.position': ['code', 'active', 'user_id'],
        'res.users': ['active', 'share'],
        'mail.activity': ['res_model_id', 'res_id', 'activity_type_id', 'user_id', 'create_date', 'date_deadline'],
    }

    user_id = fields.Many2one('res.users', readonly=True)
    kind = fields.Selection([
        ('approval', 'To Approve'), ('returned', 'To Revise'),
        ('bill', 'Confirm Bill and Pay'), ('convert', 'Prepare Purchase Order'),
        ('receipt', 'Follow up Shipment and Receive'), ('expense', 'Post and Pay Expenses'),
        ('purchase_revision', 'Revise Purchase Order'),
        ('purchase_acceptance', 'Confirm Purchase Acceptance'),
    ], string='Task', readonly=True)
    res_model = fields.Char(readonly=True)
    res_id = fields.Integer(readonly=True)
    source_id = fields.Integer(readonly=True)
    assigned_on = fields.Datetime(string='Assigned On', readonly=True)
    deadline = fields.Date(string='Due Date', readonly=True)
    document_name = fields.Char(string='Document', compute='_compute_document', search='_search_document_name', compute_sudo=False)
    model_name = fields.Char(string='Document Type', compute='_compute_document', compute_sudo=False)
    submitter_id = fields.Many2one('res.users', string='Submitted By', compute='_compute_document', compute_sudo=False)
    amount = fields.Monetary(compute='_compute_document', compute_sudo=False)
    currency_id = fields.Many2one('res.currency', compute='_compute_document', compute_sudo=False)
    note = fields.Text(string='Details', compute='_compute_document', compute_sudo=False)

    def _todo_queries(self):
        return ["""
            SELECT l.id * 10 AS id, l.user_id, 'approval'::varchar AS kind,
                   r.res_model, r.res_id, l.id AS source_id,
                   COALESCE(a.assigned_on, r.submit_date) AS assigned_on,
                   NULL::date AS deadline
            FROM aiis_approval_line l
            JOIN aiis_approval_request r ON r.id = l.request_id
            LEFT JOIN LATERAL (
                SELECT min(act.create_date) AS assigned_on
                FROM mail_activity act
                JOIN ir_model m ON m.id = act.res_model_id
                WHERE m.model = r.res_model AND act.res_id = r.res_id
                  AND act.user_id = l.user_id
                  AND act.activity_type_id = (
                      SELECT res_id FROM ir_model_data
                      WHERE module = 'aiis_approval' AND name = 'mail_activity_approval')
            ) a ON TRUE
            WHERE l.state = 'pending' AND r.state = 'pending'
        """, """
            SELECT r.id * 10 + 1, r.submitter_id, 'returned'::varchar,
                   r.res_model, r.res_id, r.id, r.done_date, NULL::date
            FROM aiis_approval_request r
            WHERE r.returned AND r.state = 'rejected'
        """]

    def _activity_query(self, kind, model, module, activity_name, join, condition):
        # All arguments are static SQL supplied by installed business modules.
        # Group team reminders into one task per document and assigned user.
        return f"""
            SELECT min(a.id) * 10 + 2 AS id, a.user_id, '{kind}'::varchar AS kind,
                   '{model}'::varchar AS res_model, a.res_id,
                   min(a.id) AS source_id, min(a.create_date) AS assigned_on,
                   min(a.date_deadline) AS deadline
            FROM mail_activity a
            JOIN ir_model m ON m.id = a.res_model_id AND m.model = '{model}'
            JOIN ir_model_data d ON d.module = '{module}' AND d.name = '{activity_name}'
                                   AND d.model = 'mail.activity.type' AND d.res_id = a.activity_type_id
            {join}
            WHERE {condition}
            GROUP BY a.user_id, a.res_id
        """

    def _finance_activity_join(self):
        # Finance duties require both explicit assignment and the CFO position.
        return """
            JOIN aiis_approval_position finance
              ON finance.code = 'cfo' AND finance.active AND finance.user_id = a.user_id
            JOIN res_users finance_user
              ON finance_user.id = finance.user_id AND finance_user.active AND NOT finance_user.share
        """

    @property
    def _table_query(self):
        return ' UNION ALL '.join(self._todo_queries())

    def _document(self):
        self.ensure_one()
        self.check_access_rights('read')
        self.check_access_rule('read')
        document = self.env[self.res_model].browse(self.res_id).exists()
        if not document:
            raise MissingError(_('This document is no longer available. Refresh your To do list.'))
        document.check_access_rights('read')
        document.check_access_rule('read')
        return document

    def _search_document_name(self, operator, value):
        # Search only documents represented in this user's inbox, using their
        # normal document ACLs and record rules rather than querying names sudo.
        tasks = self.search([])
        matches = self.browse()
        for model_name in set(tasks.mapped('res_model')):
            candidates = tasks.filtered(lambda t: t.res_model == model_name)
            try:
                # name_search, not a display_name domain: several document
                # types (e.g. purchase requests) cannot search display_name,
                # and Odoo then drops the condition and matches everything.
                found = self.env[model_name].name_search(
                    value or '', args=[('id', 'in', candidates.mapped('res_id'))], operator=operator, limit=None)
                ids = {record_id for record_id, _name in found}
                matches |= candidates.filtered(lambda t: t.res_id in ids)
            except AccessError:
                continue
        return [('id', 'in', matches.ids)]

    @api.depends_context('uid', 'allowed_company_ids', 'lang')
    @api.depends('res_model', 'res_id', 'kind', 'source_id')
    def _compute_document(self):
        for task in self:
            task.document_name = _('Document unavailable')
            task.model_name = False
            task.submitter_id = False
            task.amount = 0
            task.currency_id = False
            task.note = False
            try:
                document = task._document()
                task.document_name = document.display_name
                task.model_name = {
                    'bill': _('Vendor Bill'), 'expense': _('Expense Report'),
                }.get(task.kind) or self.env['ir.model']._get(task.res_model).name
                if task.kind in ('approval', 'returned'):
                    request = (self.env['aiis.approval.line'].browse(task.source_id).request_id
                               if task.kind == 'approval' else self.env['aiis.approval.request'].browse(task.source_id))
                    task.submitter_id = request.submitter_id
                    if task.kind == 'returned':
                        task.note = request.rejection_reason
                elif 'employee_id' in document._fields:
                    task.submitter_id = document.employee_id.user_id
                elif 'user_id' in document._fields:
                    task.submitter_id = document.user_id
                if 'currency_id' in document._fields:
                    task.currency_id = document.currency_id
                elif 'company_id' in document._fields:
                    task.currency_id = document.company_id.currency_id
                for field_name in ('amount_total', 'total_amount', 'estimated_total'):
                    if field_name in document._fields:
                        task.amount = document.read([field_name])[0][field_name]
                        break
            except (AccessError, MissingError):
                # No sudo: task assignment never grants access to a document,
                # its restricted fields, or a different company's information.
                continue

    @api.model
    def _workflow_tasks_hint(self, documents):
        """Who has to do what on these documents, from the same open tasks as
        the To do list (#64): "To Approve: CFO (TBD) · Confirm Bill and Pay: ...".
        Names only - no document data - so it is safe to compute as superuser."""
        documents = [d for d in documents if d]
        if not documents:
            return False
        domain = ['|'] * (len(documents) - 1)
        for document in documents:
            domain += ['&', ('res_model', '=', document._name), ('res_id', 'in', document.ids)]
        tasks = self.sudo().search(domain, order='assigned_on, id')
        labels = dict(self._fields['kind']._description_selection(self.env))
        grouped = {}
        for task in tasks:
            grouped.setdefault(task.kind, self.env['res.users'])
            grouped[task.kind] |= task.user_id
        if not grouped:
            return False
        return Markup(' · ').join(
            Markup('%s: <b>%s</b>') % (labels.get(kind, kind), ', '.join(users.mapped('name')))
            for kind, users in grouped.items())

    def action_open_document(self):
        document = self._document()
        return {
            'type': 'ir.actions.act_window', 'res_model': document._name,
            'res_id': document.id, 'view_mode': 'form', 'views': [(False, 'form')], 'target': 'current',
        }
