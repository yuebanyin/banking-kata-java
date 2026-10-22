package com.optivem.kata.banking.cutover;

import com.optivem.kata.banking.BankingApplication;
import com.optivem.kata.banking.adapter.driven.base.ProfileNames;
import com.optivem.kata.banking.adapter.driven.thirdparty.fake.FakeNationalIdentityGatewaySimulator;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.reactive.server.WebTestClient;

import static org.assertj.core.api.Assertions.assertThat;

/** HTTP regression for the exact first fixture request made after a Compose reset. */
@SpringBootTest(classes = BankingApplication.class,
        webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@ActiveProfiles({
        ProfileNames.ACCOUNT_CUTOVER,
        ProfileNames.ADAPTER_PERSISTENCE_JPA,
        ProfileNames.ADAPTER_PERSISTENCE_MONGO,
        ProfileNames.ADAPTER_GENERATION_RANDOM,
        ProfileNames.ADAPTER_MICROSERVICE_SIM,
        ProfileNames.ADAPTER_THIRDPARTY_SIM,
        ProfileNames.ADAPTER_TIME_SYSTEM,
        ProfileNames.ADAPTER_AUTH_NONE
})
class CutoverWorkloadFixtureEndpointIntegrationTest {
    @MockBean
    private com.optivem.kata.banking.core.ports.driven.AccountOperationJournal operationJournal;
    private static final String MISSING_CUSTOMER = "00000042030";

    @Autowired
    private WebTestClient client;

    @Autowired
    private FakeNationalIdentityGatewaySimulator identities;

    @Test
    void freshPackagedApplicationServesExactRunnerCustomerResetRequest() {
        assertThat(identities.exists(MISSING_CUSTOMER)).isFalse();

        client.post()
                .uri("/internal/cutover/fixtures/00000042030?customerCase=missing")
                .exchange()
                .expectStatus().isNoContent()
                .expectBody().isEmpty();

        // A missing-customer fixture must not accidentally make the identity exist.
        assertThat(identities.exists(MISSING_CUSTOMER)).isFalse();
    }
}
