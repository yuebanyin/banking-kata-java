package com.optivem.kata.banking.startup;

import com.optivem.kata.banking.BankingApplication;
import com.optivem.kata.banking.adapter.driven.base.ProfileNames;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.net.BindException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.web.server.LocalServerPort;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.ContextConfiguration;

import static org.assertj.core.api.Assertions.assertThat;

@SpringBootTest(classes = BankingApplication.class, webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@ActiveProfiles({ ProfileNames.ADAPTER_PERSISTENCE_JPA, ProfileNames.ADAPTER_PERSISTENCE_REDIS, ProfileNames.ADAPTER_GENERATION_RANDOM, ProfileNames.ADAPTER_TIME_SYSTEM, ProfileNames.ADAPTER_AUTH_KEYCLOAK, ProfileNames.ADAPTER_MICROSERVICE_SIM, ProfileNames.ADAPTER_THIRDPARTY_SIM})
@ContextConfiguration
class BankingApplicationTests {

    private static final int COMPOSE_APPLICATION_PORT = 8080;
    private static final ServerSocket COMPOSE_PORT_RESERVATION = reserveComposePortIfAvailable();

    @LocalServerPort
    private int testApplicationPort;

    @Test
    void contextLoadsOnRandomPortWhileComposePortIsOccupied() {
        // Port 8080 is held either by this test or by the running Compose app before this context starts.
        assertThat(testApplicationPort)
            .isPositive()
            .isNotEqualTo(COMPOSE_APPLICATION_PORT);
    }

    @AfterAll
    static void releaseComposePortReservation() throws IOException {
        if (COMPOSE_PORT_RESERVATION != null) {
            COMPOSE_PORT_RESERVATION.close();
        }
    }

    private static ServerSocket reserveComposePortIfAvailable() {
        ServerSocket socket = null;
        try {
            socket = new ServerSocket();
            socket.setReuseAddress(false);
            socket.bind(new InetSocketAddress(InetAddress.getLoopbackAddress(), COMPOSE_APPLICATION_PORT));
            return socket;
        } catch (BindException portAlreadyOccupied) {
            closeAfterBindFailure(socket, portAlreadyOccupied);
            return null;
        } catch (IOException bindFailure) {
            closeAfterBindFailure(socket, bindFailure);
            throw new UncheckedIOException(bindFailure);
        }
    }

    private static void closeAfterBindFailure(ServerSocket socket, IOException bindFailure) {
        if (socket == null) {
            return;
        }
        try {
            socket.close();
        } catch (IOException closeFailure) {
            bindFailure.addSuppressed(closeFailure);
        }
    }
}
