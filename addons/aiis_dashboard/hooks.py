def post_init_hook(env):
    env['aiis.approval.position']._aiis_sync_dashboard_group()
