# Set one database's brand: company logo and brand colour (docs/deploy/
# 正式上线计划.md, step 5). MHTT's brand is data, kept in deploy/brand.env and
# branding/ (logo).
# Run in odoo shell:
#
#   docker cp branding/mhtt-logo.png mhtt-app:/tmp/brand-logo.png
#   docker exec -u root mhtt-app chmod 644 /tmp/brand-logo.png
#   docker exec -i --env-file deploy/brand.env -e BRAND_LOGO=/tmp/brand-logo.png \
#       mhtt-app odoo shell -d mhtt --no-http < scripts/setup_company_brand.py
#
# BRAND_COLOR    #rrggbb (required): the main company's primary colour. The
#                interface (aiis_theme), the dashboards and printed documents
#                all follow it; lighter and darker shades are derived.
# BRAND_LOGO     path of the logo inside the container (optional; PNG with a
#                transparent background)
# COMPANY_NAME   legal name shown on documents (optional)
# COMPANY_NAME_AR  Arabic legal name (optional): the company tagline, printed
#                at the top right of every printed document, beside the logo
#
# Safe to run again. Reload the browser afterwards; no restart needed.
import base64
import colorsys
import os
import re

color = os.environ['BRAND_COLOR'].strip().lower()
if not re.fullmatch(r'#[0-9a-f]{6}', color):
    raise SystemExit('BRAND_COLOR must look like #233d7e, got %r' % color)
r, g, b = (int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))
h, l, s = colorsys.rgb_to_hls(r, g, b)
dark = '#%02x%02x%02x' % tuple(round(c * 255) for c in colorsys.hls_to_rgb(h, max(0, l - 0.15), s))

company = env.ref('base.main_company')
values = {'primary_color': color, 'secondary_color': dark}
if os.environ.get('COMPANY_NAME'):
    values['name'] = os.environ['COMPANY_NAME']
if os.environ.get('COMPANY_NAME_AR'):
    values['report_header'] = os.environ['COMPANY_NAME_AR']
if os.environ.get('BRAND_LOGO'):
    with open(os.environ['BRAND_LOGO'], 'rb') as logo:
        values['logo'] = base64.b64encode(logo.read())
if not company.external_report_layout_id:
    values['external_report_layout_id'] = env.ref('web.external_layout_standard').id
company.write(values)
env.cr.commit()
# Tell the running Odoo processes to rebuild their stylesheets (an odoo
# shell does not do it by itself, unlike a save in the browser).
env.registry.signal_changes()
print('%s: colour %s (dark %s), logo %s' % (
    company.name, company.primary_color, company.secondary_color, 'set' if company.logo else 'none'))
