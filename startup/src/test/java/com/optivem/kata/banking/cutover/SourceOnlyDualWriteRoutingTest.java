package com.optivem.kata.banking.cutover;

import com.optivem.kata.banking.adapter.driven.persistence.jpa.JpaBankAccountStorage;
import com.optivem.kata.banking.adapter.driven.persistence.mongo.MongoBankAccountStorage;
import com.optivem.kata.banking.core.ports.driven.AccountStorageRoute;
import com.optivem.kata.banking.core.ports.driven.BankAccountStorage;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;

import java.lang.reflect.Constructor;
import java.util.Arrays;
import java.util.Optional;

import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

class SourceOnlyDualWriteRoutingTest {
    @AfterEach
    void clearRoute() {
        AccountStorageRoute.clear();
    }

    @Test
    void baselinePublicRequestReadsPostgresWithoutTouchingMongo() throws Exception {
        Constructor<?> constructor = Arrays.stream(DualWriteBankAccountStorage.class.getDeclaredConstructors())
                .max((left, right) -> Integer.compare(left.getParameterCount(), right.getParameterCount()))
                .orElseThrow();
        Object[] dependencies = Arrays.stream(constructor.getParameterTypes())
                .map(type -> mock(type))
                .toArray();
        var source = dependency(constructor, dependencies, JpaBankAccountStorage.class);
        var target = dependency(constructor, dependencies, MongoBankAccountStorage.class);
        when(source.find("source-account")).thenReturn(Optional.empty());
        var storage = (BankAccountStorage) constructor.newInstance(dependencies);

        AccountStorageRoute.useSourceOnly();
        storage.find("source-account");

        verify(source).find("source-account");
        verifyNoInteractions(target);
    }

    private static <T> T dependency(Constructor<?> constructor, Object[] dependencies, Class<T> type) {
        Class<?>[] types = constructor.getParameterTypes();
        for (int index = 0; index < types.length; index++) {
            if (types[index].equals(type)) return type.cast(dependencies[index]);
        }
        throw new AssertionError("missing dependency " + type.getName());
    }
}
