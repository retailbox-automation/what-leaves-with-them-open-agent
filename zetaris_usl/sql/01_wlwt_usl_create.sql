-- =============================================================================
-- USL wlwt_usl: one semantic table of every record the two agents may cite,
-- over the three registered sources (Postgres chat, tickets CSV, docs REST API).
-- Run live 2026-10-27 on Zetaris 2.4.3.1 (lane CT2710-LINK); responses in zetaris_usl/logs/create/. Pattern and syntax:
-- zetaris/getting-started open_data/usl/sql/0{1,2}_*_create.sql and the SQL
-- companion section 8 (Zetaris 2.4.3.1). Send each statement on its own; send the
-- multi-table COMPILE as one statement (JDBC/REST), not through a splitting runner.
-- Prerequisite: zetaris_spike/register.py has registered WLWT_CHAT.chat,
-- wlwt_tickets.tickets and wlwt_docs.docs_raw (the same SELECTs as open_agent/sources.py).
-- =============================================================================

-- Step 0: namespaces (parent first, or the child fails with "parent namespace : metastore is not existing").
CREATE NAMESPACE IF NOT EXISTS lightning.metastore;
CREATE NAMESPACE IF NOT EXISTS lightning.metastore.wlwt;

-- Step 1: design (DDL). One table: a multi-table COMPILE (with a source_system table and a foreign key) is
-- refused by the SQL editor route ("Invalid DDL statement"; the companion guide says it needs JDBC), so the
-- system check is a DQ rule instead (id_matches_system below).
-- The quoted line is in column body: 'text' is refused as a USL column name ("Invalid DDL statement", checked 27.10).
COMPILE USL IF NOT EXISTS wlwt_usl DEPLOY NAMESPACE lightning.metastore.wlwt DDL
CREATE TABLE record (
    record_id varchar(32) NOT NULL PRIMARY KEY,
    system varchar(8) NOT NULL,
    ts varchar(32),
    author varchar(120),
    title varchar(400),
    body varchar(8000),
    refs varchar(2000),
    content_hash varchar(64)
);

UPDATE USL lightning.metastore.wlwt.wlwt_usl SET DESCRIPTION 'What Leaves With Them: every chat message, ticket and document the Investigator may cite and the Verifier re-reads by record_id.';

-- content_hash (2c mock): what we ask Zetaris to maintain natively, so an agent can ask
-- "has this record changed since I cited it?" without re-reading and comparing the text itself.

-- Step 2: activate.
-- COALESCE only satisfies the NOT NULL declaration (Zetaris checks nullability at activation); no source id is null.
ACTIVATE USL TABLE lightning.metastore.wlwt.wlwt_usl.record AS
SELECT COALESCE(record_id, '') AS record_id, system, ts, author, title, body, refs, content_hash FROM (
SELECT id AS record_id, 'chat' AS system, CAST(ts AS STRING) AS ts, author, title, text AS body, CAST(refs AS STRING) AS refs, sha2(text, 256) AS content_hash
FROM WLWT_CHAT.chat
UNION ALL
SELECT id, 'ticket', CAST(ts AS STRING), author, title, text, CAST(refs AS STRING), sha2(text, 256)
FROM wlwt_tickets.tickets
UNION ALL
SELECT d.id, 'doc', CAST(d.ts AS STRING), d.author, d.title, d.text, CAST(d.refs AS STRING), sha2(d.text, 256)
FROM wlwt_docs.docs_raw LATERAL VIEW explode(docs) x AS d
) u;

-- Step 3: custom data quality rules (boolean expression, no alias, no window functions).
-- A record the Verifier can check must have text to quote from and a parseable time.
REGISTER DQ quotable TABLE lightning.metastore.wlwt.wlwt_usl.record AS
body IS NOT NULL AND length(trim(body)) > 0 AND to_timestamp(ts) IS NOT NULL;

-- The id must look like its own system's ids (a misrouted record would be cited from the wrong source).
REGISTER DQ id_matches_system TABLE lightning.metastore.wlwt.wlwt_usl.record AS
(system = 'chat' AND record_id LIKE 'msg-%')
OR (system = 'ticket' AND record_id LIKE 'TCK-%')
OR (system = 'doc' AND (record_id LIKE 'DOC-%' OR record_id LIKE 'ADR-%' OR record_id LIKE 'RB-%' OR record_id LIKE 'PM-%' OR record_id LIKE 'ARCH-%'));

-- Step 4: run every rule (the primary key's and the two above) and list them.
LIST DQ USL lightning.metastore.wlwt.wlwt_usl;
RUN DQ TABLE lightning.metastore.wlwt.wlwt_usl.record;
-- Expect on the synthetic corpus: record 403 total (225 + 127 + 51), 403 valid, 0 invalid.

-- Teardown (only if the model must be rebuilt):
-- REMOVE USL wlwt_usl NAMESPACE lightning.metastore.wlwt;
-- DROP NAMESPACE lightning.metastore.wlwt;
