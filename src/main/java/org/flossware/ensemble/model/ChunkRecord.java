package org.flossware.ensemble.model;

import java.util.List;
import java.util.UUID;

public record ChunkRecord(
        UUID id,
        UUID documentId,
        int ordinal,
        String content,
        List<Double> embedding
) {
    public ChunkRecord {
        embedding = embedding == null ? List.of() : List.copyOf(embedding);
    }
}
