package com.optivem.kata.banking.cutover;

import com.optivem.kata.banking.adapter.driven.base.ProfileNames;
import com.optivem.kata.banking.adapter.driven.persistence.jpa.JpaBankAccountStorage;
import com.optivem.kata.banking.core.internal.cleanarch.acl.BankAccountConverter;
import com.optivem.kata.banking.core.internal.cleanarch.domain.scoring.ScoreCalculator;
import com.optivem.kata.banking.core.ports.driven.CustomerGateway;
import com.optivem.kata.banking.core.ports.driven.NationalIdentityGateway;
import org.springframework.context.annotation.Profile;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

import java.time.Instant;
import java.util.Date;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;

/**
 * Reconstructs the response of the PostgreSQL-backed public contract.
 *
 * This endpoint is deliberately read-only.  It receives request facts, never the
 * candidate response, and derives its receipt from JPA state, durable source
 * operation markers, and customer/domain rules.  Consequently a forged MongoDB
 * response cannot make a source/candidate pair agree.
 */
@RestController
@RequestMapping("/internal/evidence")
@Profile(ProfileNames.ACCOUNT_CUTOVER + " & " + ProfileNames.ADAPTER_PERSISTENCE_JPA
        + " & !" + ProfileNames.TARGET_ONLY)
public class CutoverSourceObservationController {
    private final JpaBankAccountStorage source;
    private final ScoreCalculator scoreCalculator;
    private final CustomerGateway customerGateway;
    private final NationalIdentityGateway nationalIdentityGateway;

    public CutoverSourceObservationController(JpaBankAccountStorage source,
                                               ScoreCalculator scoreCalculator,
                                               CustomerGateway customerGateway,
                                               NationalIdentityGateway nationalIdentityGateway) {
        this.source = source;
        this.scoreCalculator = scoreCalculator;
        this.customerGateway = customerGateway;
        this.nationalIdentityGateway = nationalIdentityGateway;
    }

    @PostMapping("/source-observation")
    public Receipt observe(@RequestBody Observation request) {
        if ("GET".equals(request.method())) return observeGet(request);
        if (!"POST".equals(request.method())) return unsupported("unsupported method");
        if ("/bank-accounts".equals(request.routeTemplate())) return observeCreate(request);
        if (request.routeTemplate() != null && request.routeTemplate().endsWith("/deposits")) {
            return observeMutation(request, "deposit");
        }
        if (request.routeTemplate() != null && request.routeTemplate().endsWith("/withdrawals")) {
            return observeMutation(request, "withdraw");
        }
        return unsupported("unsupported route");
    }

    private Receipt observeGet(Observation request) {
        var found = source.find(request.requestAccountNumber());
        if (found.isEmpty()) return new Receipt(404, null);
        var account = found.get();
        var body = new LinkedHashMap<String, Object>();
        body.put("accountNumber", account.getAccountNumber());
        body.put("fullName", account.getFirstName() + " " + account.getLastName());
        body.put("balance", account.getBalance());
        body.put("score", scoreCalculator.calculate(BankAccountConverter.toEntity(account)).toString());
        return new Receipt(200, body);
    }

    private Receipt observeCreate(Observation request) {
        String nationalId = stringValue(request.requestBody(), "nationalIdentityNumber");
        if (nationalId == null) return unsupported("creation national identity is missing");
        if (customerGateway.isBlacklisted(nationalId)
                || !nationalIdentityGateway.exists(nationalId)) {
            return new Receipt(500, nativeCustomerError(request.responseTimestamp()));
        }
        if (request.requestOperationId() == null) return unsupported("creation operation id is missing");
        return source.findRecoverableOperations(Set.of(request.requestOperationId())).stream()
                .filter(operation -> request.requestOperationId().equals(operation.operationId()))
                .filter(operation -> nationalId.equals(operation.account().getNationalIdentityNumber()))
                .findFirst()
                .map(operation -> {
                    var body = new LinkedHashMap<String, Object>();
                    body.put("accountNumber", operation.account().getAccountNumber());
                    return new Receipt(201, body);
                })
                .orElseGet(() -> unsupported("creation has no durable PostgreSQL operation"));
    }

    private Receipt observeMutation(Observation request, String kind) {
        // The public use cases construct TransactionAmount before loading the
        // account, so invalid amount wins over missing account.
        if (request.requestAmount() == null || request.requestAmount() <= 0) {
            return new Receipt(400, null);
        }
        var found = source.find(request.requestAccountNumber());
        if (found.isEmpty()) return new Receipt(404, null);
        if (request.requestOperationId() != null && source
                .findRecoverableOperations(Set.of(request.requestOperationId())).stream()
                .anyMatch(operation -> request.requestOperationId().equals(operation.operationId())
                        && request.requestAccountNumber().equals(operation.account().getAccountNumber()))) {
            return new Receipt(204, null);
        }
        if ("withdraw".equals(kind) && request.requestAmount() > found.get().getBalance()) {
            return new Receipt(409, null);
        }
        return unsupported("successful mutation has no durable PostgreSQL operation");
    }

    private static String stringValue(Map<String, Object> body, String key) {
        if (body == null || body.get(key) == null) return null;
        return String.valueOf(body.get(key));
    }

    private static Map<String, Object> nativeCustomerError(String timestamp) {
        var body = new LinkedHashMap<String, Object>();
        body.put("timestamp", Date.from(Instant.parse(timestamp)));
        body.put("status", 500);
        body.put("error", "Internal Server Error");
        body.put("path", "/bank-accounts");
        return body;
    }

    private static Receipt unsupported(String message) {
        throw new ResponseStatusException(HttpStatus.CONFLICT, message);
    }

    public record Observation(String method, String path, String routeTemplate,
                              String requestAccountNumber, Integer requestAmount,
                              String requestOperationId, String requestIdempotencyKey,
                              Map<String, Object> requestBody, String responseTimestamp) { }

    public record Receipt(int status, Object body) { }
}
