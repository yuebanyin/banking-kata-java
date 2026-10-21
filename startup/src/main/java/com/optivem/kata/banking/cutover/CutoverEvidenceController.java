package com.optivem.kata.banking.cutover;

import com.optivem.kata.banking.adapter.driven.base.ProfileNames;
import com.optivem.kata.banking.core.ports.driven.AccountOperationJournal;
import com.optivem.kata.banking.core.ports.driven.BankAccountStorage;
import org.springframework.aop.support.AopUtils;
import org.springframework.context.ApplicationContext;
import org.springframework.context.annotation.Profile;
import org.springframework.core.env.Environment;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;

/** Read-only endpoints whose values are derived from the running application context. */
@RestController
@RequestMapping("/internal/evidence")
@Profile(ProfileNames.ADAPTER_PERSISTENCE_MONGO)
public class CutoverEvidenceController {
    private final AccountOperationJournal journal;
    private final Environment environment;
    private final ApplicationContext context;
    private final BankAccountStorage selectedStorage;

    public CutoverEvidenceController(AccountOperationJournal journal, Environment environment,
                                     ApplicationContext context, BankAccountStorage selectedStorage) {
        this.journal = journal;
        this.environment = environment;
        this.context = context;
        this.selectedStorage = selectedStorage;
    }

    @GetMapping("/journal")
    public Object journal() { return journal.findAll(); }

    @GetMapping("/configuration")
    public Object configuration() {
        String selectedClass = AopUtils.getTargetClass(selectedStorage).getName();
        var activeProfiles = Arrays.asList(environment.getActiveProfiles());
        boolean jpaProfile = activeProfiles.contains(ProfileNames.ADAPTER_PERSISTENCE_JPA);
        boolean jpaBean = context.getBeansOfType(BankAccountStorage.class).values().stream()
                .map(bean -> AopUtils.getTargetClass(bean).getName().toLowerCase(Locale.ROOT))
                .anyMatch(name -> name.contains("jpa"));
        String storage = selectedClass.toLowerCase(Locale.ROOT).contains("mongo") ? "mongodb" : "unknown";
        Map<String, Object> observed = new LinkedHashMap<>();
        observed.put("storage", storage);
        observed.put("postgresql_enabled", jpaProfile || jpaBean);
        observed.put("active_profiles", activeProfiles);
        observed.put("selected_storage_bean", selectedClass);
        observed.put("observed_at_ms", System.currentTimeMillis());
        return observed;
    }
}
