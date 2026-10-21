package com.optivem.kata.banking.cutover;

import com.optivem.kata.banking.adapter.driven.persistence.jpa.JpaBankAccountStorage;
import com.optivem.kata.banking.adapter.driven.persistence.mongo.MongoBankAccountStorage;
import com.optivem.kata.banking.core.ports.driven.AccountOperationContext;
import com.optivem.kata.banking.core.ports.driven.AccountOperationJournal;
import com.optivem.kata.banking.core.ports.driven.BankAccountDto;
import org.junit.jupiter.api.Test;

import java.util.ArrayList;
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
    private final DualWriteBankAccountStorage storage = new DualWriteBankAccountStorage(source, target, state, journal);

    @Test
    void backfillCopiesAGenuineSourceOnlyAccount() {
        BankAccountDto account = account("legacy-1", 75);
        when(source.findAll()).thenReturn(List.of(account));
        when(source.find("legacy-1")).thenReturn(Optional.of(account));
        assertThat(storage.backfill()).isEqualTo(1);
        verify(target).upsert(account);
    }

    @Test
    void multiplePendingWritesOnOneAccountRecoverInDurableSourceOrder() {
        BankAccountDto current = account("account-1", 175);
        var first = pending("op-1", 10);
        var second = pending("op-2", 20);
        // Deliberately reverse journal order: source marker order is authoritative.
        when(journal.findAll()).thenReturn(List.of(second, first));
        when(source.findRecoverableOperations(anyCollection())).thenReturn(List.of(
                new JpaBankAccountStorage.RecoverableOperation(31, "op-1", current),
                new JpaBankAccountStorage.RecoverableOperation(32, "op-2", current)));
        var appliedIds = new ArrayList<String>();
        doAnswer(invocation -> {
            appliedIds.add(AccountOperationContext.current().orElseThrow().operationId());
            return null;
        }).when(target).upsert(current);

        assertThat(storage.recoverPendingOperations()).isEqualTo(2);

        assertThat(appliedIds).containsExactly("op-1", "op-2");
        var ordered = inOrder(journal);
        ordered.verify(journal).complete(eq("op-1"), eq("account-1"), eq("applied"), eq(true), anyLong());
        ordered.verify(journal).complete(eq("op-2"), eq("account-1"), eq("applied"), eq(true), anyLong());
    }

    @Test
    void alreadyAppliedOperationIsIdempotent() {
        var applied = new AccountOperationJournal.Entry("op-1", "key-1", "trace-1", "deposit",
                "account-1", 50, 10, 20, 1, "applied", true);
        when(journal.find("op-1")).thenReturn(Optional.of(applied));
        assertThat(storage.recover("op-1")).contains("account-1");
        verifyNoInteractions(source, target);
    }

    @Test
    void recoveryLifecycleRunsAtMinimumPhaseBeforeWebServerLifecycles() {
        DualWriteBankAccountStorage recoveringStorage = mock(DualWriteBankAccountStorage.class);
        var lifecycle = new AccountOperationRecoveryLifecycle(recoveringStorage);
        assertThat(lifecycle.getPhase()).isEqualTo(Integer.MIN_VALUE);
        assertThat(lifecycle.isAutoStartup()).isTrue();
        lifecycle.start();
        verify(recoveringStorage).recoverPendingOperations();
        assertThat(lifecycle.isRunning()).isTrue();
    }

    private static AccountOperationJournal.Entry pending(String id, long started) {
        return new AccountOperationJournal.Entry(id, "key-" + id, "trace-" + id, "deposit",
                "account-1", 50, started, 0, 0, "pending", false);
    }

    private static BankAccountDto account(String number, int balance) {
        var account = new BankAccountDto();
        account.setAccountNumber(number);
        account.setBalance(balance);
        return account;
    }
}
