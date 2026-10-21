package com.optivem.kata.banking.cutover;

import com.optivem.kata.banking.adapter.driven.base.ProfileNames;
import com.optivem.kata.banking.adapter.driven.persistence.jpa.JpaBankAccountStorage;
import com.optivem.kata.banking.core.ports.driven.BankAccountDto;
import org.springframework.context.annotation.Profile;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

import java.util.Map;
import java.util.Objects;
import java.util.Set;

/**
 * Produces a read-only response receipt from PostgreSQL after one public request.
 *
 * The cutover runner must not replay a deposit, withdrawal, or creation against a
 * second route just to manufacture a comparison.  This endpoint proves the source
 * commit (or source-visible domain failure) directly and never mutates an account.
 */
@RestController
@RequestMapping("/internal/evidence")
@Profile(ProfileNames.ACCOUNT_CUTOVER + " & " + ProfileNames.ADAPTER_PERSISTENCE_JPA
        + " & !" + ProfileNames.TARGET_ONLY)
public class CutoverSourceObservationController {
    private final JpaBankAccountStorage source;

    public CutoverSourceObservationController(JpaBankAccountStorage source) {
        this.source = source;
    }

    @PostMapping("/source-observation")
    public Receipt observe(@RequestBody Observation request) {
        if (!sourceSupports(request)) {
            throw new ResponseStatusException(HttpStatus.CONFLICT,
                    "candidate result is not supported by PostgreSQL source state");
        }
        return new Receipt(request.candidateStatus(), request.candidateBody());
    }

    private boolean sourceSupports(Observation request) {
        int status = request.candidateStatus();
        if ("GET".equals(request.method())) {
            var account = source.find(request.requestAccountNumber());
            if (status == 404) return account.isEmpty();
            if (status != 200 || account.isEmpty() || !(request.candidateBody() instanceof Map)) return false;
            Map<?, ?> body = (Map<?, ?>) request.candidateBody();
            var row = account.get();
            return Objects.equals(row.getAccountNumber(), body.get("accountNumber"))
                    && Objects.equals(row.getFirstName() + " " + row.getLastName(), body.get("fullName"))
                    && Objects.equals(String.valueOf(row.getBalance()), String.valueOf(body.get("balance")));
        }

        if (!"POST".equals(request.method())) return false;
        if (request.routeTemplate() != null && request.routeTemplate().endsWith("/bank-accounts")) {
            if (status == 201 && request.candidateBody() instanceof Map) {
                Object number = ((Map<?, ?>) request.candidateBody()).get("accountNumber");
                return number != null && source.find(String.valueOf(number)).isPresent();
            }
            // Customer-service rejections intentionally have no account row or operation marker.
            return status == 400 || status == 404 || status == 409;
        }

        var account = source.find(request.requestAccountNumber());
        if (status == 404) return account.isEmpty();
        if (status == 400 || status == 409) return account.isPresent();
        if (status != 204 || account.isEmpty() || request.requestOperationId() == null) return false;
        return source.findRecoverableOperations(Set.of(request.requestOperationId())).stream()
                .anyMatch(operation -> operation.operationId().equals(request.requestOperationId())
                        && operation.account().getAccountNumber().equals(request.requestAccountNumber()));
    }

    public record Observation(String method, String path, String routeTemplate,
                              String requestAccountNumber, Integer requestAmount,
                              String requestOperationId, String requestIdempotencyKey,
                              int candidateStatus, Object candidateBody) { }

    public record Receipt(int status, Object body) { }
}
