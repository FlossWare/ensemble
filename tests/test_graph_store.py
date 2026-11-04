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
        assert (await store.get_node(left["id"]))["properties"]["name"] == "memory"
        assert (await store.query_edges(relationship="feeds"))[0]["id"] == edge["id"]
        assert (await store.traverse(left["id"]))[0]["node"]["id"] == right["id"]
        await store.close()

        reopened = SqliteGraphStore(url)
        await reopened.initialize()
        assert (await reopened.get_node(right["id"]))["properties"]["name"] == "knowledge"
        await reopened.close()

    asyncio.run(scenario())
