def set_department_functions(env):
    """R&D department -> R&D, sales departments -> Sales, the rest G&A"""
    Department = env['hr.department'].with_context(active_test=False)
    for company in env['res.company'].search([]):
        if company.aiis_rd_department_id and not company.aiis_rd_department_id.aiis_function:
            company.aiis_rd_department_id.aiis_function = 'rd'
    Department.search([('name', '=ilike', 'sales%'), ('aiis_function', '=', False)]).aiis_function = 'sales'


def post_init_hook(env):
    env['account.account'].search([])._aiis_set_tax_lines()
    env['hr.department'].with_context(active_test=False).search([])._aiis_ensure_analytic_account()
    set_department_functions(env)
    env['res.company'].search([]).write({'force_move_auto_post': True})
    env['aiis.approval.position']._aiis_sync_finance_groups()
