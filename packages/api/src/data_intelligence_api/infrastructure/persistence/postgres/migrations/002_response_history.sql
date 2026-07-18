ALTER TABLE response_runs
    ADD COLUMN IF NOT EXISTS output_text TEXT;

ALTER TABLE response_runs
    ADD COLUMN IF NOT EXISTS evidence JSONB;

ALTER TABLE response_runs
    ADD COLUMN IF NOT EXISTS response_metadata JSONB;

CREATE INDEX IF NOT EXISTS response_runs_session_updated_idx
    ON response_runs(session_id, updated_at DESC);
