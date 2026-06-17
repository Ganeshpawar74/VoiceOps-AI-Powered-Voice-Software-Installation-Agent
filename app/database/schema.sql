-- VoiceOps PostgreSQL schema
-- Mounted by docker-compose into postgres container at startup.
-- Referenced in docker-compose.yml:
--   ./app/database/schema.sql:/docker-entrypoint-initdb.d/01-schema.sql
-- SQLAlchemy also creates this table via Base.metadata.create_all()
-- so this file is a safety net / explicit schema documentation.

CREATE TABLE IF NOT EXISTS tasks (
    id           VARCHAR(36)  PRIMARY KEY,
    user_id      VARCHAR(64)  NOT NULL,
    session_id   VARCHAR(64)  NOT NULL,
    query        TEXT         NOT NULL,
    status       VARCHAR(32)  NOT NULL DEFAULT 'pending',
    progress     INTEGER               DEFAULT 0,
    result_json  TEXT,
    error        TEXT,
    created_at   TIMESTAMP    NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMP    NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tasks_user_id   ON tasks (user_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status    ON tasks (status);
CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks (created_at DESC);