package com.optivem.kata.banking.adapter.driven.persistence.jpa;

import com.optivem.kata.banking.adapter.driven.persistence.jpa.internal.BankAccountRecord;
import com.optivem.kata.banking.adapter.driven.persistence.jpa.internal.JpaBankAccountDataAccessor;
import com.optivem.kata.banking.adapter.driven.persistence.jpa.mapper.BankAccountJpaMapper;
import com.optivem.kata.banking.core.ports.driven.AccountOperationContext;
import com.optivem.kata.banking.core.ports.driven.BankAccountDto;
import org.junit.Test;
import org.mockito.ArgumentCaptor;

import java.time.LocalDate;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.*;

public class JpaOperationRecoveryTest {
    private final JpaBankAccountDataAccessor accessor = mock(JpaBankAccountDataAccessor.class);
    private final JpaBankAccountStorage storage =
            new JpaBankAccountStorage(accessor, mock(BankAccountJpaMapper.class));

    @Test
    public void sourceRowIsAtomicallyMarkedWithTheCurrentOperation() {
        var operation = new AccountOperationContext.Operation("operation-7", "key-7", "trace-7", "create");

        AccountOperationContext.call(operation, () -> {
            storage.add(account());
            return null;
        });

        var saved = ArgumentCaptor.forClass(BankAccountRecord.class);
        verify(accessor).save(saved.capture());
        assertThat(saved.getValue().getLastOperationId()).isEqualTo("operation-7");
    }

    @Test
    public void sourceAccountCanBeLocatedForPendingOperationRecovery() {
        var record = new BankAccountRecord();
        record.setId(3);
        record.setAccountNumber("account-3");
        record.setNationalIdentityNumber("nid-3");
        record.setFirstName("Ada");
        record.setLastName("Lovelace");
        record.setOpeningDate(LocalDate.of(2020, 1, 1));
        record.setBalance(90);
        record.setLastOperationId("operation-3");
        when(accessor.findByLastOperationId("operation-3")).thenReturn(Optional.of(record));

        assertThat(storage.findByOperationId("operation-3"))
                .get().extracting(BankAccountDto::getAccountNumber, BankAccountDto::getBalance)
                .containsExactly("account-3", 90);
    }

    private static BankAccountDto account() {
        var account = new BankAccountDto();
        account.setAccountId(7);
        account.setAccountNumber("account-7");
        account.setNationalIdentityNumber("nid-7");
        account.setFirstName("Grace");
        account.setLastName("Hopper");
        account.setOpeningDate(LocalDate.of(2020, 1, 1));
        account.setBalance(100);
        return account;
    }
}
