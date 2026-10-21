package com.optivem.kata.banking.adapter.driven.persistence.jpa.internal;

import lombok.Data;

import javax.persistence.Column;
import javax.persistence.Entity;
import javax.persistence.GeneratedValue;
import javax.persistence.GenerationType;
import javax.persistence.Id;
import javax.persistence.Table;

/** Durable source-side proof that an account mutation committed. */
@Data
@Entity
@Table(name = "account_operation_source")
public class SourceAccountOperationRecord {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(name = "operation_sequence")
    private Long operationSequence;

    @Column(name = "operation_id", nullable = false, unique = true)
    private String operationId;

    @Column(name = "account_number", nullable = false)
    private String accountNumber;
}
