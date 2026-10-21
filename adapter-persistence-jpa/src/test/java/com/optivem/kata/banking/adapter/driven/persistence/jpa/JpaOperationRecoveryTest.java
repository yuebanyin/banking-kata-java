package com.optivem.kata.banking.adapter.driven.persistence.jpa;

import com.optivem.kata.banking.adapter.driven.persistence.jpa.internal.BankAccountRecord;
import com.optivem.kata.banking.adapter.driven.persistence.jpa.internal.JpaBankAccountDataAccessor;
import com.optivem.kata.banking.adapter.driven.persistence.jpa.internal.JpaSourceAccountOperationDataAccessor;
import com.optivem.kata.banking.adapter.driven.persistence.jpa.internal.SourceAccountOperationRecord;
import com.optivem.kata.banking.adapter.driven.persistence.jpa.mapper.BankAccountJpaMapper;
import com.optivem.kata.banking.core.ports.driven.AccountOperationContext;
import com.optivem.kata.banking.core.ports.driven.BankAccountDto;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDate;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.*;

class JpaOperationRecoveryTest {
    private final JpaBankAccountDataAccessor accounts = mock(JpaBankAccountDataAccessor.class);
    private final JpaSourceAccountOperationDataAccessor operations = mock(JpaSourceAccountOperationDataAccessor.class);
    private final BankAccountJpaMapper mapper = mock(BankAccountJpaMapper.class);
    private final JpaBankAccountStorage storage = new JpaBankAccountStorage(accounts, mapper, operations);

    @Test
    void everyOperationGetsItsOwnDurableMarkerInsteadOfOverwritingTheAccount() throws Exception {
        when(operations.findByOperationId(anyString())).thenReturn(Optional.empty());
        BankAccountDto account = account("account-7", 100);

        apply("operation-7", () -> storage.add(account));
        var existing = record("account-7", 100);
        when(accounts.findByAccountNumber("account-7")).thenReturn(Optional.of(existing));
        when(mapper.toRecord(account, existing)).thenReturn(existing);
        apply("operation-8", () -> storage.update(account));

        var saved = ArgumentCaptor.forClass(SourceAccountOperationRecord.class);
        verify(operations, times(2)).save(saved.capture());
        assertThat(saved.getAllValues()).extracting(SourceAccountOperationRecord::getOperationId)
                .containsExactly("operation-7", "operation-8");
        assertThat(saved.getAllValues()).extracting(SourceAccountOperationRecord::getAccountNumber)
                .containsOnly("account-7");
        assertThat(JpaBankAccountStorage.class.getMethod("add", BankAccountDto.class)
                .isAnnotationPresent(Transactional.class)).isTrue();
        assertThat(JpaBankAccountStorage.class.getMethod("update", BankAccountDto.class)
                .isAnnotationPresent(Transactional.class)).isTrue();
    }

    @Test
    void pendingOperationsAreReturnedInDurableSourceOrder() {
        SourceAccountOperationRecord second = marker(2L, "operation-2", "account-3");
        SourceAccountOperationRecord first = marker(1L, "operation-1", "account-3");
        // Repository contract returns operation_sequence order even when IDs are supplied out of order.
        when(operations.findAllByOperationIdInOrderByOperationSequenceAsc(anyCollection()))
                .thenReturn(List.of(first, second));
        when(accounts.findByAccountNumber("account-3")).thenReturn(Optional.of(record("account-3", 90)));

        assertThat(storage.findRecoverableOperations(List.of("operation-2", "operation-1")))
                .extracting(JpaBankAccountStorage.RecoverableOperation::operationId)
                .containsExactly("operation-1", "operation-2");
    }

    private static void apply(String id, Runnable action) {
        AccountOperationContext.call(new AccountOperationContext.Operation(id, "key-" + id, "trace-" + id, "deposit"), () -> {
            action.run();
            return null;
        });
    }

    private static SourceAccountOperationRecord marker(long sequence, String id, String account) {
        var marker = new SourceAccountOperationRecord();
        marker.setOperationSequence(sequence);
        marker.setOperationId(id);
        marker.setAccountNumber(account);
        return marker;
    }

    private static BankAccountRecord record(String number, int balance) {
        var record = new BankAccountRecord();
        record.setId(3);
        record.setAccountNumber(number);
        record.setNationalIdentityNumber("nid-3");
        record.setFirstName("Ada");
        record.setLastName("Lovelace");
        record.setOpeningDate(LocalDate.of(2020, 1, 1));
        record.setBalance(balance);
        return record;
    }

    private static BankAccountDto account(String number, int balance) {
        var account = new BankAccountDto();
        account.setAccountId(7);
        account.setAccountNumber(number);
        account.setNationalIdentityNumber("nid-7");
        account.setFirstName("Grace");
        account.setLastName("Hopper");
        account.setOpeningDate(LocalDate.of(2020, 1, 1));
        account.setBalance(balance);
        return account;
    }
}
