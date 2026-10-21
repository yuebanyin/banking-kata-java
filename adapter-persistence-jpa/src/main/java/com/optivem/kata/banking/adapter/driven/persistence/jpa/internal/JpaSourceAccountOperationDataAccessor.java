package com.optivem.kata.banking.adapter.driven.persistence.jpa.internal;

import org.springframework.data.repository.CrudRepository;
import org.springframework.stereotype.Repository;

import java.util.Collection;
import java.util.List;
import java.util.Optional;

@Repository
public interface JpaSourceAccountOperationDataAccessor
        extends CrudRepository<SourceAccountOperationRecord, Long> {
    Optional<SourceAccountOperationRecord> findByOperationId(String operationId);

    List<SourceAccountOperationRecord> findAllByOperationIdInOrderByOperationSequenceAsc(
            Collection<String> operationIds);
}
