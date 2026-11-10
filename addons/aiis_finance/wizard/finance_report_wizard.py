from datetime import date

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

REPORTS = {
    'pl': 'aiis_finance.mis_report_profit_loss',
    'bs': 'aiis_finance.mis_report_balance_sheet',
}
# a column after the cutoff shows nothing (design doc 6.8.3: actual figures only)
NO_LINES = "[('id', '=', 0)]"


class FinanceReportWizard(models.TransientModel):
    """Filters of the Profit & Loss and Balance Sheet (design doc 6.8.3):
    opens the MIS Builder report with one column per quarter (or month) of
    the fiscal year, up to the cutoff date."""
    _name = 'aiis.finance.report.wizard'
    _description = 'Finance Report Filters'

    report = fields.Selection([('pl', 'Profit & Loss'), ('bs', 'Balance Sheet')], required=True, default='pl')
    fiscal_year = fields.Integer(required=True, default=lambda self: fields.Date.context_today(self).year)
    cutoff_date = fields.Date(string='Cutoff Date', required=True, default=fields.Date.context_today)
    columns = fields.Selection([('quarter', 'Quarters'), ('month', 'Months')], required=True, default='quarter')
    project_id = fields.Many2one('aiis.project', string='Project')
    department_id = fields.Many2one('hr.department', string='Department')
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)

    @api.onchange('fiscal_year')
    def _onchange_fiscal_year(self):
        if self.fiscal_year and self.cutoff_date and self.cutoff_date.year != self.fiscal_year:
            self.cutoff_date = min(date(self.fiscal_year, 12, 31), fields.Date.context_today(self))

    def _fiscal_year_dates(self):
        dates = self.company_id.compute_fiscalyear_dates(date(self.fiscal_year, 6, 30))
        return dates['date_from'], dates['date_to']

    def _columns(self):
        """[(label, date_from, date_to, after_cutoff)]"""
        year_start, year_end = self._fiscal_year_dates()
        step = 3 if self.columns == 'quarter' else 1
        columns, start, index = [], year_start, 1
        while start <= year_end:
            end = start + relativedelta(months=step, days=-1)
            label = ('Q%s' % index) if step == 3 else start.strftime('%b %Y')
            columns.append((label, start, min(end, self.cutoff_date), start > self.cutoff_date))
            start, index = end + relativedelta(days=1), index + 1
        return columns

    def _analytic_domain(self):
        accounts = []
        if self.project_id:
            if not self.project_id.analytic_account_id:
                raise UserError(_("%s has no analytic account yet.", self.project_id.display_name))
            accounts.append(self.project_id.analytic_account_id.id)
        if self.department_id:
            if not self.department_id.aiis_analytic_account_id:
                raise UserError(_("%s has no analytic account.", self.department_id.display_name))
            accounts.append(self.department_id.aiis_analytic_account_id.id)
        return str([('analytic_distribution', 'in', [account]) for account in accounts])

    def _period_vals(self):
        year_start, year_end = self._fiscal_year_dates()
        periods = []
        for sequence, (label, date_from, date_to, after_cutoff) in enumerate(self._columns()):
            if self.report == 'bs':
                date_from = date_to
                label = _('%(label)s (end)', label=label) if not after_cutoff else label
            periods.append({
                'name': label,
                'sequence': sequence,
                'mode': 'fix',
                'manual_date_from': date_from,
                'manual_date_to': date_to if not after_cutoff else date_from,
                'analytic_domain': NO_LINES if after_cutoff else '[]',
            })
        if self.report == 'pl':
            periods.append({
                'name': _('Full year (to cutoff)'),
                'sequence': len(periods),
                'mode': 'fix',
                'manual_date_from': year_start,
                'manual_date_to': min(year_end, self.cutoff_date),
                'analytic_domain': NO_LINES if year_start > self.cutoff_date else '[]',
            })
        return periods

    def action_open(self):
        self.ensure_one()
        year_start, year_end = self._fiscal_year_dates()
        if self.cutoff_date < year_start:
            raise UserError(_("The cutoff date is before the fiscal year starts."))
        report = self.env.ref(REPORTS[self.report])
        title = dict(self._fields['report'].selection)[self.report]
        filters = ', '.join(filter(None, [self.project_id.display_name, self.department_id.display_name]))
        instance = self.env['mis.report.instance'].create({
            'name': '%s %s%s' % (title, self.fiscal_year, filters and ' - %s' % filters or ''),
            'report_id': report.id,
            'company_id': self.company_id.id,
            'currency_id': self.company_id.currency_id.id,
            'date': min(self.cutoff_date, year_end),
            'target_move': 'posted',
            'temporary': True,
            'landscape_pdf': True,
            'analytic_domain': self._analytic_domain(),
            'period_ids': [(0, 0, vals) for vals in self._period_vals()],
        })
        return instance.preview()
