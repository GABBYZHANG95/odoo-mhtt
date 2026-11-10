"""Phase 2: the contract stage now has steps - projects that were simply
"Contract" start at its first one (BOM check). (The R&D-head contract step
added here at the time was dropped in 17.0.1.8.0, #63.)"""


def migrate(cr, version):
    cr.execute("UPDATE aiis_project SET state = 'contract_bom' WHERE state = 'contract'")
