import re

from odoo import api, models

# The brand colour is data: the main company's primary colour
# (also used by printed documents) becomes the SCSS variable $aiis-brand of
# this database, in an attachment loaded just before primary_variables.scss.
BRAND_URL = '/_custom/aiis_theme/brand_variables.scss'
BRAND_TARGET = 'aiis_theme/static/src/scss/primary_variables.scss'
HEX_COLOR = re.compile(r'^#(?:[0-9a-fA-F]{3}){1,2}$')


class ResCompany(models.Model):
    _inherit = 'res.company'

    def write(self, vals):
        res = super().write(vals)
        if 'primary_color' in vals and self.env.ref('base.main_company') in self:
            self._aiis_sync_brand()
        return res

    @api.model
    def _aiis_brand_records(self):
        attachment = self.env['ir.attachment'].sudo().search(
            [('url', '=', BRAND_URL), ('type', '=', 'binary')], limit=1)
        asset = self.env['ir.asset'].sudo().with_context(active_test=False).search([('path', '=', BRAND_URL)])
        return attachment, asset

    @api.model
    def _aiis_sync_brand(self):
        """Compile this database's interface in the main company's primary
        colour; without a valid colour, fall back to the default (MHTT navy)."""
        color = (self.env.ref('base.main_company').primary_color or '').strip().lower()
        attachment, asset = self._aiis_brand_records()
        if not HEX_COLOR.match(color):
            # Only #rgb / #rrggbb ever reaches the stylesheet.
            asset.unlink()
            attachment.unlink()
            return
        content = ('// Generated from the company primary colour by aiis_theme. Do not edit.\n'
                   '$aiis-brand: %s;\n' % color).encode()
        if not attachment:
            self.env['ir.attachment'].sudo().create({
                'name': 'brand_variables.scss', 'type': 'binary', 'url': BRAND_URL,
                'mimetype': 'text/scss', 'raw': content})
        elif attachment.raw != content:
            attachment.write({'raw': content})
        values = {'name': 'Brand colour (aiis_theme)', 'bundle': 'web._assets_primary_variables',
                  'directive': 'before', 'target': BRAND_TARGET, 'path': BRAND_URL,
                  'sequence': 100, 'active': True}
        if not asset:
            self.env['ir.asset'].sudo().create(values)
        else:
            asset[1:].unlink()
            asset[:1].write(values)
        # The bundle version follows the attachment's write date; the cached
        # bundle list must be rebuilt for a new or changed colour.
        self.env.registry.clear_cache('assets')
