"""Toy implementation set for local phase-0 testing."""

from .evaluator import toy_evaluator
from .transformations import StripWhitespaceTransformation

__all__ = ["toy_evaluator", "StripWhitespaceTransformation"]
