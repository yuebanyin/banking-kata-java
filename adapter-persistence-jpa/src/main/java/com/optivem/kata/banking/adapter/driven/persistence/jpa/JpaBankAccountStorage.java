package com.optivem.kata.banking.adapter.driven.persistence.jpa;

import com.optivem.kata.banking.adapter.driven.base.ProfileNames;
import com.optivem.kata.banking.adapter.driven.persistence.jpa.internal.BankAccountRecord;
import com.optivem.kata.banking.adapter.driven.persistence.jpa.internal.JpaBankAccountDataAccessor;
import com.optivem.kata.banking.adapter.driven.persistence.jpa.internal.JpaSourceAccountOperationDataAccessor;
import com.optivem.kata.banking.adapter.driven.persistence.jpa.internal.SourceAccountOperationRecord;
import com.optivem.kata.banking.adapter.driven.persistence.jpa.mapper.BankAccountJpaMapper;
import com.optivem.kata.banking.core.ports.driven.AccountOperationContext;
import com.optivem.kata.banking.core.ports.driven.BankAccountDto;
import com.optivem.kata.banking.core.ports.driven.BankAccountStorage;
import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import java.util.Collection;
import java.util.List;
import java.util.Optional;
import java.util.stream.Collectors;
import java.util.stream.StreamSupport;

@Component
@Profile(ProfileNames.ADAPTER_PERSISTENCE_JPA)
public class JpaBankAccountStorage implements BankAccountStorage {
    public record RecoverableOperation(long sequence, String operationId, BankAccountDto account) {}

    private final JpaBankAccountDataAccessor dataAccessor;
    private final BankAccountJpaMapper bankAccountJpaMapper;
    private final JpaSourceAccountOperationDataAccessor operationAccessor;

    public JpaBankAccountStorage(JpaBankAccountDataAccessor dataAccessor,
                                 BankAccountJpaMapper bankAccountJpaMapper,
                                 JpaSourceAccountOperationDataAccessor operationAccessor) {
        this.dataAccessor = dataAccessor;
        this.bankAccountJpaMapper = bankAccountJpaMapper;
        this.operationAccessor = operationAccessor;
    }

    @Override
    public Optional<BankAccountDto> find(String accountNumber) {
        return dataAccessor.findByAccountNumber(accountNumber).map(this::toDto);
    }

    public Optional<BankAccountDto> findByOperationId(String operationId) {
        return operationAccessor.findByOperationId(operationId)
                .flatMap(marker -> find(marker.getAccountNumber()));
    }

    /** Return source-committed markers in database commit order, never journal iteration order. */
    public List<RecoverableOperation> findRecoverableOperations(Collection<String> operationIds) {
        if (operationIds.isEmpty()) return List.of();
        return operationAccessor.findAllByOperationIdInOrderByOperationSequenceAsc(operationIds).stream()
                .map(marker -> find(marker.getAccountNumber()).map(account -> new RecoverableOperation(
                        marker.getOperationSequence(), marker.getOperationId(), account)))
                .flatMap(Optional::stream)
                .collect(Collectors.toList());
    }

    @Override
    public List<BankAccountDto> findAll() {
        return StreamSupport.stream(dataAccessor.findAll().spliterator(), false)
                .map(this::toDto).collect(Collectors.toList());
    }

    @Override
    @Transactional
    public void add(BankAccountDto account) {
        dataAccessor.save(create(account));
        persistCurrentOperation(account.getAccountNumber());
    }

    @Override
    @Transactional
    public void update(BankAccountDto account) {
        dataAccessor.findByAccountNumber(account.getAccountNumber()).ifPresent(record -> {
            dataAccessor.save(bankAccountJpaMapper.toRecord(account, record));
            persistCurrentOperation(account.getAccountNumber());
        });
    }

    @Override
    @Transactional
    public void upsert(BankAccountDto account) {
        if (dataAccessor.findByAccountNumber(account.getAccountNumber()).isPresent()) update(account);
        else add(account);
    }

    private void persistCurrentOperation(String accountNumber) {
        AccountOperationContext.current().ifPresent(operation -> {
            if (operationAccessor.findByOperationId(operation.operationId()).isEmpty()) {
                var marker = new SourceAccountOperationRecord();
                marker.setOperationId(operation.operationId());
                marker.setAccountNumber(accountNumber);
                operationAccessor.save(marker);
            }
        });
    }

    private BankAccountRecord create(BankAccountDto account) {
        var record = new BankAccountRecord();
        record.setId(account.getAccountId());
        record.setAccountNumber(account.getAccountNumber());
        record.setNationalIdentityNumber(account.getNationalIdentityNumber());
        record.setFirstName(account.getFirstName());
        record.setLastName(account.getLastName());
        record.setOpeningDate(account.getOpeningDate());
        record.setBalance(account.getBalance());
        return record;
    }

    private BankAccountDto toDto(BankAccountRecord record) {
        var dto = new BankAccountDto();
        dto.setAccountId(record.getId());
        dto.setAccountNumber(record.getAccountNumber());
        dto.setNationalIdentityNumber(record.getNationalIdentityNumber());
        dto.setFirstName(record.getFirstName());
        dto.setLastName(record.getLastName());
        dto.setOpeningDate(record.getOpeningDate());
        dto.setBalance(record.getBalance());
        return dto;
    }
}
