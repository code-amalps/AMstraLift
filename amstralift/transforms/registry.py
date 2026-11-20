"""Standard Transformation Registry and built-in modernizations catalog (P16).

Maintains the authoritative graph of modernizations across Angular, .NET, Python, and React,
with their prerequisite dependencies, ordering constraints, conflict definitions, and atomic rollback boundaries.

Engineered by Amal P S for AMstraLift Generation 2.
"""

from amstralift.transforms.graph import TransformationGraph
from amstralift.transforms.models import (
    EdgeType,
    TransformationCategory,
    TransformationNode,
    TransformationRisk,
)


def get_standard_transformations() -> list[TransformationNode]:
    """Return all built-in transformation nodes across supported ecosystems."""
    return [
        # --- Angular Ecosystem ---
        TransformationNode(
            id="angular:workspace-config",
            name="Modernize Angular Workspace Configuration",
            description="Upgrades angular.json schema, eliminates deprecated options, and normalizes outputPath.",
            ecosystem="angular",
            category=TransformationCategory.CONFIG,
            risk=TransformationRisk.LOW,
            affected_file_patterns=["angular.json"],
            min_major_version=14,
        ),
        TransformationNode(
            id="angular:ecosystem-align",
            name="Align Angular Ecosystem Dependencies",
            description="Aligns @angular/*, tslib, @ngx-translate, and @fortawesome peer dependencies.",
            ecosystem="angular",
            category=TransformationCategory.DEPENDENCY,
            risk=TransformationRisk.LOW,
            affected_file_patterns=["package.json", "package-lock.json"],
            min_major_version=14,
        ),
        TransformationNode(
            id="angular:standalone",
            name="Angular Standalone Migration",
            description="Converts components, directives, and pipes to standalone and prunes unused NgModules.",
            ecosystem="angular",
            category=TransformationCategory.AST,
            risk=TransformationRisk.MEDIUM,
            affected_file_patterns=["**/*.ts", "**/*.html"],
            atomic_boundary="angular:ast-quartet",
            min_major_version=15,
        ),
        TransformationNode(
            id="angular:control-flow",
            name="Modern Angular Control Flow (@if, @for, @switch)",
            description="Replaces structural directives (*ngIf, *ngFor, *ngSwitch) with built-in control flow.",
            ecosystem="angular",
            category=TransformationCategory.AST,
            risk=TransformationRisk.LOW,
            affected_file_patterns=["**/*.html"],
            atomic_boundary="angular:ast-quartet",
            min_major_version=17,
        ),
        TransformationNode(
            id="angular:signals",
            name="Angular Signal Queries and Model Migration",
            description="Migrates @Input(), @Output(), and ViewChild properties to reactive Signal primitives.",
            ecosystem="angular",
            category=TransformationCategory.AST,
            risk=TransformationRisk.MEDIUM,
            affected_file_patterns=["**/*.ts", "**/*.html"],
            atomic_boundary="angular:ast-quartet",
            min_major_version=17,
        ),
        TransformationNode(
            id="angular:inject",
            name="Functional inject() Migration",
            description="Converts constructor-based dependency injection to functional inject() calls.",
            ecosystem="angular",
            category=TransformationCategory.AST,
            risk=TransformationRisk.LOW,
            affected_file_patterns=["**/*.ts"],
            atomic_boundary="angular:ast-quartet",
            min_major_version=14,
        ),
        TransformationNode(
            id="angular:self-closing",
            name="Self-Closing Template Tags Modernization",
            description="Modernizes empty and void HTML element tags in Angular templates to self-closing syntax.",
            ecosystem="angular",
            category=TransformationCategory.AST,
            risk=TransformationRisk.LOW,
            affected_file_patterns=["**/*.html"],
            atomic_boundary="angular:ast-quartet",
            min_major_version=15,
        ),
        TransformationNode(
            id="angular:material-typography",
            name="Angular Material Typography & MDC Migration",
            description="Migrates legacy Angular Material MDC styling tokens and typography variables.",
            ecosystem="angular",
            category=TransformationCategory.STYLE,
            risk=TransformationRisk.LOW,
            affected_file_patterns=["**/*.scss", "**/*.css"],
            min_major_version=15,
        ),
        TransformationNode(
            id="angular:eslint-migration",
            name="Angular ESLint Flat Config Migration",
            description="Upgrades Angular linting configuration to modern ESLint flat config.",
            ecosystem="angular",
            category=TransformationCategory.CONFIG,
            risk=TransformationRisk.LOW,
            affected_file_patterns=["eslint.config.js", ".eslintrc.json"],
            min_major_version=16,
        ),
        TransformationNode(
            id="angular:legacy-ngmodule-scaffold",
            name="Legacy NgModule Scaffolding",
            description="Creates boilerplate NgModules (mutually exclusive with Standalone architecture).",
            ecosystem="angular",
            category=TransformationCategory.AST,
            risk=TransformationRisk.HIGH,
            affected_file_patterns=["**/*.ts"],
        ),

        # --- .NET Ecosystem ---
        TransformationNode(
            id="dotnet:target-framework",
            name="Modernize .NET Target Framework (LTS)",
            description="Updates csproj TargetFramework to modern long-term supported runtime (e.g. net8.0/net9.0).",
            ecosystem="dotnet",
            category=TransformationCategory.FRAMEWORK,
            risk=TransformationRisk.MEDIUM,
            affected_file_patterns=["**/*.csproj", "Directory.Build.props"],
        ),
        TransformationNode(
            id="dotnet:cpm",
            name="Central Package Management (Directory.Packages.props)",
            description="Consolidates package versions into Directory.Packages.props and trims csproj PackageReferences.",
            ecosystem="dotnet",
            category=TransformationCategory.CONFIG,
            risk=TransformationRisk.LOW,
            affected_file_patterns=["Directory.Packages.props", "**/*.csproj"],
            atomic_boundary="dotnet:cpm-boundary",
        ),
        TransformationNode(
            id="dotnet:nullable",
            name="Enable C# Nullable Reference Types",
            description="Enables <Nullable>enable</Nullable> across all C# project files.",
            ecosystem="dotnet",
            category=TransformationCategory.CONFIG,
            risk=TransformationRisk.LOW,
            affected_file_patterns=["**/*.csproj", "Directory.Build.props"],
        ),
        TransformationNode(
            id="dotnet:format",
            name="Enforce Modern C# Formatting and Code Style",
            description="Runs dotnet format and modernizes language conventions.",
            ecosystem="dotnet",
            category=TransformationCategory.STYLE,
            risk=TransformationRisk.LOW,
            affected_file_patterns=["**/*.cs", ".editorconfig"],
        ),
        TransformationNode(
            id="dotnet:legacy-packages-config",
            name="Legacy packages.config NuGet format",
            description="Legacy NuGet packages.config manager (incompatible with modern Central Package Management).",
            ecosystem="dotnet",
            category=TransformationCategory.CONFIG,
            risk=TransformationRisk.HIGH,
            affected_file_patterns=["**/packages.config"],
        ),

        # --- Python Ecosystem ---
        TransformationNode(
            id="python:pyproject-pep621",
            name="Migrate to PEP 621 Declarative pyproject.toml",
            description="Converts legacy setup.py and setup.cfg files to standard pyproject.toml.",
            ecosystem="python",
            category=TransformationCategory.CONFIG,
            risk=TransformationRisk.MEDIUM,
            affected_file_patterns=["pyproject.toml", "setup.py", "setup.cfg"],
        ),
        TransformationNode(
            id="python:ruff",
            name="Adopt Modern Ruff Linter & Formatter",
            description="Configures and runs Ruff for fast, compliant code formatting and linting.",
            ecosystem="python",
            category=TransformationCategory.STYLE,
            risk=TransformationRisk.LOW,
            affected_file_patterns=["pyproject.toml", "ruff.toml", "**/*.py"],
        ),
        TransformationNode(
            id="python:type-annotations",
            name="Modernize Python Type Annotations (PEP 585 & 604)",
            description="Replaces typing.List, typing.Dict, typing.Union with builtins (list, dict, X | Y).",
            ecosystem="python",
            category=TransformationCategory.AST,
            risk=TransformationRisk.LOW,
            affected_file_patterns=["**/*.py"],
        ),
        TransformationNode(
            id="python:legacy-flake8-black",
            name="Legacy flake8 + black configuration",
            description="Legacy disparate linters superseded by unified Ruff toolchain.",
            ecosystem="python",
            category=TransformationCategory.CONFIG,
            risk=TransformationRisk.MEDIUM,
            affected_file_patterns=[".flake8", "setup.cfg"],
        ),

        # --- React Ecosystem ---
        TransformationNode(
            id="react:jsx-transform",
            name="Modern React JSX Transform",
            description="Migrates JSX runtime from classic React.createElement to react/jsx-runtime.",
            ecosystem="react",
            category=TransformationCategory.AST,
            risk=TransformationRisk.LOW,
            affected_file_patterns=["**/*.tsx", "**/*.jsx", "tsconfig.json"],
        ),
        TransformationNode(
            id="react:hooks-modern",
            name="Modern React Hooks & Strict Mode Conformance",
            description="Enforces modern React hook dependency rules and strict mode compliance.",
            ecosystem="react",
            category=TransformationCategory.AST,
            risk=TransformationRisk.LOW,
            affected_file_patterns=["**/*.tsx", "**/*.jsx"],
        ),
    ]


