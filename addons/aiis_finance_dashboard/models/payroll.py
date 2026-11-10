from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

# Contracts dated before this are treated as if they started here, so a
# demo contract with a stale historical date_start doesn't dump a huge
# one-off backlog of salary postings into the report.
PAYROLL_HORIZON = fields.Date.from_string('2026-01-01')


class AccountMove(models.Model):
    """Just enough of a link back to the employee for the Finance report's
    SQL view to attribute a Labor Cost posting to that employee's
    department - a plain account.move has no such link on its own since
    it isn't created from a payslip (Community has no Payroll app)."""
    _inherit = 'account.move'

    aiis_payroll_employee_id = fields.Many2one(
        'hr.employee', string='Payroll Employee', readonly=True, copy=False)


class HrContract(models.Model):
    """Turns the `wage` already recorded on each active contract into real
    monthly Labor Cost postings, since Community has no Payroll app to do
    this on its own. One posted journal entry per employee per month
    (Debit Labor Cost / Credit Bank), so it flows into the Finance report
    exactly like any other expense - see finance_entry.py branch (E).
    """
    _inherit = 'hr.contract'

    def _aiis_payroll_journal(self):
        journal = self.env['account.journal'].search(
            [('code', '=', 'PAY'), ('company_id', '=', self.env.company.id)], limit=1)
        if not journal:
            raise UserError('Payroll journal (code PAY) not found - is aiis_finance_dashboard installed?')
        return journal

    def _aiis_bank_account(self):
        account = self.env['account.account'].search(
            [('code', '=', '101401'), ('company_id', '=', self.env.company.id)], limit=1)
        if not account:
            raise UserError('Bank account 101401 not found.')
        return account

    def _aiis_post_payroll_month(self, period_start):
        """Post (or return the existing) payroll entry for this contract's
        employee for the month containing period_start."""
        self.ensure_one()
        journal = self._aiis_payroll_journal()
        ref = 'Payroll - %s - %s' % (self.employee_id.name, period_start.strftime('%Y-%m'))
        existing = self.env['account.move'].search(
            [('journal_id', '=', journal.id), ('ref', '=', ref)], limit=1)
        if existing:
            return existing
        period_end = period_start + relativedelta(day=31)
        labor_account = self.env.ref('aiis_finance_dashboard.account_labor_cost')
        bank_account = self._aiis_bank_account()
        move = self.env['account.move'].create({
            'move_type': 'entry',
            'journal_id': journal.id,
            'date': period_end,
            'ref': ref,
            'aiis_payroll_employee_id': self.employee_id.id,
            'line_ids': [
                (0, 0, {
                    'account_id': labor_account.id,
                    'name': self.employee_id.name,
                    'debit': self.wage,
                    'credit': 0.0,
                }),
                (0, 0, {
                    'account_id': bank_account.id,
                    'name': self.employee_id.name,
                    'debit': 0.0,
                    'credit': self.wage,
                }),
            ],
        })
        move.action_post()
        return move

    @api.model
    def _aiis_run_monthly_payroll(self, upto_date=None):
        """Catch up every open contract's salary postings, from the later
        of its start date / PAYROLL_HORIZON, through upto_date (defaults
        to today). Months already posted are skipped, so this is safe to
        call from the monthly cron as well as on demand."""
        upto_date = upto_date or fields.Date.context_today(self)
        contracts = self.search([('state', '=', 'open'), ('wage', '>', 0)])
        posted = self.env['account.move']
        for contract in contracts:
            cursor = max(contract.date_start, PAYROLL_HORIZON).replace(day=1)
            while cursor <= upto_date:
                posted |= contract._aiis_post_payroll_month(cursor)
                cursor = cursor + relativedelta(months=1)
        return posted
