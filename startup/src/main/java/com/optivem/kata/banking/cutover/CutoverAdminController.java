package com.optivem.kata.banking.cutover;

import com.optivem.kata.banking.adapter.driven.base.ProfileNames;
import com.optivem.kata.banking.core.ports.driven.AccountOperationJournal;
import org.springframework.context.annotation.Profile;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.LinkedHashMap;
import java.util.Map;

@RestController
@RequestMapping("/internal/cutover")
@Profile(ProfileNames.ACCOUNT_CUTOVER + " & !" + ProfileNames.TARGET_ONLY)
public class CutoverAdminController {
    private final AccountCutoverCoordinator coordinator;
    private final AccountOperationJournal journal;

    public CutoverAdminController(AccountCutoverCoordinator coordinator,
                                  AccountOperationJournal journal) {
        this.coordinator = coordinator;
        this.journal = journal;
    }

    @PostMapping("/backfill")
    public ResponseEntity<Void> backfill(@RequestParam(defaultValue = "1500") long holdMs) {
        coordinator.startBackfill(holdMs);
        return ResponseEntity.accepted().build();
    }

    @PostMapping("/cutover")
    public ResponseEntity<Void> cutover(@RequestParam(defaultValue = "1500") long holdMs) {
        coordinator.startCutover(holdMs);
        return ResponseEntity.accepted().build();
    }

    @GetMapping("/status")
    public Map<String, Object> status() {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("activeKind", coordinator.activeKind());
        result.put("mongoReads", coordinator.mongoReads());
        result.put("intervals", coordinator.intervals());
        return result;
    }

    @GetMapping("/journal")
    public Object journal() { return journal.findAll(); }
}
