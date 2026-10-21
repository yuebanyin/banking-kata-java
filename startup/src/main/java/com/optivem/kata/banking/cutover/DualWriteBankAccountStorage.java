package com.optivem.kata.banking.cutover;

import com.optivem.kata.banking.adapter.driven.base.ProfileNames;
import com.optivem.kata.banking.adapter.driven.persistence.jpa.JpaBankAccountStorage;
import com.optivem.kata.banking.adapter.driven.persistence.mongo.MongoBankAccountStorage;
import com.optivem.kata.banking.core.ports.driven.AccountOperationContext;
import com.optivem.kata.banking.core.ports.driven.AccountOperationJournal;
import com.optivem.kata.banking.core.ports.driven.AccountOperationRecovery;
import com.optivem.kata.banking.core.ports.driven.BankAccountDto;
import com.optivem.kata.banking.core.ports.driven.BankAccountStorage;
import org.springframework.context.annotation.Primary;
import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;

@Component
@Primary
@Profile(ProfileNames.ACCOUNT_CUTOVER)
public class DualWriteBankAccountStorage implements BankAccountStorage, AccountOperationRecovery {
    private final JpaBankAccountStorage source;
    private final MongoBankAccountStorage target;
    private final CutoverState state;
    private final AccountOperationJournal journal;
    private final ConcurrentHashMap<String, Object> locks = new ConcurrentHashMap<>();

    public DualWriteBankAccountStorage(JpaBankAccountStorage source,
                                       MongoBankAccountStorage target,
                                       CutoverState state,
                                       AccountOperationJournal journal) {
        this.source = source;
        this.target = target;
        this.state = state;
        this.journal = journal;
    }

    @Override
    public Optional<BankAccountDto> find(String accountNumber) {
        return state.mongoReads() ? target.find(accountNumber) : source.find(accountNumber);
    }

    @Override
    public List<BankAccountDto> findAll() {
        return state.mongoReads() ? target.findAll() : source.findAll();
    }

    @Override
    public void add(BankAccountDto account) {
        synchronized (lock(account.getAccountNumber())) {
            source.add(account);
            // The source row and its last_operation_id marker are committed together by JPA.
            // A crash from this point through journal completion is recoverable and idempotent.
            target.upsert(account);
        }
    }

    @Override
    public void update(BankAccountDto account) {
        synchronized (lock(account.getAccountNumber())) {
            source.update(account);
            target.upsert(account);
        }
    }

    @Override
    public void upsert(BankAccountDto account) {
        synchronized (lock(account.getAccountNumber())) {
            source.upsert(account);
            target.upsert(account);
        }
    }

    /**
     * Enumerate a source snapshot, then re-read every row under its mutation lock.
     * A write either finishes in both stores before this copy, or runs afterwards;
     * a stale snapshot can therefore never overwrite an acknowledged write.
     */
    public int backfill() {
        int copied = 0;
        for (BankAccountDto snapshotRow : source.findAll()) {
            synchronized (lock(snapshotRow.getAccountNumber())) {
                Optional<BankAccountDto> current = source.find(snapshotRow.getAccountNumber());
                if (current.isPresent()) {
                    target.upsert(current.get());
                    copied++;
                }
            }
        }
        return copied;
    }

    /** Recover one operation reserved in MongoDB before its source transaction ran. */
    @Override
    public Optional<String> recover(String operationId) {
        Optional<AccountOperationJournal.Entry> saved = journal.find(operationId);
        if (saved.isEmpty()) return Optional.empty();
        AccountOperationJournal.Entry entry = saved.get();
        if ("applied".equals(entry.result()) || "duplicate_ignored".equals(entry.result())) {
            return Optional.ofNullable(emptyToNull(entry.accountNumber()));
        }
        Optional<BankAccountDto> sourceAccount = source.findByOperationId(operationId);
        if (sourceAccount.isEmpty()) return Optional.empty();
        BankAccountDto account = sourceAccount.get();
        synchronized (lock(account.getAccountNumber())) {
            var operation = new AccountOperationContext.Operation(entry.operationId(),
                    entry.idempotencyKey(), entry.requestTraceId(), entry.kind());
            AccountOperationContext.call(operation, () -> {
                target.upsert(account);
                return null;
            });
            journal.complete(operationId, account.getAccountNumber(), "applied", true,
                    System.currentTimeMillis());
        }
        return Optional.of(account.getAccountNumber());
    }

    /** Called before the cutover application accepts traffic after a process restart. */
    public int recoverPendingOperations() {
        int recovered = 0;
        for (AccountOperationJournal.Entry entry : journal.findAll()) {
            if ("pending".equals(entry.result()) && recover(entry.operationId()).isPresent()) recovered++;
        }
        return recovered;
    }

    private Object lock(String accountNumber) {
        return locks.computeIfAbsent(accountNumber, ignored -> new Object());
    }

    private static String emptyToNull(String value) {
        return value == null || value.isBlank() ? null : value;
    }
}
