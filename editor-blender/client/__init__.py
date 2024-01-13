from inspect import isclass
from typing import Any, AsyncGenerator, Dict, List, Optional, Type, TypeVar, Union

from aiohttp import ClientSession
from dataclass_wizard import JSONWizard
from gql import Client
from gql.client import AsyncClientSession, ReconnectingAsyncClientSession
from gql.transport.aiohttp import AIOHTTPTransport
from gql.transport.websockets import WebsocketsTransport
from graphql import DocumentNode

from ..core.actions.state.color_map import set_color_map
from ..core.actions.state.control_map import set_control_map
from ..core.actions.state.pos_map import set_pos_map
from ..core.states import state
from ..core.utils.convert import (
    color_map_query_to_state,
    control_map_query_to_state,
    pos_map_query_to_state,
)
from ..graphqls.queries import (
    QueryColorMapPayload,
    QueryControlMapPayload,
    QueryPosMapPayload,
)
from .cache import FieldPolicy, InMemoryCache, TypePolicy, query_defs_to_field_table

GQLSession = Union[AsyncClientSession, ReconnectingAsyncClientSession]

T = TypeVar("T")


def serialize(data: Any) -> Dict[str, Any]:
    # TODO: Support enum
    if isinstance(data, JSONWizard):
        return data.to_dict()
    elif isinstance(data, list):
        return list(map(serialize, data))  # type: ignore
    elif isinstance(data, dict):
        return {key: serialize(value) for key, value in data.items()}  # type: ignore
    else:
        return data


def deserialize(response_type: Type[T], data: Any) -> Any:
    # TODO: Support enum
    if isclass(response_type) and issubclass(response_type, JSONWizard):
        return response_type.from_dict(data)
    elif isinstance(data, list):
        return list(map(lambda item: deserialize(response_type.__args__[0], item), data))  # type: ignore
    else:
        return data


class Clients:
    def __init__(self, cache: InMemoryCache):
        self.http_client: ClientSession = ClientSession(
            "http://localhost:4000", cookies={"token": state.token}
        )

        self.client: Optional[GQLSession] = None
        self.sub_client: Optional[GQLSession] = None

        self.cache = cache

    async def subscribe(
        self, data_type: Type[T], query: DocumentNode
    ) -> AsyncGenerator[Dict[str, T], None]:
        if self.sub_client is None:
            raise Exception("GraphQL client is not initialized")

        query_dict = query.to_dict()  # type: ignore
        selections = query_dict["definitions"][0]["selection_set"]["selections"]  # type: ignore
        query_name = selections[0]["name"]["value"]  # type: ignore

        async for data in self.sub_client.subscribe(query):
            # print("Sub:", data)

            # import time

            # t = time.time()
            data[query_name] = deserialize(data_type, data[query_name])  # type: ignore
            # print(time.time() - t)

            yield data

    async def execute(
        self,
        response_type: Type[T],
        query: DocumentNode,
        variables: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, T]:
        if self.client is None:
            raise Exception("GraphQL client is not initialized")

        query_dict = query.to_dict()  # type: ignore
        query_def = query_defs_to_field_table(query_dict)  # type: ignore

        definition = query_dict["definitions"][0]  # type: ignore
        query_type = definition["operation"]  # type: ignore

        if query_type != "query":
            return await self.client.execute(query)

        # TODO: Check if variables is identical in cache
        if variables is None:
            response = await self.cache.read_query(response_type, query_def)
        else:
            response = None

        if response is None:
            if variables is not None:
                params: Dict[str, Any] = serialize(variables)
                # print(params)
                response = await self.client.execute(query, variable_values=params)
            else:
                response = await self.client.execute(query)

            query_name = query_def[0]
            response[query_name] = deserialize(response_type, response[query_name])

            await self.cache.write_query(response)

        return response

    async def create_graphql_client(self):
        await self.close_graphql_client()

        token_payload = {"token": state.token}

        transport = AIOHTTPTransport(
            url="http://localhost:4000/graphql", cookies=token_payload
        )

        self.client = await Client(
            transport=transport, fetch_schema_from_transport=False
        ).connect_async(reconnecting=True)

        sub_transport = WebsocketsTransport(
            url="ws://localhost:4000/graphql",
            subprotocols=[WebsocketsTransport.GRAPHQLWS_SUBPROTOCOL],
            init_payload=token_payload,
        )

        self.sub_client = await Client(
            transport=sub_transport, fetch_schema_from_transport=False
        ).connect_async(reconnecting=True)

    async def close_graphql_client(self):
        if self.client is not None:
            await self.client.client.close_async()

        if self.sub_client is not None:
            await self.sub_client.client.close_async()

    async def update_http_client(self):
        self.http_client.cookie_jar.update_cookies({"token": state.token})

    async def close_http_client(self):
        if not self.http_client.closed:
            await self.http_client.close()


async def merge_pos_map(
    existing: Optional[QueryPosMapPayload], incoming: QueryPosMapPayload
) -> QueryPosMapPayload:
    posMap = pos_map_query_to_state(incoming)
    await set_pos_map(posMap)
    return incoming


async def merge_control_map(
    existing: Optional[QueryControlMapPayload], incoming: QueryControlMapPayload
) -> QueryControlMapPayload:
    controlMap = control_map_query_to_state(incoming)
    await set_control_map(controlMap)
    return incoming


async def merge_color_map(
    existing: Optional[QueryColorMapPayload], incoming: QueryColorMapPayload
) -> QueryColorMapPayload:
    colorMap = color_map_query_to_state(incoming)
    await set_color_map(colorMap)
    return incoming


client = Clients(
    cache=InMemoryCache(
        policies={
            "PosMap": TypePolicy(
                fields={
                    "frameIds": FieldPolicy(merge=merge_pos_map),
                }
            ),
            "ControlMap": TypePolicy(
                fields={
                    "frameIds": FieldPolicy(merge=merge_control_map),
                }
            ),
            "colorMap": TypePolicy(
                fields={
                    "colorMap": FieldPolicy(merge=merge_color_map),
                }
            ),
        }
    )
)