package com.optivem.kata.banking.core.ports.driven;

import java.util.Optional;

/** Recovers a source-committed operation whose MongoDB projection was not acknowledged. */
public interface AccountOperationRecovery {
    /**
     * Replays the source row into MongoDB when the durable source operation marker exists.
     * Empty means that the source transaction did not commit and normal execution is safe.
     */
    Optional<String> recover(String operationId);
}
