import asyncio

from sqlalchemy.ext.asyncio import create_async_engine

from ensemble.graph_store import SqliteGraphStore, graph_metadata


def test_sqlite_graph_store_persists_nodes_edges_and_traversal(tmp_path):
    async def scenario():
        database = tmp_path / "graph.db"
        url = "sqlite+aiosqlite:///" + str(database)
        store = SqliteGraphStore(url)
        async with store.engine.begin() as connection:
            await connection.run_sync(graph_metadata.create_all)
        await store.initialize()
        left = await store.add_node("service", {"name": "memory"})
        right = await store.add_node("service", {"name": "knowledge"})
        edge = await store.add_edge(left["id"], right["id"], "feeds", {"enabled": True})
        stats = await store.stats()
        assert stats == {
            "node_count": 2, "edge_count": 1,
            "nodes_by_type": {"service": 2},
            "edges_by_relationship": {"feeds": 1},
        }
        assert (await store.get_node(left["id"]))["properties"]["name"] == "memory"
        assert (await store.query_edges(relationship="feeds"))[0]["id"] == edge["id"]
        assert (await store.traverse(left["id"]))[0]["node"]["id"] == right["id"]
        await store.close()

        reopened = SqliteGraphStore(url)
        await reopened.initialize()
        assert (await reopened.get_node(right["id"]))["properties"]["name"] == "knowledge"
        await reopened.close()

    asyncio.run(scenario())


def test_graph_source_reclamation_removes_owned_edges_and_source_nodes(tmp_path):
    async def scenario():
        url = "sqlite+aiosqlite:///" + str(tmp_path / "graph-reclaim.db")
        store = SqliteGraphStore(url)
        async with store.engine.begin() as connection:
            await connection.run_sync(graph_metadata.create_all)
        await store.initialize()

        source = await store.add_node(
            "source", {"source_id": "source-to-purge", "revision": 3}, "source-node",
        )
        entity = await store.add_node(
            "entity", {"entity_type": "concept", "normalized": "shared"}, "shared-entity",
        )
        await store.add_edge(
            source["id"], entity["id"], "mentions",
            {"source_id": "source-to-purge", "revision": 3}, "owned-mention",
        )
        unrelated = await store.add_node(
            "source", {"source_id": "unrelated-source", "revision": 1}, "unrelated-source-node",
        )
        await store.add_edge(
            unrelated["id"], entity["id"], "mentions",
            {"source_id": "unrelated-source", "revision": 1}, "unrelated-mention",
        )

        reclaimed = await store.reclaim_source("source-to-purge")
        assert reclaimed == 2
        assert await store.get_node("source-node") is None
        assert await store.get_node("shared-entity") is not None
        assert [edge["id"] for edge in await store.query_edges()] == ["unrelated-mention"]
        await store.close()

    asyncio.run(scenario())
