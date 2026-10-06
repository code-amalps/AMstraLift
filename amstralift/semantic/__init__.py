"""Gate 6: Semantic & Contract Diff Engine for AMstraLift.

Provides observable contract extraction, before/after snapshot diffing,
and modernization contract evaluation across APIs, exported symbols,
routes, and configuration keys.
"""

from amstralift.semantic.engine import SemanticDiffEngine
from amstralift.semantic.extractors.config_extractor import ConfigurationExtractor
from amstralift.semantic.extractors.route_extractor import RouteExtractor
from amstralift.semantic.extractors.symbol_extractor import ExportedSymbolExtractor
from amstralift.semantic.models import (
    AllowedContractRule,
    ConfigItem,
    ContractChangeType,
    Gate6Result,
    ModernizationContract,
    RouteItem,
    SemanticDiffItem,
    SemanticSeverity,
    SemanticSnapshot,
    SymbolItem,
)

__all__ = [
    "AllowedContractRule",
    "ConfigItem",
    "ConfigurationExtractor",
    "ContractChangeType",
    "ExportedSymbolExtractor",
    "Gate6Result",
    "ModernizationContract",
    "RouteExtractor",
    "RouteItem",
    "SemanticDiffEngine",
    "SemanticDiffItem",
    "SemanticSeverity",
    "SemanticSnapshot",
    "SymbolItem",
]
