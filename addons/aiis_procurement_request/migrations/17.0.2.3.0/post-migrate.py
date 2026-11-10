from odoo import SUPERUSER_ID, api

from odoo.addons.aiis_procurement_request.hooks import set_procurement_department


def migrate(cr, version):
    """Only the procurement department converts requests: set it, and take
    the conversion tasks away from everybody else (CEO, CFO, ...)"""
    env = api.Environment(cr, SUPERUSER_ID, {})
    set_procurement_department(env)
    env['res.users']._aiis_sync_procurement_buyers()
    env['procurement.request'].search([
        ('state', '=', 'approved'), ('purchase_order_id', '=', False),
    ])._schedule_procurement_activities()
