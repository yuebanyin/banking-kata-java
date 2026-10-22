package com.optivem.kata.banking.cutover;

import com.optivem.kata.banking.adapter.driven.base.ProfileNames;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.context.annotation.Profile;

import java.time.LocalDateTime;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class CutoverWorkloadFixtureControllerTest {
    @SuppressWarnings("unchecked")
    @Test
    void targetOnlyCreationTruthUsesNoPersistenceCollaborator() {
        ObjectProvider identities = mock(ObjectProvider.class);
        ObjectProvider customers = mock(ObjectProvider.class);
        WorkloadAccountNumberGenerator numbers = mock(WorkloadAccountNumberGenerator.class);
        WorkloadDateTimeService clock = mock(WorkloadDateTimeService.class);
        when(clock.now()).thenReturn(LocalDateTime.of(2025, 1, 1, 0, 0));
        var controller = new CutoverWorkloadFixtureController(
                identities, customers, numbers, clock);

        var response = controller.creationTruth("TARGET-42", "HELDOUT-ID", 0, "2025-01-01T00:00:00Z",
                        "John", "Smith");

        assertEquals(200, response.getStatusCodeValue());
        assertNotNull(response.getBody());
        assertEquals("fixture_creation_truth", response.getBody().get("collectorKind"));
        assertEquals("TARGET-42", response.getBody().get("accountNumber"));
        assertEquals("HELDOUT-ID", response.getBody().get("nationalIdentityNumber"));
        assertEquals(0, response.getBody().get("initialBalance"));
        assertEquals("2025-01-01T00:00:00Z", response.getBody().get("controlledClock"));
        assertEquals("John", response.getBody().get("firstName"));
        assertEquals("Smith", response.getBody().get("lastName"));
        assertEquals("John Smith", response.getBody().get("fullName"));
        assertEquals(0, response.getBody().get("balance"));
        assertEquals("B", response.getBody().get("score").toString());
        verifyNoInteractions(identities, customers, numbers);
    }

    @Test
    void fixtureControllerIsAvailableInAccountCutoverIncludingTargetOnly() {
        Profile profile = CutoverWorkloadFixtureController.class.getAnnotation(Profile.class);
        assertNotNull(profile);
        assertArrayEquals(new String[]{ProfileNames.ACCOUNT_CUTOVER}, profile.value());
    }
}
