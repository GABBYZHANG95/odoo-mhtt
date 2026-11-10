from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """#63: the contract gate is CFO -> CEO; drop the R&D-head step"""
    env = api.Environment(cr, SUPERUSER_ID, {})
    template = env.ref('aiis_project.approval_template_project_contract', raise_if_not_found=False)
    if template:
        template.node_ids.filtered(lambda n: n.node_type == 'department_head').unlink()
