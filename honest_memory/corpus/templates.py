"""Static text material for the corpus generator.

Everything here is fictional. Names come from generic first/last name lists;
any resemblance to real people or companies is coincidental.
"""

COMPANY = "Quillfield Billing"

FIRST_NAMES = [
    "Mara", "Teo", "Imani", "Lars", "Priya", "Jonas", "Noor", "Felix", "Anika", "Mateo",
    "Sana", "Oskar", "Leona", "Ravi", "Tessa", "Dmitri", "Ines", "Kofi", "Yuki", "Hana",
    "Emil", "Zara", "Nico", "Aiko", "Luca", "Selma", "Arjun", "Freya", "Omar", "Greta",
    "Pavel", "Lina", "Tomas", "Esme", "Idris", "Mina", "Viktor", "Rosa", "Ansel", "Dara",
]

LAST_NAMES = [
    "Hartwell", "Lindqvist", "Okonkwo", "Vance", "Moreau", "Castellan", "Brandt", "Nakamura",
    "Alvarez", "Kowalski", "Fenwick", "Haddad", "Ostrander", "Petrov", "Sandoval", "Thorne",
    "Varga", "Whitlock", "Yilmaz", "Zimmer", "Abara", "Bellamy", "Calloway", "Dunmore",
    "Eriksen", "Farrow", "Galloway", "Hollis", "Iverson", "Jarrett", "Kessler", "Lowell",
    "Marsh", "Novak", "Olsen", "Prescott", "Quinn", "Rademacher", "Stroud", "Tanaka",
]

# team -> roles; the first role of each team is its lead
TEAMS = {
    "Payments": ["Payments Lead", "Backend Engineer", "Backend Engineer", "Data Engineer"],
    "Platform": ["Platform Lead", "Site Reliability Engineer", "Backend Engineer", "Security Engineer"],
    "Support": ["Support Lead", "Support Specialist", "Support Specialist"],
    "Growth": ["Growth Lead", "Product Analyst", "Marketing Engineer"],
}

TEAM_CHANNEL = {
    "Payments": "#payments",
    "Platform": "#platform",
    "Support": "#support",
    "Growth": "#growth",
}

# name -> purpose, owner_team (initial owner comes from it), pool (teams whose people
# do the day-to-day work on it), channel, components (what work items talk about)
SYSTEMS = {
    "billing-api": {
        "purpose": "public API for plans, subscriptions and invoices",
        "owner_team": "Payments", "pool": ("Payments", "Platform"), "channel": "#payments",
        "components": ["proration rounding", "plan upgrade endpoint", "idempotency key lookup",
                       "invoice list pagination", "coupon validation", "currency conversion"],
    },
    "invoice-renderer": {
        "purpose": "turns invoices into PDFs and emails",
        "owner_team": "Payments", "pool": ("Payments", "Platform"), "channel": "#payments",
        "components": ["line item layout", "PDF font embedding", "tax summary block",
                       "logo scaling", "page break logic", "locale formatting"],
    },
    "ledger-db": {
        "purpose": "primary database for ledger entries",
        "owner_team": "Payments", "pool": ("Payments", "Platform"), "channel": "#payments",
        "components": ["entry partitioning", "reversal entry index", "month-end close query",
                       "backup verification", "connection pool limits", "balance snapshot job"],
    },
    "auth": {
        "purpose": "login, sessions and API keys",
        "owner_team": "Platform", "pool": ("Platform", "Payments"), "channel": "#platform",
        "components": ["session expiry", "API key rotation", "password reset flow",
                       "SSO callback", "rate limit on login", "token signing"],
    },
    "notifications": {
        "purpose": "email and in-app messages to customers",
        "owner_team": "Platform", "pool": ("Platform", "Growth"), "channel": "#platform",
        "components": ["send queue", "bounce handling", "template variables",
                       "unsubscribe links", "batch sender", "delivery receipts"],
    },
    "webhooks": {
        "purpose": "outgoing event delivery to customer endpoints",
        "owner_team": "Platform", "pool": ("Platform", "Payments"), "channel": "#platform",
        "components": ["retry backoff", "payload signing", "event ordering",
                       "dead letter queue", "endpoint health check", "replay tool"],
    },
    "reporting": {
        "purpose": "scheduled reports and dashboards",
        "owner_team": "Growth", "pool": ("Growth", "Payments"), "channel": "#growth",
        "components": ["cohort query", "CSV export", "timezone handling",
                       "dashboard filters", "scheduled email report", "revenue rollup"],
    },
    "admin-console": {
        "purpose": "internal tool for support and operations",
        "owner_team": "Support", "pool": ("Support", "Platform"), "channel": "#support",
        "components": ["customer search", "refund screen", "audit trail view",
                       "role permissions", "impersonation banner", "bulk actions"],
    },
    "status-page": {
        "purpose": "public incident and uptime page",
        "owner_team": "Support", "pool": ("Support", "Platform"), "channel": "#support",
        "components": ["incident banner", "uptime widget", "subscriber emails",
                       "maintenance schedule", "component grouping", "history view"],
    },
}

