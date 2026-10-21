"""Deterministic synthetic corpus of a fictional company (no LLM, no network, no real data)."""

from .generator import GENERATOR_VERSION, build, write_corpus

__all__ = ["GENERATOR_VERSION", "build", "write_corpus"]
