"""
Pydantic schema for classifier response — §9.7 of CLAUDE_STRATEGY_SPEC_v3.

Used for validating the tool-use response from Claude.
"""
from pydantic import BaseModel, Field, field_validator
from typing import Literal


class ClassifierResponseSchema(BaseModel):
    regime: Literal["TRENDING", "RANGING", "UNCLEAR"]
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(max_length=280)
    key_features: list[str] = Field(default_factory=list)

    @field_validator("key_features")
    @classmethod
    def limit_key_features(cls, v):
        if len(v) > 5:
            return v[:5]
        return v


CLASSIFY_REGIME_TOOL = {
    "name": "classify_regime",
    "description": "Classify the current market regime for an instrument.",
    "input_schema": {
        "type": "object",
        "properties": {
            "regime": {
                "type": "string",
                "enum": ["TRENDING", "RANGING", "UNCLEAR"],
                "description": "The classified market regime.",
            },
            "confidence": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                "description": "Confidence in the classification (0.0 to 1.0).",
            },
            "rationale": {
                "type": "string",
                "maxLength": 280,
                "description": "Concise rationale for the classification.",
            },
            "key_features": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 5,
                "description": "Up to 5 key features that drove the classification.",
            },
        },
        "required": ["regime", "confidence", "rationale"],
    },
}
