package com.optivem.kata.banking.core.ports.driven;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class AccountStorageRouteTest {
    @Test
    void sourceOnlyScopeCanBeClearedWithoutLeakingToAnotherRequest() {
        assertFalse(AccountStorageRoute.isSourceOnly());
        AccountStorageRoute.useSourceOnly();
        assertTrue(AccountStorageRoute.isSourceOnly());
        AccountStorageRoute.clear();
        assertFalse(AccountStorageRoute.isSourceOnly());
    }
}
