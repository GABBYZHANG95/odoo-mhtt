from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# Report lines (design doc 6.1, 6.2, 6.7.6). The P&L and Balance Sheet add
# up accounts by these keys, so any chart of accounts can feed them.
PL_LINES = [
    ('revenue', 'P&L / Recognized revenue'),
    ('cost_of_revenue', 'P&L / Cost of revenue'),
    ('ga_personnel', 'P&L / G&A / Personnel cost'),
    ('ga_rent', 'P&L / G&A / Office rent and utilities'),
    ('ga_professional', 'P&L / G&A / Legal, audit and professional fees'),
    ('ga_insurance', 'P&L / G&A / Insurance, licenses and administration'),
    ('ga_it', 'P&L / G&A / IT systems and subscriptions'),
    ('ga_travel', 'P&L / G&A / Travel, training and recruitment'),
    ('ga_depreciation', 'P&L / G&A / Depreciation and amortization'),
    ('ga_other', 'P&L / G&A / Other administrative expenses'),
    ('rd_personnel', 'P&L / R&D / Personnel cost'),
    ('rd_cloud', 'P&L / R&D / Development cloud and compute'),
    ('rd_software', 'P&L / R&D / Development software and tools'),
    ('rd_contractors', 'P&L / R&D / Contractors and technical consulting'),
    ('rd_research', 'P&L / R&D / Research, testing and prototypes'),
    ('rd_depreciation', 'P&L / R&D / Depreciation and amortization'),
    ('rd_other', 'P&L / R&D / Other R&D expenses'),
    ('sales_personnel', 'P&L / Sales / Personnel cost'),
    ('sales_marketing', 'P&L / Sales / Marketing and advertising'),
    ('sales_travel', 'P&L / Sales / Sales travel and entertainment'),
    ('sales_events', 'P&L / Sales / Events, exhibitions and sponsorship'),
    ('sales_commissions', 'P&L / Sales / Commissions and channel incentives'),
    ('sales_crm', 'P&L / Sales / CRM and sales tools'),
    ('sales_depreciation', 'P&L / Sales / Depreciation and amortization'),
    ('sales_other', 'P&L / Sales / Other sales expenses'),
    ('other_income', 'P&L / Net other income'),
    ('finance_costs', 'P&L / Finance costs'),
    ('zakat', 'P&L / Zakat'),
    ('income_tax', 'P&L / Income tax'),
]
BS_LINES = [
    ('bank', 'BS / Bank and cash'),
    ('receivable', 'BS / Accounts receivable'),
    ('vat_input', 'BS / Input VAT'),
    ('prepaid', 'BS / Prepayments to suppliers'),
    ('other_receivable', 'BS / Other receivables'),
    ('fixed_assets', 'BS / Fixed assets (cost less depreciation)'),
    ('other_non_current_assets', 'BS / Other non-current assets'),
    ('payable', 'BS / Accounts payable'),
    ('vat_output', 'BS / VAT payable'),
    ('accrued', 'BS / Accrued expenses'),
    ('payroll_payable', 'BS / Salaries and employee payables'),
    ('deferred_revenue', 'BS / Deferred maintenance revenue'),
    ('customer_advances', 'BS / Customer advances'),
    ('zakat_payable', 'BS / Zakat payable'),
    ('income_tax_payable', 'BS / Income tax payable'),
    ('eosb', 'BS / End-of-service benefits provision'),
    ('share_capital', 'BS / Share capital'),
    ('retained_earnings', 'BS / Retained earnings'),
]
PL_KEYS = {key for key, _label in PL_LINES}

# where an account goes when nobody said otherwise
DEFAULT_LINE = {
    'income': 'revenue',
    'income_other': 'other_income',
    'expense_direct_cost': 'cost_of_revenue',
    'expense': 'ga_other',
    'expense_depreciation': 'ga_depreciation',
    'asset_cash': 'bank',
    'asset_receivable': 'receivable',
    'asset_prepayments': 'prepaid',
    'asset_current': 'other_receivable',
    'asset_fixed': 'fixed_assets',
    'asset_non_current': 'other_non_current_assets',
    'liability_payable': 'payable',
    'liability_credit_card': 'accrued',
    'liability_current': 'accrued',
    'liability_non_current': 'eosb',
    'equity': 'retained_earnings',
    'equity_unaffected': 'retained_earnings',
}


class AccountAccount(models.Model):
    _inherit = 'account.account'

    aiis_report_line = fields.Selection(
        PL_LINES + BS_LINES, string='Report Line', compute='_compute_aiis_report_line',
        store=True, readonly=False, index=True,
        help='The line of the Profit & Loss or Balance Sheet this account adds up into.')

    @api.depends('account_type')
    def _compute_aiis_report_line(self):
        for account in self:
            account.aiis_report_line = DEFAULT_LINE.get(account.account_type)

    @api.constrains('aiis_report_line', 'account_type')
    def _check_aiis_report_line(self):
        for account in self.filtered('aiis_report_line'):
            pl_account = not account.include_initial_balance
            if pl_account != (account.aiis_report_line in PL_KEYS):
                raise ValidationError(_(
                    "%(account)s: an income or expense account goes on a Profit & Loss line, "
                    "any other account on a Balance Sheet line.", account=account.display_name))

    def _aiis_set_tax_lines(self):
        """Tax accounts default to the VAT lines of the balance sheet"""
        lines = self.env['account.tax.repartition.line'].search([
            ('account_id', '!=', False), ('repartition_type', '=', 'tax'),
            ('tax_id.company_id', 'in', self.env.companies.ids)])
        for line in lines:
            account = line.account_id
            if account.include_initial_balance and account.aiis_report_line in ('other_receivable', 'accrued', 'eosb', False):
                account.aiis_report_line = 'vat_input' if line.tax_id.type_tax_use == 'purchase' else 'vat_output'
