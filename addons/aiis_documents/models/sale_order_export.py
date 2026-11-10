import base64
import io

import xlsxwriter

from odoo import _, fields, models
from odoo.exceptions import UserError

BRAND_GREEN = '#233d7e'  # MHTT navy


class SaleOrderExport(models.TransientModel):
    """Holds a generated workbook just long enough to be downloaded; the
    transient vacuum cleans it up afterwards."""
    _name = 'aiis.sale.order.export'
    _description = 'Sales Order Excel Export'

    file = fields.Binary(readonly=True, attachment=False)
    filename = fields.Char(readonly=True)


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def action_export_xlsx(self):
        if not self:
            raise UserError(_("Select at least one Sales Order to export."))
        orders = self.sorted(lambda o: (o.date_order, o.id))
        if len(orders) == 1:
            filename = '%s.xlsx' % orders.name.replace('/', '_')
        else:
            filename = 'Sales Orders %s.xlsx' % fields.Date.context_today(self)
        export = self.env['aiis.sale.order.export'].create({
            'file': orders._build_export_xlsx(),
            'filename': filename,
        })
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/?model=aiis.sale.order.export&id=%s&field=file'
                   '&filename_field=filename&download=true' % export.id,
            'target': 'self',
        }

    def _build_export_xlsx(self):
        buf = io.BytesIO()
        wb = xlsxwriter.Workbook(buf, {'in_memory': True})
        fmt = {
            'head': wb.add_format({'bold': True, 'font_color': 'white', 'bg_color': BRAND_GREEN,
                                   'border': 1, 'text_wrap': True, 'valign': 'vcenter'}),
            'text': wb.add_format({'border': 1, 'valign': 'top'}),
            'wrap': wb.add_format({'border': 1, 'valign': 'top', 'text_wrap': True}),
            'date': wb.add_format({'border': 1, 'valign': 'top', 'num_format': 'yyyy-mm-dd'}),
            'num': wb.add_format({'border': 1, 'valign': 'top', 'num_format': '#,##0.00'}),
            'qty': wb.add_format({'border': 1, 'valign': 'top', 'num_format': '#,##0.###'}),
            'total_label': wb.add_format({'bold': True, 'top': 2}),
        }
        state_labels = dict(self._fields['state']._description_selection(self.env))
        invoice_labels = dict(self._fields['invoice_status']._description_selection(self.env))
        contract_labels = dict(self.env['customer.contract']._fields['state']._description_selection(self.env))

        def order_date(order):
            return fields.Datetime.context_timestamp(order, order.date_order).replace(tzinfo=None)

        def write_sheet(name, columns, rows):
            ws = wb.add_worksheet(name)
            for col, (title, width, _kind) in enumerate(columns):
                ws.write(0, col, title, fmt['head'])
                ws.set_column(col, col, width)
            ws.set_row(0, 30)
            for r, row in enumerate(rows, start=1):
                for col, value in enumerate(row):
                    kind = columns[col][2]
                    # `0 in (False, ...)` is True in Python - test identity so zeros are kept.
                    if value is None or value is False or value == '':
                        ws.write_blank(r, col, None, fmt['text'])
                    elif kind == 'date':
                        ws.write_datetime(r, col, value, fmt['date'])
                    else:
                        ws.write(r, col, value, fmt[kind])
            ws.freeze_panes(1, 1)
            ws.autofilter(0, 0, max(len(rows), 1), len(columns) - 1)
            return ws

        order_columns = [
            ('SO No.', 18, 'text'), ('Status', 13, 'text'), ('Order Date', 12, 'date'),
            ('Customer', 30, 'text'), ('Customer PO No.', 18, 'text'),
            ('Contract No.', 16, 'text'), ('Contract Status', 16, 'text'),
            ('Salesperson', 18, 'text'), ('Currency', 9, 'text'),
            ('Untaxed Amount', 15, 'num'), ('Tax', 13, 'num'), ('Total', 15, 'num'),
            ('Invoice Status', 16, 'text'), ('Purchase Requests', 20, 'wrap'),
            ('Purchase Orders', 20, 'wrap'),
        ]
        order_rows = [(
            o.name, state_labels.get(o.state), order_date(o),
            o.partner_id.display_name, o.client_order_ref,
            o.contract_id.name, contract_labels.get(o.contract_id.state),
            o.user_id.name, o.currency_id.name,
            o.amount_untaxed, o.amount_tax, o.amount_total,
            invoice_labels.get(o.invoice_status),
            ', '.join(o.procurement_request_ids.mapped('name')),
            ', '.join(o.order_line.purchase_line_ids.order_id.mapped('name')),
        ) for o in self]
        ws = write_sheet(_('Orders'), order_columns, order_rows)
        # Per-currency totals under the order list - mixing SAR and USD in
        # one sum would be meaningless.
        r = len(order_rows) + 2
        for currency in self.currency_id:
            cur_orders = self.filtered(lambda o: o.currency_id == currency)
            ws.write(r, 7, _('Total %s', currency.name), fmt['total_label'])
            ws.write(r, 8, currency.name, fmt['total_label'])
            for col, field in ((9, 'amount_untaxed'), (10, 'amount_tax'), (11, 'amount_total')):
                ws.write(r, col, sum(cur_orders.mapped(field)), fmt['num'])
            r += 1

        line_columns = [
            ('SO No.', 18, 'text'), ('Customer', 28, 'text'), ('Product', 28, 'text'),
            ('Description', 40, 'wrap'), ('Quantity', 10, 'qty'), ('Unit', 9, 'text'),
            ('Unit Price', 13, 'num'), ('Discount %', 10, 'num'), ('Taxes', 14, 'text'),
            ('Subtotal', 14, 'num'), ('Total', 14, 'num'), ('Currency', 9, 'text'),
            ('Delivered', 10, 'qty'), ('Invoiced', 10, 'qty'), ('Purchased', 10, 'qty'),
        ]
        line_rows = []
        for o in self:
            for line in o.order_line:
                if line.display_type:
                    # Section / note lines: keep them so the sheet reads like the order.
                    line_rows.append((o.name, o.partner_id.display_name, None, line.name) + (None,) * 11)
                    continue
                line_rows.append((
                    o.name, o.partner_id.display_name, line.product_id.display_name, line.name,
                    line.product_uom_qty, line.product_uom.name, line.price_unit, line.discount,
                    ', '.join(line.tax_id.mapped('name')), line.price_subtotal, line.price_total,
                    o.currency_id.name, line.qty_delivered, line.qty_invoiced,
                    line.purchase_covered_qty,
                ))
        write_sheet(_('Lines'), line_columns, line_rows)

        wb.close()
        return base64.b64encode(buf.getvalue())
