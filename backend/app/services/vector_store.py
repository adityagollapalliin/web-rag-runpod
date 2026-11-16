"""Everything that touches Qdrant. Every read and delete is scoped to one tenant."""

import uuid
from dataclasses import dataclass

from qdrant_client import AsyncQdrantClient, models

from app.core.config import Settings
from app.services.chunking import Chunk

DENSE = "dense"
MULTI = "colbert"  # optional late-interaction (one vector per token) field
TEXT_FIELD = "text"
TENANT_FIELD = "tenant_id"
DOCUMENT_FIELD = "document_id"
_UPSERT_BATCH = 128

MultiVector = list[list[float]]


@dataclass(frozen=True)
class SearchHit:
    point_id: str
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


def _hits(points: list[models.ScoredPoint]) -> list[SearchHit]:
    hits = []
    for point in points:
        payload = point.payload or {}
        hits.append(
            SearchHit(
                point_id=str(point.id),
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


class VectorStore:
    def __init__(
        self,
        client: AsyncQdrantClient,
        collection: str,
        dimension: int,
        multivector_dimension: int | None = None,
    ) -> None:
        self._client = client
        self._collection = collection
        self._dimension = dimension
        self._multivector_dimension = multivector_dimension

    @property
    def has_multivectors(self) -> bool:
        return self._multivector_dimension is not None

    async def ensure_collection(self) -> None:
        """Create the collection and its payload indexes if missing. Safe to call repeatedly."""
        if not await self._client.collection_exists(self._collection):
            vectors: dict[str, models.VectorParams] = {
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
            }
            if self._multivector_dimension is not None:
                vectors[MULTI] = models.VectorParams(
                    size=self._multivector_dimension,
                    distance=models.Distance.COSINE,
                    multivector_config=models.MultiVectorConfig(
                        comparator=models.MultiVectorComparator.MAX_SIM
                    ),
                    # Only ever used to re-score a short candidate list, so no index is built.
                    hnsw_config=models.HnswConfigDiff(m=0, payload_m=0),
                )
            await self._client.create_collection(
                self._collection,
                vectors_config=vectors,
                # One shared HNSW graph is useless across tenants; build per-tenant links instead.
                hnsw_config=models.HnswConfigDiff(m=0, payload_m=16),
            )
        elif self._multivector_dimension is not None:
            info = await self._client.get_collection(self._collection)
            existing = info.config.params.vectors
            if not isinstance(existing, dict) or MULTI not in existing:
                raise RuntimeError(
                    f"Collection '{self._collection}' was created without multi-vectors. "
                    "Set DOCQA_QDRANT_COLLECTION to a new name; documents are re-ingested into it."
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
        multivectors: list[MultiVector] | None = None,
    ) -> None:
        """Store a document's chunks, removing any earlier version first (so retries are safe)."""
        if self.has_multivectors and multivectors is None:
            raise ValueError("This collection needs a multi-vector for every chunk")
        await self.delete_document(tenant_id, document_id)
        points = []
        for position, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True)):
            named: dict[str, list[float] | MultiVector] = {DENSE: vector}
            if self.has_multivectors and multivectors is not None:
                named[MULTI] = multivectors[position]
            points.append(
                models.PointStruct(
                    id=str(uuid.uuid5(document_id, str(chunk.index))),
                    vector=named,
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
            )
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

    async def search(
        self,
        tenant_id: uuid.UUID,
        query_text: str,
        query_vector: list[float],
        *,
        limit: int = 30,
        use_text: bool = True,
        query_multivector: MultiVector | None = None,
        candidate_limit: int = 100,
    ) -> list[SearchHit]:
        """Search one tenant's chunks.

        - dense only (`use_text=False`): nearest neighbours of the query vector;
        - hybrid (default): dense and BM25 candidates fused with reciprocal rank fusion;
        - with `query_multivector`: the candidates above are re-scored inside Qdrant by
          late interaction (MaxSim over per-token vectors) before the top `limit` return.
        """
        if not await self._client.collection_exists(self._collection):
            return []  # nothing has been ingested yet
        tenant = _tenant_filter(tenant_id)
        pool = candidate_limit if query_multivector is not None else limit
        dense = models.Prefetch(query=query_vector, using=DENSE, filter=tenant, limit=pool)
        text = models.Prefetch(
            query=models.TextQuery(text=query_text), using=TEXT_FIELD, filter=tenant, limit=pool
        )
        fusion = models.FusionQuery(fusion=models.Fusion.RRF)

        if query_multivector is not None:
            candidates = (
                models.Prefetch(prefetch=[dense, text], query=fusion, filter=tenant, limit=pool)
                if use_text
                else dense
            )
            response = await self._client.query_points(
                self._collection,
                prefetch=candidates,
                query=query_multivector,
                using=MULTI,
                query_filter=tenant,
                limit=limit,
            )
        elif use_text:
            response = await self._client.query_points(
                self._collection,
                prefetch=[dense, text],
                query=fusion,
                query_filter=tenant,
                limit=limit,
            )
        else:
            response = await self._client.query_points(
                self._collection, query=query_vector, using=DENSE, query_filter=tenant, limit=limit
            )
        return _hits(response.points)

    async def hybrid_search(
        self, tenant_id: uuid.UUID, query_text: str, query_vector: list[float], limit: int = 30
    ) -> list[SearchHit]:
        return await self.search(tenant_id, query_text, query_vector, limit=limit)

    async def feedback_search(
        self,
        tenant_id: uuid.UUID,
        query_vector: list[float],
        judged: list[tuple[str, float]],
        weights: tuple[float, float, float],
        limit: int = 30,
    ) -> list[SearchHit]:
        """A second pass steered by relevance judgements on first-pass results (Qdrant 1.17+).

        `judged` pairs a point id with a relevance score from a stronger model (here the
        reranker). Qdrant moves the search toward what scored high and away from what scored
        low. The judged points themselves are not returned.
        """
        a, b, c = weights
        response = await self._client.query_points(
            self._collection,
            query=models.RelevanceFeedbackQuery(
                relevance_feedback=models.RelevanceFeedbackInput(
                    target=query_vector,
                    feedback=[
                        models.FeedbackItem(example=point_id, score=score)
                        for point_id, score in judged
                    ],
                    strategy=models.NaiveFeedbackStrategy(
                        naive=models.NaiveFeedbackStrategyParams(a=a, b=b, c=c)
                    ),
                )
            ),
            using=DENSE,
            query_filter=_tenant_filter(tenant_id),
            limit=limit,
        )
        return _hits(response.points)


def build_vector_store(client: AsyncQdrantClient, settings: Settings) -> VectorStore:
    return VectorStore(
        client,
        settings.qdrant_collection,
        settings.embedding_dimension,
        settings.multivector_dimension if settings.multivector_provider != "none" else None,
    )
