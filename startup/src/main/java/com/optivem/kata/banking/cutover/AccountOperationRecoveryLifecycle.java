package com.optivem.kata.banking.cutover;

import com.optivem.kata.banking.adapter.driven.base.ProfileNames;
import org.springframework.context.SmartLifecycle;
import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Component;

/**
 * Drains source-committed operations before Spring starts the embedded web server.
 * SmartLifecycle components start by ascending phase; Boot's web-server lifecycle
 * uses a near-maximum phase, while this recovery gate uses the minimum phase.
 */
@Component
@Profile(ProfileNames.ACCOUNT_CUTOVER)
public class AccountOperationRecoveryLifecycle implements SmartLifecycle {
    private final DualWriteBankAccountStorage storage;
    private volatile boolean running;

    public AccountOperationRecoveryLifecycle(DualWriteBankAccountStorage storage) {
        this.storage = storage;
    }

    @Override
    public void start() {
        storage.recoverPendingOperations();
        running = true;
    }

    @Override
    public void stop() { running = false; }

    @Override
    public boolean isRunning() { return running; }

    @Override
    public boolean isAutoStartup() { return true; }

    @Override
    public int getPhase() { return Integer.MIN_VALUE; }
}
