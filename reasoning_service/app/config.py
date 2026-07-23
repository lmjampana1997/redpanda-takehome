"""Environment-driven settings. No defaults that mask a missing .env in prod —
only defaults that match docker-compose's own service names, so local dev
still works if a var is left unset."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    redpanda_brokers: str
    topic_enriched: str
    topic_review: str
    consumer_group_id: str

    database_url: str

    wiki_user_agent: str

    llm_provider: str
    ollama_base_url: str
    ollama_model: str
    anthropic_api_key: str
    anthropic_model: str
    openai_api_key: str
    openai_model: str


def load_settings() -> Settings:
    return Settings(
        redpanda_brokers=os.environ.get("REDPANDA_BROKERS", "redpanda:9092"),
        topic_enriched=os.environ.get("TOPIC_ENRICHED", "wiki.edits.enriched"),
        topic_review=os.environ.get("TOPIC_REVIEW", "wiki.edits.review"),
        consumer_group_id=os.environ.get("CONSUMER_GROUP_ID", "reasoning-service"),
        database_url=os.environ.get(
            "DATABASE_URL", "postgresql://wiki:wiki@postgres:5432/wiki_edits"
        ),
        wiki_user_agent=os.environ.get("WIKI_USER_AGENT", "fde-takehome-bot/0.1"),
        llm_provider=os.environ.get("LLM_PROVIDER", "ollama"),
        ollama_base_url=os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434"),
        ollama_model=os.environ.get("OLLAMA_MODEL", "llama3.1"),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        anthropic_model=os.environ.get("ANTHROPIC_MODEL", "claude-3-5-haiku-latest"),
        openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
        openai_model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
    )