# (name, goal, systems, lead team, target week)
PROJECTS = [
    ("Invoice Reliability", "make invoice creation and delivery boringly dependable",
     ("billing-api", "invoice-renderer", "notifications"), "Payments", 7),
    ("Ledger Hardening", "protect ledger integrity and speed up month-end close",
     ("ledger-db", "reporting"), "Payments", 8),
    ("Self-Serve Onboarding", "let small businesses sign up and send a first invoice alone",
     ("auth", "billing-api"), "Growth", 6),
    ("Webhook v2", "ordered, signed and replayable webhook delivery",
     ("webhooks", "auth"), "Platform", 8),
    ("Support Console Refresh", "faster customer lookup and safer refunds",
     ("admin-console", "status-page"), "Support", 5),
]

# (number, title, draft week, accepted week, author team, context, decision, consequences)
# ADR-0009 (ownership transfer) is written by the generator because it names people.
ADRS = [
    (1, "Idempotency keys on billing-api writes", 1, 1, "Payments",
     "Client retries created duplicate charges in the sandbox.",
     "Every write endpoint of billing-api requires an Idempotency-Key header; keys are stored for 24 hours.",
     "Clients must send a key. Replays return the original response."),
    (2, "Append-only ledger entries", 1, 1, "Payments",
     "Edits to ledger rows made audits hard to follow.",
     "ledger-db stores entries as append-only rows; corrections are new reversing entries.",
     "Reports must net entries. Storage grows faster."),
    (3, "Signed webhooks with retry backoff", 1, 2, "Platform",
     "Customers could not verify who sent a webhook.",
     "webhooks signs payloads with a per-tenant secret and retries with exponential backoff for up to 24 hours.",
     "Customers must verify signatures. Retries can reorder events."),
    (4, "Render invoices synchronously", 1, 2, "Payments",
     "Invoice PDFs are small and rendering is fast in tests.",
     "billing-api renders the invoice PDF inside the request that creates the invoice.",
     "Request latency depends on rendering. One code path to maintain."),
    (5, "SSO required for admin-console", 2, 3, "Platform",
     "Shared admin passwords were still in use.",
     "admin-console accepts only SSO logins through auth; local passwords are removed.",
     "Support staff need SSO accounts before the cutover."),
    (6, "Status page updates come from on-call", 2, 3, "Support",
     "Conflicting messages appeared on status-page during an incident.",
     "Only the on-call engineer or the support lead posts to status-page during an incident.",
     "Fewer voices, slower first message."),
    (7, "Per-tenant rate limits in notifications", 3, 4, "Platform",
     "One tenant's bulk send delayed everyone's email.",
     "notifications applies per-tenant send limits with a shared burst pool.",
     "Large tenants may see queued sends."),
    (8, "Reporting reads from a replica", 3, 5, "Growth",
     "Heavy reports slowed down writes on the primary database.",
     "reporting queries a read replica of ledger-db; reports may lag by up to 5 minutes.",
     "Dashboards show a freshness timestamp."),
    (10, "Audit logs kept for 400 days", 3, 5, "Platform",
     "Retention differed between services.",
     "auth and admin-console keep audit logs for 400 days, then archive them.",
     "Storage cost rises slightly."),
    (11, "Render invoices asynchronously through invoice-renderer", 3, 4, "Payments",
     "Synchronous rendering caused request timeouts for invoices with many line items.",
     "billing-api enqueues a render job and invoice-renderer produces the PDF; "
     "the API returns the invoice at once with a pending PDF link.",
     "PDF links can be pending for a few seconds."),
    (12, "Schema freeze on ledger-db during month-end close", 6, 7, "Payments",
     "A migration during the close slowed ledger entries down.",
     "No schema changes on ledger-db from the 28th to the 3rd of each month.",
     "Migrations are planned around the close."),
]

