package com.optivem.kata.banking.adapter.driver.restapi.spring.controllers.requests;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class AccountMutationRequest {
    private int amount;
    private String operationId;
}
