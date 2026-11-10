from collections import defaultdict
from datetime import datetime, time, timedelta

import pytz
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError
from markupsafe import Markup

GROUP = 'aiis_dashboard.group_operations_dashboard'
PAGES = ('overview', 'expenses', 'procurement', 'approvals', 'projects', 'maintenance')
PERIODS = ('day', 'month', 'quarter', 'year')
STEP = {'day': relativedelta(days=1), 'month': relativedelta(months=1),
        'quarter': relativedelta(months=3), 'year': relativedelta(years=1)}
PROJECT_STAGES = [('initiation', 'Initiation'), ('solution', 'Solution design'), ('contract', 'Contract'),
                  ('delivery', 'Delivery'), ('maintenance', 'Maintenance')]
CLAIM_TYPES = [('sales', 'Sales expenses'), ('travel', 'Project travel'), ('other', 'Other')]
WEEKEND = (4, 5)  # Friday, Saturday


def _stage_of_label(label):
    """Project stage of a tracked state label. Labels are what Odoo keeps in
    the tracking and some were renamed over time, so match loosely."""
    label = (label or '').strip().lower()
    if label in ('draft', 'initiation approval'):
        return 'initiation'
    if label in ('solution design', 'solution approval', 'costing', 'costing approval', 'quotation sent'):
        return 'solution'
    if label.startswith('contract'):
        return 'contract'
    if label.startswith('delivery'):
        return 'delivery'
    if label in ('maintenance', 'closing approval'):
        return 'maintenance'
    if label == 'completed':
        return 'done'
    return None


