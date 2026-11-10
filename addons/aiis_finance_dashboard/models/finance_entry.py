from odoo import fields, models, tools


class AiisFinanceEntry(models.Model):
    """Read-only reporting model (backed by a SQL view) that turns *actual
    cash movements* (posted account.payment records) into a company cash
    flow ledger — money that has really been received from customers or
    paid out to suppliers, not just invoices/bills that have been posted
    but not yet settled.

    - income  : posted inbound customer payments (minus outbound refunds we
                pay back to a customer), attributed to the project
                (`analytic_account_id`) of whichever customer invoice the
                payment is reconciled against, when it is
    - cost    : the portion of a supplier payment that settles a bill line
                booked to a Cost of Goods Sold account (account_type =
                'expense_direct_cost') - i.e. something bought specifically
                to resell to a customer.
    - expense : the portion of a supplier payment that settles a bill line
                booked to a regular Expense account (account_type =
                'expense') - i.e. internal operating cost unrelated to any
                specific deal, PLUS monthly salary postings from the
                dedicated Payroll journal (see models/payroll.py), which
                are plain journal entries rather than vendor payments.
                Broken down by `category_id` (the actual
                account.account the line posted to), since each expense
                "type" (Labor Cost, Office Supplies, IT & Software
                Services, Employee Reimbursement, Other Administrative
                Expenses) has its own dedicated account. Grouping by the
                account itself - not the product's purchase category -
                means several different product categories that happen to
                route to the same account (e.g. "Office Furniture" and
                "Office Supplies") still roll up into one bucket instead of
                showing as separate rows.

    Cost vs Expense is decided purely by Odoo's own account_type - no
    custom classification logic. A bill that mixes several categories
    (e.g. part Office Supplies, part IT & Software) has its payment split
    proportionally across every (bucket, account) combination present on
    that bill, via Odoo's own reconciliation records
    (account.partial.reconcile).

    `amount` is the signed P&L contribution (income positive, cost/expense
    negative), so summing `amount` directly gives Profit = Revenue - Cost -
    Expense for whatever grouping (date, category, project via
    `analytic_account_id`, department via `department_id`, ...) is picked
    in the view.

    `department_id` is who the Cost/Expense belongs to: for a Procurement
    Request-sourced bill, the requester's department; for Labor Cost, the
    employee's department. It is always empty for Revenue - cash received
    isn't "owned" by any one department, so grouping Revenue by department
    puts it under "None", which is the correct answer, not a bug.

    Every amount here is already converted to the COMPANY's currency
    (SAR), regardless of what currency the underlying invoice/bill/payment
    was actually made in - that's what these reports are for: one
    consistent unit for "how much did we make/spend", even though
    documents themselves (customer payments, PRs, ...) can freely use
    USD/CNY/SAR. `currency_id` is set to the company currency to match, so
    the Monetary widget never shows the wrong currency symbol next to an
    already-converted number.
    """
    _name = 'aiis.finance.entry'
    _description = 'Company Cash Flow Entry (Revenue / Cost / Expense, cash basis)'
    _auto = False
    _order = 'date desc, id desc'

    date = fields.Date(string='Payment Date', readonly=True)
    name = fields.Char(string='Reference', readonly=True)
    entry_type = fields.Selection([
        ('income', 'Revenue'),
        ('cost', 'Cost'),
        ('expense', 'Expense'),
    ], string='Type', readonly=True)
    amount = fields.Monetary(
        string='Amount', readonly=True,
        help='Signed amount: Revenue is positive, Cost and Expense are negative.')
    category_id = fields.Many2one(
        'account.account', string='Expense Category', readonly=True,
        help='Which item type this Cost/Expense belongs to (Labor Cost, '
             'Office Supplies, IT & Software Services, Employee '
             'Reimbursement, Other Administrative Expenses...) - one '
             'account per type. Empty for Revenue.')
    partner_id = fields.Many2one('res.partner', string='Partner', readonly=True)
    analytic_account_id = fields.Many2one(
        'account.analytic.account', string='Project / Analytic Account', readonly=True)
    department_id = fields.Many2one(
        'hr.department', string='Department', readonly=True,
        help='For Cost/Expense coming from a Procurement Request: the '
             "requester's department at the time of the request. For "
             "Labor Cost: the employee's current department. Always "
             'empty for Revenue - money is never "owned" by a department.')
    company_id = fields.Many2one('res.company', string='Company', readonly=True)
    currency_id = fields.Many2one('res.currency', string='Currency', readonly=True)
    move_id = fields.Many2one('account.move', string='Payment Entry', readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                WITH move_cat AS (
                    -- for every bill/refund: how much of it falls in each
                    -- (Cost vs Expense) x (account = item type) bucket
                    SELECT
                        m.id AS move_id,
                        CASE WHEN acc.account_type = 'expense_direct_cost'
                             THEN 'cost' ELSE 'expense' END AS bucket,
                        acc.id AS category_id,
                        SUM(aml.price_subtotal) AS cat_subtotal,
                        (
                            SELECT (jsonb_object_keys(COALESCE(l2.analytic_distribution, '{{}}'::jsonb)))::int
                            FROM account_move_line l2
                            WHERE l2.move_id = m.id AND l2.display_type = 'product'
                              AND l2.analytic_distribution IS NOT NULL
                              AND l2.analytic_distribution != '{{}}'::jsonb
                            LIMIT 1
                        ) AS analytic_account_id,
                        (
                            -- the department that originally requested this
                            -- purchase, via the Procurement Request that was
                            -- converted into the PO this bill line came from.
                            -- NULL for any bill not created from a PR (e.g. a
                            -- manually entered vendor bill) - that's fine, it
                            -- just shows up as "no department" when grouping.
                            SELECT pr.department_id
                            FROM account_move_line l3
                            JOIN purchase_order_line pol ON pol.id = l3.purchase_line_id
                            JOIN procurement_request pr ON pr.purchase_order_id = pol.order_id
                            WHERE l3.move_id = m.id AND l3.display_type = 'product'
                              AND l3.purchase_line_id IS NOT NULL
                            LIMIT 1
                        ) AS department_id
                    FROM account_move_line aml
                    JOIN account_move m ON m.id = aml.move_id
                    JOIN account_account acc ON acc.id = aml.account_id
                    WHERE aml.display_type = 'product'
                      AND m.move_type IN ('in_invoice', 'in_refund')
                      AND acc.account_type IN ('expense_direct_cost', 'expense', 'expense_depreciation')
                    GROUP BY m.id, bucket, acc.id
                ),
                move_total AS (
                    SELECT move_id, SUM(cat_subtotal) AS total_subtotal
                    FROM move_cat
                    GROUP BY move_id
                ),
                pay_line AS (
                    -- the receivable/payable settlement line on each payment's own entry
                    SELECT p.id AS payment_id, aml.id AS aml_id
                    FROM account_payment p
                    JOIN account_move_line aml ON aml.move_id = p.move_id
                    JOIN account_account acc ON acc.id = aml.account_id
                    WHERE acc.account_type IN ('liability_payable', 'asset_receivable')
                ),
                recon AS (
                    -- which invoice(s)/bill(s) each payment actually settles, and how much
                    -- (generic across both directions: a customer payment only ever
                    -- reconciles against out_invoice/out_refund lines, a supplier
                    -- payment only against in_invoice/in_refund lines, since that's
                    -- all Odoo's own reconciliation ever matches a receivable/
                    -- payable line against)
                    SELECT
                        pl.payment_id,
                        pr.amount AS matched_amount,
                        other_aml.move_id AS other_move_id
                    FROM pay_line pl
                    JOIN account_partial_reconcile pr
                        ON pr.debit_move_id = pl.aml_id OR pr.credit_move_id = pl.aml_id
                    JOIN account_move_line other_aml
                        ON other_aml.id = (CASE WHEN pr.debit_move_id = pl.aml_id
                                                THEN pr.credit_move_id ELSE pr.debit_move_id END)
                    JOIN account_payment p2 ON p2.id = pl.payment_id
                    WHERE other_aml.move_id != p2.move_id
                ),
                recon_cat AS (
                    -- split each reconciled amount across every (bucket, category)
                    -- present on that bill, proportional to its share of the bill
                    SELECT
                        r.payment_id,
                        mc.bucket,
                        mc.category_id,
                        mc.analytic_account_id,
                        mc.department_id,
                        r.matched_amount * (mc.cat_subtotal / NULLIF(mt.total_subtotal, 0)) AS cat_matched_amount
                    FROM recon r
                    JOIN move_cat mc ON mc.move_id = r.other_move_id
                    JOIN move_total mt ON mt.move_id = r.other_move_id
                ),
                recon_agg AS (
                    SELECT
                        payment_id, bucket, category_id, analytic_account_id, department_id,
                        SUM(cat_matched_amount) AS amount
                    FROM recon_cat
                    GROUP BY payment_id, bucket, category_id, analytic_account_id, department_id
                ),
                recon_signed_total AS (
                    -- signed (in company currency), how much of this payment
                    -- has actually been matched to an invoice/bill so far
                    SELECT
                        r.payment_id,
                        SUM(r.matched_amount * (CASE WHEN p3.payment_type = 'outbound' THEN -1 ELSE 1 END)) AS signed_matched
                    FROM recon r
                    JOIN account_payment p3 ON p3.id = r.payment_id
                    GROUP BY r.payment_id
                ),
                income_recon_cat AS (
                    -- same idea as recon_cat, but for the customer side: a
                    -- customer invoice only ever has ONE project on it (same
                    -- one-analytic-per-move simplification as move_cat above),
                    -- so there's no proportional split to do - just look up
                    -- which project (if any) the reconciled invoice belongs to.
                    SELECT
                        r.payment_id,
                        (
                            SELECT (jsonb_object_keys(COALESCE(l2.analytic_distribution, '{{}}'::jsonb)))::int
                            FROM account_move_line l2
                            WHERE l2.move_id = r.other_move_id AND l2.display_type = 'product'
                              AND l2.analytic_distribution IS NOT NULL
                              AND l2.analytic_distribution != '{{}}'::jsonb
                            LIMIT 1
                        ) AS analytic_account_id,
                        r.matched_amount AS amount
                    FROM recon r
                    JOIN account_move om ON om.id = r.other_move_id
                        AND om.move_type IN ('out_invoice', 'out_refund')
                ),
                income_recon_agg AS (
                    SELECT payment_id, analytic_account_id, SUM(amount) AS amount
                    FROM income_recon_cat
                    GROUP BY payment_id, analytic_account_id
                )

                -- (A) the part of a customer payment that's reconciled to a
                --     specific invoice: attributed to that invoice's project.
                --     `ia.amount` comes from account.partial.reconcile, which
                --     Odoo always stores in company currency, so no
                --     conversion needed - only the sign depends on direction.
                SELECT
                    p.id * 1000000 + 500000 + COALESCE(ia.analytic_account_id, 0) AS id,
                    pm.date AS date, pm.name AS name,
                    'income'::varchar AS entry_type,
                    ia.amount * (CASE WHEN p.payment_type = 'outbound' THEN -1 ELSE 1 END) AS amount,
                    NULL::int AS category_id,
                    p.partner_id AS partner_id, ia.analytic_account_id AS analytic_account_id,
                    NULL::int AS department_id,
                    pm.company_id AS company_id, rc.currency_id AS currency_id, pm.id AS move_id
                FROM account_payment p
                JOIN account_move pm ON pm.id = p.move_id AND pm.state = 'posted'
                JOIN res_company rc ON rc.id = pm.company_id
                JOIN income_recon_agg ia ON ia.payment_id = p.id
                WHERE p.partner_type = 'customer' AND ia.amount != 0

                UNION ALL
                -- (C) money paid to / received back from a supplier, split by
                --     bucket (cost/expense) and category. `ra.amount` already
                --     comes from account.partial.reconcile, which Odoo always
                --     stores in company currency, so no conversion needed here
                --     - only the sign depends on payment direction.
                SELECT
                    p.id * 1000000 + 1000
                        + COALESCE(ra.category_id, 0) * 2
                        + (CASE WHEN ra.bucket = 'expense' THEN 1 ELSE 0 END) AS id,
                    pm.date, pm.name, ra.bucket::varchar,
                    ra.amount * (CASE WHEN p.payment_type = 'outbound' THEN -1 ELSE 1 END),
                    ra.category_id,
                    p.partner_id, ra.analytic_account_id, ra.department_id,
                    pm.company_id, rc.currency_id, pm.id
                FROM account_payment p
                JOIN account_move pm ON pm.id = p.move_id AND pm.state = 'posted'
                JOIN res_company rc ON rc.id = pm.company_id
                JOIN recon_agg ra ON ra.payment_id = p.id
                WHERE p.partner_type = 'supplier' AND ra.amount != 0

                UNION ALL
                -- (E) monthly salary postings (see models/payroll.py) -
                --     these are plain journal entries in the dedicated
                --     "Payroll" journal (code PAY), Debit Labor Cost /
                --     Credit Bank, one per employee per month. Only the
                --     debit (expense) side counts here, same as every
                --     other branch above; the credit/bank side is not a
                --     P&L line. Matched by journal code, not id/xmlid, so
                --     this keeps working across reinstalls. Department
                --     comes from the employee's CURRENT department (via
                --     the aiis_payroll_employee_id link set at posting
                --     time) - if they transferred since, past months show
                --     under their new department too; that's an accepted
                --     simplification, not worth a historical snapshot.
                SELECT
                    2000000000 + aml.id, am.date, am.ref, 'expense'::varchar,
                    -aml.debit,
                    aml.account_id,
                    NULL::int, NULL::int, he.department_id, am.company_id, rc.currency_id, am.id
                FROM account_move_line aml
                JOIN account_move am ON am.id = aml.move_id AND am.state = 'posted'
                JOIN account_journal aj ON aj.id = am.journal_id AND aj.code = 'PAY'
                JOIN account_account acc ON acc.id = aml.account_id AND acc.account_type = 'expense'
                JOIN res_company rc ON rc.id = am.company_id
                LEFT JOIN hr_employee he ON he.id = am.aiis_payroll_employee_id
                WHERE aml.debit > 0

                UNION ALL
                -- (D) whatever part of a payment isn't reconciled to any
                --     (categorized) invoice/bill yet. For a supplier payment
                --     this is conservatively counted as Expense / Other
                --     Administrative so nothing silently disappears; for a
                --     customer payment it's simply counted as Revenue (cash
                --     received counts regardless of which invoice it'll end
                --     up settling - same as every customer payment used to
                --     be counted before project-splitting was added above).
                --     Both sides of this subtraction are already signed and
                --     in company currency, so it's safe regardless of what
                --     currency the payment itself was made in.
                SELECT
                    p.id * 1000000 + 3, pm.date, pm.name,
                    (CASE WHEN p.partner_type = 'customer' THEN 'income' ELSE 'expense' END)::varchar,
                    p.amount_company_currency_signed - COALESCE(rst.signed_matched, 0),
                    NULL::int,
                    p.partner_id, NULL::int, NULL::int, pm.company_id, rc.currency_id, pm.id
                FROM account_payment p
                JOIN account_move pm ON pm.id = p.move_id AND pm.state = 'posted'
                JOIN res_company rc ON rc.id = pm.company_id
                LEFT JOIN recon_signed_total rst ON rst.payment_id = p.id
                WHERE p.partner_type IN ('customer', 'supplier')
                  AND (p.amount_company_currency_signed - COALESCE(rst.signed_matched, 0)) != 0
            )
        """)