# (system, system it needs to work): the week-1 architecture overview. The longest
# upstream chain is four hops: status-page -> notifications -> invoice-renderer
# -> billing-api -> ledger-db (or auth).
DEPENDENCIES = [
    ("notifications", "invoice-renderer"),
    ("invoice-renderer", "billing-api"),
    ("billing-api", "ledger-db"),
    ("webhooks", "billing-api"),
    ("admin-console", "billing-api"),
    ("billing-api", "auth"),
    ("reporting", "ledger-db"),
    ("status-page", "notifications"),
]

# A decision in week 5 that adds a dependency missing from the overview: reporting -> billing-api.
# (number, title, draft week, accepted week, author team, context, decision, consequences)
DEPENDENCY_ADR = (
    13, "Revenue rollups read invoice state through billing-api", 4, 5, "Growth",
    "Revenue rollups need invoice states, and totals built from ledger entries alone differed from the invoice list.",
    "reporting depends on billing-api: the revenue rollup calls the invoice list endpoint of billing-api "
    "in addition to reading ledger-db.",
    "billing-api rate limits apply to reporting jobs. Reporting must handle API errors.",
)

# (week, system, title, impact, root cause, actions, author team or None = system owner)
POSTMORTEMS = [
    (2, "webhooks", "Delayed webhook deliveries",
     "About 2% of webhook deliveries arrived more than 30 minutes late for three hours.",
     "A retry queue shared a worker pool with a bulk replay job.",
     ["Separate the retry and replay worker pools.", "Alert when the oldest queued delivery passes 10 minutes."]),
    (3, "reporting", "Dashboard totals off by one day",
     "Scheduled reports showed totals shifted by one day for tenants outside UTC.",
     "A report window used server time instead of tenant time.",
     ["Store the tenant timezone with every report definition.", "Add a timezone test to the report suite."]),
    (4, "invoice-renderer", "Invoice rendering timeouts",
     "Invoices with more than 200 line items timed out for 90 minutes.",
     "Rendering ran inside the billing-api request and exceeded the gateway timeout.",
     ["Move rendering to a queue.", "Add a pending-PDF state to the invoice page."]),
    (5, "notifications", "Duplicate invoice emails",
     "Roughly 600 customers received each invoice email twice.",
     "A deploy restarted the sender while a batch was half sent, and the batch was replayed.",
     ["Mark each message as sent before the next one starts.", "Pause batch sends during deploys."]),
    (6, "auth", "Login loop after certificate rotation",
     "Some users could not log in for 40 minutes after a signing certificate was rotated.",
     "Old and new certificates were not served together during the switch.",
     ["Serve both certificates for 24 hours during rotation.", "Add a login check to the rotation runbook."]),
    (8, "ledger-db", "Slow month-end close queries",
     "The month-end close took four hours instead of one.",
     "A balance snapshot job competed with close queries for connections.",
     ["Run the snapshot job outside the close window.", "Raise the connection pool limit for close queries."]),
]

