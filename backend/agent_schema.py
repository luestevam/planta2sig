"""Validated contracts shared by the executor, model and review UI."""
import os
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class Limits(StrictModel):
    max_tools: int = Field(default=48, ge=1, le=300)
    max_model_calls: int = Field(default=24, ge=0, le=100)
    max_seconds: float = Field(default=240, ge=1, le=1800)
    max_cost_usd: float = Field(default=.5, ge=0, le=100)
    retries: int = Field(default=1, ge=0, le=3)
    max_features: int = Field(default=3000, ge=10, le=10000)
    max_output_tokens: int = Field(default=3000, ge=256, le=16000)
    input_usd_per_million: float | None = Field(default=None, gt=0)
    output_usd_per_million: float | None = Field(default=None, gt=0)

    @classmethod
    def environment(cls):
        values = {}
        for key in cls.model_fields:
            value = os.getenv('AGENT_'+key.upper())
            if value is not None: values[key] = value
        return cls(**values)


class Region(StrictModel):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,80}$')
    role: Literal['main_map','legend','stamp','table','location_map','unknown']
    bbox: list[float] = Field(min_length=4, max_length=4)
    evidence_ids: list[str] = Field(default_factory=list, max_length=30)
    confirmed: bool = False
    method: str = Field(default='model_proposal', max_length=80)


class LegendClass(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    text_id: str = Field(max_length=100)
    symbol_ids: list[str] = Field(default_factory=list, max_length=12)
    geometry_types: list[Literal['Polygon','LineString','Point','Annotation']] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=500)


class Classification(StrictModel):
    feature_id: str
    class_id: str
    evidence_ids: list[str] = Field(min_length=1, max_length=20)
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=500)


class Semantics(StrictModel):
    regions: list[Region] = Field(default_factory=list, max_length=30)
    classes: list[LegendClass] = Field(default_factory=list, max_length=60)
    classifications: list[Classification] = Field(default_factory=list, max_length=200)
    issues: list[str] = Field(default_factory=list, max_length=30)


class AgentRequest(StrictModel):
    mode: Literal['local','ai'] = 'local'
    pages: list[int] | None = Field(default=None, max_length=20)
    regions: dict[str, list[Region]] = Field(default_factory=dict)


def object_type(feature):
    return feature.get('object_type') or {'Polygon':'polygon','LineString':'line','Point':'point'}[feature.get('geometry_type','Polygon')]


def normalize_feature(f):
    """Additive migration: existing IDs, statuses and coordinates are preserved."""
    from shapely.geometry import mapping
    from .geometry import page_geometry
    f.setdefault('geometry_type', (f.get('geometry') or {}).get('type','Polygon'))
    f.setdefault('object_type', object_type(f))
    f.setdefault('suggested_class', f['category'])
    f.setdefault('attributes', {})
    f.setdefault('extraction_method', 'legacy_profile')
    f.setdefault('quality', {'reading': None, 'classification': None,
                             'georeferencing': {'rmse_m': None, 'external_accuracy_m': None}})
    f.setdefault('page_geometry', mapping(page_geometry(f['page_ring'], f['geometry_type'])))
    f.setdefault('page_holes', [])
    f.setdefault('original_page_geometry', f['page_geometry'])
    f.setdefault('linked_feature_id', None)
    return f