class OperationsDashboard(models.AbstractModel):
    """Figures of the Dashboard menu (design doc 6.9, #66): company-wide
    operating figures for the CEO and the Chairman. Actual figures only,
    in company currency. Each page is returned as cards and blocks (charts,
    bar lists, tables) the client renders as they come."""
    _name = 'aiis.operations.dashboard'
    _description = 'Operations Dashboard'

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    @api.model
    def get_page(self, page, params=None):
        if not self.env.user.has_group(GROUP):
            raise AccessError(_("The operations dashboard is for the CEO and the Chairman."))
        if page not in PAGES:
            raise AccessError(_("Unknown dashboard page."))
        params = params or {}
        # company-wide figures; the viewer's own rights only decide which
        # documents open from the tables
        board = self.sudo()
        p = board._period(params)
        p['params'] = params
        data = getattr(board, '_page_%s' % page)(p)
        data['period'] = p['info']
        data['filters'] = board._filters(page, params)
        return data

    # ------------------------------------------------------------------
    # Period
    # ------------------------------------------------------------------

    def _bounds(self, kind, day):
        if kind == 'day':
            return day, day
        if kind == 'month':
            start = day.replace(day=1)
        elif kind == 'quarter':
            start = day.replace(day=1, month=3 * ((day.month - 1) // 3) + 1)
        else:
            start = day.replace(day=1, month=1)
        return start, start + STEP[kind] - relativedelta(days=1)

    def _fmt_range(self, a, b):
        if a == b:
            return '%s %s' % (a.day, a.strftime('%b %Y'))
        if (a.year, a.month) == (b.year, b.month):
            return '%s–%s %s' % (a.day, b.day, b.strftime('%b %Y'))
        if a.year == b.year:
            return '%s %s – %s %s' % (a.day, a.strftime('%b'), b.day, b.strftime('%b %Y'))
        return '%s – %s' % (a.strftime('%d %b %Y'), b.strftime('%d %b %Y'))

    def _period(self, params):
        today = fields.Date.context_today(self)
        kind = params.get('period') if params.get('period') in PERIODS else 'month'
        anchor = fields.Date.to_date(params.get('date')) or today
        if anchor > today:
            anchor = today
        start, end = self._bounds(kind, anchor)
        upto = min(end, today)
        span = (upto - start).days
        prev_start = start - STEP[kind]
        prev_upto = min(prev_start + timedelta(days=span), start - timedelta(days=1))
        next_start = start + STEP[kind]

        if kind == 'day':
            title = anchor.strftime('%A ') + self._fmt_range(anchor, anchor)
            sub = _("Today so far") if anchor == today else ''
        elif kind == 'month':
            title = start.strftime('%B %Y')
        elif kind == 'quarter':
            title = 'Q%s %s' % ((start.month - 1) // 3 + 1, start.year)
        else:
            title = str(start.year)
        if kind != 'day':
            sub = (_("%s so far", self._fmt_range(start, upto)) if upto < end
                   else self._fmt_range(start, end))

        buckets = []
        if kind == 'month':
            day = start
            while day <= end:
                buckets.append({'key': day.isoformat(), 'label': str(day.day), 'future': day > today,
                                'weekend': day.weekday() in WEEKEND, 'today': day == today,
                                'drill': {'period': 'day', 'date': day.isoformat()}})
                day += timedelta(days=1)
        elif kind in ('quarter', 'year'):
            month = start
            while month <= end:
                buckets.append({'key': month.strftime('%Y-%m'), 'label': month.strftime('%b'),
                                'future': month > today, 'weekend': False,
                                'today': (month.year, month.month) == (today.year, today.month),
                                'drill': {'period': 'month', 'date': month.isoformat()}})
                month += relativedelta(months=1)

        return {
            'kind': kind, 'today': today, 'start': start, 'end': end, 'upto': upto,
            'prev_start': prev_start, 'prev_upto': prev_upto, 'buckets': buckets,
            'compare': self._fmt_range(prev_start, prev_upto),
            'company': self.env.company,
            'info': {
                'kind': kind, 'date': anchor.isoformat(), 'title': title, 'sub': sub,
                'compare': self._fmt_range(prev_start, prev_upto),
                'prev_date': prev_start.isoformat(),
                'next_date': next_start.isoformat() if next_start <= today else False,
                'today': today.isoformat(),
            },
        }

    def _bucket_key(self, p, day):
        return day.isoformat() if p['kind'] == 'month' else day.strftime('%Y-%m')

    def _utc(self, day, end=False):
        """UTC datetime where the viewer's local day starts (or ends)"""
        tz = pytz.timezone(self.env.user.tz or 'Asia/Riyadh')
        moment = datetime.combine(day + timedelta(days=1) if end else day, time.min)
        return tz.localize(moment).astimezone(pytz.utc).replace(tzinfo=None)

    def _dt_domain(self, field, date_from, date_to):
        return [(field, '>=', self._utc(date_from)), (field, '<', self._utc(date_to, end=True))]

    def _local_date(self, moment):
        if not moment:
            return None
        if not isinstance(moment, datetime):
            return moment
        tz = pytz.timezone(self.env.user.tz or 'Asia/Riyadh')
        return pytz.utc.localize(moment).astimezone(tz).date()

    def _days_since(self, moment, p):
        day = self._local_date(moment)
        return (p['today'] - day).days if day else 0

    # ------------------------------------------------------------------
    # Formatting: the client shows these strings as they are
    # ------------------------------------------------------------------

    def _money(self, amount, p=None, short=False):
        currency = self.env.company.currency_id
        symbol = currency.symbol or currency.name
        if short and abs(amount) >= 1000000:
            return '%.2fM %s' % (amount / 1000000.0, symbol)
        return '%s %s' % ('{:,.0f}'.format(amount or 0.0), symbol)

    def _count_delta(self, p, count, prev, one, many):
        """'3 quotations · 1–8 Oct 2026: 0': the count under an amount card"""
        return _("%(count)s %(noun)s · %(compare)s: %(prev)s",
                 count=count, noun=one if count == 1 else many, compare=p['compare'], prev=prev)

    def _num(self, value, digits=0):
        return ('{:,.%sf}' % digits).format(value or 0.0)

    def _delta(self, p, cur, prev, money=True, cost=False, unit=''):
        """'▲ 12% vs 1–8 Oct (13,050)' and its tone: rising costs are
        orange, falling ones blue; other figures stay neutral"""
        fmt = (lambda v: '{:,.0f}'.format(v)) if money else (lambda v: self._num(v, 1 if unit else 0) + unit)
        if not prev:
            if not cur:
                return _("Same as %s", p['compare']), 'flat'
            return _("%(compare)s: 0", compare=p['compare']), 'flat'
        change = (cur - prev) / abs(prev) * 100.0
        arrow = '▲' if change > 0 else ('▼' if change < 0 else '=')
        text = '%s %s%% vs %s (%s)' % (arrow, '{:,.0f}'.format(abs(change)), p['compare'], fmt(prev))
        tone = 'flat'
        if cost and change > 0:
            tone = 'up'
        elif cost and change < 0:
            tone = 'down'
        return text, tone

    def _card(self, title, value, delta='', tone='flat', note='', tag=None):
        return {'title': title, 'value': value, 'delta': delta, 'tone': tone, 'note': note, 'tag': tag}

    def _ref(self, record, label=None):
        """A document cell: opens only if the viewer may read it"""
        if not record:
            return {'text': label or ''}
        viewer = record.with_env(self.env(su=False))
        try:
            viewer.check_access_rights('read')
            viewer.check_access_rule('read')
            can_open = True
        except AccessError:
            can_open = False
        return {'text': label or record.display_name, 'model': record._name, 'id': record.id, 'open': can_open}

    def _bars(self, rows, money=True, unit='', highlight=None):
        """[(label, value)] -> bar list rows scaled to the largest"""
        top = max([abs(v) for _l, v in rows] or [0]) or 1.0
        out = []
        for label, value in rows:
            out.append({'label': label, 'value': value, 'pct': round(abs(value) / top * 100.0, 1),
                        'display': self._money(value) if money else self._num(value, 1 if unit else 0) + unit,
                        'warn': bool(highlight and highlight(label, value))})
        return out

    def _series(self, p, labels, points):
        """Stacked bars over the period's buckets. points: {bucket key: [v per label]}"""
        buckets = []
        for bucket in p['buckets']:
            values = points.get(bucket['key']) or [0.0] * len(labels)
            buckets.append(dict(bucket, values=values, total=sum(values)))
        return {'labels': labels, 'buckets': buckets, 'empty': not any(b['total'] for b in buckets)}

    # ------------------------------------------------------------------
    # Filters
    # ------------------------------------------------------------------

    def _departments(self):
        return self.env['hr.department'].search([('company_id', 'in', (False, self.env.company.id))],
                                                order='complete_name')

    def _filters(self, page, params):
        if page == 'expenses':
            return [{'key': 'department_id', 'label': _("Department"), 'value': params.get('department_id') or '',
                     'options': [{'value': '', 'label': _("All departments")}]
                     + [{'value': str(d.id), 'label': d.name} for d in self._departments()]}]
        if page == 'procurement':
            return [{'key': 'po_type', 'label': _("Type"), 'value': params.get('po_type') or '',
                     'options': [{'value': '', 'label': _("Internal and project")},
                                 {'value': 'internal', 'label': _("Internal purchases")},
                                 {'value': 'project', 'label': _("Project purchases")}]}]
        if page == 'projects':
            people = self.env['aiis.project'].search([]).salesperson_id
            return [{'key': 'salesperson_id', 'label': _("Salesperson"), 'value': params.get('salesperson_id') or '',
                     'options': [{'value': '', 'label': _("All salespeople")}]
                     + [{'value': str(u.id), 'label': u.name} for u in people.sorted('name')]}]
        return []

    def _param_record(self, p, key, model):
        value = p['params'].get(key)
        return self.env[model].browse(int(value)) if value else self.env[model]

    # ------------------------------------------------------------------
    # Shared figures
    # ------------------------------------------------------------------

    def _finance_board_params(self, p):
        return {'company': p['company'], 'project': None, 'partner': None}

    def _order_value(self, p, date_from, date_to):
        finance = self.env['aiis.finance.board']
        orders = self.env['sale.order'].search(finance._order_domain(self._finance_board_params(p), date_from, date_to))
        return finance._order_value(orders), orders

    def _revenue(self, p, date_from, date_to):
        finance = self.env['aiis.finance.board']
        return -finance._sum_balance(finance._aml_domain(self._finance_board_params(p), 'revenue', date_from, date_to))

    def _claim_type(self, sheet):
        if 'aiis_sales_request_id' in sheet._fields and sheet.aiis_sales_request_id:
            return 'sales'
        if sheet.aiis_travel_request_id:
            return 'travel'
        return 'other'

    def _expense_lines(self, p, date_from, date_to, department=None):
        domain = [('sheet_id.state', 'in', ('approve', 'post', 'done')), ('date', '>=', date_from),
                  ('date', '<=', date_to), ('company_id', '=', p['company'].id)]
        if department:
            domain.append(('employee_id.department_id', 'child_of', department.id))
        return self.env['hr.expense'].search(domain)

    def _po_amount(self, order):
        return order.amount_untaxed / (order.currency_rate or 1.0)

    def _approved_pos(self, p, date_from, date_to):
        return self.env['purchase.order'].search(
            [('state', 'in', ('purchase', 'done')), ('company_id', '=', p['company'].id)]
            + self._dt_domain('date_approve', date_from, date_to))

    def _project_pos(self, orders):
        """The orders of these that are project purchases"""
        if not orders:
            return orders
        from_pr = self.env['procurement.request'].search(
            [('purchase_order_id', 'in', orders.ids), ('aiis_project_id', '!=', False)]).purchase_order_id
        from_bom = orders.filtered(lambda o: o.order_line.filtered('aiis_bom_line_id'))
        return orders & (from_pr | from_bom)

    def _pending_approvals(self):
        return self.env['aiis.todo'].search([('kind', '=', 'approval')])

    def _payment_tasks(self):
        """What the CFO still has to pay: the same tasks as their To do"""
        tasks = self.env['aiis.todo'].search([('kind', 'in', ('bill', 'expense'))])
        rows = []
        for task in tasks:
            document = self.env[task.res_model].browse(task.res_id).exists()
            if not document:
                continue
            if task.res_model == 'account.move':
                amount = abs(document.amount_residual_signed)
            else:
                amount = self._sheet_to_pay(document)
            rows.append({'task': task, 'document': document, 'amount': amount, 'kind': task.kind})
        return rows

    def _sheet_to_pay(self, sheet):
        """Still to pay on an expense report: Odoo only knows the residual
        once it is posted; before that, all of it"""
        return sheet.amount_residual if sheet.account_move_ids else sheet.total_amount

    def _sales_budget_card(self, p):
        budgets = self.env['aiis.sales.budget'].search([
            ('date_from', '<=', p['upto']), ('date_to', '>=', p['upto']), ('company_id', '=', p['company'].id)])
        if not budgets:
            return self._card(_("Sales Expense Budget"), _("Not set"), note=_("No budget covers %s", p['upto']))
        if len(budgets) == 1:
            pct = budgets.used / budgets.amount * 100.0 if budgets.amount else 0.0
            over = budgets.used - budgets.amount
            return self._card(
                _("Sales Expense Budget"), _("%s%% used", '{:,.0f}'.format(pct)),
                _("Over by %s", self._money(over)) if over > 0 else _("%s left", self._money(-over)),
                'up' if over > 0 else 'flat',
                _("%(dept)s, %(start)s – %(end)s", dept=budgets.department_id.name,
                  start=budgets.date_from, end=budgets.date_to))
        over = budgets.filtered(lambda b: b.used > b.amount)
        return self._card(_("Over-budget Departments"), '%s of %s' % (len(over), len(budgets)),
                          ', '.join(over.department_id.mapped('name')), 'up' if over else 'flat',
                          _("Sales expense budgets"))

    # ------------------------------------------------------------------
    # Projects: state history from the tracking
    # ------------------------------------------------------------------

    def _project_history(self):
        """{project id: [(utc datetime, old label, new label)]}"""
        self.env.cr.execute("""
            SELECT m.res_id, m.date, v.old_value_char, v.new_value_char
            FROM mail_tracking_value v
            JOIN mail_message m ON m.id = v.mail_message_id
            JOIN ir_model_fields f ON f.id = v.field_id
            WHERE f.model = 'aiis.project' AND f.name = 'state'
            ORDER BY m.res_id, m.date, v.id
        """)
        history = defaultdict(list)
        for res_id, moment, old, new in self.env.cr.fetchall():
            history[res_id].append((moment, old, new))
        return history

    def _project_events(self, history, kind, date_from, date_to):
        """[(project id, local date)] of one kind of state change in the period"""
        events = []
        for project_id, changes in history.items():
            for moment, old, new in changes:
                hit = False
                if kind == 'quoted':
                    hit = (new or '').lower() == 'quotation sent'
                elif kind == 'initiated':
                    hit = (old or '').lower() == 'initiation approval' and _stage_of_label(new) == 'solution'
                elif kind == 'contract':
                    hit = _stage_of_label(old) == 'contract' and _stage_of_label(new) == 'delivery'
                day = self._local_date(moment)
                if hit and date_from <= day <= date_to:
                    events.append((project_id, day))
        return events

    def _stage_intervals(self, project, changes):
        """[(stage, entered, left or None)] - consecutive states of one stage merged"""
        intervals = []
        current, since = 'initiation', project.create_date
        for moment, _old, new in changes:
            stage = _stage_of_label(new)
            if not stage or stage == current:
                continue
            intervals.append((current, since, moment))
            current, since = stage, moment
        intervals.append((current, since, None))
        return intervals

    # ------------------------------------------------------------------
    # Overview
    # ------------------------------------------------------------------

    def _page_overview(self, p):
        a, b, pa, pb = p['start'], p['upto'], p['prev_start'], p['prev_upto']
        cards = []

        order_value, orders = self._order_value(p, a, b)
        prev_orders, _po = self._order_value(p, pa, pb)
        delta, tone = self._delta(p, order_value, prev_orders)
        cards.append(self._card(_("Sales Order Value"), self._money(order_value), delta, tone,
                                _("Same figure as Finance › Overview"), 'finance'))

        revenue = self._revenue(p, a, b)
        delta, tone = self._delta(p, revenue, self._revenue(p, pa, pb))
        cards.append(self._card(_("Recognized Revenue"), self._money(revenue), delta, tone,
                                _("Same figure as Finance › Overview"), 'finance'))

        history = self._project_history()
        contracts = self.env['aiis.project'].browse([pid for pid, _d in self._project_events(history, 'contract', a, b)])
        prev_contracts = self._project_events(history, 'contract', pa, pb)
        cards.append(self._card(
            _("New Contracts"), self._money(sum(contracts.mapped('quotation_amount'))),
            self._count_delta(p, len(contracts), len(prev_contracts), _("contract"), _("contracts")), 'flat',
            _("Project contracts approved by the CEO")))

        pos = self._approved_pos(p, a, b)
        project_pos = self._project_pos(pos)
        po_total = sum(self._po_amount(o) for o in pos)
        delta, tone = self._delta(p, po_total, sum(self._po_amount(o) for o in self._approved_pos(p, pa, pb)), cost=True)
        cards.append(self._card(_("Purchase Orders Approved"), self._money(po_total), delta, tone,
                                _("%(n)s POs · %(i)s internal, %(j)s project",
                                  n=len(pos), i=len(pos - project_pos), j=len(project_pos))))

        pending = self._pending_approvals()
        oldest = pending.sorted(lambda t: t.assigned_on or datetime.max)[:1]
        cards.append(self._card(
            _("Pending Approvals"), str(len(pending)),
            _("Longest: %s days", self._days_since(oldest.assigned_on, p)) if oldest else _("Nothing waiting"),
            'up' if oldest and self._days_since(oldest.assigned_on, p) >= 3 else 'flat',
            _("%(doc)s with %(user)s", doc=oldest.document_name, user=oldest.user_id.name) if oldest else '', 'now'))

        payments = self._payment_tasks()
        bills = [r for r in payments if r['kind'] == 'bill']
        claims = [r for r in payments if r['kind'] == 'expense']
        cards.append(self._card(_("Awaiting Payment"), self._money(sum(r['amount'] for r in payments)),
                                _("%(b)s bills · %(c)s expense claims", b=len(bills), c=len(claims)), 'flat',
                                _("Approved, CFO to pay"), 'now'))

        cards.append(self._sales_budget_card(p))

        lines = self._expense_lines(p, a, b)
        spent = sum(lines.mapped('total_amount'))
        delta, tone = self._delta(p, spent, sum(self._expense_lines(p, pa, pb).mapped('total_amount')), cost=True)
        cards.append(self._card(_("Employee Expenses"), self._money(spent), delta, tone,
                                _("%(n)s expense lines · %(e)s employees", n=len(lines), e=len(lines.employee_id))))

        blocks = []
        if p['buckets']:
            points = defaultdict(lambda: [0.0, 0.0])
            for order in orders:
                points[self._bucket_key(p, order.date_order.date())][0] += order.amount_untaxed / (order.currency_rate or 1.0)
            finance = self.env['aiis.finance.board']
            for line in self.env['account.move.line'].search(
                    finance._aml_domain(self._finance_board_params(p), 'revenue', a, b)):
                points[self._bucket_key(p, line.date)][1] -= line.balance
            blocks.append({'type': 'chart', 'width': 'wide', 'title': _("Sales orders and recognized revenue"),
                           'sub': _("SAR, untaxed · same figures as Finance › Overview · actual figures only"),
                           'chart': self._series(p, [_("Sales order value"), _("Recognized revenue")], points),
                           'grouped': True})
        blocks.append(self._approver_block(pending, p, width='narrow'))
        blocks.append({'type': 'bars', 'width': 'half', 'title': _("Approved purchase orders"),
                       'sub': '%s · %s' % (p['info']['sub'] or p['info']['title'], _("untaxed")),
                       'rows': self._bars([(_("Project POs"), sum(self._po_amount(o) for o in project_pos)),
                                           (_("Internal POs"), sum(self._po_amount(o) for o in pos - project_pos))])})
        by_dept = defaultdict(float)
        for line in lines:
            by_dept[line.employee_id.department_id.name or _("No department")] += line.total_amount
        blocks.append({'type': 'bars', 'width': 'half', 'title': _("Employee expenses by department"),
                       'sub': p['info']['sub'] or p['info']['title'],
                       'rows': self._bars(sorted(by_dept.items(), key=lambda kv: -kv[1]))})
        return {'cards': cards, 'blocks': blocks}

    def _approver_block(self, pending, p, width='half'):
        per_user = defaultdict(list)
        for task in pending:
            per_user[task.user_id].append(task)
        rows = []
        for user, tasks in sorted(per_user.items(), key=lambda kv: -len(kv[1])):
            longest = max(self._days_since(t.assigned_on, p) for t in tasks)
            rows.append([{'text': user.name, 'sub': self._position_of(user)},
                         {'text': str(len(tasks)), 'bold': True},
                         {'text': _("%s days", longest), 'tone': 'up' if longest >= 3 else None, 'bold': True}])
        return {'type': 'table', 'width': width, 'title': _("Waiting with approvers"), 'tag': 'now',
                'columns': [{'label': _("Approver")}, {'label': _("Documents"), 'align': 'end'},
                            {'label': _("Longest wait"), 'align': 'end'}],
                'rows': rows, 'empty': _("Nothing is waiting for approval.")}

    def _position_of(self, user):
        position = self.env['aiis.approval.position'].search([('user_id', '=', user.id)], limit=1)
        if position:
            return position.name
        employee = user.employee_ids[:1]
        if employee.department_id.manager_id == employee:
            return _("%s department head", employee.department_id.name)
        return employee.job_title or ''

    # ------------------------------------------------------------------
    # Expenses
    # ------------------------------------------------------------------

    def _page_expenses(self, p):
        a, b, pa, pb = p['start'], p['upto'], p['prev_start'], p['prev_upto']
        department = self._param_record(p, 'department_id', 'hr.department')
        Sheet = self.env['hr.expense.sheet']
        dept_domain = [('employee_id.department_id', 'child_of', department.id)] if department else []
        company = [('company_id', '=', p['company'].id)]

        lines = self._expense_lines(p, a, b, department)
        spent = sum(lines.mapped('total_amount'))
        delta, tone = self._delta(p, spent, sum(self._expense_lines(p, pa, pb, department).mapped('total_amount')), cost=True)
        cards = [self._card(_("Employee Expenses"), self._money(spent), delta, tone,
                            _("Approved claims, by expense date"))]

        in_approval = Sheet.search([('state', '=', 'submit')] + dept_domain + company)
        cards.append(self._card(_("In Approval"), self._money(sum(in_approval.mapped('total_amount'))),
                                _("%s claims", len(in_approval)), 'flat', _("Submitted, not yet fully approved"), 'now'))

        to_pay = Sheet.search([('state', 'in', ('approve', 'post'))] + dept_domain + company)
        cards.append(self._card(_("Awaiting Payment"), self._money(sum(self._sheet_to_pay(s) for s in to_pay)),
                                _("%s claims", len(to_pay)), 'flat', _("Approved, CFO to post and pay"), 'now'))

        decided = Sheet.search([('state', 'in', ('approve', 'post', 'done'))] + dept_domain + company
                               + self._dt_domain('approval_date', a, b))
        overs = []
        for sheet in decided:
            request = sheet._aiis_claim_request()
            if not request:
                continue
            estimate = request._claim_estimate()
            actual = request._claim_amount_in_currency(sheet.total_amount)
            if actual > estimate:
                overs.append((sheet, request, estimate, actual))
        cards.append(self._card(_("Over the Estimate"), _("%s claims", len(overs)),
                                _("+%s over requests", self._money(sum(o[3] - o[2] for o in overs))) if overs else '',
                                'up' if overs else 'flat', _("Approvers already decided")))

        trips = self._trips(p, a, b, department)
        cards.append(self._card(_("Trips"), _("%(t)s trips · %(d)s days", t=len(trips), d=sum(t['days'] for t in trips)),
                                _("%s claimed", self._money(sum(t['claimed'] for t in trips))), 'flat',
                                _("%(s)s sales, %(p)s project", s=len([t for t in trips if t['kind'] == 'sales']),
                                  p=len([t for t in trips if t['kind'] == 'travel']))))

        blocks = []
        if p['buckets']:
            index = {key: i for i, (key, _label) in enumerate(CLAIM_TYPES)}
            points = defaultdict(lambda: [0.0] * len(CLAIM_TYPES))
            for line in lines:
                points[self._bucket_key(p, line.date)][index[self._claim_type(line.sheet_id)]] += line.total_amount
            blocks.append({'type': 'chart', 'width': 'wide', 'title': _("Employee expenses by claim type"),
                           'sub': _("Approved, posted and paid claims, by expense date · SAR"),
                           'chart': self._series(p, [label for _k, label in CLAIM_TYPES], points)})
        by_category = defaultdict(float)
        for line in lines:
            by_category[line.product_id.name or _("Uncategorised")] += line.total_amount
        blocks.append({'type': 'bars', 'width': 'narrow', 'title': _("By expense category"),
                       'sub': p['info']['sub'] or p['info']['title'],
                       'rows': self._bars(sorted(by_category.items(), key=lambda kv: -kv[1]))})

        budgets = self.env['aiis.sales.budget'].search([
            ('date_from', '<=', b), ('date_to', '>=', b), ('company_id', '=', p['company'].id)]
            + ([('department_id', 'child_of', department.id)] if department else []))
        rows = []
        for budget in budgets:
            dept_ids = self.env['hr.department'].search([('id', 'child_of', budget.department_id.id)]).ids
            this_period = sum(l.total_amount for l in lines if self._claim_type(l.sheet_id) == 'sales'
                              and l.employee_id.department_id.id in dept_ids)
            pct = budget.used / budget.amount * 100.0 if budget.amount else 0.0
            over = budget.used > budget.amount
            rows.append([{'text': budget.department_id.name}, {'text': self._num(budget.amount)},
                         {'text': self._num(budget.used)}, {'text': self._num(this_period)},
                         {'text': self._num(budget.amount - budget.used), 'bold': True, 'tone': 'up' if over else None},
                         {'meter': min(pct, 100.0), 'text': '%s%%' % self._num(pct), 'tone': 'up' if over else None}])
        blocks.append({'type': 'table', 'width': 'full', 'title': _("Sales expenses against department budgets"),
                       'sub': _("Used = approved requests: the estimate until reimbursed, then the reimbursed amount"),
                       'columns': [{'label': _("Department")}, {'label': _("Budget"), 'align': 'end'},
                                   {'label': _("Used"), 'align': 'end'}, {'label': _("This period"), 'align': 'end'},
                                   {'label': _("Remaining"), 'align': 'end'}, {'label': _("Usage"), 'meter': True}],
                       'rows': rows, 'empty': _("No sales expense budget covers this period.")})

        blocks.append({'type': 'table', 'width': 'half', 'title': _("Claims over the estimate"),
                       'sub': _("Approved in the period, actual amount above the approved request"),
                       'columns': [{'label': _("Request")}, {'label': _("Employee")},
                                   {'label': _("Estimate"), 'align': 'end'}, {'label': _("Actual"), 'align': 'end'},
                                   {'label': _("Over"), 'align': 'end'}],
                       'rows': [[self._ref(request), {'text': sheet.employee_id.name},
                                 {'text': self._num(est)}, {'text': self._num(act)},
                                 {'text': '+' + self._num(act - est), 'bold': True, 'tone': 'up'}]
                                for sheet, request, est, act in overs],
                       'empty': _("No claim went over its request.")})
        blocks.append({'type': 'table', 'width': 'half', 'title': _("Trips"),
                       'sub': _("Sales trips and project trips starting in the period"),
                       'columns': [{'label': _("Trip")}, {'label': _("Traveller")}, {'label': _("Project / customer")},
                                   {'label': _("Days"), 'align': 'end'}, {'label': _("Claimed"), 'align': 'end'}],
                       'rows': [[self._ref(t['record']), {'text': t['who']}, {'text': t['what']},
                                 {'text': str(t['days'])}, {'text': self._num(t['claimed']) if t['claimed'] else '—'}]
                                for t in trips],
                       'empty': _("No trips in this period.")})
        return {'cards': cards, 'blocks': blocks}

    def _trips(self, p, date_from, date_to, department=None):
        trips = []
        dept = [('employee_id.department_id', 'child_of', department.id)] if department else []
        for request in self.env['aiis.sales.expense.request'].search(
                [('expense_type', '=', 'trip'), ('state', '=', 'approved'),
                 ('date_from', '>=', date_from), ('date_from', '<=', date_to)] + dept):
            trips.append({'record': request, 'kind': 'sales', 'who': request.employee_id.name,
                          'what': request.partner_id.name or '',
                          'days': ((request.date_to or request.date_from) - request.date_from).days + 1,
                          'claimed': request.claim_total if request.claim_sheet_id else 0.0})
        for request in self.env['aiis.travel.request'].search(
                [('state', '=', 'approved'), ('date_from', '>=', date_from), ('date_from', '<=', date_to)] + dept):
            trips.append({'record': request, 'kind': 'travel', 'who': request.employee_id.name,
                          'what': request.project_id.display_name or '',
                          'days': (request.date_to - request.date_from).days + 1,
                          'claimed': request.claim_total if request.claim_sheet_id else 0.0})
        return sorted(trips, key=lambda t: t['record'].date_from)

    # ------------------------------------------------------------------
    # Procurement
    # ------------------------------------------------------------------

    def _page_procurement(self, p):
        a, b, pa, pb = p['start'], p['upto'], p['prev_start'], p['prev_upto']
        po_type = p['params'].get('po_type') or ''

        def of_type(orders):
            if not po_type:
                return orders
            project = self._project_pos(orders)
            return project if po_type == 'project' else orders - project

        Request = self.env['procurement.request']

        def requests(date_from, date_to):
            found = Request.search([('state', '!=', 'draft'),
                                    ('request_date', '>=', date_from), ('request_date', '<=', date_to)])
            if po_type == 'project':
                found = found.filtered('aiis_project_id')
            elif po_type == 'internal':
                found = found.filtered(lambda r: not r.aiis_project_id)
            return found

        prs = requests(a, b)
        prev_prs = requests(pa, pb)
        reference = sum(r.currency_id._convert(r.estimated_total, p['company'].currency_id, p['company'], r.request_date)
                        for r in prs if r.currency_id)
        cards = [self._card(
            _("New Purchase Requests"), str(len(prs)),
            _("%(compare)s: %(count)s", compare=p['compare'], count=len(prev_prs)), 'flat',
            _("%(i)s internal · %(j)s project · %(ref)s reference",
              i=len(prs.filtered(lambda r: not r.aiis_project_id)), j=len(prs.filtered('aiis_project_id')),
              ref=self._money(reference)))]

        pos = of_type(self._approved_pos(p, a, b))
        project_pos = self._project_pos(pos)
        total = sum(self._po_amount(o) for o in pos)
        delta, tone = self._delta(p, total, sum(self._po_amount(o) for o in of_type(self._approved_pos(p, pa, pb))), cost=True)
        cards.append(self._card(_("Purchase Orders Approved"), self._money(total), delta, tone,
                                _("%(n)s POs · %(i)s internal, %(j)s project", n=len(pos),
                                  i=self._money(sum(self._po_amount(o) for o in pos - project_pos)),
                                  j=self._money(sum(self._po_amount(o) for o in project_pos)))))

        bill_tasks = [r for r in self._payment_tasks() if r['kind'] == 'bill']
        bill_rows = []
        for row in bill_tasks:
            orders = row['document'].invoice_line_ids.purchase_line_id.order_id
            if po_type and not of_type(orders):
                continue
            bill_rows.append(dict(row, orders=orders))
        cards.append(self._card(_("Waiting for Payment"), self._money(sum(r['amount'] for r in bill_rows)),
                                _("%s bills", len(bill_rows)), 'flat', _("Approved, CFO to pay"), 'now'))

        not_received = []
        for order in of_type(self.env['purchase.order'].search([
                ('state', '=', 'purchase'), ('aiis_payment_status', '=', 'paid'), ('company_id', '=', p['company'].id)])):
            if order.is_fully_received:
                continue
            bills = order.invoice_ids.filtered(lambda m: m.move_type == 'in_invoice' and m.state == 'posted')
            paid_on = max([pay.date for bill in bills for pay in bill._get_reconciled_payments()] or bills.mapped('date') or [p['today']])
            not_received.append((order, paid_on))
        not_received.sort(key=lambda x: x[1])
        oldest = (p['today'] - not_received[0][1]).days if not_received else 0
        cards.append(self._card(_("Paid, Not Received"), self._money(sum(self._po_amount(o) for o, _d in not_received)),
                                _("Oldest: %s days", oldest) if not_received else _("Nothing outstanding"),
                                'up' if oldest > 14 else 'flat', _("%s POs", len(not_received)), 'now'))

        blocks = []
        if p['buckets']:
            points = defaultdict(lambda: [0.0, 0.0])
            for order in pos:
                key = self._bucket_key(p, self._local_date(order.date_approve))
                points[key][0 if order in project_pos else 1] += self._po_amount(order)
            blocks.append({'type': 'chart', 'width': 'wide', 'title': _("Purchase orders approved"),
                           'sub': _("By approval date · SAR, untaxed"),
                           'chart': self._series(p, [_("Project"), _("Internal")], points)})
        by_supplier = defaultdict(float)
        for order in pos:
            by_supplier[order.partner_id.commercial_partner_id.name] += self._po_amount(order)
        blocks.append({'type': 'bars', 'width': 'narrow', 'title': _("Top suppliers"),
                       'sub': _("Approved POs · SAR"),
                       'rows': self._bars(sorted(by_supplier.items(), key=lambda kv: -kv[1])[:6])})

        rows = []
        for row in sorted(bill_rows, key=lambda r: r['task'].assigned_on or datetime.max):
            bill, orders = row['document'], row['orders']
            projects = self.env['procurement.request'].search([('purchase_order_id', 'in', orders.ids)]).aiis_project_id
            requesters = self.env['procurement.request'].search([('purchase_order_id', 'in', orders.ids)]).department_id
            waited = self._days_since(row['task'].assigned_on, p)
            rows.append([self._ref(orders[:1] or bill, ', '.join(orders.mapped('name')) or bill.name),
                         {'text': bill.partner_id.commercial_partner_id.name},
                         {'text': ', '.join(projects.mapped('display_name') or requesters.mapped('name'))},
                         {'text': self._num(row['amount'])},
                         {'text': _("%s days", waited), 'bold': True, 'tone': 'up' if waited > 7 else None}])
        blocks.append({'type': 'table', 'width': 'half', 'title': _("Waiting for CFO payment"), 'tag': 'now',
                       'columns': [{'label': _("PO")}, {'label': _("Supplier")}, {'label': _("For")},
                                   {'label': _("To pay"), 'align': 'end'}, {'label': _("Waiting"), 'align': 'end'}],
                       'rows': rows, 'empty': _("Nothing waiting for payment.")})
        blocks.append({'type': 'table', 'width': 'half', 'title': _("Paid, not received yet"), 'tag': 'now',
                       'sub': _("More than 14 days since payment is highlighted"),
                       'columns': [{'label': _("PO")}, {'label': _("Supplier")}, {'label': _("Paid"), 'align': 'end'},
                                   {'label': _("Paid on")}, {'label': _("Since payment"), 'align': 'end'}],
                       'rows': [[self._ref(order), {'text': order.partner_id.commercial_partner_id.name},
                                 {'text': self._num(self._po_amount(order))}, {'text': str(paid_on)},
                                 {'text': _("%s days", (p['today'] - paid_on).days), 'bold': True,
                                  'tone': 'up' if (p['today'] - paid_on).days > 14 else None}]
                                for order, paid_on in not_received],
                       'empty': _("Every paid order has been received.")})
        return {'cards': cards, 'blocks': blocks}

    # ------------------------------------------------------------------
    # Approvals
    # ------------------------------------------------------------------

    def _doc_type(self, request):
        if request.res_model == 'aiis.project':
            return _("Project – %s", (request.gate or '').replace('_', ' ').capitalize() or _("approval"))
        return request.model_name or request.res_model

    def _page_approvals(self, p):
        a, b, pa, pb = p['start'], p['upto'], p['prev_start'], p['prev_upto']
        Request = self.env['aiis.approval.request']
        pending = self._pending_approvals()
        ceo = self.env['aiis.approval.position']._holder('ceo')
        oldest = pending.sorted(lambda t: t.assigned_on or datetime.max)

        def approved(date_from, date_to):
            return Request.search([('state', '=', 'approved')] + self._dt_domain('done_date', date_from, date_to))

        def avg_days(requests):
            spans = [(r.done_date - r.submit_date).total_seconds() / 86400.0 for r in requests if r.done_date]
            return sum(spans) / len(spans) if spans else 0.0

        done = approved(a, b)
        avg, prev_avg = avg_days(done), avg_days(approved(pa, pb))
        submitted = Request.search(self._dt_domain('submit_date', a, b))
        rejected = Request.search([('state', '=', 'rejected')] + self._dt_domain('done_date', a, b))
        prev_submitted = Request.search_count(self._dt_domain('submit_date', pa, pb))
        prev_rejected = Request.search_count([('state', '=', 'rejected')] + self._dt_domain('done_date', pa, pb))
        rate = len(rejected) / len(submitted) * 100.0 if submitted else 0.0

        longest = self._days_since(oldest[:1].assigned_on, p) if oldest else 0
        delta_avg = ''
        tone_avg = 'flat'
        if prev_avg:
            diff = avg - prev_avg
            delta_avg = '%s %s days vs %s (%s)' % ('▲' if diff > 0 else '▼', self._num(abs(diff), 1), p['compare'],
                                                   self._num(prev_avg, 1))
            tone_avg = 'up' if diff > 0 else 'down'
        cards = [
            self._card(_("Pending Approvals"), str(len(pending)),
                       _("%s with the CEO", len(pending.filtered(lambda t: t.user_id == ceo))) if ceo else '',
                       'flat', _("Across all document types"), 'now'),
            self._card(_("Longest Wait"), _("%s days", longest) if oldest else '—',
                       oldest[:1].document_name or '', 'up' if longest >= 3 else 'flat',
                       _("Waiting with %s", oldest[:1].user_id.name) if oldest else _("Nothing waiting"), 'now'),
            self._card(_("Average Time to Approve"), _("%s days", self._num(avg, 1)) if done else '—', delta_avg, tone_avg,
                       _("%s documents fully approved", len(done))),
            self._card(_("Rejection Rate"), '%s%%' % self._num(rate), _("%(r)s of %(s)s submitted", r=len(rejected), s=len(submitted)),
                       'flat', _("%(compare)s: %(r)s of %(s)s", compare=p['compare'], r=prev_rejected, s=prev_submitted)),
        ]

        blocks = [self._approver_block(pending, p)]
        by_type = defaultdict(list)
        for request in done:
            by_type[self._doc_type(request)].append(request)
        speed = sorted(((label, avg_days(reqs)) for label, reqs in by_type.items()), key=lambda kv: -kv[1])
        blocks.append({'type': 'bars', 'width': 'half', 'title': _("Average time to approve"),
                       'sub': _("Fully approved in the period · submit to last approval · days"),
                       'rows': self._bars(speed, money=False, unit=' d', highlight=lambda _l, v: v >= 3)})

        rows = []
        for task in oldest[:10]:
            document = self.env[task.res_model].browse(task.res_id)
            waited = self._days_since(task.assigned_on, p)
            request = self.env['aiis.approval.line'].browse(task.source_id).request_id
            rows.append([self._ref(document), {'text': self._doc_type(request)},
                         {'text': request.submitter_id.name or ''},
                         {'text': self._num(request.amount) if request.amount else '—'},
                         {'text': task.user_id.name},
                         {'text': _("%s days", waited), 'bold': True, 'tone': 'up' if waited >= 3 else None}])
        blocks.append({'type': 'table', 'width': 'full', 'title': _("Longest waiting documents"), 'tag': 'now',
                       'columns': [{'label': _("Document")}, {'label': _("Type")}, {'label': _("Submitted by")},
                                   {'label': _("Amount"), 'align': 'end'}, {'label': _("Waiting with")},
                                   {'label': _("Waiting"), 'align': 'end'}],
                       'rows': rows, 'empty': _("Nothing is waiting for approval.")})

        sub_by_type, rej_by_type = defaultdict(int), defaultdict(int)
        for request in submitted:
            sub_by_type[self._doc_type(request)] += 1
        for request in rejected:
            rej_by_type[self._doc_type(request)] += 1
        type_rows = []
        for label in sorted(set(sub_by_type) | set(rej_by_type), key=lambda l: -sub_by_type[l]):
            share = rej_by_type[label] / sub_by_type[label] * 100.0 if sub_by_type[label] else 0.0
            type_rows.append([{'text': label}, {'text': str(sub_by_type[label])}, {'text': str(rej_by_type[label])},
                              {'text': '%s%%' % self._num(share), 'bold': True, 'tone': 'up' if share >= 20 else None}])
        blocks.append({'type': 'table', 'width': 'half', 'title': _("Rejections by document type"),
                       'sub': _("Submitted and rejected in the period"),
                       'columns': [{'label': _("Type")}, {'label': _("Submitted"), 'align': 'end'},
                                   {'label': _("Rejected"), 'align': 'end'}, {'label': _("Rate"), 'align': 'end'}],
                       'rows': type_rows, 'empty': _("Nothing submitted in this period.")})
        reasons = []
        for request in rejected.sorted('done_date', reverse=True)[:8]:
            document = self.env[request.res_model].browse(request.res_id).exists()
            reasons.append({'ref': self._ref(document, request.document_name),
                            'meta': _("%(type)s · rejected by %(by)s · %(date)s", type=self._doc_type(request),
                                      by=request.rejection_note or '', date=self._local_date(request.done_date)),
                            'text': request.rejection_reason or ''})
        blocks.append({'type': 'notes', 'width': 'half', 'title': _("Recent rejections and reasons"),
                       'items': reasons, 'empty': _("No rejections in this period.")})
        return {'cards': cards, 'blocks': blocks}

    # ------------------------------------------------------------------
    # Projects & Sales
    # ------------------------------------------------------------------

    def _page_projects(self, p):
        a, b, pa, pb = p['start'], p['upto'], p['prev_start'], p['prev_upto']
        salesperson = self._param_record(p, 'salesperson_id', 'res.users')
        Project = self.env['aiis.project']
        scope = [('salesperson_id', '=', salesperson.id)] if salesperson else []
        projects = Project.search(scope)
        history = self._project_history()

        def events(kind, date_from, date_to):
            return [(pid, day) for pid, day in self._project_events(history, kind, date_from, date_to)
                    if pid in projects.ids]

        active = projects.filtered(lambda r: r.status == 'active' and r.state != 'done')
        paused = projects.filtered(lambda r: r.status == 'paused')
        quoted = events('quoted', a, b)
        quoted_projects = Project.browse([pid for pid, _d in quoted])
        prev_quoted = events('quoted', pa, pb)

        def decisions(date_from, date_to):
            return projects.filtered(lambda r: r.customer_decision_date
                                     and date_from <= r.customer_decision_date <= date_to)

        decided = decisions(a, b)
        won = decided.filtered(lambda r: r.customer_decision == 'accepted')
        prev_decided = decisions(pa, pb)
        prev_won = prev_decided.filtered(lambda r: r.customer_decision == 'accepted')
        contracts = Project.browse([pid for pid, _d in events('contract', a, b)])
        prev_contracts = events('contract', pa, pb)
        initiated = events('initiated', a, b)
        delivering = active.filtered(lambda r: r.stage == 'delivery')
        over = delivering.filtered(lambda r: r.cost_budget and r.budget_committed > r.cost_budget)

        cards = [
            self._card(_("Active Projects"), str(len(active)), _("+ %s paused", len(paused)), 'flat',
                       _("In progress, all stages"), 'now'),
            self._card(_("Quotations Sent"), self._money(sum(quoted_projects.mapped('quotation_amount')), short=True),
                       self._count_delta(p, len(quoted_projects), len(prev_quoted), _("quotation"), _("quotations")), 'flat',
                       _("Untaxed quotation value")),
            self._card(_("Win Rate"), '%s%%' % self._num(len(won) / len(decided) * 100.0) if decided else '—',
                       _("%(w)s accepted · %(r)s rejected", w=len(won), r=len(decided) - len(won)), 'flat',
                       _("%(compare)s: %(w)s of %(d)s", compare=p['compare'], w=len(prev_won), d=len(prev_decided))),
            self._card(_("Contracts Approved"), self._money(sum(contracts.mapped('quotation_amount'))),
                       self._count_delta(p, len(contracts), len(prev_contracts), _("contract"), _("contracts")), 'flat',
                       _("Approved by the CEO")),
            self._card(_("New Projects"), str(len(initiated)),
                       _("%(compare)s: %(count)s", compare=p['compare'], count=len(events('initiated', pa, pb))), 'flat',
                       _("Initiation approved")),
            self._card(_("Over-budget Deliveries"), _("%(o)s of %(d)s", o=len(over), d=len(delivering)),
                       ', '.join(over.mapped('name')), 'up' if over else 'flat',
                       _("Approved project POs over the cost budget"), 'now'),
        ]

        funnel = []
        for key, label in PROJECT_STAGES:
            in_stage = active.filtered(lambda r, k=key: r.stage == k)
            funnel.append({'label': label, 'count': len(in_stage),
                           'display': self._money(sum(in_stage.mapped('quotation_amount'))) if in_stage.filtered('quotation_id') else '—'})
        top = max([f['count'] for f in funnel] or [0]) or 1
        for row in funnel:
            row['pct'] = round(row['count'] / top * 100.0, 1)
        blocks = [{'type': 'funnel', 'width': 'half', 'title': _("Project pipeline"), 'tag': 'now',
                   'sub': _("Projects in progress by stage · quotation value where there is one"),
                   'rows': funnel, 'footer': _("Paused: %s projects (not in the stages above)", len(paused))}]

        spent = defaultdict(list)
        current = []
        for project in projects:
            for stage, entered, left in self._stage_intervals(project, history.get(project.id, [])):
                if left and a <= self._local_date(left) <= b:
                    spent[stage].append((left - entered).total_seconds() / 86400.0)
                if left is None and project in active and stage != 'done':
                    current.append((project, stage, (fields.Datetime.now() - entered).total_seconds() / 86400.0))
        labels = dict(PROJECT_STAGES)
        stage_rows = [(labels[key], sum(spent[key]) / len(spent[key])) for key, _l in PROJECT_STAGES if spent.get(key)]
        current.sort(key=lambda c: -c[2])
        blocks.append({'type': 'bars', 'width': 'half', 'title': _("Time spent in each stage"),
                       'sub': _("Projects that left a stage in the period · average days"),
                       'rows': self._bars(stage_rows, money=False, unit=' d'),
                       'empty': _("No project changed stage in this period."),
                       'table': {'title': _("Longest in the current stage"), 'tag': 'now',
                                 'rows': [[self._ref(project, project.name),
                                           {'text': labels.get(stage, stage),
                                            'sub': Markup(project.workflow_hint or '').striptags()},
                                           {'text': _("%s days", self._num(days)), 'bold': True,
                                            'tone': 'up' if days > 30 else None}]
                                          for project, stage, days in current[:5]]}})

        outcome = dict(Project._fields['customer_decision']._description_selection(self.env))
        blocks.append({'type': 'table', 'width': 'wide', 'title': _("Customer decisions"),
                       'sub': _("Quotations the customer accepted or rejected in the period"),
                       'columns': [{'label': _("Project")}, {'label': _("Salesperson")},
                                   {'label': _("Quotation"), 'align': 'end'}, {'label': _("Decision")},
                                   {'label': _("Reason / customer PO")}],
                       'rows': [[dict(self._ref(r, r.name), sub='%s · %s' % (r.partner_id.name, r.customer_decision_date)),
                                 {'text': r.salesperson_id.name}, {'text': self._num(r.quotation_amount)},
                                 {'text': outcome.get(r.customer_decision), 'badge': 'good' if r.customer_decision == 'accepted' else 'bad'},
                                 {'text': r.customer_po_ref if r.customer_decision == 'accepted' else (r.close_reason or '')}]
                                for r in decided.sorted('customer_decision_date')],
                       'empty': _("No customer decisions in this period.")})

        reps = defaultdict(lambda: {'quoted': 0, 'won': 0, 'decided': 0, 'contracts': 0.0})
        for project in quoted_projects:
            reps[project.salesperson_id]['quoted'] += 1
        for project in decided:
            reps[project.salesperson_id]['decided'] += 1
            reps[project.salesperson_id]['won'] += project.customer_decision == 'accepted'
        for project in contracts:
            reps[project.salesperson_id]['contracts'] += project.quotation_amount
        blocks.append({'type': 'table', 'width': 'narrow', 'title': _("By salesperson"),
                       'sub': p['info']['sub'] or p['info']['title'],
                       'columns': [{'label': _("Salesperson")}, {'label': _("Quoted"), 'align': 'end'},
                                   {'label': _("Won"), 'align': 'end'}, {'label': _("Win rate"), 'align': 'end'},
                                   {'label': _("Contracts"), 'align': 'end'}],
                       'rows': [[{'text': user.name}, {'text': str(v['quoted'])}, {'text': str(v['won'])},
                                 {'text': '%s%%' % self._num(v['won'] / v['decided'] * 100.0) if v['decided'] else '—', 'bold': True},
                                 {'text': self._num(v['contracts']) if v['contracts'] else '—'}]
                                for user, v in sorted(reps.items(), key=lambda kv: kv[0].name or '')],
                       'empty': _("No sales activity in this period.")})

        budget_rows = []
        for project in delivering.sorted(lambda r: -(r.budget_committed / r.cost_budget if r.cost_budget else 0)):
            pct = project.budget_committed / project.cost_budget * 100.0 if project.cost_budget else 0.0
            hot = project.cost_budget and project.budget_committed > project.cost_budget
            budget_rows.append([dict(self._ref(project, project.name), sub=project.partner_id.name),
                                {'text': project.project_manager_id.name or '—'},
                                {'text': self._num(project.cost_budget)}, {'text': self._num(project.budget_committed)},
                                {'text': self._num(project.budget_pending) if project.budget_pending else '—'},
                                {'text': self._num(project.budget_remaining), 'bold': True, 'tone': 'up' if hot else None},
                                {'meter': min(pct, 100.0), 'text': '%s%%' % self._num(pct), 'tone': 'up' if hot else None}])
        blocks.append({'type': 'table', 'width': 'full', 'title': _("Delivery projects against their cost budget"), 'tag': 'now',
                       'sub': _("Budget confirmed by the CFO at quotation (#61) · committed = approved project POs"),
                       'columns': [{'label': _("Project")}, {'label': _("Project manager")},
                                   {'label': _("Budget"), 'align': 'end'}, {'label': _("Committed"), 'align': 'end'},
                                   {'label': _("Pending approval"), 'align': 'end'}, {'label': _("Remaining"), 'align': 'end'},
                                   {'label': _("Usage"), 'meter': True}],
                       'rows': budget_rows, 'empty': _("No project is in delivery.")})

        blocks.append({'type': 'table', 'width': 'full', 'title': _("Paused projects"), 'tag': 'now',
                       'columns': [{'label': _("Project")}, {'label': _("Salesperson")}, {'label': _("Why paused")},
                                   {'label': _("Expected restart"), 'align': 'end'}],
                       'rows': [[dict(self._ref(r, r.name), sub=r.partner_id.name), {'text': r.salesperson_id.name},
                                 {'text': r.close_reason or ''},
                                 {'text': '%s%s' % (r.restart_date or '—', _(" · passed") if r.restart_date and r.restart_date < p['today'] else ''),
                                  'bold': True, 'tone': 'up' if r.restart_date and r.restart_date < p['today'] else None}]
                                for r in paused.sorted(lambda r: r.restart_date or p['today'])],
                       'empty': _("No paused projects.")})
        return {'cards': cards, 'blocks': blocks}

    # ------------------------------------------------------------------
    # Maintenance & People
    # ------------------------------------------------------------------

    def _maintenance_revenue(self, p, date_from, date_to):
        account = self.env.ref('aiis_finance.account_maintenance_revenue', raise_if_not_found=False)
        if not account:
            return 0.0
        groups = self.env['account.move.line'].read_group(
            [('account_id', '=', account.id), ('parent_state', '=', 'posted'),
             ('date', '>=', date_from), ('date', '<=', date_to)], ['balance:sum'], [])
        return -(groups[0]['balance'] or 0.0) if groups else 0.0

    def _personnel_costs(self, p, date_from, date_to):
        return self.env['aiis.personnel.cost'].search([
            ('state', '=', 'posted'), ('date', '>=', date_from), ('date', '<=', date_to),
            ('company_id', '=', p['company'].id)])

    def _headcount(self, p, at):
        """{department name: (employees at the date, joined in period, left in period)}"""
        Employee = self.env['hr.employee'].with_context(active_test=False)
        employees = Employee.search([('company_id', '=', p['company'].id)])
        starts = {}
        for contract in self.env['hr.contract'].search([('employee_id', 'in', employees.ids), ('state', '!=', 'cancel')]):
            if contract.employee_id.id not in starts or contract.date_start < starts[contract.employee_id.id]:
                starts[contract.employee_id.id] = contract.date_start
        rows = defaultdict(lambda: [0, 0, 0])
        for employee in employees:
            joined = starts.get(employee.id) or employee.create_date.date()
            left = employee.departure_date if not employee.active else None
            if not employee.active and not left:
                continue  # archived without a departure date: not an employee
            name = employee.department_id.name or _("No department")
            if joined <= at and (not left or left > at):
                rows[name][0] += 1
            if p['start'] <= joined <= at:
                rows[name][1] += 1
            if left and p['start'] <= left <= at:
                rows[name][2] += 1
        return rows

    def _page_maintenance(self, p):
        a, b, pa, pb = p['start'], p['upto'], p['prev_start'], p['prev_upto']
        today = p['today']
        projects = self.env['aiis.project'].search([('stage', '=', 'maintenance'), ('status', '=', 'active')])
        Hours = self.env['aiis.service.hours.request']
        maint = []
        for project in projects.sorted(lambda r: r.maintenance_end or today):
            grants = Hours.search([('project_id', '=', project.id), ('state', '=', 'approved')])
            approved = sum(grants.mapped('hours_approved'))
            used = sum(grants.mapped('hours_used'))
            days_left = (project.maintenance_end - today).days if project.maintenance_end else None
            maint.append({'project': project, 'approved': approved, 'used': used, 'days_left': days_left,
                          'pct': used / approved * 100.0 if approved else 0.0})
        ending = [m for m in maint if m['days_left'] is not None and m['days_left'] <= 90]
        almost = [m for m in maint if m['pct'] >= 80.0]

        Record = self.env['aiis.service.record']
        records = Record.search([('date', '>=', a), ('date', '<=', b)])
        hours = sum(records.mapped('hours'))
        prev_hours = sum(Record.search([('date', '>=', pa), ('date', '<=', pb)]).mapped('hours'))
        revenue = self._maintenance_revenue(p, a, b)
        costs = self._personnel_costs(p, a, b)
        prev_costs = self._personnel_costs(p, pa, pb)
        heads = self._headcount(p, b)
        total_heads = sum(v[0] for v in heads.values())

        delta, tone = self._delta(p, hours, prev_hours, money=False, unit=' h')
        rev_delta, rev_tone = self._delta(p, revenue, self._maintenance_revenue(p, pa, pb))
        cost_delta, cost_tone = self._delta(p, sum(costs.mapped('amount')), sum(prev_costs.mapped('amount')), cost=True)
        cards = [
            self._card(_("Under Maintenance"), str(len(maint)),
                       _("%s in contracts", self._money(sum(m['project'].quotation_amount for m in maint), short=True)),
                       'flat', _("Projects in the maintenance stage"), 'now'),
            self._card(_("Ending in 90 Days"), str(len(ending)),
                       '%s · %s' % (ending[0]['project'].name, ending[0]['project'].maintenance_end) if ending else '',
                       'up' if ending else 'flat', _("Renewal to discuss"), 'now'),
            self._card(_("Service Hours"), '%s h' % self._num(hours, 1), delta, tone, _("Logged on service records")),
            self._card(_("Hours Almost Used"), str(len(almost)),
                       ', '.join(m['project'].name for m in almost), 'up' if almost else 'flat',
                       _("80% or more of the approved hours used"), 'now'),
            self._card(_("Maintenance Revenue"), self._money(revenue), rev_delta, rev_tone,
                       _("Recognized month by month"), 'finance'),
            self._card(_("Personnel Cost"), self._money(sum(costs.mapped('amount'))), cost_delta, cost_tone,
                       _("Monthly salaries post at month end"), 'finance'),
            self._card(_("Headcount"), str(total_heads),
                       _("+%(j)s joined · %(l)s left", j=sum(v[1] for v in heads.values()), l=sum(v[2] for v in heads.values())),
                       'flat', _("At %s", b)),
        ]

        blocks = [{'type': 'table', 'width': 'full', 'title': _("Projects under maintenance"), 'tag': 'now',
                   'sub': _("Highlighted: ends within 90 days (renewal) or 80% of the hours used (extension)"),
                   'columns': [{'label': _("Project")}, {'label': _("Contract"), 'align': 'end'},
                               {'label': _("Maintenance ends")}, {'label': _("Hours approved"), 'align': 'end'},
                               {'label': _("Used"), 'align': 'end'}, {'label': _("Remaining"), 'align': 'end'},
                               {'label': _("Hours used"), 'meter': True}],
                   'rows': [[dict(self._ref(m['project'], m['project'].name), sub=m['project'].partner_id.name),
                             {'text': self._num(m['project'].quotation_amount)},
                             {'text': '%s · %s' % (m['project'].maintenance_end, _("%s days", m['days_left']))
                              if m['project'].maintenance_end else '—',
                              'tone': 'up' if m in ending else None, 'bold': m in ending},
                             {'text': '%s h' % self._num(m['approved'], 1)}, {'text': '%s h' % self._num(m['used'], 1)},
                             {'text': '%s h' % self._num(m['approved'] - m['used'], 1), 'bold': True,
                              'tone': 'up' if m in almost else None},
                             {'meter': min(m['pct'], 100.0), 'text': '%s%%' % self._num(m['pct']),
                              'tone': 'up' if m in almost else None}] for m in maint],
                   'empty': _("No project is under maintenance.")}]

        if p['buckets']:
            per_project = defaultdict(float)
            for record in records:
                per_project[record.project_id] += record.hours
            shown = [proj for proj, _h in sorted(per_project.items(), key=lambda kv: -kv[1])][:4]
            labels = [proj.name for proj in shown] + ([_("Other")] if len(per_project) > len(shown) else [])
            points = defaultdict(lambda: [0.0] * len(labels))
            for record in records:
                index = shown.index(record.project_id) if record.project_id in shown else len(labels) - 1
                points[self._bucket_key(p, record.date)][index] += record.hours
            blocks.append({'type': 'chart', 'width': 'wide', 'title': _("Service hours by project"),
                           'sub': _("Hours logged on service records"), 'unit': 'h',
                           'chart': self._series(p, labels or [_("Hours")], points)})
        per_engineer = defaultdict(float)
        for record in records:
            per_engineer[record.employee_id.name] += record.hours
        blocks.append({'type': 'bars', 'width': 'narrow', 'title': _("Service hours by engineer"),
                       'sub': p['info']['sub'] or p['info']['title'],
                       'rows': self._bars(sorted(per_engineer.items(), key=lambda kv: -kv[1]), money=False, unit=' h')})

        by_dept, by_type = defaultdict(float), defaultdict(float)
        for cost in costs:
            by_dept[cost.department_id.name or _("No department")] += cost.amount
            by_type[cost.type_id.name] += cost.amount
        blocks.append({'type': 'bars', 'width': 'half', 'title': _("Personnel cost by department"), 'tag': 'finance',
                       'sub': _("Posted personnel costs · monthly salaries post at month end"),
                       'rows': self._bars(sorted(by_dept.items(), key=lambda kv: -kv[1])),
                       'empty': _("No personnel cost posted in this period.")})
        blocks.append({'type': 'bars', 'width': 'half', 'title': _("Personnel cost by type"), 'tag': 'finance',
                       'sub': p['info']['sub'] or p['info']['title'],
                       'rows': self._bars(sorted(by_type.items(), key=lambda kv: -kv[1])),
                       'empty': _("No personnel cost posted in this period.")})
        head_rows = [[{'text': name}, {'text': str(v[0]), 'bold': True}, {'text': str(v[1])}, {'text': str(v[2])}]
                     for name, v in sorted(heads.items(), key=lambda kv: (-kv[1][0], kv[0]))]
        head_rows.append([{'text': _("Total"), 'bold': True}, {'text': str(total_heads), 'bold': True},
                          {'text': str(sum(v[1] for v in heads.values())), 'bold': True},
                          {'text': str(sum(v[2] for v in heads.values())), 'bold': True}])
        blocks.append({'type': 'table', 'width': 'full', 'title': _("Headcount by department"),
                       'sub': _("At %(date)s · joined and left in %(period)s", date=b, period=p['info']['sub'] or p['info']['title']),
                       'columns': [{'label': _("Department")}, {'label': _("Employees"), 'align': 'end'},
                                   {'label': _("Joined"), 'align': 'end'}, {'label': _("Left"), 'align': 'end'}],
                       'rows': head_rows})
        return {'cards': cards, 'blocks': blocks}
