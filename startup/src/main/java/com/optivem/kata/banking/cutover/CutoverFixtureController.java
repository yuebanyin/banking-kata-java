package com.optivem.kata.banking.cutover;

import com.optivem.kata.banking.adapter.driven.base.ProfileNames;
import com.optivem.kata.banking.adapter.driven.microservice.fake.FakeCustomerGateway;
import com.optivem.kata.banking.adapter.driven.thirdparty.fake.FakeNationalIdentityGateway;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.context.annotation.Profile;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.time.LocalDateTime;
import java.time.OffsetDateTime;

/** Controlled collaborators used only by repeatable cutover workloads. */
@RestController
@RequestMapping("/internal/cutover/fixtures")
@Profile(ProfileNames.ACCOUNT_CUTOVER)
public class CutoverFixtureController {
    private final ObjectProvider<FakeNationalIdentityGateway> identities;
    private final ObjectProvider<FakeCustomerGateway> customers;
    private final WorkloadAccountNumberGenerator accountNumbers;
    private final WorkloadDateTimeService clock;

    public CutoverFixtureController(ObjectProvider<FakeNationalIdentityGateway> identities,
                                    ObjectProvider<FakeCustomerGateway> customers,
                                    WorkloadAccountNumberGenerator accountNumbers,
                                    WorkloadDateTimeService clock) {
        this.identities = identities;
        this.customers = customers;
        this.accountNumbers = accountNumbers;
        this.clock = clock;
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
