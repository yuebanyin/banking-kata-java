package com.optivem.kata.banking.cutover;

import com.optivem.kata.banking.adapter.driven.base.ProfileNames;
import com.optivem.kata.banking.adapter.driven.persistence.jpa.JpaBankAccountStorage;
import com.optivem.kata.banking.core.ports.driven.BankAccountDto;
import org.springframework.context.annotation.Profile;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.time.LocalDate;

/** Source seeding is available only while the live PostgreSQL cutover graph is active. */
@RestController
@RequestMapping("/internal/cutover/fixtures")
@Profile(ProfileNames.ACCOUNT_CUTOVER + " & !" + ProfileNames.TARGET_ONLY)
public class CutoverFixtureController {
    private final JpaBankAccountStorage sourceStorage;

    public CutoverFixtureController(JpaBankAccountStorage sourceStorage) {
        this.sourceStorage = sourceStorage;
    }

    public record SourceAccountSeed(long accountId, String accountNumber,
                                    String nationalIdentityNumber, String firstName,
                                    String lastName, String openingDate, int balance) {}

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
}
