package com.optivem.kata.banking.cutover;

import com.optivem.kata.banking.adapter.driven.base.ProfileNames;
import com.optivem.kata.banking.adapter.driven.persistence.jpa.JpaBankAccountStorage;
import com.optivem.kata.banking.adapter.driven.persistence.mongo.MongoBankAccountStorage;
import com.optivem.kata.banking.core.ports.driven.AccountStorageRoute;
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
@Profile(ProfileNames.ACCOUNT_CUTOVER + " & !" + ProfileNames.TARGET_ONLY)
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
        if (AccountStorageRoute.isSourceOnly()) return source.find(accountNumber);
        return state.mongoReads() ? target.find(accountNumber) : source.find(accountNumber);
    }

    @Override
    public List<BankAccountDto> findAll() {
        return state.mongoReads() ? target.findAll() : source.findAll();
    }

    @Override
    public void add(BankAccountDto account) {
        if (AccountStorageRoute.isSourceOnly()) {
            source.add(account);
            return;
        }
        synchronized (lock(account.getAccountNumber())) {
            source.add(account);
            // The source row and its last_operation_id marker are committed together by JPA.
            // A crash from this point through journal completion is recoverable and idempotent.
            target.upsert(account);
        }
    }

    @Override
    public void update(BankAccountDto account) {
        if (AccountStorageRoute.isSourceOnly()) {
            source.update(account);
            return;
        }
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

    /**
     * Recover pending operations in durable source commit order. Recovering one request
     * deliberately drains all committed predecessors for that account, so retries that
     * arrive out of order cannot reorder MongoDB appliedOperationIds.
     */
    @Override
    public Optional<String> recover(String operationId) {
        Optional<AccountOperationJournal.Entry> before = journal.find(operationId);
        if (before.isEmpty()) return Optional.empty();
        if (isComplete(before.get())) return Optional.ofNullable(emptyToNull(before.get().accountNumber()));
        recoverPendingOperations();
        return journal.find(operationId).filter(this::isComplete)
                .map(AccountOperationJournal.Entry::accountNumber)
                .map(DualWriteBankAccountStorage::emptyToNull);
    }

    /** Called by the pre-web-server lifecycle and safe to invoke on every retry. */
    public int recoverPendingOperations() {
        var pending = journal.findAll().stream()
                .filter(entry -> "pending".equals(entry.result()))
                .collect(java.util.stream.Collectors.toMap(
                        AccountOperationJournal.Entry::operationId, entry -> entry, (left, right) -> left));
        var ordered = source.findRecoverableOperations(pending.keySet());
        int recovered = 0;
        for (JpaBankAccountStorage.RecoverableOperation sourceOperation : ordered) {
            AccountOperationJournal.Entry entry = pending.get(sourceOperation.operationId());
            if (entry == null) continue;
            BankAccountDto account = sourceOperation.account();
            synchronized (lock(account.getAccountNumber())) {
                var operation = new AccountOperationContext.Operation(entry.operationId(),
                        entry.idempotencyKey(), entry.requestTraceId(), entry.kind());
                AccountOperationContext.call(operation, () -> {
                    target.upsert(account);
                    return null;
                });
                journal.complete(entry.operationId(), account.getAccountNumber(), "applied", true,
                        System.currentTimeMillis());
                recovered++;
            }
        }
        return recovered;
    }

    private boolean isComplete(AccountOperationJournal.Entry entry) {
        return "applied".equals(entry.result()) || "duplicate_ignored".equals(entry.result());
    }

    private Object lock(String accountNumber) {
        return locks.computeIfAbsent(accountNumber, ignored -> new Object());
    }

    private static String emptyToNull(String value) {
        return value == null || value.isBlank() ? null : value;
    }
}
