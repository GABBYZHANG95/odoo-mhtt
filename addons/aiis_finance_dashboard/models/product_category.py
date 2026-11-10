from odoo import api, models

# Categories that book to cost of revenue / to the catch-all expense account
COST_CATEGORIES = [
    'aiis_finance_dashboard.product_category_resale_software',
    'aiis_procurement_request.product_category_project_cost',
    'aiis_procurement_request.product_category_project_hardware',
    'aiis_procurement_request.product_category_project_cloud',
    'aiis_procurement_request.product_category_project_subcontracting',
]
OTHER_ADMIN_CATEGORIES = [
    'aiis_procurement_request.product_category_internal_expense',
    'aiis_procurement_request.product_category_internal_other_admin',
]
OTHER_ADMIN_NAME = 'Other Administrative Expenses'
OTHER_ADMIN_CODE = '605000'


class ProductCategory(models.Model):
    _inherit = 'product.category'

    @api.model
    def _aiis_set_default_accounts(self):
        """Install-time expense accounts of the purchase categories
        (data/product_category_data.xml). Found by account type, not by the
        xml ids of one chart of accounts, so the module installs on Odoo's
        generic chart (test database) and on the Saudi chart l10n_sa
        (production) alike. Without a chart yet there is nothing to set:
        the CFO sets the accounts on the categories later."""
        company = self.env.ref('base.main_company')
        Account = self.env['account.account'].sudo().with_company(company)
        if not Account.search_count([('company_id', '=', company.id)]):
            return
        cost = self._aiis_cost_of_revenue_account(company)
        other = self._aiis_other_admin_account(company)
        for xmlids, account in ((COST_CATEGORIES, cost), (OTHER_ADMIN_CATEGORIES, other)):
            for xmlid in xmlids:
                category = self.env.ref(xmlid, raise_if_not_found=False)
                if category and account:
                    category.sudo().with_company(company).property_account_expense_categ_id = account

    @api.model
    def _aiis_cost_of_revenue_account(self, company):
        """The chart's default category expense account when it is a cost of
        revenue account (l10n_sa: 400001 Cost of Goods Sold), else the first
        cost of revenue account by code (generic chart: 500000)."""
        default = self.env['ir.property'].with_company(company)._get(
            'property_account_expense_categ_id', 'product.category')
        if default and default.account_type == 'expense_direct_cost':
            return default
        return self.env['account.account'].sudo().search([
            ('company_id', '=', company.id), ('account_type', '=', 'expense_direct_cost'),
            ('deprecated', '=', False)], order='code', limit=1)

    @api.model
    def _aiis_other_admin_account(self, company):
        """The catch-all expense account. On the generic chart it is the
        chart's own 'Expenses' account, renamed; elsewhere AIIS adds its own
        (605000, next to the other dashboard accounts 601000-604000)."""
        generic = self.env.ref('account.%s_expense' % company.id, raise_if_not_found=False)
        if generic and generic._name == 'account.account':
            generic.sudo().name = OTHER_ADMIN_NAME
            return generic
        Account = self.env['account.account'].sudo()
        return Account.search([('company_id', '=', company.id), ('code', '=', OTHER_ADMIN_CODE)], limit=1) \
            or Account.create({'name': OTHER_ADMIN_NAME, 'code': OTHER_ADMIN_CODE,
                               'account_type': 'expense', 'company_id': company.id})
