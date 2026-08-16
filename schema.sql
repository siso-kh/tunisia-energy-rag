-- ============================================================
-- GENERATED REFERENCE SNAPSHOT — do not edit by hand.
-- Produced with:  python -m alembic upgrade head --sql
-- The authoritative schema is the Alembic migrations in alembic/versions/
-- (generated for the production Postgres dialect).
-- ============================================================

BEGIN;

CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL, 
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

-- Running upgrade  -> 0001_initial

CREATE TABLE users (
    id UUID NOT NULL, 
    email VARCHAR(255), 
    password_hash VARCHAR(255), 
    display_name VARCHAR(100), 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL, 
    PRIMARY KEY (id)
);

CREATE UNIQUE INDEX ix_users_email ON users (email);

CREATE TABLE conversations (
    id UUID NOT NULL, 
    user_id UUID NOT NULL, 
    title VARCHAR(255), 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_conversations_user_id ON conversations (user_id);

CREATE TABLE messages (
    id UUID NOT NULL, 
    conversation_id UUID NOT NULL, 
    role VARCHAR(20) NOT NULL, 
    content TEXT NOT NULL, 
    sources JSONB, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(conversation_id) REFERENCES conversations (id) ON DELETE CASCADE
);

CREATE INDEX ix_messages_conversation_id ON messages (conversation_id);

CREATE TABLE settings (
    key VARCHAR(100) NOT NULL, 
    value TEXT NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL, 
    PRIMARY KEY (key)
);

CREATE TYPE utilitytype AS ENUM ('STEG', 'SONEDE', 'OTHER');

CREATE TYPE reportstatus AS ENUM ('PENDING', 'VERIFIED', 'RESOLVED');

CREATE TABLE outage_reports (
    id UUID NOT NULL, 
    user_id UUID, 
    utility utilitytype NOT NULL, 
    region VARCHAR(100) NOT NULL, 
    latitude FLOAT NOT NULL, 
    longitude FLOAT NOT NULL, 
    description TEXT, 
    status reportstatus NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_outage_reports_region ON outage_reports (region);

INSERT INTO alembic_version (version_num) VALUES ('0001_initial') RETURNING alembic_version.version_num;

COMMIT;

