package com.optivem.kata.banking.adapter.driven.persistence.jpa;

import com.optivem.kata.banking.BankingApplication;
import com.optivem.kata.banking.adapter.driven.base.BankAccountStorageTest;
import com.optivem.kata.banking.adapter.driven.base.ProfileNames;
import com.optivem.kata.banking.core.ports.driven.AccountIdGenerator;
import com.optivem.kata.banking.core.ports.driven.AccountNumberGenerator;
import com.optivem.kata.banking.core.ports.driven.BankAccountStorage;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.core.env.Environment;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.ContextConfiguration;

import java.util.Arrays;

import static org.assertj.core.api.Assertions.assertThat;

@SpringBootTest(classes = BankingApplication.class)
@ActiveProfiles({ ProfileNames.ADAPTER_PERSISTENCE_JPA, ProfileNames.ADAPTER_GENERATION_RANDOM, ProfileNames.ADAPTER_TIME_SYSTEM, ProfileNames.ADAPTER_MICROSERVICE_SIM, ProfileNames.ADAPTER_THIRDPARTY_SIM})
@ContextConfiguration
public class JpaBankAccountStorageTest extends BankAccountStorageTest {
    private final BankAccountStorage storage;
    private final Environment environment;

    @Autowired
    public JpaBankAccountStorageTest(BankAccountStorage storage, AccountIdGenerator accountIdGenerator,
                                     AccountNumberGenerator accountNumberGenerator, Environment environment) {
        super(storage, accountIdGenerator, accountNumberGenerator);
        this.storage = storage;
        this.environment = environment;
    }

    @Test
    void should_isolate_jpa_contract_from_other_persistence_profiles() {
        assertThat(storage).isInstanceOf(JpaBankAccountStorage.class);
        assertThat(Arrays.asList(environment.getActiveProfiles()))
                .contains(ProfileNames.ADAPTER_PERSISTENCE_JPA)
                .doesNotContain(ProfileNames.ADAPTER_PERSISTENCE_REDIS);
    }
}
