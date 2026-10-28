BEGIN;

CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL, 
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);


CREATE TABLE accounts (
    id SERIAL NOT NULL, 
    key VARCHAR(32) NOT NULL, 
    name VARCHAR(100) NOT NULL, 
    data_type VARCHAR(20) NOT NULL, 
    color VARCHAR(16), 
    status VARCHAR(20) DEFAULT '작성중' NOT NULL, 
    memo TEXT, 
    PRIMARY KEY (id), 
    UNIQUE (key)
);

CREATE TABLE account_steps (
    id SERIAL NOT NULL, 
    account_id SMALLINT NOT NULL, 
    step_order SMALLINT NOT NULL, 
    tag VARCHAR(20) NOT NULL, 
    title VARCHAR(100) NOT NULL, 
    PRIMARY KEY (id), 
    UNIQUE (account_id, step_order), 
    FOREIGN KEY(account_id) REFERENCES accounts (id)
);

CREATE TABLE account_progress (
    id SERIAL NOT NULL, 
    account_id SMALLINT NOT NULL, 
    current_step_order SMALLINT DEFAULT '0' NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(account_id) REFERENCES accounts (id), 
    FOREIGN KEY(account_id, current_step_order) REFERENCES account_steps (account_id, step_order), 
    UNIQUE (account_id)
);

CREATE TABLE overseas_labs (
    id SERIAL NOT NULL, 
    pk VARCHAR(20) NOT NULL, 
    lab_code VARCHAR(10) NOT NULL, 
    part_code VARCHAR(10), 
    name VARCHAR(50) NOT NULL, 
    country VARCHAR(20) NOT NULL, 
    PRIMARY KEY (id), 
    UNIQUE (pk)
);

CREATE TABLE overseas_cost_entries (
    id SERIAL NOT NULL, 
    lab_id INTEGER NOT NULL, 
    version VARCHAR(10) NOT NULL, 
    category_key VARCHAR(40) NOT NULL, 
    month SMALLINT NOT NULL, 
    amount NUMERIC(14, 2) DEFAULT '0' NOT NULL, 
    extra JSONB, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    UNIQUE (lab_id, version, category_key, month), 
    FOREIGN KEY(lab_id) REFERENCES overseas_labs (id)
);

CREATE TABLE overseas_uploads (
    id SERIAL NOT NULL, 
    lab_id INTEGER NOT NULL, 
    version VARCHAR(10) NOT NULL, 
    filename VARCHAR(255) NOT NULL, 
    uploaded_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(lab_id) REFERENCES overseas_labs (id)
);

CREATE TABLE fx_rates (
    id SERIAL NOT NULL, 
    version VARCHAR(10) NOT NULL, 
    currency VARCHAR(6) NOT NULL, 
    rate_type VARCHAR(10) NOT NULL, 
    month SMALLINT NOT NULL, 
    value NUMERIC(12, 4) NOT NULL, 
    PRIMARY KEY (id), 
    UNIQUE (version, currency, rate_type, month)
);

CREATE TABLE overseas_notes (
    id SERIAL NOT NULL, 
    base_version VARCHAR(10) NOT NULL, 
    period VARCHAR(4) NOT NULL, 
    lab_scope VARCHAR(20) NOT NULL, 
    category_key VARCHAR(40) NOT NULL, 
    note_text TEXT NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    UNIQUE (base_version, period, lab_scope, category_key)
);

INSERT INTO alembic_version (version_num) VALUES ('1608bd61f4a7') RETURNING alembic_version.version_num;

COMMIT;
