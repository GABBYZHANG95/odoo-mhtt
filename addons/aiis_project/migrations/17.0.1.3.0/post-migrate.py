"""Project information uploaded so far was not attached to its project, so
only the uploader could open it (approvers got an access error): attach it."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['aiis.project'].search([])._attach_project_information()
