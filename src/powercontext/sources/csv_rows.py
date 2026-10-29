from typing import Any
from urllib.parse import quote
from pydantic import BaseModel, ConfigDict
import csv
import io
import json
import dataclasses

from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.projections import TextEvidence, TEXT_EVIDENCE_PROJECTION_KEY

class CSVDocumentInput(BaseModel):
    document_id: str
    primary_key: str
    content: bytes

class CSVRowInput(BaseModel):
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]

class CSVRowSource(Source):
    model_config = ConfigDict(extra="allow")
    
    name: str
    source_id: str
    canonical_value: str
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]
    # 【修复1】使用官方的枚举类型
    materialization: SourceMaterialization = SourceMaterialization.CAPTURED
    definition_version: str = "1"

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, CSVRowSource):
            return self.source_id == other.source_id
        if isinstance(other, CSVRowInput):
            return (
                self.document_id == other.document_id
                and self.primary_key == other.primary_key
                and self.primary_key_value == other.primary_key_value
                and self.columns == other.columns
                and self.values == other.values
            )
        return super().__eq__(other)

def csv_row_source_id(document_id: str, primary_key_value: str) -> str:
    if not document_id or not document_id.strip() or document_id != document_id.strip():
        raise ValueError("document_id must be non-empty and trimmed")
    doc_encoded = quote(document_id, safe="")
    pk_encoded = quote(primary_key_value, safe="")
    source_id = f"csv:{doc_encoded}:{pk_encoded}"
    if len(source_id) > 256:
        raise ValueError("source_id exceeds 256 characters")
    return source_id

def parse_csv_document(value: CSVDocumentInput) -> tuple[CSVRowInput, ...]:
    csv_row_source_id(value.document_id, "dummy")
    try:
        text = value.content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("Invalid UTF-8 encoding")
    if not text.strip():
        raise ValueError("Empty file")
    reader = csv.reader(io.StringIO(text), strict=True)
    try:
        rows = list(reader)
    except csv.Error as e:
        raise ValueError(f"Malformed CSV: {e}")
    if not rows:
        raise ValueError("Empty CSV")
    header = rows[0]
    if not header or any(not h.strip() for h in header) or len(set(header)) != len(header):
        raise ValueError("Invalid header")
    if not value.primary_key or value.primary_key not in header:
        raise ValueError("Invalid primary key")
    
    pk_index = header.index(value.primary_key)
    result = []
    seen_pks = set()
    for row in rows[1:]:
        if not row or all(cell.strip() == "" for cell in row):
            continue
        if len(row) != len(header):
            raise ValueError("Field count mismatch")
        pk_val = row[pk_index]
        if not pk_val.strip():
            raise ValueError("Empty primary key value")
        if pk_val in seen_pks:
            raise ValueError("Duplicate primary key")
        seen_pks.add(pk_val)
        
        result.append(CSVRowInput(
            document_id=value.document_id,
            primary_key=value.primary_key,
            primary_key_value=pk_val,
            columns=tuple(header),
            values=tuple(row)
        ))
    return tuple(result)

class CSVAdapter:
    name = "csv-row"
    input_class = CSVRowInput
    source_class = CSVRowSource

    async def resolve(self, value: CSVRowInput) -> CSVRowSource:
        sid = csv_row_source_id(value.document_id, value.primary_key_value)
        return CSVRowSource(
            name=sid,
            source_id=sid,
            canonical_value=sid,
            document_id=value.document_id,
            primary_key=value.primary_key,
            primary_key_value=value.primary_key_value,
            columns=value.columns,
            values=value.values,
            # 【修复2】这里也使用枚举
            materialization=SourceMaterialization.CAPTURED
        )

    async def read(self, source: CSVRowSource) -> Any:
        return source

@dataclasses.dataclass(frozen=True)
class CSVProjection:
    name: str = TEXT_EVIDENCE_PROJECTION_KEY.name
    version: str = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class: type = CSVRowSource
    output_class: type = TextEvidence

    def project(self, source: CSVRowSource) -> TextEvidence:
        row_obj = {col: val for col, val in zip(source.columns, source.values)}
        metadata = {
            "document_id": source.document_id,
            "primary_key": source.primary_key,
            "primary_key_value": source.primary_key_value,
            "columns": list(source.columns),
            "row": row_obj
        }
        content = json.dumps(row_obj, ensure_ascii=False, separators=(',', ':'))
        
        return TextEvidence(
            source_type="csv-row",
            source_id=source.source_id,
            content=content,
            metadata=metadata
        )

_csv_adapter = CSVAdapter()

CSV_ROW_SOURCE_DEFINITION = AdapterSourceDefinition(
    adapter=_csv_adapter,
    version="1",
    projections=(CSVProjection(),)
)