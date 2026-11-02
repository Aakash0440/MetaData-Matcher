"""Pydantic contracts for everything the LLM returns. The LLM never writes to the model directly."""
from typing import Literal

from pydantic import BaseModel, Field

SemanticType = Literal["identifier", "code", "date", "timestamp", "money_gbp", "measure", "score", "count",
                       "flag", "name", "free_text", "address", "contact", "json", "year"]
PiiCategory = Literal["none", "direct_identifier", "quasi_identifier", "contact", "address", "financial"]
EntityKind = Literal["entity", "link", "reference", "audit"]


class ColumnMeta(BaseModel):
    column: str
    business_name: str = Field(min_length=2, max_length=80)
    description: str = Field(min_length=5, max_length=300)
    semantic_type: SemanticType
    pii_category: PiiCategory
    tags: list[str] = Field(default_factory=list, max_length=3)
    glossary_term: str = Field(min_length=2, max_length=80)
    term_definition: str = Field(min_length=5, max_length=300)
    reasoning: str = ""
    self_confidence: float = Field(ge=0, le=1)


class RawTable(BaseModel):
    """Columns stay as raw dicts so one bad column does not discard the good ones."""
    table: str
    business_name: str = Field(min_length=2, max_length=80)
    description: str = Field(min_length=5, max_length=400)
    entity: str = Field(min_length=2, max_length=60)
    entity_kind: EntityKind
    tags: list[str] = Field(default_factory=list, max_length=3)
    reasoning: str = ""
    self_confidence: float = Field(ge=0, le=1)
    columns: list[dict]
