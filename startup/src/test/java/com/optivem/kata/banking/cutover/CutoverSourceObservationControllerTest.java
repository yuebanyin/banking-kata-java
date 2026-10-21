package com.optivem.kata.banking.cutover;

import com.optivem.kata.banking.adapter.driven.persistence.jpa.JpaBankAccountStorage;
import com.optivem.kata.banking.core.internal.cleanarch.domain.scoring.Score;
import com.optivem.kata.banking.core.internal.cleanarch.domain.scoring.ScoreCalculator;
import com.optivem.kata.banking.core.ports.driven.BankAccountDto;
import com.optivem.kata.banking.core.ports.driven.CustomerGateway;
import com.optivem.kata.banking.core.ports.driven.NationalIdentityGateway;
import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.time.LocalDate;
import java.util.Date;
import java.util.Map;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class CutoverSourceObservationControllerTest {
    private final JpaBankAccountStorage source = mock(JpaBankAccountStorage.class);
    private final ScoreCalculator scores = mock(ScoreCalculator.class);
    private final CustomerGateway customers = mock(CustomerGateway.class);
    private final NationalIdentityGateway identities = mock(NationalIdentityGateway.class);
    private final CutoverSourceObservationController controller =
            new CutoverSourceObservationController(source, scores, customers, identities);

    @Test
    void derivesEveryGetFieldFromJpaAndScoringInsteadOfCandidateInput() {
        var account = BankAccountDto.builder()
                .accountId(7).accountNumber("A-1").nationalIdentityNumber("123")
                .firstName("Source").lastName("Holder")
                .openingDate(LocalDate.of(2020, 1, 1)).balance(41).build();
        when(source.find("A-1")).thenReturn(Optional.of(account));
        when(scores.calculate(any())).thenReturn(Score.B);

        var receipt = controller.observe(new CutoverSourceObservationController.Observation(
                "GET", "/bank-accounts/A-1", "/bank-accounts/{accountNumber}",
                "A-1", null, null, null, null, "2025-02-03T10:15:30Z"));

        assertEquals(200, receipt.status());
        assertEquals(Map.of("accountNumber", "A-1", "fullName", "Source Holder",
                "balance", 41, "score", "B"), receipt.body());
    }

    @Test
    void derivesMissingGetAndExactNativeCustomerErrorIndependently() {
        when(source.find("missing")).thenReturn(Optional.empty());
        var missing = controller.observe(new CutoverSourceObservationController.Observation(
                "GET", "/bank-accounts/missing", "/bank-accounts/{accountNumber}",
                "missing", null, null, null, null, "2025-02-03T10:15:30Z"));
        assertEquals(404, missing.status());
        assertNull(missing.body());

        when(customers.isBlacklisted("999")).thenReturn(false);
        when(identities.exists("999")).thenReturn(false);
        var rejected = controller.observe(new CutoverSourceObservationController.Observation(
                "POST", "/bank-accounts", "/bank-accounts", null, null, null, null,
                Map.of("nationalIdentityNumber", "999", "firstName", "No", "lastName", "Customer",
                        "balance", 0), "2025-02-03T10:15:30Z"));
        assertEquals(500, rejected.status());
        assertEquals(Map.of("timestamp", Date.from(Instant.parse("2025-02-03T10:15:30Z")),
                "status", 500, "error", "Internal Server Error", "path", "/bank-accounts"),
                rejected.body());
    }
    @Test
    void invalidAmountPrecedesMissingAccountLikeThePublicUseCase() {
        when(source.find("missing")).thenReturn(Optional.empty());

        var receipt = controller.observe(new CutoverSourceObservationController.Observation(
                "POST", "/bank-accounts/missing/withdrawals",
                "/bank-accounts/{accountNumber}/withdrawals",
                "missing", 0, "invalid-op", "invalid-op",
                Map.of("amount", 0, "operationId", "invalid-op"),
                "2025-02-03T10:15:30Z"));

        assertEquals(400, receipt.status());
        assertNull(receipt.body());
    }

}
