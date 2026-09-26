"""SQL fragments for publication evidence of a rebuilt entity.

An entity can be tied to an article by a named mention or by an operator's identification
of an unnamed quote. Callers decide which columns they need, but all use this one CTE.
"""

PERSON_EVIDENCE_CTE = """
person_evidence AS (
    SELECT gm.group_id, a.id AS article_id, a.title, a.published_at, d.canonical_url,
           s.name AS source, m.start_offset, m.end_offset,
           substr(a.text, greatest(m.start_offset - :context, 0) + 1,
                  m.end_offset - greatest(m.start_offset - :context, 0) + :context) AS quote,
           greatest(m.start_offset - :context, 0) AS quote_start,
           'mention' AS kind,
           NULL::text AS resolution,
           NULL::text AS rf_name,
           NULL::date AS rf_birth_date,
           NULL::timestamptz AS decided_at
    FROM entity_group_mentions gm
    JOIN entity_mentions m ON m.id = gm.mention_id
    JOIN article_extraction_runs r ON r.id = m.extraction_run_id
    JOIN parsed_articles a ON a.id = r.article_id
    JOIN source_documents d ON d.id = a.document_id
    JOIN sources s ON s.id = d.source_id
    UNION ALL
    SELECT gum.group_id, a.id AS article_id, a.title, a.published_at, d.canonical_url,
           s.name AS source, u.start_offset, u.end_offset, u.quote,
           u.start_offset AS quote_start,
           'unnamed_resolution' AS kind,
           ir.resolution, ir.rf_name, ir.rf_birth_date, ir.decided_at
    FROM entity_group_unnamed_mentions gum
    JOIN unnamed_figurants u ON u.key = gum.figurant_key
    JOIN unnamed_identity_resolutions ir ON ir.figurant_key = gum.figurant_key
    JOIN parsed_articles a ON a.id = u.article_id
    JOIN source_documents d ON d.id = a.document_id
    JOIN sources s ON s.id = d.source_id
)
"""


def person_evidence_cte() -> str:
    return PERSON_EVIDENCE_CTE
