-- Discovery evidence stays separate from reviewed eligibility and writing patterns.
CREATE TABLE IF NOT EXISTS benefitlens.exploration_run (
 run_id text PRIMARY KEY, payload jsonb NOT NULL, updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS benefitlens.exploration_url (
 run_id text NOT NULL, url text NOT NULL, lane text NOT NULL, status text NOT NULL,
 payload jsonb NOT NULL, updated_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(run_id,url)
);
CREATE INDEX IF NOT EXISTS exploration_url_status ON benefitlens.exploration_url(run_id,status);
CREATE TABLE IF NOT EXISTS benefitlens.experience_discovery_case (
 case_key text PRIMARY KEY, program_id text NOT NULL, person_name text,
 application_round text, outcome_claim text NOT NULL, evidence_status text NOT NULL,
 source_url text NOT NULL, payload jsonb NOT NULL, first_seen_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS experience_discovery_program ON benefitlens.experience_discovery_case(program_id);
CREATE TABLE IF NOT EXISTS benefitlens.exploration_search_batch (
 batch_key text PRIMARY KEY, run_id text NOT NULL, wave integer NOT NULL,
 payload jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO platform.schema_migration(version) VALUES('021_exploration_frontier') ON CONFLICT DO NOTHING;