CUSTOMER_SYMPTOMS = [
    "a payment link that expires too early",
    "a missing line item on an invoice PDF",
    "a login loop on the customer portal",
    "webhook deliveries that arrive late",
    "tax rounded the wrong way on a credit note",
    "a status banner that never clears",
    "a CSV export that drops the last row",
    "a refund that shows as pending for days",
    "an invoice dated in the wrong month",
    "a receipt sent to the old billing contact",
    "a coupon applied twice",
    "duplicate invoice emails",
]
PLANS = ["Basic", "Pro", "Business"]
CUSTOMER_FOLLOWUPS = [
    "They attached a screenshot.",
    "They asked for an update by the end of the week.",
    "I could reproduce it once on a test account.",
    "Waiting for them to confirm when it started.",
    "Tagging for triage.",
]

TICKET_TITLES = [
    "Fix {c} in {s}",
    "Investigate {c} regression in {s}",
    "Tidy up {c} in {s}",
    "Add tests for {c} in {s}",
    "Harden {c} in {s}",
    "Document {c} for {s}",
]
TICKET_OPENINGS = [
    "Picked this up from the backlog.",
    "Following up on last week's discussion.",
    "Raised during code review.",
    "Noticed this while on call.",
]
CAUSES = [
    "a missing null check", "a stale cache entry", "an off-by-one in the date window",
    "a retry that fired twice", "a config value that differed between environments",
    "a slow query on a large tenant", "a timezone assumption",
]
ACTIONS = [
    "I changed the handler and added a regression test.",
    "I patched it and verified against staging data.",
    "I rewrote the check and ran the full suite.",
    "I shipped a fix behind a flag and watched the dashboards.",
    "I split the change into two small pull requests.",
]
CLOSINGS = [
    "Ready for review.", "Deployed to staging.", "Rolled out to all tenants.",
    "Will monitor for a few days.", "Closing once the next release goes out.",
]
CHAT_WORK = [
    "Pushed a fix for {c} in {s}; the cause was {cause}.",
    "Finished the {c} cleanup in {s}, pull request is up.",
    "Deployed the {c} change in {s} to staging, looks good.",
    "Wrapped up tests for {c} in {s}, merging after lunch.",
    "Rolled out the {c} fix in {s} to all tenants.",
]
CLOSURE_PINGS = [
    "Closed {t}, it goes out with the next release.",
    "{t} is done and waiting for review.",
    "Marked {t} as resolved.",
]
CHITCHAT = [
    "Standup moves to {time} UTC today.",
    "Does anyone have the link to the onboarding checklist?",
    "Lunch order closes in ten minutes.",
    "Thanks {name} for the quick review.",
    "I'm out on {day}, back the day after.",
    "Reminder: timesheets are due {day}.",
    "Happy {day}, everyone!",
    "The coffee machine on the second floor is fixed.",
    "Can someone cover the support queue for an hour at {time} UTC?",
    "Great work this week, {name}.",
    "Is the retro still on for {day}?",
    "I pushed the slide deck to the shared drive.",
    "Fire drill at {time} UTC, please ignore the alarm.",
    "New laptops arrive {day}; ping me if yours is on the list.",
    "Quick poll: should we move the weekly sync to {day}?",
    "Welcome back {name}!",
    "Kudos to {name} for staying late on the release.",
    "VPN is flaky for me this morning, anyone else?",
    "Reminder to update your out-of-office calendar.",
    "I'll be online a bit later than usual today.",
]
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
TIMES = ["13:30", "14:00", "15:30", "16:00", "17:30"]

MEETING_ITEMS = [
    "Reviewed the open ticket queue and closed stale items.",
    "Agreed to keep deploys off Friday evenings.",
    "Discussed hiring plans for the next quarter.",
    "Walked through the on-call rota for the next two weeks.",
    "Reviewed customer feedback from the last release.",
    "Agreed to tidy the shared documentation folder.",
    "Planned the next release window.",
    "Talked through the budget for tooling.",
    "Reviewed the support response-time numbers.",
    "Agreed on a weekly demo slot.",
]
RETRO_GOOD = ["Faster reviews", "Clear incident updates", "Less weekend work", "Good demos"]
RETRO_BAD = ["Too many meetings", "Unclear priorities", "Slow staging environment", "Flaky tests"]

STREETS = ["Marlowe Court", "Larkspur Avenue", "Quillon Street", "Harrow Lane", "Fenmore Road"]

