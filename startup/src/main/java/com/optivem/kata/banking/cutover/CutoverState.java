package com.optivem.kata.banking.cutover;

import com.optivem.kata.banking.adapter.driven.base.ProfileNames;
import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Component;

import java.util.concurrent.atomic.AtomicBoolean;

@Component
@Profile(ProfileNames.ACCOUNT_CUTOVER + " & !" + ProfileNames.TARGET_ONLY)
public class CutoverState {
    private final AtomicBoolean mongoReads = new AtomicBoolean(false);
    public boolean mongoReads() { return mongoReads.get(); }
    public void switchToMongo() { mongoReads.set(true); }
}
