def post_init_hook(env):
    dashboards = env.ref('spreadsheet_dashboard.spreadsheet_dashboard_menu_root', raise_if_not_found=False)
    if dashboards:
        dashboards.groups_id = [(6, 0, env.ref('base.group_system').ids)]
    env['res.users']._aiis_sync_sales_members()
