-- Ramco Payce pilot: one database per region. Structured data, vectors and
-- the semantic cache all live here. Three systems means three hops and three
-- bills; one database means one hop, and the peak-week target is a first
-- reply in 3 seconds.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ---------------------------------------------------------------- lookup
CREATE TABLE IF NOT EXISTS customers (
    customer_id      text PRIMARY KEY,
    name             text NOT NULL,
    tier             text NOT NULL,
    region           text NOT NULL,
    monthly_fee_usd  numeric NOT NULL,
    employees_paid   integer NOT NULL
);

-- ---------------------------------------------------------------- tickets
-- Only masked text is stored. The raw body never reaches this database: the
-- PII gate runs before the loader, not after it.
CREATE TABLE IF NOT EXISTS tickets (
    ticket_id         text PRIMARY KEY,
    channel           text NOT NULL,
    customer_id       text REFERENCES customers(customer_id),
    region            text,
    tier              text,
    subject_masked    text,
    body_masked       text,
    raw_category      text,
    canonical_category text,
    priority          text,
    status            text,
    received_at       timestamptz,
    first_response_at timestamptz,
    resolved_at       timestamptz,
    queue_intake      text,
    queue_final       text,
    case_id           text,
    pii_counts        jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS tickets_customer_idx ON tickets(customer_id);
CREATE INDEX IF NOT EXISTS tickets_received_idx ON tickets(received_at);
CREATE INDEX IF NOT EXISTS tickets_case_idx     ON tickets(case_id);

-- ------------------------------------------------------------ KB articles
-- Metadata only, because that is all the export contains: every article's
-- body lives in Confluence and `excerpt` is a pointer to it. Stored anyway so
-- the answer step can say "the article exists and is N days past review"
-- instead of inventing its contents.
CREATE TABLE IF NOT EXISTS kb_articles (
    id             text PRIMARY KEY,
    title          text NOT NULL,
    region         text,
    topic          text,
    last_reviewed  date,
    owner          text,
    status         text,
    views_90d      integer,
    helpful_pct    integer,
    excerpt        text
);

-- -------------------------------------------------------- statutory rules
-- effective_from is a COLUMN, not an embedding. A June payslip question must
-- be answered with the June rule; the EPF ceiling moved from INR 15,000 to
-- 25,000 on 2026-08-01 and the last vendor pilot got exactly this wrong.
-- effective_to is derived at load time from the next rule on the same topic.
CREATE TABLE IF NOT EXISTS statutory_rules (
    rule_id        text PRIMARY KEY,
    region         text NOT NULL,
    topic          text NOT NULL,
    title          text NOT NULL,
    effective_from date NOT NULL,
    effective_to   date,
    old_value      text,
    new_value      text,
    source         text,
    status         text
);
CREATE INDEX IF NOT EXISTS rules_topic_idx ON statutory_rules(topic, effective_from);

-- ------------------------------------------------------------- doc chunks
-- Every chunking strategy writes into the same table under its own
-- `strategy` label, so the bake-off compares them on identical queries
-- without rebuilding the database between runs.
CREATE TABLE IF NOT EXISTS doc_chunks (
    chunk_id       bigserial PRIMARY KEY,
    strategy       text NOT NULL,
    source_type    text NOT NULL,      -- process | statutory | kb
    source_id      text NOT NULL,
    heading        text,
    text           text NOT NULL,
    region         text NOT NULL DEFAULT 'ALL',
    effective_from date,
    effective_to   date,
    token_estimate integer,
    embedding      vector(512),
    tsv            tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED
);
CREATE INDEX IF NOT EXISTS chunks_strategy_idx ON doc_chunks(strategy);
CREATE INDEX IF NOT EXISTS chunks_tsv_idx      ON doc_chunks USING gin(tsv);
CREATE INDEX IF NOT EXISTS chunks_dates_idx    ON doc_chunks(effective_from, effective_to);
-- Cosine HNSW. m/ef_construction left at defaults: the corpus is ~60 chunks
-- per strategy and tuning them here would be theatre.
CREATE INDEX IF NOT EXISTS chunks_vec_idx
    ON doc_chunks USING hnsw (embedding vector_cosine_ops);

-- --------------------------------------------------------- semantic cache
-- Same database, as the study note argues. A hit returns the stored answer:
-- no model call, no cost. Region is part of the key so an answer never
-- crosses a regional boundary through the cache.
CREATE TABLE IF NOT EXISTS answer_cache (
    cache_id     bigserial PRIMARY KEY,
    region       text NOT NULL,
    question     text NOT NULL,
    embedding    vector(512) NOT NULL,
    answer       text NOT NULL,
    citations    jsonb NOT NULL DEFAULT '[]'::jsonb,
    as_of        date,
    -- The window during which this cached answer is TRUE, taken from the
    -- effective dates of the passages it was drafted from. Without it the
    -- cache quietly undoes the effective-dating: a September question
    -- matched a June question at 0.958 similarity and was served June's
    -- answer, which is the precise failure the dated policy store exists to
    -- prevent. Region was part of the key and time was not.
    valid_from   date,
    valid_to     date,
    created_at   timestamptz NOT NULL DEFAULT now(),
    hits         integer NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS cache_vec_idx
    ON answer_cache USING hnsw (embedding vector_cosine_ops);

-- ------------------------------------------------------------- audit log
-- One row per box per ticket. This is what lets the pilot prove the PII gate
-- ran, prove which document an answer came from, and prove a human approved
-- the money cases — rather than asserting it in a policy document.
CREATE TABLE IF NOT EXISTS audit_log (
    audit_id   bigserial PRIMARY KEY,
    ticket_id  text,
    stage      text NOT NULL,
    detail     jsonb NOT NULL DEFAULT '{}'::jsonb,
    at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS audit_ticket_idx ON audit_log(ticket_id, at);
