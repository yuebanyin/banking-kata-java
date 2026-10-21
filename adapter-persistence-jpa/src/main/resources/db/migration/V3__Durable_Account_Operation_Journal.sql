CREATE TABLE IF NOT EXISTS account_operation_source (
    operation_sequence BIGSERIAL PRIMARY KEY,
    operation_id VARCHAR(255) NOT NULL UNIQUE,
    account_number VARCHAR(255) NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_account_operation_source_account
    ON account_operation_source(account_number, operation_sequence);
