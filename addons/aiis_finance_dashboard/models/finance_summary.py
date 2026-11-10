from odoo import fields, models, tools


class AiisFinanceSummary(models.Model):
    """A proper profit statement, not a filtered/grouped transaction list -
    the point is that opening this page answers "how much did we make"
    directly, with "Profit" as a literal, always-present row (Revenue +
    Cost + Expense; Cost and Expense are already negative, so it's a plain
    sum), instead of requiring you to expand groups in a transaction
    browser and do the subtraction yourself.

    Unlike the first version of this model, a row here is not a single
    global total: it keeps `date`, `analytic_account_id` (Project) and
    `department_id` as real columns, one row per (type, date, project,
    department) combination that actually occurs in `aiis.finance.entry`.
    That's what lets the Pivot view filter by period (month/quarter/year,
    via the standard date-filter widget on `date`) and group by Project or
    Department interactively, while every number shown is still a plain
    SUM(amount) - Revenue/Cost/Expense/Profit never need recomputing by
    hand for whatever slice is currently selected.

    This model reads straight from `aiis.finance.entry`, which is a plain
    transaction list with no synthetic/total rows of its own, so summing
    it here can't double-count anything.
    """
    _name = 'aiis.finance.summary'
    _description = 'Profit Statement (Revenue / Cost / Expense / Profit)'
    _auto = False
    _order = 'date desc'

    type = fields.Selection([
        ('income', 'Revenue'),
        ('cost', 'Cost'),
        ('expense', 'Expense'),
        ('profit', 'Profit'),
    ], string='Line', readonly=True)
    is_total = fields.Boolean(readonly=True, help='True for the Profit row.')
    date = fields.Date(string='Payment Date', readonly=True)
    analytic_account_id = fields.Many2one(
        'account.analytic.account', string='Project', readonly=True)
    department_id = fields.Many2one('hr.department', string='Department', readonly=True)
    amount = fields.Monetary(string='Amount', readonly=True)
    company_id = fields.Many2one('res.company', string='Company', readonly=True)
    currency_id = fields.Many2one('res.currency', string='Currency', readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                WITH totals AS (
                    SELECT
                        entry_type AS type, date, analytic_account_id, department_id,
                        SUM(amount) AS amount,
                        MAX(company_id) AS company_id, MAX(currency_id) AS currency_id
                    FROM aiis_finance_entry
                    GROUP BY entry_type, date, analytic_account_id, department_id
                ),
                profit AS (
                    SELECT
                        'profit'::varchar AS type, date, analytic_account_id, department_id,
                        SUM(amount) AS amount,
                        MAX(company_id) AS company_id, MAX(currency_id) AS currency_id
                    FROM aiis_finance_entry
                    GROUP BY date, analytic_account_id, department_id
                )
                SELECT
                    row_number() OVER (ORDER BY date, analytic_account_id, department_id, type) AS id,
                    type, false AS is_total, amount,
                    date, analytic_account_id, department_id, company_id, currency_id
                FROM totals

                UNION ALL
                SELECT
                    1000000000 + row_number() OVER (ORDER BY date, analytic_account_id, department_id) AS id,
                    type, true AS is_total, amount,
                    date, analytic_account_id, department_id, company_id, currency_id
                FROM profit
            )
        """)
