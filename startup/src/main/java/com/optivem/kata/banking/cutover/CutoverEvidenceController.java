package com.optivem.kata.banking.cutover;

import com.optivem.kata.banking.adapter.driven.base.ProfileNames;
import com.optivem.kata.banking.core.ports.driven.AccountOperationJournal;
import org.springframework.context.annotation.Profile;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/** Read-only access to the durable journal for independent migration evidence. */
@RestController
@RequestMapping("/internal/evidence")
@Profile(ProfileNames.ADAPTER_PERSISTENCE_MONGO)
public class CutoverEvidenceController {
    private final AccountOperationJournal journal;

    public CutoverEvidenceController(AccountOperationJournal journal) {
        this.journal = journal;
    }

    @GetMapping("/journal")
    public Object journal() {
        return journal.findAll();
    }
}
