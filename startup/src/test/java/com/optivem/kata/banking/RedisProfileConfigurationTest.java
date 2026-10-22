package com.optivem.kata.banking;

import org.junit.jupiter.api.Test;
import org.springframework.boot.env.YamlPropertySourceLoader;
import org.springframework.core.env.MutablePropertySources;
import org.springframework.core.env.PropertySourcesPropertyResolver;
import org.springframework.core.io.ClassPathResource;

import java.io.IOException;

import static org.assertj.core.api.Assertions.assertThat;

class RedisProfileConfigurationTest {

    @Test
    void redisProfileHasResolvableComposeCompatibleDefaults() throws IOException {
        var yaml = new YamlPropertySourceLoader().load(
                "redis", new ClassPathResource("application-adapter-persistence-redis.yml")).get(0);
        var sources = new MutablePropertySources();
        sources.addFirst(yaml);
        var resolver = new PropertySourcesPropertyResolver(sources);

        assertThat(resolver.getRequiredProperty("spring.redis.host")).isEqualTo("localhost");
        assertThat(resolver.getRequiredProperty("spring.redis.port")).isEqualTo("6379");
        assertThat(resolver.getRequiredProperty("spring.redis.password"))
                .isEqualTo("b4EA2xC6LlKnlizPu9bwRRrQM");
    }
}