# ---- the departure scene (generator 0.3.0) ------------------------------------------------------------
# One engineer resigns. What leaves with them is built so that a join answers only part of it: two systems
# nobody else touches, two decisions only they wrote, one decision they share, and work that links two teams
# that otherwise never meet. Everything is fictional, including the customer.

CUSTOMER = "Fernhill Outfitters"

# name -> kind, purpose, what it needs (DEPENDS_ON, or None), the document kind that declares it, work components
SCENE_SYSTEMS = {
    "fernhill-sso": {
        "kind": "integration", "kind_text": "customer integration",
        "purpose": f"single sign-on for the customer {CUSTOMER}", "depends_on": "auth", "channel": "#platform",
        "components": ["SAML assertion mapping", "certificate rollover", "user provisioning", "login redirect"],
    },
    "oncall-runbook": {
        "kind": "runbook", "kind_text": "on-call runbook",
        "purpose": "the on-call runbook: paging, triage and incident handover", "depends_on": None, "channel": "#platform",
        "components": ["paging policy", "severity table", "escalation steps", "handover checklist"],
    },
}

INTEGRATION_BODY = (f"{CUSTOMER} signs in through their own identity provider; fernhill-sso maps their users to "
                    f"{COMPANY} accounts.\nThe certificate rollover steps for this integration are written down here "
                    "and nowhere else.")
RUNBOOK_BODY = ("Paging: page the on-call engineer through the paging tool; a second page after 10 minutes goes to "
                "the team lead.\nTriage: pick the severity from the severity table, then open an incident channel.\n"
                "Handover: the outgoing on-call writes a handover note before the shift ends.")

# (number, title, draft week, accepted week, system it constrains, context, decision, consequences, shared)
# shared=False: the leaver is the only author. shared=True: the owner of the constrained system writes it and the
# leaver co-authors it (the control: a decision with a second author is not single-source).
SCENE_ADRS = [
    (14, "Encrypt ledger-db backups with rotating keys", 2, 3, "ledger-db",
     "A backup copy of ledger-db could be read by anyone with access to the storage bucket.",
     "Every ledger-db backup is encrypted with a key that rotates every 90 days; a restore needs the current key "
     "from the security on-call.",
     "Restores take a few minutes longer. Losing the key history makes old backups unreadable.", False),
    (16, "Rotate auth signing keys every 30 days", 3, 4, "auth",
     "Signing keys for auth had not been rotated since launch.",
     "auth rotates its token signing keys every 30 days; the security on-call runs the rotation.",
     "Clients must fetch keys from the published key set.", True),
    (15, "Mask customer names in reporting exports", 4, 5, "reporting",
     "CSV exports from reporting carried customer names to people who only needed totals.",
     "reporting masks customer names in every CSV export unless the requester holds the export role.",
     "Analysts ask for the export role when they need names.", False),
]

# Work the leaver does on systems outside their own team, plus the two systems only they touch.
SCENE_WORK = (("fernhill-sso", 4), ("oncall-runbook", 3), ("reporting", 2), ("admin-console", 2))
SCENE_WORK_TICKETS = 7

HANDOVER_NOTE = "Writing handover notes for fernhill-sso and oncall-runbook this week."

# The handoff session between the resignation and the last day: the leaver answers some of the handoff questions in
# writing, the named successor confirms what they now hold. Each line repeats a fact the graph already has, so the gate
# CONFIRMs it with one more source; the on-call runbook and any unwritten customer promise stay unanswered on purpose.
HANDOFF_ANSWERS = {
    "ledger-db": "the backup key rotates every 90 days and lives with the security on-call; restoring an older backup "
                 "needs the key that was current when it was taken",
    "reporting": "masking runs in the CSV export step, and the Growth lead grants the export role",
    "fernhill-sso": "the SAML certificate for the customer rolls over once a year, two weeks before it expires, "
                    "following the steps in {decl}",
}
HANDOFF_CONFIRMS = {
    "ledger-db": "confirmed; I hold the key rotation schedule from now on",
    "reporting": "confirmed; I review export role requests from now on",
}
