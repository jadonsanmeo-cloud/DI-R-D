CREATE TABLE IF NOT EXISTS response_runs (
    response_id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN (
        'preparing', 'awaiting_confirmation', 'revising', 'executing',
        'completed', 'failed', 'expired'
    )),
    current_revision INTEGER NOT NULL CHECK (current_revision > 0),
    confirmation_token_hash TEXT NOT NULL,
    request_payload JSONB NOT NULL,
    prepared_execution JSONB NOT NULL,
    intent_payload JSONB NOT NULL,
    user_id TEXT,
    session_id TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    started_execution_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS response_spec_revisions (
    response_id TEXT NOT NULL REFERENCES response_runs(response_id) ON DELETE CASCADE,
    revision INTEGER NOT NULL CHECK (revision > 0),
    spec_payload JSONB NOT NULL,
    source TEXT NOT NULL CHECK (source IN (
        'initial', 'structured_edit', 'feedback_revision',
        'structured_edit_and_feedback'
    )),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (response_id, revision)
);

CREATE TABLE IF NOT EXISTS response_decisions (
    decision_id BIGSERIAL PRIMARY KEY,
    response_id TEXT NOT NULL REFERENCES response_runs(response_id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('confirm', 'revise')),
    feedback TEXT,
    edited_spec JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS response_runs_status_idx ON response_runs(status);
CREATE INDEX IF NOT EXISTS response_runs_expires_at_idx ON response_runs(expires_at);
CREATE INDEX IF NOT EXISTS response_runs_session_status_idx ON response_runs(session_id, status);
