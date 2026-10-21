package com.optivem.kata.banking.cutover;

import com.optivem.kata.banking.adapter.driven.base.ProfileNames;
import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

@Component
@Profile(ProfileNames.ACCOUNT_CUTOVER + " & !" + ProfileNames.TARGET_ONLY)
public class AccountCutoverCoordinator {
    public record Interval(String intervalId, String kind, long startedMs, long completedMs) {}

    private final DualWriteBankAccountStorage storage;
    private final CutoverState state;
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final List<Interval> intervals = new ArrayList<>();
    private volatile String activeKind = "idle";

    public AccountCutoverCoordinator(DualWriteBankAccountStorage storage, CutoverState state) {
        this.storage = storage;
        this.state = state;
    }

    public synchronized void startBackfill(long holdMs) {
        ensureIdle();
        activeKind = "backfill";
        executor.submit(() -> runBackfill(Math.max(holdMs, 250L)));
    }

    public synchronized void startCutover(long holdMs) {
        ensureIdle();
        activeKind = "cutover";
        executor.submit(() -> runCutover(Math.max(holdMs, 250L)));
    }

    public synchronized List<Interval> intervals() { return List.copyOf(intervals); }
    public String activeKind() { return activeKind; }
    public boolean mongoReads() { return state.mongoReads(); }

    private void runBackfill(long holdMs) {
        long started = System.currentTimeMillis();
        storage.backfill();
        sleepUntil(started + holdMs);
        finish("backfill", started);
    }

    private void runCutover(long holdMs) {
        long started = System.currentTimeMillis();
        sleepUntil(started + holdMs);
        state.switchToMongo();
        finish("cutover", started);
    }

    private synchronized void finish(String kind, long started) {
        intervals.add(new Interval(kind + "-" + started, kind, started, System.currentTimeMillis()));
        activeKind = "idle";
    }

    private synchronized void ensureIdle() {
        if (!"idle".equals(activeKind)) throw new IllegalStateException("migration interval already active");
    }

    private static void sleepUntil(long deadline) {
        long delay = deadline - System.currentTimeMillis();
        if (delay > 0) try { Thread.sleep(delay); } catch (InterruptedException ex) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("cutover interrupted", ex);
        }
    }
}
