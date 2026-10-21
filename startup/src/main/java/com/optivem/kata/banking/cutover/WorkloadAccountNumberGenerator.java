package com.optivem.kata.banking.cutover;

import com.optivem.kata.banking.adapter.driven.base.ProfileNames;
import com.optivem.kata.banking.core.ports.driven.AccountNumberGenerator;
import org.springframework.context.annotation.Primary;
import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Component;

import java.util.Queue;
import java.util.UUID;
import java.util.concurrent.ConcurrentLinkedQueue;

@Component
@Primary
@Profile(ProfileNames.ACCOUNT_CUTOVER)
public class WorkloadAccountNumberGenerator implements AccountNumberGenerator {
    private final Queue<String> values = new ConcurrentLinkedQueue<>();
    public void enqueue(String accountNumber) { values.add(accountNumber); }
    @Override public String next() {
        String value = values.poll();
        return value == null ? UUID.randomUUID().toString() : value;
    }
}