def build_default_graph() -> TransformationGraph:
    """Construct and populate the default TransformationGraph with built-in rules and constraints."""
    graph = TransformationGraph(nodes=get_standard_transformations())

    # --- Angular Dependency Chains ---
    graph.add_edge(
        "angular:ecosystem-align",
        "angular:workspace-config",
        EdgeType.PREREQUISITE,
        "Package dependencies must be upgraded before workspace configuration schema is modernized",
    )
    graph.add_edge(
        "angular:workspace-config",
        "angular:standalone",
        EdgeType.PREREQUISITE,
        "Workspace schema must be normalized before standalone component migration",
    )
    graph.add_edge(
        "angular:standalone",
        "angular:control-flow",
        EdgeType.PREREQUISITE,
        "Standalone migration establishes component boundary before template control flow rewrites",
    )
    graph.add_edge(
        "angular:standalone",
        "angular:signals",
        EdgeType.PREREQUISITE,
        "Standalone architecture required before converting component inputs/outputs to Signals",
    )
    graph.add_edge(
        "angular:standalone",
        "angular:inject",
        EdgeType.PREREQUISITE,
        "Standalone components cleanly support functional inject() patterns",
    )
    graph.add_edge(
        "angular:control-flow",
        "angular:self-closing",
        EdgeType.ORDERING,
        "Control-flow block transforms should run before self-closing tag cleanup",
    )
    graph.add_edge(
        "angular:standalone",
        "angular:eslint-migration",
        EdgeType.ORDERING,
        "Component structure should be modernized before linting rule migrations",
    )
    graph.add_edge(
        "angular:control-flow",
        "angular:eslint-migration",
        EdgeType.ORDERING,
        "Template migrations should complete before template lint rules are updated",
    )

    # Angular Conflicts & Invalidations
    graph.add_edge(
        "angular:legacy-ngmodule-scaffold",
        "angular:standalone",
        EdgeType.CONFLICT,
        "Cannot scaffold legacy NgModules when migrating to Standalone architecture",
    )
    graph.add_edge(
        "angular:standalone",
        "angular:legacy-ngmodule-scaffold",
        EdgeType.INVALIDATION,
        "Migrating to Standalone invalidates legacy NgModule scaffolding",
    )

    # --- .NET Dependency Chains ---
    graph.add_edge(
        "dotnet:target-framework",
        "dotnet:cpm",
        EdgeType.PREREQUISITE,
        "Modern target framework required before enabling Central Package Management",
    )
    graph.add_edge(
        "dotnet:cpm",
        "dotnet:format",
        EdgeType.ORDERING,
        "Configuration setup should precede code style formatting",
    )
    graph.add_edge(
        "dotnet:legacy-packages-config",
        "dotnet:cpm",
        EdgeType.CONFLICT,
        "packages.config is incompatible with Central Package Management (Directory.Packages.props)",
    )
    graph.add_edge(
        "dotnet:cpm",
        "dotnet:legacy-packages-config",
        EdgeType.INVALIDATION,
        "Enabling Central Package Management invalidates packages.config format",
    )

    # --- Python Dependency Chains ---
    graph.add_edge(
        "python:pyproject-pep621",
        "python:ruff",
        EdgeType.PREREQUISITE,
        "Declarative pyproject.toml required to configure Ruff toolchain",
    )
    graph.add_edge(
        "python:ruff",
        "python:type-annotations",
        EdgeType.ORDERING,
        "Formatter/linter baseline should be established before AST type rewrites",
    )
    graph.add_edge(
        "python:legacy-flake8-black",
        "python:ruff",
        EdgeType.CONFLICT,
        "Legacy flake8/black configs conflict with unified Ruff configuration",
    )
    graph.add_edge(
        "python:ruff",
        "python:legacy-flake8-black",
        EdgeType.INVALIDATION,
        "Adopting Ruff supersedes and invalidates flake8 and black configs",
    )

    # --- React Dependency Chains ---
    graph.add_edge(
        "react:jsx-transform",
        "react:hooks-modern",
        EdgeType.ORDERING,
        "JSX transform should be established before modernizing hook conventions",
    )

    return graph


def get_transformations_for_ecosystem(ecosystem: str) -> list[TransformationNode]:
    """Retrieve all standard transformations belonging to a given ecosystem."""
    eco = ecosystem.strip().lower()
    return [t for t in get_standard_transformations() if t.ecosystem.lower() == eco]
