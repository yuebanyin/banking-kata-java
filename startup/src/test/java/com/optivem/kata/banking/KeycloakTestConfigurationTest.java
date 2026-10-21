package com.optivem.kata.banking;

import org.junit.jupiter.api.Test;
import org.springframework.boot.env.YamlPropertySourceLoader;
import org.springframework.core.env.StandardEnvironment;
import org.springframework.core.io.ClassPathResource;

import static org.assertj.core.api.Assertions.assertThat;

class KeycloakTestConfigurationTest {

    @Test
    void systemTestsHaveALocalKeycloakDefault() throws Exception {
        var environment = new StandardEnvironment();
        var sources = new YamlPropertySourceLoader().load(
                "keycloak-test", new ClassPathResource("application-adapter-auth-keycloak.yml"));
        environment.getPropertySources().addFirst(sources.get(0));

        assertThat(environment.getProperty(
                "spring.security.oauth2.resourceserver.jwt.issuer-uri"))
                .isEqualTo("http://localhost:10000/auth/realms/banking-kata");
        assertThat(environment.getProperty("token-uri"))
                .isEqualTo("http://localhost:10000/auth/realms/banking-kata/protocol/openid-connect/token");
        assertThat(environment.getProperty("client-id")).isEqualTo("test-client");
        assertThat(environment.getProperty("client-secret")).isNotBlank();
    }
}
