package com.optivem.kata.banking.cutover;

import com.optivem.kata.banking.adapter.driven.base.ProfileNames;
import com.optivem.kata.banking.core.internal.cleanarch.acl.BankAccountConverter;
import com.optivem.kata.banking.core.internal.cleanarch.domain.scoring.DefaultScoreCalculator;
import com.optivem.kata.banking.core.ports.driven.BankAccountDto;
import com.optivem.kata.banking.adapter.driven.microservice.fake.FakeCustomerGateway;
import com.optivem.kata.banking.adapter.driven.thirdparty.fake.FakeNationalIdentityGateway;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.context.annotation.Profile;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.OffsetDateTime;
import java.util.LinkedHashMap;
import java.util.Map;

/** Controlled collaborators used only by repeatable cutover workloads. */
@RestController
@RequestMapping("/internal/cutover/fixtures")
@Profile(ProfileNames.ACCOUNT_CUTOVER)
public class CutoverWorkloadFixtureController {
    private final ObjectProvider<FakeNationalIdentityGateway> identities;
    private final ObjectProvider<FakeCustomerGateway> customers;
    private final WorkloadAccountNumberGenerator accountNumbers;
    private final WorkloadDateTimeService clock;

    public CutoverWorkloadFixtureController(ObjectProvider<FakeNationalIdentityGateway> identities,
                                    ObjectProvider<FakeCustomerGateway> customers,
                                    WorkloadAccountNumberGenerator accountNumbers,
                                    WorkloadDateTimeService clock) {
        this.identities = identities;
        this.customers = customers;
        this.accountNumbers = accountNumbers;
        this.clock = clock;
    }


    /**
     * Derives creation truth solely from controlled customer/scoring inputs.
     * It never reads either persistence adapter, so it remains usable after
     * PostgreSQL has been denied to the replacement container.
     */
    @GetMapping("/creation-truth/{accountNumber}")
    public ResponseEntity<Map<String, Object>> creationTruth(
            @PathVariable String accountNumber,
            @RequestParam String nationalIdentityNumber,
            @RequestParam(defaultValue = "0") int balance,
            @RequestParam String controlledClock,
            @RequestParam String firstName,
            @RequestParam String lastName) {
        LocalDateTime expectedClock;
        try { expectedClock = OffsetDateTime.parse(controlledClock).toLocalDateTime(); }
        catch (RuntimeException ignored) { expectedClock = LocalDateTime.parse(controlledClock); }
        if (!clock.now().equals(expectedClock)) return ResponseEntity.badRequest().build();

        var account = new BankAccountDto();
        account.setAccountId(1L);
        account.setAccountNumber(accountNumber);
        account.setNationalIdentityNumber(nationalIdentityNumber);
        account.setFirstName(firstName);
        account.setLastName(lastName);
        account.setOpeningDate(clock.now().toLocalDate());
        account.setBalance(balance);

        var body = new LinkedHashMap<String, Object>();
        body.put("collectorKind", "fixture_creation_truth");
        body.put("accountNumber", accountNumber);
        body.put("nationalIdentityNumber", nationalIdentityNumber);
        body.put("initialBalance", balance);
        body.put("controlledClock", controlledClock);
        body.put("firstName", firstName);
        body.put("lastName", lastName);
        body.put("fullName", firstName + " " + lastName);
        body.put("balance", balance);
        body.put("score", DefaultScoreCalculator.create(clock)
                .calculate(BankAccountConverter.toEntity(account)).toString());
        return ResponseEntity.ok(body);
    }

    @PostMapping("/account-numbers/{accountNumber}")
    public ResponseEntity<Void> accountNumber(@PathVariable String accountNumber) {
        accountNumbers.enqueue(accountNumber);
        return ResponseEntity.noContent().build();
    }

    @PostMapping("/clock")
    public ResponseEntity<Void> clock(@RequestParam String value) {
        try { clock.set(OffsetDateTime.parse(value).toLocalDateTime()); }
        catch (RuntimeException ignored) { clock.set(LocalDateTime.parse(value)); }
        return ResponseEntity.noContent().build();
    }

    @PostMapping("/{nationalIdentityNumber}")
    public ResponseEntity<Void> configure(@PathVariable String nationalIdentityNumber,
                                          @RequestParam String customerCase) {
        FakeNationalIdentityGateway identity = identities.getIfAvailable();
        FakeCustomerGateway customer = customers.getIfAvailable();
        if (identity == null || customer == null) return ResponseEntity.notFound().build();
        if (!"missing".equals(customerCase)) identity.setupExistent(nationalIdentityNumber);
        if ("blacklisted".equals(customerCase)) customer.setupBlacklisted(nationalIdentityNumber);
        else customer.setupWhitelisted(nationalIdentityNumber);
        return ResponseEntity.noContent().build();
    }
}
