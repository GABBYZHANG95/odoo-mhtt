from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Fill the Procurement Department (Buyer) group and hand the conversion
    tasks to its members only"""
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['res.users']._aiis_sync_procurement_buyers()
    env['procurement.request'].search([
        ('state', '=', 'approved'), ('purchase_order_id', '=', False),
    ])._schedule_procurement_activities()
