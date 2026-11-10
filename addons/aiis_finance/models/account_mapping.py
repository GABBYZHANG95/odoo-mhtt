from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .hr_department import FUNCTIONS


class AccountMapping(models.Model):
    """Which expense account a cost goes to (design doc 6.3, 6.4): the
    product category says what it is (nature), the requester's department
    says who uses it (function)."""
    _name = 'aiis.account.mapping'
    _description = 'Expense Account Mapping'
    _order = 'categ_id, function'

    categ_id = fields.Many2one('product.category', string='Nature (Product Category)', required=True,
                               ondelete='cascade', index=True)
    function = fields.Selection(FUNCTIONS, help='Empty: any department.')
    account_id = fields.Many2one(
        'account.account', string='Account', required=True, ondelete='cascade',
        domain="[('company_id', '=', company_id), ('include_initial_balance', '=', False)]")
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)

    _sql_constraints = [
        ('categ_function_uniq', 'unique(company_id, categ_id, function)',
         'There is already an account for this nature and function.'),
    ]


class ProductCategory(models.Model):
    _inherit = 'product.category'

    aiis_mapping_ids = fields.One2many('aiis.account.mapping', 'categ_id', string='Expense Accounts')
    aiis_asset_account_id = fields.Many2one(
        'account.account', string='Fixed Asset Account', company_dependent=True,
        domain="[('account_type', '=', 'asset_fixed')]",
        help='Equipment of this category bought for the company at or above the capitalization '
             'threshold a unit becomes a fixed asset on this account (its asset profile sets the '
             'depreciation). Below the threshold, or when empty, it is an expense.')

    def _aiis_mapped_account(self, function, company):
        """The mapping of the nearest category up the tree"""
        category = self
        while category:
            mappings = category.aiis_mapping_ids.filtered(lambda m: m.company_id == company)
            mapping = (mappings.filtered(lambda m: m.function == function)
                       or mappings.filtered(lambda m: not m.function))[:1]
            if mapping:
                return mapping.account_id
            category = category.parent_id
        return self.env['account.account']

    def _aiis_asset_account(self, company):
        category = self.with_company(company)
        while category:
            if category.aiis_asset_account_id:
                return category.aiis_asset_account_id
            category = category.parent_id
        return self.env['account.account']

    def _aiis_expense_account(self, company, department=None, unit_price=0.0, project=False):
        """Account for a cost of this category, `unit_price` in company currency.

        - for a project: Cost of revenue
        - equipment at or above the threshold: its fixed asset account
        - otherwise the mapping for the department's function
        Empty when no rule applies (Odoo's own account stays)."""
        self.ensure_one()
        category = self.with_company(company)
        if project:
            # Only an account set on the category itself counts: the chart's
            # company-wide default is a cost of revenue account too on
            # l10n_sa (400001 Cost of Goods Sold in Trading), and would
            # otherwise catch every project purchase of an internal category.
            own = self.env['ir.property'].sudo().search([
                ('name', '=', 'property_account_expense_categ_id'),
                ('res_id', '=', 'product.category,%s' % self.id),
                ('company_id', 'in', (company.id, False))], limit=1)
            account = category.property_account_expense_categ_id
            if own and account.account_type == 'expense_direct_cost':
                return account
            return self.env.ref('aiis_finance.account_project_cost', raise_if_not_found=False) or account
        asset_account = category._aiis_asset_account(company)
        if asset_account and unit_price >= company.aiis_capitalization_threshold:
            return asset_account
        function = department._aiis_get_function() if department else 'ga'
        return category._aiis_mapped_account(function, company)


class ResCompany(models.Model):
    _inherit = 'res.company'

    aiis_capitalization_threshold = fields.Monetary(
        string='Capitalization Threshold', currency_field='currency_id', default=5000.0,
        help='Equipment from this unit price (company currency) is a fixed asset.')

    @api.constrains('aiis_capitalization_threshold')
    def _check_aiis_capitalization_threshold(self):
        if any(company.aiis_capitalization_threshold < 0 for company in self):
            raise ValidationError(_("The capitalization threshold cannot be negative."))
