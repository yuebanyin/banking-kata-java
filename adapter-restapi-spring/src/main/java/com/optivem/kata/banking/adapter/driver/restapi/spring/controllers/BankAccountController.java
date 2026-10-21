package com.optivem.kata.banking.adapter.driver.restapi.spring.controllers;

import com.optivem.kata.banking.adapter.driver.restapi.spring.controllers.base.BaseController;
import com.optivem.kata.banking.adapter.driver.restapi.spring.controllers.requests.AccountMutationRequest;
import com.optivem.kata.banking.core.ports.driven.AccountOperationContext;
import com.optivem.kata.banking.core.ports.driven.AccountOperationJournal;
import com.optivem.kata.banking.core.ports.driven.AccountOperationRecovery;
import com.optivem.kata.banking.core.ports.driven.AccountProjectionWriter;
import com.optivem.kata.banking.core.ports.driven.AccountStorageRoute;
import com.optivem.kata.banking.core.ports.driver.accounts.depositfunds.DepositFundsRequest;
import com.optivem.kata.banking.core.ports.driver.accounts.openaccount.OpenAccountRequest;
import com.optivem.kata.banking.core.ports.driver.accounts.openaccount.OpenAccountResponse;
import com.optivem.kata.banking.core.ports.driver.accounts.viewaccount.ViewAccountRequest;
import com.optivem.kata.banking.core.ports.driver.accounts.viewaccount.ViewAccountResponse;
import com.optivem.kata.banking.core.ports.driver.accounts.withdrawfunds.WithdrawFundsRequest;
import com.optivem.kata.banking.core.ports.driver.exceptions.ValidationException;
import com.optivem.kata.banking.core.ports.driver.exceptions.ValidationMessages;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.time.Instant;
import java.util.Date;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

@RestController
public class BankAccountController extends BaseController {
    private final ConcurrentHashMap<String, Object> accountLocks = new ConcurrentHashMap<>();

    @Autowired(required = false)
    private AccountOperationJournal journal;

    @Autowired(required = false)
    private AccountProjectionWriter projectionWriter;

    @Autowired(required = false)
    private AccountOperationRecovery operationRecovery;

    @PostMapping("/bank-accounts")
    public ResponseEntity<?> openAccount(
            @RequestBody OpenAccountRequest request,
            @RequestHeader(value = "X-Operation-Id", required = false) String operationId,
            @RequestHeader(value = "Idempotency-Key", required = false) String idempotencyKey,
            @RequestHeader(value = "X-Trace-Id", required = false) String traceId,
            @RequestHeader(value = "X-Evidence-Time", required = false) String evidenceTime) {
        try {
            return openAccountSuccess(request, operationId, idempotencyKey, traceId);
        } catch (ValidationException error) {
            if (!ValidationMessages.NATIONAL_IDENTITY_NUMBER_NONEXISTENT.equals(error.getMessage())
                    && !ValidationMessages.NATIONAL_IDENTITY_NUMBER_BLACKLISTED.equals(error.getMessage())) {
                throw error;
            }
            var body = new LinkedHashMap<String, Object>();
            body.put("timestamp", Date.from(evidenceTime == null
                    ? Instant.now() : Instant.parse(evidenceTime)));
            body.put("status", 500);
            body.put("error", "Internal Server Error");
            body.put("path", "/bank-accounts");
            return new ResponseEntity<>(body, HttpStatus.INTERNAL_SERVER_ERROR);
        }
    }

    private ResponseEntity<OpenAccountResponse> openAccountSuccess(
            OpenAccountRequest request, String operationId, String idempotencyKey, String traceId) {
        if (AccountStorageRoute.isSourceOnly()) {
            if (operationId == null) {
                return new ResponseEntity<>(pipeline.send(request), HttpStatus.CREATED);
            }
            String key = valueOr(idempotencyKey, operationId);
            String trace = valueOr(traceId, operationId);
            var operation = new AccountOperationContext.Operation(operationId, key, trace, "create");
            OpenAccountResponse response = AccountOperationContext.call(operation, () -> pipeline.send(request));
            return new ResponseEntity<>(response, HttpStatus.CREATED);
        }
        if (operationId == null || journal == null) {
            return new ResponseEntity<>(pipeline.send(request), HttpStatus.CREATED);
        }
        String key = valueOr(idempotencyKey, operationId);
        String trace = valueOr(traceId, operationId);
        long now = System.currentTimeMillis();
        var reserved = journal.reserve(operationId, key, trace, "create", "", 0, now);
        if ("duplicate_ignored".equals(reserved.result()) || "applied".equals(reserved.result())) {
            return new ResponseEntity<>(OpenAccountResponse.builder()
                    .accountNumber(reserved.accountNumber()).build(), HttpStatus.CREATED);
        }
        if (operationRecovery != null) {
            var recoveredAccount = operationRecovery.recover(operationId);
            if (recoveredAccount.isPresent()) {
                return new ResponseEntity<>(OpenAccountResponse.builder()
                        .accountNumber(recoveredAccount.get()).build(), HttpStatus.CREATED);
            }
        }
        var operation = new AccountOperationContext.Operation(operationId, key, trace, "create");
        OpenAccountResponse response = AccountOperationContext.call(operation, () -> pipeline.send(request));
        journal.complete(operationId, response.getAccountNumber(), "applied", true,
                System.currentTimeMillis());
        return new ResponseEntity<>(response, HttpStatus.CREATED);
    }

