-- Queries for wlwt_usl. Run live 2026-10-27 (lane CT2710-LINK); responses in zetaris_usl/logs/select/.
-- 1b: exploratory queries per raw source (2-3 each), then 1c: queries through the USL.

-- ---- 1b. Chat (Postgres) ----
SELECT count(*) AS messages, min(ts) AS first, max(ts) AS last FROM WLWT_CHAT.chat;                    -- expect 225
SELECT title AS channel, count(*) AS messages FROM WLWT_CHAT.chat GROUP BY title ORDER BY messages DESC;
SELECT id, ts, author, text FROM WLWT_CHAT.chat WHERE instr(text, 'billing-api') > 0 ORDER BY ts;

-- ---- 1b. Tickets (CSV) ----
SELECT count(*) AS tickets FROM wlwt_tickets.tickets;                                                   -- expect 127
SELECT author, count(*) AS tickets FROM wlwt_tickets.tickets GROUP BY author ORDER BY tickets DESC;
SELECT id, ts, author, title FROM wlwt_tickets.tickets WHERE instr(title, 'billing-api') > 0 ORDER BY ts;

-- ---- 1b. Docs (REST API) ----
SELECT count(*) AS docs FROM wlwt_docs.docs_raw LATERAL VIEW explode(docs) x AS d;                       -- expect 51
SELECT split(d.id, '-')[0] AS doc_kind, count(*) AS docs FROM wlwt_docs.docs_raw LATERAL VIEW explode(docs) x AS d GROUP BY 1;
SELECT d.id, d.ts, d.author, d.title FROM wlwt_docs.docs_raw LATERAL VIEW explode(docs) x AS d WHERE d.id LIKE 'ADR-%' ORDER BY d.ts;

-- ---- 1c. Through the USL ----
-- Q1: the USL is active and holds every source (expect chat 225, doc 51, ticket 127).
SELECT system, count(*) AS records FROM lightning.metastore.wlwt.wlwt_usl.record GROUP BY system ORDER BY system;

-- Q2: the Verifier's point read by id, one statement for any system.
SELECT record_id, system, ts, author, body FROM lightning.metastore.wlwt.wlwt_usl.record WHERE record_id = 'msg-0148';

-- Q3: who has touched billing-api, across chat, tickets and docs, newest first.
SELECT system, record_id, ts, author, title
FROM lightning.metastore.wlwt.wlwt_usl.record
WHERE instr(body, 'billing-api') > 0
ORDER BY ts DESC;

-- Q4: the absence check the Verifier runs, now one query over the USL instead of a UNION ALL of three.
SELECT count(*) AS hits FROM lightning.metastore.wlwt.wlwt_usl.record WHERE instr(body, 'payroll-export') > 0;   -- expect 0

-- ---- DQ results ----
SHOW DQ VALID RECORD quotable TABLE lightning.metastore.wlwt.wlwt_usl.record;
SHOW DQ INVALID RECORD quotable TABLE lightning.metastore.wlwt.wlwt_usl.record;
SHOW DQ ALL INVALID TABLE lightning.metastore.wlwt.wlwt_usl.record;
-- Live demo of the rule catching something: blank one chat message in Postgres (as the tamper
-- script already edits msg-0148), RUN DQ again, see 1 invalid record for `quotable`, then restore.
