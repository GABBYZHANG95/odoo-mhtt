def uninstall_hook(env):
    attachment, asset = env['res.company']._aiis_brand_records()
    asset.unlink()
    attachment.unlink()
