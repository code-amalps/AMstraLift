"""Data models and contract definitions for Gate 6: Semantic & Contract Diff.

Answers the enterprise modernization question:
"We successfully transformed the code, and it builds and passes tests,
but did we accidentally violate the application's observable interface or contract?"
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, computed_field


class ContractChangeType(str, Enum):
    """Categorization of contract-visible changes."""

    ROUTE_ADDED = "ROUTE_ADDED"
    ROUTE_REMOVED = "ROUTE_REMOVED"
    ROUTE_CHANGED = "ROUTE_CHANGED"

    SYMBOL_ADDED = "SYMBOL_ADDED"
    SYMBOL_REMOVED = "SYMBOL_REMOVED"
    SYMBOL_CHANGED = "SYMBOL_CHANGED"

    CONFIG_KEY_ADDED = "CONFIG_KEY_ADDED"
    CONFIG_KEY_REMOVED = "CONFIG_KEY_REMOVED"
    CONFIG_KEY_CHANGED = "CONFIG_KEY_CHANGED"


class SemanticSeverity(str, Enum):
    """Severity classification of contract diffs."""

    BREAKING = "BREAKING"        # Silent removal or incompatible modification
    COMPATIBLE = "COMPATIBLE"    # Safe addition or non-breaking expansion
    EXPECTED = "EXPECTED"        # Explicitly declared in ModernizationContract


class RouteItem(BaseModel):
    """Observable HTTP route endpoint contract."""

    method: str = "GET"          # GET, POST, PUT, DELETE, etc.
    path: str                    # e.g. "/api/orders" or "/api/orders/{id}"
    handler: str | None = None   # Function or class name
    file_path: str = ""

    @property
    def canonical_id(self) -> str:
        return f"{self.method.upper()} {self.path}"


class SymbolItem(BaseModel):
    """Observable exported API or module symbol contract."""

    name: str                    # e.g. "OrderService", "createOrder"
    kind: str                    # "class", "function", "interface", "type", "variable"
    file_path: str = ""
    signature: str | None = None

    @property
    def canonical_id(self) -> str:
        return f"{self.kind}:{self.name}"


class ConfigItem(BaseModel):
    """Observable environment variable or configuration key contract."""

    key: str                     # e.g. "DATABASE_CONNECTION_STRING"
    source_file: str = ""
    default_value: str | None = None

    @property
    def canonical_id(self) -> str:
        return self.key


class SemanticSnapshot(BaseModel):
    """Complete snapshot of observable contracts before or after modernization."""

    repo_path: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    routes: list[RouteItem] = Field(default_factory=list)
    exported_symbols: list[SymbolItem] = Field(default_factory=list)
    config_keys: list[ConfigItem] = Field(default_factory=list)

    @computed_field
    def total_observable_elements(self) -> int:
        return len(self.routes) + len(self.exported_symbols) + len(self.config_keys)


class SemanticDiffItem(BaseModel):
    """An individual difference detected between before and after contract snapshots."""

    id: str = Field(default_factory=lambda: f"sd_{uuid4().hex[:8]}")
    category: Literal["route", "symbol", "config"]
    change_type: ContractChangeType
    item_id: str
    before_value: str | None = None
    after_value: str | None = None
    severity: SemanticSeverity = SemanticSeverity.BREAKING
    file_path: str = ""
    is_expected: bool = False
    matched_rule: str | None = None
    explanation: str = ""


class AllowedContractRule(BaseModel):
    """Specification of an explicitly planned and authorized contract change."""

    category: Literal["route", "symbol", "config", "all"] = "all"
    change_type: ContractChangeType | None = None
    from_pattern: str | None = None   # Regex pattern matching before_value or item_id
    to_pattern: str | None = None     # Regex pattern matching after_value
    item_pattern: str | None = None   # Regex pattern matching item_id
    description: str = ""

    def matches(self, diff: SemanticDiffItem) -> bool:
        """Evaluate if this rule explicitly authorizes the given diff item."""
        if self.category != "all" and self.category != diff.category:
            return False
        if self.change_type and self.change_type != diff.change_type:
            return False

        if self.item_pattern and not re.search(self.item_pattern, diff.item_id):
            return False

        if self.from_pattern and diff.before_value:
            if not re.search(self.from_pattern, diff.before_value):
                return False

        if self.to_pattern and diff.after_value:
            if not re.search(self.to_pattern, diff.after_value):
                return False

        return True


class ModernizationContract(BaseModel):
    """Declared modernization contract against which Gate 6 evaluates semantic changes."""

    allowed: list[AllowedContractRule] = Field(default_factory=list)
    forbidden: list[str] = Field(default_factory=list)
    allow_safe_additions: bool = True  # Newly added routes/symbols are compatible by default


class Gate6Result(BaseModel):
    """Outcome of Gate 6: Semantic & Contract Diff verification."""

    status: Literal["passed", "passed_with_expected_changes", "failed"] = "passed"
    violations: list[SemanticDiffItem] = Field(default_factory=list)
    expected_changes: list[SemanticDiffItem] = Field(default_factory=list)
    compatible_additions: list[SemanticDiffItem] = Field(default_factory=list)
    summary: str = ""

    @computed_field
    def is_passed(self) -> bool:
        return self.status in ("passed", "passed_with_expected_changes")
