def migrate(cr, version):
    # #65: the expenses of a trip claim carry the link to the travel request
    cr.execute("""
        UPDATE hr_expense e SET aiis_travel_request_id = s.aiis_travel_request_id
        FROM hr_expense_sheet s
        WHERE e.sheet_id = s.id AND s.aiis_travel_request_id IS NOT NULL
          AND e.aiis_travel_request_id IS NULL
    """)
