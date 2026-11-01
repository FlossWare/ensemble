package org.flossware.ensemble;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.flossware.ensemble.embedding.DisabledEmbeddingProvider;
import org.flossware.ensemble.embedding.EmbeddingProvider;
import org.flossware.ensemble.embedding.OpenAiCompatibleEmbeddingProvider;
import org.flossware.ensemble.http.EnsembleHttpServer;
import org.flossware.ensemble.ingestion.Chunker;
import org.flossware.ensemble.ingestion.DocumentService;
import org.flossware.ensemble.search.SearchService;
import org.flossware.ensemble.store.DocumentStore;
import org.flossware.ensemble.store.InMemoryDocumentStore;
import org.flossware.ensemble.store.PostgresDocumentStore;

import java.net.InetSocketAddress;
import java.net.http.HttpClient;
import java.time.Duration;

public final class EnsembleApplication {
    private EnsembleApplication() {
    }

    public static void main(String[] args) throws Exception {
        var environment = System.getenv();
        var host = environment.getOrDefault("ENSEMBLE_HOST", "127.0.0.1");
        var port = Integer.parseInt(environment.getOrDefault("ENSEMBLE_PORT", "8090"));
        var mapper = new ObjectMapper().findAndRegisterModules();

        DocumentStore store = createStore(environment, mapper);
        store.initialize();

        EmbeddingProvider embeddings = createEmbeddingProvider(environment, mapper);
        var documentService = new DocumentService(store, new Chunker(), embeddings);
        var searchService = new SearchService(store, embeddings);
        var server = new EnsembleHttpServer(
                new InetSocketAddress(host, port),
                mapper,
                documentService,
                searchService
        );

        Runtime.getRuntime().addShutdownHook(new Thread(server::stop));
        server.start();
        System.out.printf("Ensemble listening on http://%s:%d%n", host, port);
        System.out.printf("Persistence: %s%n", store.getClass().getSimpleName());
        System.out.printf("Embeddings: %s%n", embeddings.getClass().getSimpleName());
    }

    private static DocumentStore createStore(java.util.Map<String, String> env, ObjectMapper mapper) {
        var mode = env.getOrDefault("ENSEMBLE_STORE", "memory");
        if ("postgres".equalsIgnoreCase(mode)) {
            var jdbcUrl = required(env, "ENSEMBLE_JDBC_URL");
            var user = env.getOrDefault("ENSEMBLE_DB_USER", "");
            var password = env.getOrDefault("ENSEMBLE_DB_PASSWORD", "");
            return new PostgresDocumentStore(jdbcUrl, user, password, mapper);
        }
        if (!"memory".equalsIgnoreCase(mode)) {
            throw new IllegalArgumentException("ENSEMBLE_STORE must be 'memory' or 'postgres'");
        }
        return new InMemoryDocumentStore();
    }

    private static EmbeddingProvider createEmbeddingProvider(
            java.util.Map<String, String> env, ObjectMapper mapper) {
        var baseUrl = env.get("ENSEMBLE_EMBEDDING_BASE_URL");
        var model = env.get("ENSEMBLE_EMBEDDING_MODEL");
        if (baseUrl == null || baseUrl.isBlank() || model == null || model.isBlank()) {
            return new DisabledEmbeddingProvider();
        }
        var apiKey = env.getOrDefault("ENSEMBLE_EMBEDDING_API_KEY", "");
        var client = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(10))
                .build();
        return new OpenAiCompatibleEmbeddingProvider(
                client, mapper, baseUrl, model, apiKey, Duration.ofSeconds(60));
    }

    private static String required(java.util.Map<String, String> env, String name) {
        var value = env.get(name);
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(name + " is required when ENSEMBLE_STORE=postgres");
        }
        return value;
    }
}