    @GetMapping("/bank-accounts/{accountNumber}")
    public ResponseEntity<ViewAccountResponse> viewAccount(@PathVariable String accountNumber) {
        var request = ViewAccountRequest.builder().accountNumber(accountNumber).build();
        try {
            ViewAccountResponse response = pipeline.send(request);
            if (projectionWriter != null) {
                projectionWriter.updateObservedScore(accountNumber, response.getScore().toString());
            }
            return new ResponseEntity<>(response, HttpStatus.OK);
        } catch (ValidationException ex) {
            if (ValidationMessages.ACCOUNT_NUMBER_NOT_EXIST.equals(ex.getMessage())) {
                return new ResponseEntity<>(HttpStatus.NOT_FOUND);
            }
            throw ex;
        }
    }

    @PostMapping("/bank-accounts/{accountNumber}/deposits")
    public ResponseEntity<Void> deposit(@PathVariable String accountNumber,
                                        @RequestBody AccountMutationRequest body,
                                        @RequestHeader(value = "Idempotency-Key", required = false) String headerKey,
                                        @RequestHeader(value = "X-Trace-Id", required = false) String traceId) {
        return mutate("deposit", accountNumber, body, headerKey, traceId);
    }

    @PostMapping("/bank-accounts/{accountNumber}/withdrawals")
    public ResponseEntity<Void> withdraw(@PathVariable String accountNumber,
                                         @RequestBody AccountMutationRequest body,
                                         @RequestHeader(value = "Idempotency-Key", required = false) String headerKey,
                                         @RequestHeader(value = "X-Trace-Id", required = false) String traceId) {
        return mutate("withdraw", accountNumber, body, headerKey, traceId);
    }

    private ResponseEntity<Void> mutate(String kind, String accountNumber, AccountMutationRequest body,
                                        String headerKey, String traceId) {
        String operationId = valueOr(body.getOperationId(), UUID.randomUUID().toString());
        String key = valueOr(headerKey, operationId);
        String trace = valueOr(traceId, operationId);
        long started = System.currentTimeMillis();
        AccountOperationJournal.Entry reserved = AccountStorageRoute.isSourceOnly() || journal == null ? null
                : journal.reserve(operationId, key, trace, kind, accountNumber, body.getAmount(), started);
        if (reserved != null && ("duplicate_ignored".equals(reserved.result())
                || "applied".equals(reserved.result()))) {
            return ResponseEntity.noContent().build();
        }
        if (reserved != null && operationRecovery != null
                && operationRecovery.recover(operationId).isPresent()) {
            return ResponseEntity.noContent().build();
        }

        synchronized (accountLocks.computeIfAbsent(accountNumber, ignored -> new Object())) {
            try {
                var context = new AccountOperationContext.Operation(operationId, key, trace, kind);
                AccountOperationContext.call(context, () -> {
                    if ("deposit".equals(kind)) {
                        pipeline.send(DepositFundsRequest.builder().accountNumber(accountNumber)
                                .amount(body.getAmount()).build());
                    } else {
                        pipeline.send(WithdrawFundsRequest.builder().accountNumber(accountNumber)
                                .amount(body.getAmount()).build());
                    }
                    return null;
                });
                complete(reserved, operationId, accountNumber, "applied", true);
                return ResponseEntity.noContent().build();
            } catch (ValidationException ex) {
                String result;
                HttpStatus status;
                if (ValidationMessages.ACCOUNT_NUMBER_NOT_EXIST.equals(ex.getMessage())) {
                    result = "account_not_found";
                    status = HttpStatus.NOT_FOUND;
                } else if (ValidationMessages.AMOUNT_NOT_POSITIVE.equals(ex.getMessage())) {
                    result = "invalid_amount";
                    status = HttpStatus.BAD_REQUEST;
                } else if (ValidationMessages.INSUFFICIENT_FUNDS.equals(ex.getMessage())) {
                    result = "insufficient_funds";
                    status = HttpStatus.CONFLICT;
                } else {
                    throw ex;
                }
                complete(reserved, operationId, accountNumber, result, false);
                return new ResponseEntity<>(status);
            }
        }
    }

    private void complete(AccountOperationJournal.Entry reserved, String operationId,
                          String accountNumber, String result, boolean acknowledged) {
        if (reserved != null) {
            journal.complete(operationId, accountNumber, result, acknowledged, System.currentTimeMillis());
        }
    }

    private static String valueOr(String value, String fallback) {
        return value == null || value.isBlank() ? fallback : value;
    }
}
