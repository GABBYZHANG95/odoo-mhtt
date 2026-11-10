"""Only R&D maintains project BOMs now (#43): tell the company which
department R&D is (guessed by name, editable on the company)."""
from odoo import SUPERUSER_ID, api

from odoo.addons.aiis_project.hooks import set_rd_department


def migrate(cr, version):
    set_rd_department(api.Environment(cr, SUPERUSER_ID, {}))
