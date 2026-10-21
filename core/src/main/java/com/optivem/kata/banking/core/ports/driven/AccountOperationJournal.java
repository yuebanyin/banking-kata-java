package com.optivem.kata.banking.core.ports.driven;

import java.util.List;
import java.util.Optional;

/** Durable, externally observable idempotency journal for account mutations. */
public interface AccountOperationJournal {
    record Entry(String operationId, String idempotencyKey, String requestTraceId,
                 String kind, String accountNumber, int amount, long startedMs,
                 long completedMs, long linearizationIndex, String result,
                 boolean acknowledged) {}

    /** Creates a pending row, or a duplicate_ignored row when the key already applied. */
    Entry reserve(String operationId, String idempotencyKey, String requestTraceId,
                  String kind, String accountNumber, int amount, long startedMs);

    Entry complete(String operationId, String accountNumber, String result,
                   boolean acknowledged, long completedMs);

    Optional<Entry> find(String operationId);

    List<Entry> findAll();
}
