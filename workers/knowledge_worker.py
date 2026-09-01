from __future__ import annotations

import asyncio
import logging
from time import perf_counter

from app.core.settings import get_settings
from app.db.session import Database
from app.infrastructure.qwen import QwenProvider
from app.repositories.knowledge import KnowledgeRepository
from app.tasks.streams import RedisStreams
from app.services.knowledge_ingestion import KnowledgeIngestionService
from app.common.logging import configure_logging
from app.common.logging.config import get_logger


logger = get_logger(__name__)


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    database = Database(settings)
    queue = RedisStreams(settings)
    stream = f"{settings.redis_stream_prefix}:ingestion"
    group = f"{settings.redis_consumer_group}-knowledge"
    consumer = f"{settings.redis_consumer_name}-knowledge"
    await queue.ensure_group(stream, group)
    service = KnowledgeIngestionService(KnowledgeRepository(database), QwenProvider(settings))
    logger.info("knowledge worker started stream=%s group=%s consumer=%s", stream, group, consumer)
    try:
        while True:
            messages = await queue.claim_pending(stream, group, consumer)
            messages.extend(await queue.consume_once(stream, group, consumer))
            for message in messages:
                try:
                    document_id = message.data.get("document_id", "")
                    started = perf_counter()
                    logger.info("knowledge_ingestion_started document_id=%s message_id=%s", document_id, message.message_id)
                    result = await service.ingest_document(document_id)
                    logger.info("knowledge_ingestion_completed document_id=%s status=%s chunks=%s duration_ms=%.2f", document_id, result.status, result.chunk_count, (perf_counter() - started) * 1000)
                except Exception:
                    logger.exception("knowledge ingestion failed message_id=%s", message.message_id)
                finally:
                    await queue.ack(message)
    finally:
        await queue.close()
        await database.dispose()


if __name__ == "__main__":
    asyncio.run(main())
