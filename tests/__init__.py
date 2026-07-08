"""Marks ``tests`` as a regular package so ``tests/fixtures.py`` is reliably
importable as ``from tests.fixtures import ...`` from every test module,
across every phase (avoids pytest import-mode / namespace-package
ambiguity). Does not change how ``tests/test_reuse_imports.py`` (Phase 0,
self-contained) collects or runs.
"""
