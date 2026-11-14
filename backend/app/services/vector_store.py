"""Everything that touches Qdrant. Every read and delete is scoped to one tenant."""

import uuid
from dataclasses import dataclass

from qdrant_client import AsyncQdrantClient, models

from app.services.chunking import Chunk

DENSE = "dense"
TEXT_FIELD = "text"
TENANT_FIELD = "tenant_id"
DOCUMENT_FIELD = "document_id"
_UPSERT_BATCH = 128


@dataclass(frozen=True)
class SearchHit:
    document_id: uuid.UUID
    chunk_index: int
    text: str
    filename: str
    page_start: int
    page_end: int
    score: float


def _tenant_filter(tenant_id: uuid.UUID, document_id: uuid.UUID | None = None) -> models.Filter:
    must: list[models.Condition] = [
        models.FieldCondition(key=TENANT_FIELD, match=models.MatchValue(value=str(tenant_id)))
    ]
    if document_id is not None:
        must.append(
            models.FieldCondition(
                key=DOCUMENT_FIELD, match=models.MatchValue(value=str(document_id))
            )
        )
    return models.Filter(must=must)


class VectorStore:
    def __init__(self, client: AsyncQdrantClient, collection: str, dimension: int) -> None:
        self._client = client
        self._collection = collection
        self._dimension = dimension

    async def ensure_collection(self) -> None:
        """Create the collection and its payload indexes if missing. Safe to call repeatedly."""
        if not await self._client.collection_exists(self._collection):
            await self._client.create_collection(
                self._collection,
                vectors_config={
                    DENSE: models.VectorParams(
                        size=self._dimension,
                        distance=models.Distance.COSINE,
                        # TurboQuant (Qdrant 1.19+): 4-bit codes, ~8x smaller than float32.
                        quantization_config=models.TurboQuantization(
                            turbo=models.TurboQuantQuantizationConfig(
                                bits=models.TurboQuantBitSize.BITS4
                            )
                        ),
                    )
                },
                # One shared HNSW graph is useless across tenants; build per-tenant links instead.
                hnsw_config=models.HnswConfigDiff(m=0, payload_m=16),
            )
        # Index creation is idempotent, so a half-finished first run repairs itself.
        await self._client.create_payload_index(
            self._collection,
            TENANT_FIELD,
            models.KeywordIndexParams(type=models.KeywordIndexType.KEYWORD, is_tenant=True),
        )
        await self._client.create_payload_index(
            self._collection,
            DOCUMENT_FIELD,
            models.KeywordIndexParams(type=models.KeywordIndexType.KEYWORD),
        )
        await self._client.create_payload_index(
            self._collection,
            TEXT_FIELD,
            # BM25 ranking on the full-text index (Qdrant 1.20): no sparse model needed.
            models.TextIndexParams(
                type=models.TextIndexType.TEXT,
                tokenizer=models.TokenizerType.WORD,
                lowercase=True,
                scoring=True,
            ),
        )

    async def replace_document(
        self,
        tenant_id: uuid.UUID,
        document_id: uuid.UUID,
        filename: str,
        chunks: list[Chunk],
        vectors: list[list[float]],
    ) -> None:
        """Store a document's chunks, removing any earlier version first (so retries are safe)."""
        await self.delete_document(tenant_id, document_id)
        points = [
            models.PointStruct(
                id=str(uuid.uuid5(document_id, str(chunk.index))),
                vector={DENSE: vector},
                payload={
                    TENANT_FIELD: str(tenant_id),
                    DOCUMENT_FIELD: str(document_id),
                    "chunk_index": chunk.index,
                    "filename": filename,
                    "page_start": chunk.page_start,
                    "page_end": chunk.page_end,
                    TEXT_FIELD: chunk.text,
                },
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        for start in range(0, len(points), _UPSERT_BATCH):
            await self._client.upsert(
                self._collection, points[start : start + _UPSERT_BATCH], wait=True
            )

    async def delete_document(self, tenant_id: uuid.UUID, document_id: uuid.UUID) -> None:
        if not await self._client.collection_exists(self._collection):
            return
        await self._client.delete(
            self._collection,
            models.FilterSelector(filter=_tenant_filter(tenant_id, document_id)),
            wait=True,
        )

    async def count(self, tenant_id: uuid.UUID, document_id: uuid.UUID | None = None) -> int:
        if not await self._client.collection_exists(self._collection):
            return 0
        result = await self._client.count(
            self._collection, count_filter=_tenant_filter(tenant_id, document_id), exact=True
        )
        return result.count

    async def hybrid_search(
        self, tenant_id: uuid.UUID, query_text: str, query_vector: list[float], limit: int = 30
    ) -> list[SearchHit]:
        """Dense + BM25 candidates fused with reciprocal rank fusion, within one tenant."""
        if not await self._client.collection_exists(self._collection):
            return []  # nothing has been ingested yet
        tenant = _tenant_filter(tenant_id)
        response = await self._client.query_points(
            self._collection,
            prefetch=[
                models.Prefetch(query=query_vector, using=DENSE, filter=tenant, limit=limit),
                models.Prefetch(
                    query=models.TextQuery(text=query_text),
                    using=TEXT_FIELD,
                    filter=tenant,
                    limit=limit,
                ),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            query_filter=tenant,
            limit=limit,
            with_payload=True,
        )
        hits = []
        for point in response.points:
            payload = point.payload or {}
            hits.append(
                SearchHit(
                    document_id=uuid.UUID(payload[DOCUMENT_FIELD]),
                    chunk_index=payload["chunk_index"],
                    text=payload[TEXT_FIELD],
                    filename=payload["filename"],
                    page_start=payload["page_start"],
                    page_end=payload["page_end"],
                    score=point.score,
                )
            )
        return hits
