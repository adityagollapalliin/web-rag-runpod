"""Compare retrieval strategies on a small question set.

Loads a dataset of documents and questions, indexes the documents into a temporary Qdrant
collection using the configured models, runs every question through each strategy, and
reports how often and how highly the passage containing the answer is retrieved.

Usage: python -m app.evaluate [dataset.json]       (default: eval/sample.json)

Dataset format:
  {"documents": [{"filename": "...", "text": "..."}],
   "questions": [{"question": "...", "expected": "text the right passage contains"}]}

With the fake models the numbers only show that the pipeline runs; run it where real models
are configured (on the pod) for numbers that mean something.
"""

import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any

from qdrant_client import AsyncQdrantClient

from app.core.config import Settings, get_settings
from app.services.answering import rank
from app.services.chunking import chunk_pages
from app.services.embedding import build_embedder
from app.services.multivector import build_multivector_embedder
from app.services.rerank import build_reranker
from app.services.vector_store import build_vector_store

Metrics = dict[str, float]

# Which stages each strategy switches on (see answering.rank).
STRATEGIES: dict[str, dict[str, bool]] = {
    "dense only": {"use_text": False, "use_rerank": False, "use_multivector": False},
    "hybrid (dense + BM25)": {"use_rerank": False, "use_multivector": False},
    "hybrid + rerank": {"use_multivector": False},
    "hybrid + rerank + feedback": {"use_multivector": False, "use_feedback": True},
}
MULTIVECTOR_STRATEGIES: dict[str, dict[str, bool]] = {
    "hybrid + multi-vector": {"use_rerank": False},
    "hybrid + multi-vector + rerank": {},
}


async def evaluate(
    dataset: dict[str, Any], settings: Settings, chunk_chars: int = 160
) -> dict[str, Metrics]:
    """Return {strategy: {"hit@1", "hit@k", "mrr"}} where k is `settings.context_chunks`."""
    settings = settings.model_copy(update={"qdrant_collection": f"eval_{uuid.uuid4().hex[:10]}"})
    qdrant = AsyncQdrantClient(url=settings.qdrant_url, check_compatibility=False)
    embedder = build_embedder(settings)
    reranker = build_reranker(settings)
    multivector_embedder = build_multivector_embedder(settings)
    store = build_vector_store(qdrant, settings)
    tenant = uuid.uuid4()
    strategies = dict(STRATEGIES)
    if multivector_embedder is not None:
        strategies |= MULTIVECTOR_STRATEGIES
    k = settings.context_chunks
    try:
        await store.ensure_collection()
        for document in dataset["documents"]:
            # Small chunks, so each document yields several and ranking has work to do.
            chunks = chunk_pages([document["text"]], max_chars=chunk_chars, overlap_chars=0)
            texts = [chunk.text for chunk in chunks]
            multivectors = await multivector_embedder.embed(texts) if multivector_embedder else None
            await store.replace_document(
                tenant,
                uuid.uuid4(),
                document["filename"],
                chunks,
                await embedder.embed(texts),
                multivectors,
            )

        totals = {name: {"hit@1": 0.0, "hit@k": 0.0, "mrr": 0.0} for name in strategies}
        for item in dataset["questions"]:
            question, expected = item["question"], item["expected"].lower()
            vector = (await embedder.embed([settings.embedding_query_prefix + question]))[0]
            for name, stages in strategies.items():
                ranked = await rank(
                    user_id=tenant,
                    question=question,
                    vector=vector,
                    store=store,
                    reranker=reranker,
                    settings=settings,
                    multivector_embedder=multivector_embedder,
                    **stages,
                )
                position = next(
                    (
                        index
                        for index, (_, hit) in enumerate(ranked[:k], start=1)
                        if expected in hit.text.lower()
                    ),
                    None,
                )
                if position is not None:
                    totals[name]["hit@k"] += 1
                    totals[name]["mrr"] += 1 / position
                    totals[name]["hit@1"] += position == 1
        count = len(dataset["questions"])
        return {
            name: {metric: round(value / count, 3) for metric, value in metrics.items()}
            for name, metrics in totals.items()
        }
    finally:
        await qdrant.delete_collection(settings.qdrant_collection)
        await qdrant.close()
        await embedder.aclose()
        await reranker.aclose()
        if multivector_embedder is not None:
            await multivector_embedder.aclose()


def main() -> None:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "eval/sample.json")
    dataset = json.loads(path.read_text())
    settings = get_settings()
    results = asyncio.run(evaluate(dataset, settings))
    k = settings.context_chunks
    print(f"{len(dataset['questions'])} questions over {len(dataset['documents'])} documents")
    print(
        f"models: embedding={settings.embedding_provider}, rerank={settings.rerank_provider}, "
        f"multi-vector={settings.multivector_provider}"
    )
    print(f"\n{'strategy':<34}{'hit@1':>8}{f'hit@{k}':>8}{'MRR':>8}")
    for name, metrics in results.items():
        print(f"{name:<34}{metrics['hit@1']:>8.2f}{metrics['hit@k']:>8.2f}{metrics['mrr']:>8.2f}")


if __name__ == "__main__":
    main()
