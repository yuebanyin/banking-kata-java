package com.optivem.kata.banking.adapter.driven.persistence.jpa.configuration;

import com.optivem.kata.banking.adapter.driven.base.ProfileNames;
import org.springframework.beans.factory.config.BeanFactoryPostProcessor;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Profile;

/**
 * Keeps JPA as the selected account store in legacy mixed JPA/Redis contexts.
 * During a live cutover the dual-write store is primary instead, so the source
 * JPA store must remain an unqualified delegate there.
 */
@Configuration(proxyBeanMethods = false)
@Profile(ProfileNames.ADAPTER_PERSISTENCE_JPA + " & !" + ProfileNames.ACCOUNT_CUTOVER)
public class JpaBankAccountStoragePrimaryConfiguration {
    static final String JPA_STORAGE_BEAN_NAME = "jpaBankAccountStorage";

    @Bean
    static BeanFactoryPostProcessor selectJpaBankAccountStorage() {
        return beanFactory -> beanFactory
                .getBeanDefinition(JPA_STORAGE_BEAN_NAME)
                .setPrimary(true);
    }
}
