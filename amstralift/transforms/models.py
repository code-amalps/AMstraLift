"""Models and data structures for the Transformation Dependency Graph (P16).

Engineered by Amal P S for AMstraLift Generation 2.
"""

from enum import Enum
from typing import Any, Literal
from pydantic import BaseModel, Field


class EdgeType(str, Enum):
    """Relationship types between transformation nodes."""

    PREREQUISITE = "prerequisite"  # Node A must succeed before Node B can run (A -> B)
    CONFLICT = "conflict"          # Node A and Node B cannot both run in the same execution
    INVALIDATION = "invalidation"  # Execution of Node A invalidates Node B's preconditions
    ORDERING = "ordering"          # Node A should run before Node B if both are chosen


class TransformationCategory(str, Enum):
    """Classification of transformation intent."""

    DEPENDENCY = "dependency"  # Manifest package updates, lockfile alignments
    FRAMEWORK = "framework"    # Framework-level architecture shifts (e.g. CLI, workspace)
    AST = "ast"                # Code-level AST rewrites (e.g. control flow, signals, inject)
    CONFIG = "config"          # Tooling configuration migrations (e.g. tsconfig, CPM, ruff)
    STYLE = "style"            # Formatting, linting, cosmetic alignment
    CLEANUP = "cleanup"        # Deprecated artifact pruning, gitignore updates


class TransformationRisk(str, Enum):
    """Risk tier of applying a specific transformation."""

    LOW = "low"            # Purely mechanical, high confidence, verified idempotent
    MEDIUM = "medium"      # May alter runtime wiring or build semantics
    HIGH = "high"          # Structural architectural refactor requiring thorough testing
    CRITICAL = "critical"  # Potentially breaking breaking-change migration


class TransformationNode(BaseModel):
    """An individual transformation or modernization unit in the graph."""

    id: str = Field(description="Unique identifier (e.g. 'angular:standalone')")
    name: str = Field(description="Human-readable title")
    description: str = Field(default="", description="Detailed explanation of the change")
    ecosystem: str = Field(description="Target ecosystem (e.g. 'angular', 'dotnet', 'python', 'react')")
    category: TransformationCategory = Field(default=TransformationCategory.AST)
    risk: TransformationRisk = Field(default=TransformationRisk.LOW)
    affected_file_patterns: list[str] = Field(
        default_factory=list,
        description="Glob patterns of files touched (e.g. ['angular.json'], ['**/*.ts'], ['**/*.html'])",
    )
    atomic_boundary: str | None = Field(
        default=None,
        description="Identifier of atomic rollback transaction group (e.g. 'angular:ast-quartet')",
    )
    min_major_version: int | None = Field(
        default=None,
        description="Minimum major version required to apply this transformation",
    )
    max_major_version: int | None = Field(
        default=None,
        description="Maximum major version applicable for this transformation",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary additional metadata (e.g. documentation URL, CLI schematics)",
    )


class DependencyEdge(BaseModel):
    """A directed edge between two transformation nodes expressing a dependency constraint."""

    source: str = Field(description="Source transformation ID (prerequisite, invalidator, or predecessor)")
    target: str = Field(description="Target transformation ID (dependent, invalidated, or successor)")
    edge_type: EdgeType = Field(default=EdgeType.PREREQUISITE)
    reason: str = Field(default="", description="Human-readable justification for the edge")


class ConflictReport(BaseModel):
    """Report detailing a detected conflict between two transformations."""

    source_id: str
    target_id: str
    reason: str
    conflict_type: Literal["explicit", "resource_overlap"] = "explicit"
    conflicting_patterns: list[str] = Field(default_factory=list)


class ParallelBatch(BaseModel):
    """A collection of transformations within an execution phase that can safely run concurrently."""

    batch_index: int = Field(description="Sequential index of the parallel batch within a phase")
    nodes: list[str] = Field(description="List of transformation node IDs in this concurrent batch")
    is_concurrent: bool = Field(default=True, description="True if nodes can run in parallel without file collisions")
