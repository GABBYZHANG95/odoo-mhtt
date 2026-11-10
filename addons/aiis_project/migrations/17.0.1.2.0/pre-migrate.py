"""The approval engine's status field is now aiis_approval_state (Odoo's own
expense reports already have an approval_state): keep the data."""


def migrate(cr, version):
    cr.execute("""
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'aiis_project' AND column_name = 'approval_state'
    """)
    if cr.fetchone():
        cr.execute('ALTER TABLE aiis_project RENAME COLUMN approval_state TO aiis_approval_state')
