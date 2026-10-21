package com.optivem.kata.banking.cutover;

import com.optivem.kata.banking.adapter.driven.base.ProfileNames;
import com.optivem.kata.banking.adapter.driven.persistence.jpa.JpaBankAccountStorage;
import com.optivem.kata.banking.adapter.driven.persistence.mongo.MongoBankAccountStorage;
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
public class DualWriteBankAccountStorage implements BankAccountStorage {
    private final JpaBankAccountStorage source;
    private final MongoBankAccountStorage target;
    private final CutoverState state;
    private final ConcurrentHashMap<String, Object> locks = new ConcurrentHashMap<>();

    public DualWriteBankAccountStorage(JpaBankAccountStorage source,
                                       MongoBankAccountStorage target,
                                       CutoverState state) {
        this.source = source;
        this.target = target;
        this.state = state;
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

    private Object lock(String accountNumber) {
        return locks.computeIfAbsent(accountNumber, ignored -> new Object());
    }
}
