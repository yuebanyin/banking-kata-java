package com.optivem.kata.banking.adapter.driven.persistence.jpa.configuration;

import com.optivem.kata.banking.adapter.driven.base.ProfileNames;
import com.optivem.kata.banking.adapter.driven.persistence.jpa.internal.BankAccountRecord;
import com.optivem.kata.banking.adapter.driven.persistence.jpa.internal.JpaBankAccountDataAccessor;
import com.optivem.kata.banking.adapter.driven.persistence.jpa.internal.JpaSourceAccountOperationDataAccessor;
import com.optivem.kata.banking.adapter.driven.persistence.jpa.internal.SourceAccountOperationRecord;
import org.springframework.boot.autoconfigure.domain.EntityScan;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Profile;
import org.springframework.data.jpa.repository.config.EnableJpaRepositories;

/** Assigns the JPA repositories explicitly when JPA and Mongo share a context. */
@Configuration(proxyBeanMethods = false)
@Profile(ProfileNames.ADAPTER_PERSISTENCE_JPA)
@EntityScan(basePackageClasses = {BankAccountRecord.class, SourceAccountOperationRecord.class})
@EnableJpaRepositories(basePackageClasses = {
        JpaBankAccountDataAccessor.class,
        JpaSourceAccountOperationDataAccessor.class
})
public class JpaPersistenceConfiguration {
}
