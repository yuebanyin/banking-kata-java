package com.optivem.kata.banking.core.ports.driven;

/** Maintains target-only fields derived by the public account view. */
public interface AccountProjectionWriter {
    void updateObservedScore(String accountNumber, String score);
}
