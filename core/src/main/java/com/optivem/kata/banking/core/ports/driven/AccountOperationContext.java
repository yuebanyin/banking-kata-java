package com.optivem.kata.banking.core.ports.driven;

import java.util.Optional;
import java.util.function.Supplier;

/** Request-scoped metadata carried from HTTP to persistence without changing domain DTOs. */
public final class AccountOperationContext {
    public record Operation(String operationId, String idempotencyKey, String traceId, String kind) {}
    private static final ThreadLocal<Operation> CURRENT = new ThreadLocal<>();

    private AccountOperationContext() {}

    public static Optional<Operation> current() { return Optional.ofNullable(CURRENT.get()); }

    public static <T> T call(Operation operation, Supplier<T> action) {
        Operation previous = CURRENT.get();
        CURRENT.set(operation);
        try { return action.get(); }
        finally {
            if (previous == null) CURRENT.remove(); else CURRENT.set(previous);
        }
    }
}
