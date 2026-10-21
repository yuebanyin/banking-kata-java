package com.optivem.kata.banking.cutover;

import com.optivem.kata.banking.adapter.driven.persistence.jpa.JpaBankAccountStorage;
import com.optivem.kata.banking.adapter.driven.persistence.mongo.MongoBankAccountStorage;
import com.optivem.kata.banking.core.ports.driven.AccountOperationJournal;
import com.optivem.kata.banking.core.ports.driven.BankAccountDto;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class DualWriteBankAccountStorageTest {
    private final JpaBankAccountStorage source = mock(JpaBankAccountStorage.class);
    private final MongoBankAccountStorage target = mock(MongoBankAccountStorage.class);
    private final CutoverState state = mock(CutoverState.class);
    private final AccountOperationJournal journal = mock(AccountOperationJournal.class);
    private final DualWriteBankAccountStorage storage =
            new DualWriteBankAccountStorage(source, target, state, journal);

    @Test
    void backfillCopiesAGenuineSourceOnlyAccount() {
        BankAccountDto account = account("legacy-1", 75);
        when(source.findAll()).thenReturn(List.of(account));
        when(source.find("legacy-1")).thenReturn(Optional.of(account));

        assertThat(storage.backfill()).isEqualTo(1);

        verify(target).upsert(account);
        verify(target, never()).find(anyString());
    }

    @Test
    void pendingSourceCommittedOperationIsRecoveredIntoMongoAndCompleted() {
        BankAccountDto account = account("account-1", 125);
        var pending = new AccountOperationJournal.Entry(
                "op-1", "key-1", "trace-1", "deposit", "account-1", 50,
                10, 0, 0, "pending", false);
        when(journal.find("op-1")).thenReturn(Optional.of(pending));
        when(source.findByOperationId("op-1")).thenReturn(Optional.of(account));

        assertThat(storage.recover("op-1")).contains("account-1");

        verify(target).upsert(account);
        verify(journal).complete(eq("op-1"), eq("account-1"), eq("applied"), eq(true), anyLong());
    }

    @Test
    void alreadyAppliedOperationIsIdempotent() {
        var applied = new AccountOperationJournal.Entry(
                "op-1", "key-1", "trace-1", "deposit", "account-1", 50,
                10, 20, 1, "applied", true);
        when(journal.find("op-1")).thenReturn(Optional.of(applied));

        assertThat(storage.recover("op-1")).contains("account-1");

        verifyNoInteractions(source, target);
        verify(journal, never()).complete(anyString(), anyString(), anyString(), anyBoolean(), anyLong());
    }

    @Test
    void startupCoordinatorRecoversPendingOperationsBeforeMigration() {
        DualWriteBankAccountStorage recoveringStorage = mock(DualWriteBankAccountStorage.class);
        var coordinator = new AccountCutoverCoordinator(recoveringStorage, state);

        coordinator.recoverInterruptedWrites();

        verify(recoveringStorage).recoverPendingOperations();
    }

    private static BankAccountDto account(String number, int balance) {
        var account = new BankAccountDto();
        account.setAccountNumber(number);
        account.setBalance(balance);
        return account;
    }
}
