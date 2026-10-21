ALTER TABLE bank_account ADD COLUMN IF NOT EXISTS last_operation_id VARCHAR(255);
CREATE INDEX IF NOT EXISTS idx_bank_account_last_operation_id
    ON bank_account(last_operation_id);
