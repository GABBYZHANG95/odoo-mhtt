"""The approval engine's status field is now aiis_approval_state (Odoo's own
expense reports already have an approval_state): keep the data."""


def migrate(cr, version):
    for table in ('procurement_request', 'purchase_order'):
        cr.execute("""
            SELECT 1 FROM information_schema.columns
             WHERE table_name = %s AND column_name = 'approval_state'
        """, (table,))
        if cr.fetchone():
            cr.execute('ALTER TABLE "%s" RENAME COLUMN approval_state TO aiis_approval_state' % table)
