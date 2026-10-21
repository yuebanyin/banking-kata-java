package com.optivem.kata.banking.adapter.driven.persistence.mongo.internal;

import com.mongodb.client.MongoClient;
import com.mongodb.client.MongoClients;
import com.mongodb.client.MongoDatabase;
import com.optivem.kata.banking.adapter.driven.base.ProfileNames;
import org.springframework.context.annotation.Profile;
import org.springframework.data.mongodb.core.MongoTemplate;
import org.springframework.stereotype.Component;

@Component
@Profile({ProfileNames.ADAPTER_PERSISTENCE_MONGO, ProfileNames.ACCOUNT_CUTOVER})
public class MongoTemplateCustomDataAccessor {
    private final MongoClient mongoClient;
    private final MongoTemplate mongoTemplate;

    public MongoTemplateCustomDataAccessor() {
        String uri = environment("MONGODB_URI", "mongodb://localhost:27017");
        String databaseName = environment("MONGODB_DATABASE_NAME", "banking_kata_db");
        mongoClient = MongoClients.create(uri);
        MongoDatabase database = mongoClient.getDatabase(databaseName);
        mongoTemplate = new MongoTemplate(mongoClient, database.getName());
    }

    public MongoTemplate getMongoTemplate() { return mongoTemplate; }
    public void close() { mongoClient.close(); }

    private static String environment(String key, String fallback) {
        String value = System.getenv(key);
        return value == null || value.isBlank() ? fallback : value;
    }
}
