package com.optivem.kata.banking.adapter.driven.persistence.mongo;

import com.optivem.kata.banking.adapter.driven.base.ProfileNames;
import com.optivem.kata.banking.adapter.driven.persistence.mongo.internal.MongoTemplateCustomDataAccessor;
import com.optivem.kata.banking.core.ports.driven.AccountOperationContext;
import com.optivem.kata.banking.core.ports.driven.AccountProjectionWriter;
import com.optivem.kata.banking.core.ports.driven.BankAccountDto;
import com.optivem.kata.banking.core.ports.driven.BankAccountStorage;
import org.springframework.context.annotation.Profile;
import org.springframework.data.domain.Sort;
import org.springframework.data.mongodb.core.FindAndModifyOptions;
import org.springframework.data.mongodb.core.MongoTemplate;
import org.springframework.data.mongodb.core.index.Index;
import org.springframework.data.mongodb.core.query.Criteria;
import org.springframework.data.mongodb.core.query.Query;
import org.springframework.data.mongodb.core.query.Update;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.Optional;

@Component
@Profile({ProfileNames.ADAPTER_PERSISTENCE_MONGO, ProfileNames.ACCOUNT_CUTOVER})
public class MongoBankAccountStorage implements BankAccountStorage, AccountProjectionWriter {
    public static final String COLLECTION_NAME = "bank_account";
    public final String collectionName = COLLECTION_NAME;
    private final MongoTemplateCustomDataAccessor dataAccessor;
    private volatile boolean indexReady;

    public MongoBankAccountStorage(MongoTemplateCustomDataAccessor dataAccessor) {
        this.dataAccessor = dataAccessor;
    }

    @Override
    public Optional<BankAccountDto> find(String accountNumber) {
        ensureIndex();
        return Optional.ofNullable(template().findOne(
                Query.query(Criteria.where("accountNumber").is(accountNumber)),
                BankAccountDto.class, COLLECTION_NAME));
    }

    @Override
    public List<BankAccountDto> findAll() {
        ensureIndex();
        return template().findAll(BankAccountDto.class, COLLECTION_NAME);
    }

    @Override
    public void add(BankAccountDto bankAccount) { upsert(bankAccount); }

    @Override
    public void update(BankAccountDto bankAccount) { upsert(bankAccount); }

    @Override
    public void upsert(BankAccountDto account) {
        ensureIndex();
        Update update = new Update()
                .set("accountId", account.getAccountId())
                .set("accountNumber", account.getAccountNumber())
                .set("nationalIdentityNumber", account.getNationalIdentityNumber())
                .set("firstName", account.getFirstName())
                .set("lastName", account.getLastName())
                .set("fullName", account.getFirstName() + " " + account.getLastName())
                .set("openingDate", account.getOpeningDate())
                .set("balance", account.getBalance())
                .setOnInsert("score", "C");
        AccountOperationContext.current().ifPresent(operation ->
                update.addToSet("appliedOperationIds", operation.operationId()));
        template().findAndModify(
                Query.query(Criteria.where("accountNumber").is(account.getAccountNumber())),
                update,
                FindAndModifyOptions.options().upsert(true).returnNew(true),
                BankAccountDto.class,
                COLLECTION_NAME);
    }


    @Override
    public void updateObservedScore(String accountNumber, String score) {
        template().updateFirst(Query.query(Criteria.where("accountNumber").is(accountNumber)),
                new Update().set("score", score), COLLECTION_NAME);
    }

    private MongoTemplate template() { return dataAccessor.getMongoTemplate(); }

    private void ensureIndex() {
        if (indexReady) return;
        synchronized (this) {
            if (!indexReady) {
                template().indexOps(COLLECTION_NAME)
                        .ensureIndex(new Index().on("accountNumber", Sort.Direction.ASC).unique());
                indexReady = true;
            }
        }
    }
}
