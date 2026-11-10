from odoo import api, fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    aiis_customer_code = fields.Char(
        string='Customer Code', copy=False, index=True,
        help='Short code used in project numbers, e.g. STC -> STC-2026-001. '
             'Required before a project for this customer can be submitted.')

    _sql_constraints = [
        ('aiis_customer_code_unique', 'unique(aiis_customer_code)', 'This customer code is already used.'),
    ]

    @api.model
    def _normalize_customer_code(self, vals):
        if vals.get('aiis_customer_code'):
            vals['aiis_customer_code'] = vals['aiis_customer_code'].strip().upper()
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        return super().create([self._normalize_customer_code(vals) for vals in vals_list])

    def write(self, vals):
        return super().write(self._normalize_customer_code(vals))
