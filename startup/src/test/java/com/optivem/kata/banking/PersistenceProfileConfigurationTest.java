package com.optivem.kata.banking;

import com.optivem.kata.banking.adapter.driven.base.ProfileNames;
import com.optivem.kata.banking.adapter.driven.persistence.mongo.MongoBankAccountStorage;
import com.optivem.kata.banking.adapter.driven.persistence.jpa.configuration.JpaPersistenceConfiguration;
import com.optivem.kata.banking.cutover.AccountCutoverCoordinator;
import com.optivem.kata.banking.cutover.AccountOperationRecoveryLifecycle;
import com.optivem.kata.banking.cutover.CutoverAdminController;
import com.optivem.kata.banking.cutover.CutoverFixtureController;
import com.optivem.kata.banking.cutover.DualWriteBankAccountStorage;
import org.junit.jupiter.api.Test;
import org.springframework.context.annotation.AnnotationConfigApplicationContext;

import static org.assertj.core.api.Assertions.assertThat;

class PersistenceProfileConfigurationTest {

    @Test
    void explicitlyRegistersJpaRepositoriesInMultiStoreApplication() {
        try (var context = lazyContext(ProfileNames.ADAPTER_PERSISTENCE_JPA)) {
            context.register(JpaPersistenceConfiguration.class);
            context.refresh();

            assertThat(context.containsBeanDefinition("jpaBankAccountDataAccessor")).isTrue();
            assertThat(context.containsBeanDefinition("jpaSourceAccountOperationDataAccessor")).isTrue();
        }
    }

    @Test
    void targetOnlyProfileSelectsMongoWithoutSourceCutoverGraph() {
        try (var context = lazyContext(
                ProfileNames.ACCOUNT_CUTOVER,
                ProfileNames.ADAPTER_PERSISTENCE_MONGO,
                ProfileNames.TARGET_ONLY)) {
            context.register(
                    MongoBankAccountStorage.class,
                    DualWriteBankAccountStorage.class,
                    AccountCutoverCoordinator.class,
                    AccountOperationRecoveryLifecycle.class,
                    CutoverAdminController.class,
                    CutoverFixtureController.class);
            context.refresh();

            assertThat(context.containsBeanDefinition("mongoBankAccountStorage")).isTrue();
            assertThat(context.containsBeanDefinition("dualWriteBankAccountStorage")).isFalse();
            assertThat(context.containsBeanDefinition("accountCutoverCoordinator")).isFalse();
            assertThat(context.containsBeanDefinition("accountOperationRecoveryLifecycle")).isFalse();
            assertThat(context.containsBeanDefinition("cutoverAdminController")).isFalse();
            assertThat(context.containsBeanDefinition("cutoverFixtureController")).isFalse();
        }
    }

    @Test
    void liveCutoverProfileStillEnablesDualWritePath() {
        try (var context = lazyContext(
                ProfileNames.ACCOUNT_CUTOVER,
                ProfileNames.ADAPTER_PERSISTENCE_JPA,
                ProfileNames.ADAPTER_PERSISTENCE_MONGO)) {
            context.register(MongoBankAccountStorage.class, DualWriteBankAccountStorage.class);
            context.refresh();

            assertThat(context.containsBeanDefinition("mongoBankAccountStorage")).isTrue();
            assertThat(context.containsBeanDefinition("dualWriteBankAccountStorage")).isTrue();
        }
    }

    private static AnnotationConfigApplicationContext lazyContext(String... profiles) {
        var context = new AnnotationConfigApplicationContext();
        context.getEnvironment().setActiveProfiles(profiles);
        context.addBeanFactoryPostProcessor(factory -> {
            for (String name : factory.getBeanDefinitionNames()) {
                factory.getBeanDefinition(name).setLazyInit(true);
            }
        });
        return context;
    }
}
