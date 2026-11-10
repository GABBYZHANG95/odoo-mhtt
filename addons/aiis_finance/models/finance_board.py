from collections import defaultdict
from datetime import date

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError
from odoo.tools import float_is_zero

CASH_ROWS = [
    ('receipts', 'Customer receipts'),
    ('project', 'Project supplier payments'),
    ('opex', 'Operating expense payments'),
    ('capex', 'Capital expenditure payments'),
    ('tax', 'Tax payments'),
    ('other', 'Other and not yet reconciled'),
]
TAX_LINES = ('vat_input', 'vat_output', 'zakat_payable', 'income_tax_payable')


class FinanceBoard(models.AbstractModel):
    """Figures of Finance > Overview, Cash Flow and Checks (design doc
    6.8.2, 6.8.4, 6.8.5). Everything is actual and in company currency
    (SAR); the P&L lines are the accounts' report lines, the same ones the
    MIS reports add up."""
    _name = 'aiis.finance.board'
    _description = 'Finance Board'

    # ------------------------------------------------------------------
    # common
    # ------------------------------------------------------------------
    def _check_access(self):
        if not self.env.user.has_group('aiis_finance.group_finance_board'):
            raise AccessError(_("The finance board is for the CFO, the CEO and the Chairman."))

    def _params(self, params):
        params = dict(params or {})
        today = fields.Date.context_today(self)
        company = self.env.company
        year = int(params.get('year') or today.year)
        year_dates = company.compute_fiscalyear_dates(date(year, 6, 30))
        cutoff = fields.Date.to_date(params.get('cutoff')) or min(today, year_dates['date_to'])
        project = self.env['aiis.project'].browse(int(params['project_id'])) if params.get('project_id') else None
        partner = self.env['res.partner'].browse(int(params['partner_id'])) if params.get('partner_id') else None
        return {
            'company': company,
            'year': year,
            'year_from': year_dates['date_from'],
            'year_to': year_dates['date_to'],
            'cutoff': cutoff,
            'period': params.get('period') or 'year',
            'project': project,
            'partner': partner,
        }

    def _quarters(self, p):
        quarters, start = [], p['year_from']
        for index in range(1, 5):
            end = start + relativedelta(months=3, days=-1)
            quarters.append(('Q%s' % index, start, end))
            start = end + relativedelta(days=1)
        return quarters

    def _period_dates(self, p):
        """(from, to) of the chosen period, the end capped at the cutoff"""
        period = p['period']
        if period.startswith('q'):
            _label, start, end = self._quarters(p)[int(period[1]) - 1]
        elif period.startswith('m'):
            start = p['year_from'] + relativedelta(months=int(period[1:]) - 1)
            end = start + relativedelta(months=1, days=-1)
        else:
            start, end = p['year_from'], p['year_to']
        return start, min(end, p['cutoff'])

    def _aml_domain(self, p, lines, date_from=None, date_to=None):
        domain = [('parent_state', '=', 'posted'), ('company_id', '=', p['company'].id),
                  ('account_id.aiis_report_line', 'in', [lines] if isinstance(lines, str) else lines)]
        if date_from:
            domain.append(('date', '>=', date_from))
        if date_to:
            domain.append(('date', '<=', date_to))
        if p['project']:
            domain.append(('analytic_distribution', 'in', [p['project'].analytic_account_id.id or 0]))
        if p['partner']:
            domain.append(('partner_id', 'child_of', p['partner'].commercial_partner_id.id))
        return domain

    def _sum_balance(self, domain):
        groups = self.env['account.move.line'].read_group(domain, ['balance:sum'], [])
        return (groups[0]['balance'] or 0.0) if groups else 0.0

    # ------------------------------------------------------------------
    # Overview
    # ------------------------------------------------------------------
    def _order_domain(self, p, date_from=None, date_to=None):
        domain = [('state', '=', 'sale'), ('company_id', '=', p['company'].id)]
        if date_from:
            domain.append(('date_order', '>=', fields.Datetime.to_datetime(date_from)))
        if date_to:
            domain.append(('date_order', '<', fields.Datetime.to_datetime(date_to + relativedelta(days=1))))
        if p['project']:
            domain.append(('aiis_project_id', '=', p['project'].id))
        if p['partner']:
            domain.append(('partner_id', 'child_of', p['partner'].commercial_partner_id.id))
        return domain

    @api.model
    def _order_value(self, orders):
        return sum(order.amount_untaxed / (order.currency_rate or 1.0) for order in orders)

    def _recognized_on_orders(self, orders, date_to):
        """Revenue recognised up to date_to on these orders: their invoice
        lines on revenue accounts, plus what the spread of deferred
        (maintenance) lines has released"""
        if not orders:
            return 0.0
        invoice_lines = self.env['account.move.line'].search([
            ('sale_line_ids', 'in', orders.order_line.ids), ('parent_state', '=', 'posted')])
        revenue = -sum(line.balance for line in invoice_lines
                       if line.account_id.aiis_report_line == 'revenue' and line.date <= date_to)
        # sudo: the CEO and Chairman read the books but not the spread boards
        spreads = self.env['account.spread'].sudo().search([('invoice_line_id', 'in', invoice_lines.ids)])
        released = spreads.line_ids.move_id.line_ids.filtered(
            lambda l: l.parent_state == 'posted' and l.date <= date_to and l.account_id.aiis_report_line == 'revenue')
        return revenue - sum(released.mapped('balance'))

    def _backlog(self, p, at):
        orders = self.env['sale.order'].search(self._order_domain(p, date_to=at))
        return self._order_value(orders) - self._recognized_on_orders(orders, at)

    def _kpis(self, p, date_from, date_to):
        orders = self.env['sale.order'].search(self._order_domain(p, date_from, date_to))
        revenue = -self._sum_balance(self._aml_domain(p, 'revenue', date_from, date_to))
        cost = self._sum_balance(self._aml_domain(p, 'cost_of_revenue', date_from, date_to))
        gross = revenue - cost
        return {
            'order_value': self._order_value(orders),
            'revenue': revenue,
            'gross_profit': gross,
            'gross_margin': (gross / revenue * 100.0) if revenue else None,
            'backlog': self._backlog(p, date_to),
        }

    @api.model
    def get_overview(self, params=None):
        self._check_access()
        p = self._params(params)
        date_from, date_to = self._period_dates(p)
        quarters = []
        for label, start, end in self._quarters(p):
            if start > p['cutoff']:
                quarters.append({'label': label, 'future': True})
                continue
            quarters.append(dict(self._kpis(p, start, min(end, p['cutoff'])), label=label, future=False))
        return dict(self._header(p), date_from=date_from, date_to=date_to,
                    kpis=self._kpis(p, date_from, date_to), quarters=quarters)

    def _header(self, p):
        return {
            'year': p['year'],
            'cutoff': p['cutoff'],
            'period': p['period'],
            'currency': p['company'].currency_id.name,
            'project': p['project'] and [p['project'].id, p['project'].display_name],
            'partner': p['partner'] and [p['partner'].id, p['partner'].display_name],
        }

    @api.model
    def open_drill(self, kind, params=None, quarter=None):
        """List behind an Overview figure"""
        self._check_access()
        p = self._params(params)
        if quarter:
            _label, date_from, date_to = self._quarters(p)[int(quarter) - 1]
            date_to = min(date_to, p['cutoff'])
        else:
            date_from, date_to = self._period_dates(p)
        if kind in ('order_value', 'backlog'):
            domain = self._order_domain(p, None if kind == 'backlog' else date_from, date_to)
            return {
                'type': 'ir.actions.act_window',
                'name': _('Sales Orders') if kind == 'order_value' else _('Orders in Backlog'),
                'res_model': 'sale.order',
                'views': [(False, 'list'), (False, 'form')],
                'domain': domain,
                'context': {'create': False},
            }
        lines = ['revenue'] if kind == 'revenue' else ['revenue', 'cost_of_revenue']
        return {
            'type': 'ir.actions.act_window',
            'name': _('Recognized Revenue') if kind == 'revenue' else _('Revenue and Cost of Revenue'),
            'res_model': 'account.move.line',
            'views': [(False, 'list'), (False, 'form')],
            'domain': self._aml_domain(p, lines, date_from, date_to),
            'context': {'create': False, 'search_default_group_by_account': 1},
        }

    # ------------------------------------------------------------------
    # Cash Flow
    # ------------------------------------------------------------------
    def _bank_balance(self, p, date_to, date_from=None):
        domain = [('parent_state', '=', 'posted'), ('company_id', '=', p['company'].id),
                  ('account_id.aiis_report_line', '=', 'bank'), ('date', '<=', date_to)]
        if date_from:
            domain.append(('date', '>=', date_from))
        return self._sum_balance(domain)

    def _classify_document(self, move, project_accounts):
        """{row: weight} of what a settled document was for"""
        if move.move_type in ('out_invoice', 'out_refund', 'out_receipt'):
            return {'receipts': 1.0}
        weights = defaultdict(float)
        for line in move.line_ids:
            account = line.account_id
            if line.display_type in ('payment_term',) or account.account_type in ('asset_receivable', 'liability_payable'):
                continue
            if line.tax_line_id:
                continue
            amount = abs(line.balance)
            if account.account_type == 'asset_fixed':
                weights['capex'] += amount
            elif (account.aiis_report_line == 'cost_of_revenue'
                  or {key for keys in (line.analytic_distribution or {}) for key in keys.split(',')} & project_accounts):
                weights['project'] += amount
            elif account.aiis_report_line in TAX_LINES:
                weights['tax'] += amount
            else:
                weights['opex'] += amount
        total = sum(weights.values())
        if not total:
            return {'opex': 1.0}
        return {row: weight / total for row, weight in weights.items()}

    def _matched_documents(self, line):
        """Documents a receivable/payable/outstanding line was matched with"""
        partials = line.matched_debit_ids | line.matched_credit_ids
        others = (partials.debit_move_id | partials.credit_move_id) - line
        moves = self.env['account.move']
        for other in others:
            payment = other.move_id.payment_id
            if payment:
                moves |= payment.reconciled_invoice_ids | payment.reconciled_bill_ids
                if not (payment.reconciled_invoice_ids | payment.reconciled_bill_ids):
                    moves |= payment.move_id
            else:
                moves |= other.move_id
        return moves

    def _classify_statement_line(self, st_line, project_accounts):
        """{row: amount} of one reconciled bank statement line"""
        result = defaultdict(float)
        _liquidity, _suspense, other_lines = st_line._seek_for_lines()
        transfer = st_line.company_id.transfer_account_id
        for counterpart in other_lines:
            amount = -counterpart.balance
            account = counterpart.account_id
            if account.account_type == 'asset_cash' or account == transfer:
                continue  # internal transfers cancel out
            if account.aiis_report_line in TAX_LINES:
                result['tax'] += amount
                continue
            documents = self._matched_documents(counterpart)
            if not documents:
                result['receipts' if account.account_type == 'asset_receivable' else 'opex'] += amount
                continue
            weights = defaultdict(float)
            for document in documents:
                size = abs(document.amount_total_signed) or 1.0
                for row, share in self._classify_document(document, project_accounts).items():
                    weights[row] += share * size
            total = sum(weights.values())
            for row, weight in weights.items():
                result[row] += amount * weight / total
        return result

    def _cash_movements(self, p, date_from, date_to):
        rows = {key: 0.0 for key, _label in CASH_ROWS}
        project_accounts = self._project_accounts()
        st_lines = self.env['account.bank.statement.line'].search([
            ('company_id', '=', p['company'].id), ('date', '>=', date_from), ('date', '<=', date_to),
            ('state', '=', 'posted'), ('is_reconciled', '=', True)])
        for st_line in st_lines:
            for row, amount in self._classify_statement_line(st_line, project_accounts).items():
                rows[row] += amount
        # whatever moved the bank accounts without a reconciled statement line
        net = self._bank_balance(p, date_to, date_from)
        rows['other'] += net - sum(rows.values())
        return rows

    @api.model
    def get_cash_flow(self, params=None):
        self._check_access()
        p = self._params(params)
        columns = []
        for label, start, end in self._quarters(p):
            if start > p['cutoff']:
                columns.append({'label': label, 'future': True})
                continue
            end = min(end, p['cutoff'])
            opening = self._bank_balance(p, start - relativedelta(days=1))
            rows = self._cash_movements(p, start, end)
            net = sum(rows.values())
            columns.append(dict(rows, label=label, future=False, opening=opening, net=net,
                                closing=opening + net, date_from=start, date_to=end))
        return dict(self._header(p), rows=CASH_ROWS, columns=columns)

    # ------------------------------------------------------------------
    # Checks
    # ------------------------------------------------------------------
    def _balance_at(self, p, line):
        return self._sum_balance([('parent_state', '=', 'posted'), ('company_id', '=', p['company'].id),
                                  ('account_id.aiis_report_line', '=', line), ('date', '<=', p['cutoff'])])

    def _aged_total(self, p, line):
        accounts = self.env['account.account'].search(
            [('company_id', '=', p['company'].id), ('aiis_report_line', '=', line), ('reconcile', '=', True)])
        if not accounts:
            return 0.0
        wizard = self.env['aged.partner.balance.report.wizard'].create({
            'company_id': p['company'].id,
            'date_at': p['cutoff'],
            'target_move': 'posted',
            'account_ids': [(6, 0, accounts.ids)],
            'age_partner_config_id': self.env.ref('aiis_finance.age_configuration').id,
        })
        # as the report receives it from the web client: dates as strings
        data = {key: fields.Date.to_string(value) if isinstance(value, date) else value
                for key, value in wizard._prepare_report_data().items()}
        values = self.env['report.account_financial_report.aged_partner_balance']._get_report_values(wizard.ids, data)
        return sum(account['residual'] for account in values['aged_partner_balance'])

    def _project_accounts(self):
        plan = self.env.ref('aiis_project.analytic_plan_projects', raise_if_not_found=False)
        if not plan:
            return set()
        return set(map(str, self.env['account.analytic.account'].search([('plan_id', 'child_of', plan.id)]).ids))

    def _project_share(self, lines, project_accounts):
        """Part of these lines' balance that is on projects"""
        total = 0.0
        for line in lines:
            share = sum(pct for key, pct in (line.analytic_distribution or {}).items()
                        if set(key.split(',')) & project_accounts)
            total += line.balance * min(share, 100.0) / 100.0
        return total

    def _open_at(self, line, cutoff):
        """Amount of a receivable/payable line still open at the cutoff"""
        residual = line.balance
        for partial in line.matched_credit_ids.filtered(lambda r: r.max_date <= cutoff):
            residual -= partial.amount
        for partial in line.matched_debit_ids.filtered(lambda r: r.max_date <= cutoff):
            residual += partial.amount
        return residual

    def _project_open(self, p, line_key, project_accounts):
        lines = self.env['account.move.line'].search([
            ('parent_state', '=', 'posted'), ('company_id', '=', p['company'].id),
            ('account_id.aiis_report_line', '=', line_key), ('date', '<=', p['cutoff'])])
        total = 0.0
        for move, move_lines in lines.grouped('move_id').items():
            product_lines = move.invoice_line_ids
            on_project = abs(self._project_share(product_lines, project_accounts))
            whole = abs(sum(product_lines.mapped('balance')))
            if not on_project or not whole:
                continue
            total += sum(self._open_at(line, p['cutoff']) for line in move_lines) * on_project / whole
        return total

    @api.model
    def get_checks(self, params=None):
        self._check_access()
        p = self._params(params)
        dp = p['company'].currency_id.decimal_places
        project_accounts = self._project_accounts()
        AML = self.env['account.move.line']
        upto = [('parent_state', '=', 'posted'), ('company_id', '=', p['company'].id), ('date', '<=', p['cutoff'])]
        revenue_lines = AML.search(upto + [('account_id.aiis_report_line', '=', 'revenue')])
        cost_lines = AML.search(upto + [('account_id.aiis_report_line', '=', 'cost_of_revenue')])
        ar = self._balance_at(p, 'receivable')
        ap = -self._balance_at(p, 'payable')
        checks = [
            (_('Receivables aging vs balance sheet'), _('Aging total'), self._aged_total(p, 'receivable'),
             _('Accounts receivable'), ar),
            (_('Payables aging vs balance sheet'), _('Aging total'), -self._aged_total(p, 'payable'),
             _('Accounts payable'), ap),
            (_('Project revenue vs company revenue'), _('Sum of projects'),
             -self._project_share(revenue_lines, project_accounts), _('Recognized revenue (cumulative)'),
             -sum(revenue_lines.mapped('balance'))),
            (_('Project cost vs cost of revenue'), _('Sum of projects'),
             self._project_share(cost_lines, project_accounts), _('Cost of revenue (cumulative)'),
             sum(cost_lines.mapped('balance'))),
            (_('Project receivables vs company receivables'), _('Sum of projects'),
             self._project_open(p, 'receivable', project_accounts), _('Accounts receivable'), ar),
            (_('Project payables vs company payables'), _('Sum of projects'),
             -self._project_open(p, 'payable', project_accounts), _('Accounts payable'), ap),
        ]
        unmapped = self.env['account.account'].search(
            [('company_id', '=', p['company'].id), ('aiis_report_line', '=', False)])
        unmapped_balance = self._sum_balance(upto + [('account_id', 'in', unmapped.ids)]) if unmapped else 0.0
        result = []
        for name, left_label, left, right_label, right in checks:
            diff = left - right
            result.append({'name': name, 'left_label': left_label, 'left': left, 'right_label': right_label,
                           'right': right, 'diff': diff, 'ok': float_is_zero(diff, precision_digits=dp)})
        result.append({'name': _('Accounts without a report line'), 'left_label': _('Balance'),
                       'left': unmapped_balance, 'right_label': '', 'right': 0.0, 'diff': unmapped_balance,
                       'ok': float_is_zero(unmapped_balance, precision_digits=dp)})
        return dict(self._header(p), checks=result)
