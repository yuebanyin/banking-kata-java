package com.optivem.kata.banking.adapter.driver.restapi.spring.controllers;

import an.awesome.pipelinr.Pipeline;
import com.optivem.kata.banking.core.internal.cleanarch.domain.scoring.Score;
import com.optivem.kata.banking.core.ports.driven.AccountProjectionWriter;
import com.optivem.kata.banking.core.ports.driver.accounts.viewaccount.ViewAccountRequest;
import com.optivem.kata.banking.core.ports.driver.accounts.viewaccount.ViewAccountResponse;
import org.junit.jupiter.api.Test;

import java.lang.reflect.Field;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class BankAccountControllerProjectionTest {
    @Test
    void public_get_persists_the_recalculated_score_projection() throws Exception {
        Pipeline pipeline = mock(Pipeline.class);
        AccountProjectionWriter projection = mock(AccountProjectionWriter.class);
        var response = ViewAccountResponse.builder()
                .accountNumber("ACC-1")
                .fullName("John Smith")
                .balance(1)
                .score(Score.A)
                .build();
        when(pipeline.send(any(ViewAccountRequest.class))).thenReturn(response);

        var controller = new TestController(pipeline);
        Field writer = BankAccountController.class.getDeclaredField("projectionWriter");
        writer.setAccessible(true);
        writer.set(controller, projection);

        var result = controller.viewAccount("ACC-1");

        assertEquals(response, result.getBody());
        verify(projection).updateObservedScore("ACC-1", "A");
    }

    private static final class TestController extends BankAccountController {
        private TestController(Pipeline pipeline) {
            this.pipeline = pipeline;
        }
    }
}
