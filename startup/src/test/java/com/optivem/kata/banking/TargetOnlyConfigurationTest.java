package com.optivem.kata.banking;

import org.junit.jupiter.api.Test;
import org.springframework.boot.env.YamlPropertySourceLoader;
import org.springframework.core.io.ClassPathResource;

import static org.assertj.core.api.Assertions.assertThat;

class TargetOnlyConfigurationTest {

    @Test
    void relationalAutoConfigurationIsExcludedOnlyByTargetOnlyProfile() throws Exception {
        var loader = new YamlPropertySourceLoader();
        var mongo = loader.load("mongo", new ClassPathResource(
                "application-adapter-persistence-mongo.yml")).get(0);
        var target = loader.load("target", new ClassPathResource(
                "application-target-only.yml")).get(0);

        assertThat(mongo.containsProperty("spring.autoconfigure.exclude")).isFalse();
        assertThat(target.getProperty("spring.autoconfigure.exclude[0]"))
                .isEqualTo("org.springframework.boot.autoconfigure.jdbc.DataSourceAutoConfiguration");
        assertThat(target.getProperty("spring.autoconfigure.exclude[1]"))
                .isEqualTo("org.springframework.boot.autoconfigure.orm.jpa.HibernateJpaAutoConfiguration");
        assertThat(target.getProperty("spring.autoconfigure.exclude[2]"))
                .isEqualTo("org.springframework.boot.autoconfigure.flyway.FlywayAutoConfiguration");
    }
}
