package com.optivem.kata.banking.core.ports.driven;

/**
 * Request-scoped routing used only while measuring the pre-migration public
 * baseline.  It keeps the public HTTP contract unchanged while ensuring the
 * measured persistence path is PostgreSQL-only, rather than a dual-write.
 */
public final class AccountStorageRoute {
    private static final ThreadLocal<Boolean> SOURCE_ONLY =
            ThreadLocal.withInitial(() -> Boolean.FALSE);

    private AccountStorageRoute() { }

    public static void useSourceOnly() {
        SOURCE_ONLY.set(Boolean.TRUE);
    }

    public static boolean isSourceOnly() {
        return SOURCE_ONLY.get();
    }

    public static void clear() {
        SOURCE_ONLY.remove();
    }
}
