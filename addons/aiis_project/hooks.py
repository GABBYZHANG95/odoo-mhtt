def find_rd_department(env):
    return env['hr.department'].search(
        ['|', ('name', 'ilike', 'Research'), ('name', '=ilike', 'R&D')], limit=1)


def set_rd_department(env):
    """Projects need to know which department is R&D: guess it by name.
    An administrator can change it on the company (Settings > Companies)."""
    department = find_rd_department(env)
    if department:
        env['res.company'].search([('aiis_rd_department_id', '=', False)]).aiis_rd_department_id = department


def post_init_hook(env):
    set_rd_department(env)
