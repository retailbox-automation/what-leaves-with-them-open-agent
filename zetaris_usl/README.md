# Zetaris Unified Semantic Layer (added 27 Oct 2026, after submission)

Added on 27 October 2026, after we submitted, against the "Build with Zetaris evaluation criteria" Zetaris published on HackOS on 26 October. Nothing here changes the agents or the submitted code: the Investigator and the Verifier still read the three sources through the per-system SQL in [open_agent/sources.py](../open_agent/sources.py). This folder is the USL built on the same live sources, the logs of every statement, and what we learned.

Run live on Zetaris 2.4.3.1 (local bundle), Mac Studio shared with other work, 27 Oct 2026 17:35 to 17:40 UTC.

## 1. The USL

| | |
|---|---|
| Use case and sources (1a) | the same as the project: who owns what after someone leaves, from three systems: team chat in Postgres (225 records), tickets in a CSV file (127), documents behind a REST API (51) |
| Exploratory queries (1b) | 3 per source, 9 in all: counts, a breakdown (chat by channel, tickets by author, docs by kind) and everything about billing-api ([sql/02_wlwt_usl_select.sql](sql/02_wlwt_usl_select.sql), logs `logs/select/01..09.json`) |
| USL and its DDL (1c) | `wlwt_usl` in `lightning.metastore.wlwt`, one table `record`: `record_id` (primary key), `system`, `ts`, `author`, `title`, `body`, `refs`, `content_hash` (SHA-256 of the text), activated with a `UNION ALL` over the three sources ([sql/01_wlwt_usl_create.sql](sql/01_wlwt_usl_create.sql), logs `logs/create/`) |
| Queries through the USL (1c) | records per system: chat 225, doc 51, ticket 127; the Verifier's point read by id (`msg-0148`, one row); everything that mentions billing-api in any system (28 rows); the absence check (`payroll-export`: 0) (`logs/select/10..13.json`) |
| Data quality (1c) | three rules: the primary key's, `quotable` (text to quote from and a parseable time) and `id_matches_system` (a chat id looks like a chat id, and so on). `RUN DQ`: 403 total, 403 valid, 0 invalid for each rule (`logs/create/09.json`) |
| A rule at work (1c) | we blanked one chat message in Postgres (`msg-0002`): `RUN DQ` reported 402 valid and 1 invalid for `quotable`, and `SHOW DQ INVALID RECORD` named `msg-0002`; after we put the text back, 0 invalid again ([dq_demo.py](dq_demo.py), `logs/dq-demo.json`) |

Two things the USL refused that we did not expect, both fixed in the SQL: a column named `text` ("Invalid DDL statement", so the quoted line is in `body`), and a primary key the activation query could not prove non-null (fixed with `COALESCE`, no source id is null). We also wanted a second table of systems with a foreign key; the SQL editor route refused the two-table model (the Zetaris companion guide says it needs JDBC, which we did not try), so the system check is a rule instead.

Rebuild: `ZETARIS_BUNDLE_ENV=<bundle>/.env python3 -m zetaris_usl.run_usl zetaris_usl/sql/01_wlwt_usl_create.sql zetaris_usl/logs/create` (one statement at a time, every response logged), then the same with `02_wlwt_usl_select.sql`, `python3 -m zetaris_usl.dq_demo` and `python3 -m zetaris_usl.measure`.

## 2a. Time: the USL against what we hand-coded

What we hand-coded in the build window, in [open_agent/sources.py](../open_agent/sources.py): one `SELECT` per system, a re-read that has to know which system a record came from (`by_id(channel, record_id)`), and an absence check that is a `UNION ALL` of three subqueries (`mentions(term)`). The re-read needs the system to pick the table, so every citation carries it. That is about 20 lines of source plumbing.

With the USL the same three jobs are one table: the re-read is `WHERE record_id = ...` with no system needed, the absence check is one query, and the checks we would otherwise write in Python (empty text, an id from the wrong system) run inside Zetaris as `RUN DQ`. The statements took under two minutes on 27 October, most of it spent on the two refusals above.

Measured side by side on the same machine, 15 runs each, alternating, live sources ([measure.py](measure.py), `logs/measure.json`):

| Read | Hand-coded SQL, median (p90) | Through the USL, median (p90) |
|---|---|---|
| point read of a chat message by id | 0.115 s (0.229) | 0.405 s (0.924) |
| point read of a document by id | 0.186 s (0.595) | 0.380 s (0.604) |
| absence check over all three systems | 0.218 s (0.623) | 0.303 s (0.896) |

So the USL saved code and gave us data quality checks we did not have, but on this setup it did not save query time: a point read through the USL is two to three times slower than the per-system query. Our reading is that the activation query is evaluated across all three sources before the filter applies, and the REST source is fetched again on every query (we saw that during the build). The machine was busy (load average 17 to 25), so treat the absolute numbers as rough; both columns ran under the same load.

## 2b. Where it would help us, and where it would not

We build agents that read clients' systems: a CRM, a ticket desk, a shared drive. The slow part is never the model. It is agreeing on what a "record" is across systems and proving that an agent's citation points at a real one. A USL table with a declared key is that agreement written down, and `RUN DQ` is a gate we can run before an agent reads anything. We would use it for that.

Where it would not help as it stands: our Verifier re-reads one record per citation, many times per answer, and on the numbers above that is slower through the USL. Materializing would make it fast but would break the Verifier's promise that it reads the source as it is now. So we would keep the agents' hot path on the per-system queries and use the USL for the record contract, the quality gate and the cross-system questions.

Bonus, a project that might benefit, with our forecast (a guess, not a measurement): a client with three to five systems where an agent answers "who owns X now" for a support team. Defining the record once in a USL would replace the per-system read code we wrote here for each system and the rules we would otherwise hand-write, which we estimate at one to two days of work saved on such a project.

## 2c. A requirement for the USL, written as a PM

**Problem.** An agent that cites a record needs to know later whether that record has changed since it was cited. Today every agent, in every project, re-reads the whole record and compares the text itself, and through the USL that re-read is the slow path.

**Requirement.** USL tables keep a content version per row: a hash of the selected columns and the time it last changed, readable in SQL (for example `ROW_VERSION(record)` and `WHERE CHANGED SINCE '<timestamp>'`), plus a data quality rule type "changed since snapshot" that lists the rows whose content moved after a given point. A point read by primary key should go to the one source that holds the key instead of evaluating the whole union.

**Why it matters.** It turns "is my evidence still true?" into one cheap query for any source Zetaris federates, and makes the USL the natural home for agent provenance.

**Mock.** `wlwt_usl.record.content_hash` is `sha2(text, 256)` computed at query time. In the live run, `msg-0148` read `2f0a8504…53b4` before the tamper edit our demo makes, `644048b5…b6d4` after it, and `2f0a8504…53b4` again once the text was put back (`logs/dq-demo.json`). A native version would also keep the time of the change, which our mock cannot.
