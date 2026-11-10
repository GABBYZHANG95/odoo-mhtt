def migrate(cr, version):
    """#66: the customer's last decision on the quotation, from the chatter"""
    cr.execute("""
        WITH decisions AS (
            SELECT DISTINCT ON (m.res_id) m.res_id, m.date,
                   CASE WHEN m.body LIKE '%%Customer accepted%%' THEN 'accepted'
                        WHEN m.body LIKE '%%Customer rejected the quotation - paused%%' THEN 'rejected_paused'
                        ELSE 'rejected_closed' END AS decision
            FROM mail_message m
            WHERE m.model = 'aiis.project' AND m.message_type IN ('comment', 'notification')
              AND (m.body LIKE '%%Customer accepted%%' OR m.body LIKE '%%Customer rejected the quotation%%')
            ORDER BY m.res_id, m.date DESC
        )
        UPDATE aiis_project p SET customer_decision = d.decision, customer_decision_date = d.date::date
        FROM decisions d
        WHERE d.res_id = p.id AND p.customer_decision IS NULL
    """)
