from odoo import SUPERUSER_ID, api
from odoo.addons.aiis_finance.hooks import add_cfo_bank_operations


def migrate(cr, version):
    add_cfo_bank_operations(api.Environment(cr, SUPERUSER_ID, {}))
