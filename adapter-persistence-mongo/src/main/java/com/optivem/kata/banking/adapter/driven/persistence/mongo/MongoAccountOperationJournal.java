package com.optivem.kata.banking.adapter.driven.persistence.mongo;

import com.optivem.kata.banking.adapter.driven.base.ProfileNames;
import com.optivem.kata.banking.adapter.driven.persistence.mongo.internal.MongoTemplateCustomDataAccessor;
import com.optivem.kata.banking.core.ports.driven.AccountOperationJournal;
import org.bson.Document;
import org.springframework.context.annotation.Profile;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.data.domain.Sort;
import org.springframework.data.mongodb.core.FindAndModifyOptions;
import org.springframework.data.mongodb.core.MongoTemplate;
import org.springframework.data.mongodb.core.index.Index;
import org.springframework.data.mongodb.core.query.Criteria;
import org.springframework.data.mongodb.core.query.Query;
import org.springframework.data.mongodb.core.query.Update;
import org.springframework.stereotype.Component;

import java.util.Comparator;
import java.util.List;
import java.util.Optional;
import java.util.stream.Collectors;

@Component
@Profile({ProfileNames.ADAPTER_PERSISTENCE_MONGO, ProfileNames.ACCOUNT_CUTOVER})
public class MongoAccountOperationJournal implements AccountOperationJournal {
    public static final String COLLECTION = "account_operation_journal";
    private final MongoTemplate mongo;
    private boolean indexesReady;

    public MongoAccountOperationJournal(MongoTemplateCustomDataAccessor accessor) {
        this.mongo = accessor.getMongoTemplate();
    }

    @Override
    public synchronized Entry reserve(String operationId, String idempotencyKey, String traceId,
                                      String kind, String accountNumber, int amount, long startedMs) {
        ensureIndexes();
        Optional<Entry> sameOperation = find(operationId);
        if (sameOperation.isPresent()) return sameOperation.get();
        Optional<Entry> prior = findAppliedByKey(idempotencyKey);
        long index = nextIndex();
        String result = prior.isPresent() ? "duplicate_ignored" : "pending";
        boolean acknowledged = prior.isPresent();
        long completed = prior.isPresent() ? System.currentTimeMillis() : 0L;
        String effectiveAccount = prior.map(Entry::accountNumber).orElse(accountNumber == null ? "" : accountNumber);
        Document row = new Document("_id", operationId)
                .append("operationId", operationId).append("idempotencyKey", idempotencyKey)
                .append("requestTraceId", traceId).append("kind", kind)
                .append("accountNumber", effectiveAccount).append("amount", amount)
                .append("startedMs", startedMs).append("completedMs", completed)
                .append("linearizationIndex", index).append("result", result)
                .append("acknowledged", acknowledged);
        try { mongo.insert(row, COLLECTION); }
        catch (DuplicateKeyException ignored) { return find(operationId).orElseThrow(); }
        return from(row);
    }

    @Override
    public synchronized Entry complete(String operationId, String accountNumber, String result,
                                       boolean acknowledged, long completedMs) {
        mongo.updateFirst(Query.query(Criteria.where("_id").is(operationId)),
                new Update().set("accountNumber", accountNumber == null ? "" : accountNumber)
                        .set("result", result).set("acknowledged", acknowledged)
                        .set("completedMs", completedMs), COLLECTION);
        return find(operationId).orElseThrow();
    }

    @Override
    public Optional<Entry> find(String operationId) {
        ensureIndexes();
        return Optional.ofNullable(mongo.findOne(Query.query(Criteria.where("_id").is(operationId)),
                Document.class, COLLECTION)).map(this::from);
    }

    @Override
    public List<Entry> findAll() {
        ensureIndexes();
        return mongo.findAll(Document.class, COLLECTION).stream().map(this::from)
                .sorted(Comparator.comparingLong(Entry::linearizationIndex)).collect(Collectors.toList());
    }

    private Optional<Entry> findAppliedByKey(String key) {
        if (key == null || key.isBlank()) return Optional.empty();
        Query query = Query.query(Criteria.where("idempotencyKey").is(key)
                .and("result").in("applied", "duplicate_ignored"));
        query.with(Sort.by(Sort.Direction.ASC, "linearizationIndex"));
        return Optional.ofNullable(mongo.findOne(query, Document.class, COLLECTION)).map(this::from);
    }

    private long nextIndex() {
        Document result = mongo.findAndModify(Query.query(Criteria.where("_id").is("journal")),
                new Update().inc("value", 1), FindAndModifyOptions.options().upsert(true).returnNew(true),
                Document.class, "cutover_counter");
        return ((Number) result.get("value")).longValue();
    }

    private synchronized void ensureIndexes() {
        if (indexesReady) return;
        mongo.indexOps(COLLECTION).ensureIndex(new Index().on("operationId", Sort.Direction.ASC).unique());
        mongo.indexOps(COLLECTION).ensureIndex(new Index().on("idempotencyKey", Sort.Direction.ASC));
        indexesReady = true;
    }

    private Entry from(Document d) {
        return new Entry(d.getString("operationId"), d.getString("idempotencyKey"),
                d.getString("requestTraceId"), d.getString("kind"), d.getString("accountNumber"),
                number(d, "amount"), longNumber(d, "startedMs"), longNumber(d, "completedMs"),
                longNumber(d, "linearizationIndex"), d.getString("result"),
                Boolean.TRUE.equals(d.getBoolean("acknowledged")));
    }

    private int number(Document d, String key) { return ((Number) d.getOrDefault(key, 0)).intValue(); }
    private long longNumber(Document d, String key) { return ((Number) d.getOrDefault(key, 0L)).longValue(); }
}
