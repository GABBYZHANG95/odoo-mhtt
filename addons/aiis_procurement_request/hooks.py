def set_procurement_department(env):
    """The department called Procurement converts purchase requests (#48)"""
    for company in env['res.company'].search([('aiis_procurement_department_id', '=', False)]):
        department = env['hr.department'].search(
            [('name', '=ilike', 'procurement'), ('company_id', 'in', (company.id, False))], limit=1)
        if department:
            company.aiis_procurement_department_id = department


def post_init_hook(env):
    set_procurement_department(env)
    env['res.users']._aiis_sync_procurement_buyers()
