package com.optivem.kata.banking.cutover;

import com.optivem.kata.banking.adapter.driven.base.ProfileNames;
import com.optivem.kata.banking.adapter.driven.microservice.fake.FakeCustomerGatewaySimulator;
import com.optivem.kata.banking.adapter.driven.thirdparty.fake.FakeNationalIdentityGatewaySimulator;
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

    @SuppressWarnings("unchecked")
    @Test
    void customerConfigurationControlsTheActiveSimulatorBeans() {
        ObjectProvider<FakeNationalIdentityGatewaySimulator> identities = mock(ObjectProvider.class);
        ObjectProvider<FakeCustomerGatewaySimulator> customers = mock(ObjectProvider.class);
        FakeNationalIdentityGatewaySimulator identity = mock(FakeNationalIdentityGatewaySimulator.class);
        FakeCustomerGatewaySimulator customer = mock(FakeCustomerGatewaySimulator.class);
        when(identities.getIfAvailable()).thenReturn(identity);
        when(customers.getIfAvailable()).thenReturn(customer);
        var controller = new CutoverWorkloadFixtureController(
                identities, customers, mock(WorkloadAccountNumberGenerator.class),
                mock(WorkloadDateTimeService.class));

        var missing = controller.configure("00000042030", "missing");
        var eligible = controller.configure("00000042031", "eligible");
        var blacklisted = controller.configure("00000042032", "blacklisted");

        assertEquals(204, missing.getStatusCodeValue());
        assertEquals(204, eligible.getStatusCodeValue());
        assertEquals(204, blacklisted.getStatusCodeValue());
        verify(identity, never()).setupExistent("00000042030");
        verify(identity).setupExistent("00000042031");
        verify(identity).setupExistent("00000042032");
        verify(customer).setupWhitelisted("00000042030");
        verify(customer).setupWhitelisted("00000042031");
        verify(customer).setupBlacklisted("00000042032");
    }

    @Test
    void fixtureControllerIsAvailableInAccountCutoverIncludingTargetOnly() {
        Profile profile = CutoverWorkloadFixtureController.class.getAnnotation(Profile.class);
        assertNotNull(profile);
        assertArrayEquals(new String[]{ProfileNames.ACCOUNT_CUTOVER}, profile.value());
    }
}
