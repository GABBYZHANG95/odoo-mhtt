from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestBrandColour(TransactionCase):
    """One codebase, one brand colour per database: the main company's
    primary colour."""

    def _css(self, bundle):
        assets = self.env['ir.qweb']._get_asset_bundle(bundle, css=True, js=False)
        return b''.join(assets.css().mapped('raw')).decode().lower()

    def test_interface_follows_the_primary_colour(self):
        company = self.env.ref('base.main_company')
        company.primary_color = '#233D7E'
        attachment, asset = company._aiis_brand_records()
        self.assertIn(b'$aiis-brand: #233d7e;', attachment.raw)
        self.assertEqual((asset.directive, asset.bundle), ('before', 'web._assets_primary_variables'))
        # Login page and website-side pages: compiled in the new colour
        self.assertIn('#233d7e', self._css('web.assets_frontend'))
        # Backend with the dashboards (shades derived in SCSS) compiles too
        backend = self._css('web.assets_backend')
        self.assertIn('#233d7e', backend)
        self.assertNotIn('#059669', backend)

        company.primary_color = '#059669'
        self.assertIn(b'$aiis-brand: #059669;', company._aiis_brand_records()[0].raw)
        self.assertIn('#059669', self._css('web.assets_frontend'))

    def test_only_a_colour_reaches_the_stylesheet(self):
        company = self.env.ref('base.main_company')
        company.primary_color = '#233D7E'
        company.primary_color = 'red; } body { display: none'
        attachment, asset = company._aiis_brand_records()
        self.assertFalse(attachment)
        self.assertFalse(asset)

    def test_other_companies_do_not_change_the_brand(self):
        company = self.env.ref('base.main_company')
        company.primary_color = '#233D7E'
        other = self.env['res.company'].create({'name': 'Second company', 'primary_color': '#ff0000'})
        other.primary_color = '#00ff00'
        self.assertIn(b'#233d7e', company._aiis_brand_records()[0].raw)
