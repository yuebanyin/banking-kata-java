package com.optivem.kata.banking.cutover;

import com.optivem.kata.banking.adapter.driven.base.ProfileNames;
import com.optivem.kata.banking.adapter.driven.persistence.jpa.JpaBankAccountStorage;
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

/** Controlled collaborators used only by repeatable cutover workloads. */
@RestController
@RequestMapping("/internal/cutover/fixtures")
@Profile(ProfileNames.ACCOUNT_CUTOVER)
public class CutoverFixtureController {
    private final ObjectProvider<FakeNationalIdentityGateway> identities;
    private final ObjectProvider<FakeCustomerGateway> customers;
    private final WorkloadAccountNumberGenerator accountNumbers;
    private final WorkloadDateTimeService clock;
    private final JpaBankAccountStorage sourceStorage;

    public CutoverFixtureController(ObjectProvider<FakeNationalIdentityGateway> identities,
                                    ObjectProvider<FakeCustomerGateway> customers,
                                    WorkloadAccountNumberGenerator accountNumbers,
                                    WorkloadDateTimeService clock,
                                    JpaBankAccountStorage sourceStorage) {
        this.identities = identities;
        this.customers = customers;
        this.accountNumbers = accountNumbers;
        this.clock = clock;
        this.sourceStorage = sourceStorage;
    }

    public record SourceAccountSeed(long accountId, String accountNumber,
                                    String nationalIdentityNumber, String firstName,
                                    String lastName, String openingDate, int balance) {}

    /** Seeds a real source-only row so backfill is exercised rather than pre-populating MongoDB. */
    @PostMapping("/source-accounts")
    public ResponseEntity<Void> sourceAccount(@RequestBody SourceAccountSeed seed) {
        var account = new BankAccountDto();
        account.setAccountId(seed.accountId());
        account.setAccountNumber(seed.accountNumber());
        account.setNationalIdentityNumber(seed.nationalIdentityNumber());
        account.setFirstName(seed.firstName());
        account.setLastName(seed.lastName());
        account.setOpeningDate(LocalDate.parse(seed.openingDate()));
        account.setBalance(seed.balance());
        sourceStorage.upsert(account);
        return ResponseEntity.noContent().build();
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
