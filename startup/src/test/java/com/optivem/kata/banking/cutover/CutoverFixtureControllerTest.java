package com.optivem.kata.banking.cutover;

import com.optivem.kata.banking.adapter.driven.base.ProfileNames;
import com.optivem.kata.banking.adapter.driven.persistence.jpa.JpaBankAccountStorage;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.context.annotation.Profile;

import java.time.LocalDateTime;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class CutoverFixtureControllerTest {
    @SuppressWarnings("unchecked")
    @Test
    void targetOnlyCreationTruthDoesNotTouchEitherPersistenceAdapter() {
        ObjectProvider identities = mock(ObjectProvider.class);
        ObjectProvider customers = mock(ObjectProvider.class);
        ObjectProvider<JpaBankAccountStorage> source = mock(ObjectProvider.class);
        WorkloadAccountNumberGenerator numbers = mock(WorkloadAccountNumberGenerator.class);
        WorkloadDateTimeService clock = mock(WorkloadDateTimeService.class);
        when(clock.now()).thenReturn(LocalDateTime.of(2025, 1, 1, 0, 0));
        var controller = new CutoverFixtureController(
                identities, customers, numbers, clock, source);

        var response = controller.creationTruth("TARGET-42", "HELDOUT-ID", 0, "2025-01-01T00:00:00Z");

        assertEquals(200, response.getStatusCodeValue());
        assertNotNull(response.getBody());
        assertEquals("fixture_creation_truth", response.getBody().get("collectorKind"));
        assertEquals("TARGET-42", response.getBody().get("accountNumber"));
        assertEquals("HELDOUT-ID", response.getBody().get("nationalIdentityNumber"));
        assertEquals(0, response.getBody().get("initialBalance"));
        assertEquals("2025-01-01T00:00:00Z", response.getBody().get("controlledClock"));
        assertEquals("John Smith", response.getBody().get("fullName"));
        assertEquals(0, response.getBody().get("balance"));
        assertTrue(response.getBody().get("score").toString().matches("A|B|C"));
        verifyNoInteractions(source, identities, customers, numbers);
    }

    @Test
    void fixtureControllerIsAvailableInAccountCutoverIncludingTargetOnly() {
        Profile profile = CutoverFixtureController.class.getAnnotation(Profile.class);
        assertNotNull(profile);
        assertArrayEquals(new String[]{ProfileNames.ACCOUNT_CUTOVER}, profile.value());
    }
}
