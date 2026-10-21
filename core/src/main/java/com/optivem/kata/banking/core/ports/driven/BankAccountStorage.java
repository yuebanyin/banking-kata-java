package com.optivem.kata.banking.core.ports.driven;

import java.util.List;
import java.util.Optional;

public interface BankAccountStorage {
    Optional<BankAccountDto> find(String accountNumber);

    void add(BankAccountDto bankAccount);

    void update(BankAccountDto bankAccount);

    /** A stable storage snapshot used by an online backfill. */
    default List<BankAccountDto> findAll() {
        throw new UnsupportedOperationException("This storage does not support backfill enumeration");
    }

    /**
     * Idempotently copies a complete account. Target adapters override this with an
     * atomic upsert; ordinary adapters retain the original add contract.
     */
    default void upsert(BankAccountDto bankAccount) {
        if (find(bankAccount.getAccountNumber()).isPresent()) update(bankAccount);
        else add(bankAccount);
    }
}
