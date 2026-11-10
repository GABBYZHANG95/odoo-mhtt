from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['procurement.request'].search([
        ('state', '=', 'approved'), ('purchase_order_id', '=', False),
    ])._schedule_procurement_activities()
