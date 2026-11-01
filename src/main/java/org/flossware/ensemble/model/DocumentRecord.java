package org.flossware.ensemble.model;

import java.time.Instant;
import java.util.UUID;

public record DocumentRecord(
        UUID id,
        String sourceId,
        int revision,
        String title,
        String content,
        Instant createdAt
) {
}
