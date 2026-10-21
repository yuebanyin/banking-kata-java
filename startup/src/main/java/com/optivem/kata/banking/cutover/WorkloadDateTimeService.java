package com.optivem.kata.banking.cutover;

import com.optivem.kata.banking.adapter.driven.base.ProfileNames;
import com.optivem.kata.banking.core.ports.driven.DateTimeService;
import org.springframework.context.annotation.Primary;
import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Component;

import java.time.LocalDateTime;
import java.util.concurrent.atomic.AtomicReference;

@Component
@Primary
@Profile(ProfileNames.ACCOUNT_CUTOVER)
public class WorkloadDateTimeService implements DateTimeService {
    private final AtomicReference<LocalDateTime> controlled = new AtomicReference<>();
    public void set(LocalDateTime value) { controlled.set(value); }
    @Override public LocalDateTime now() {
        LocalDateTime value = controlled.get();
        return value == null ? LocalDateTime.now() : value;
    }
}
