from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['aiis.approval.position'].search([('code', '=', 'cfo')])._sync_finance_tasks()
