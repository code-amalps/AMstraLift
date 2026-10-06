"""Refusal Engine for AMstraLift.

Implements the fundamental engineering philosophy:
"A system that knows when not to modify code is more trustworthy than one that always produces a patch."

Refuses unsafe, ambiguous, or unverifiable code transformations deterministically.
Guarantees that whenever a refusal occurs, the target source code is left 100% untouched
(no TODO comments or partial modifications injected into user files).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable

from amstralift.evidence.models import RefusalCategory, RefusalRecord

logger = logging.getLogger(__name__)


class RefusalEngine:
    """Evaluates transformation candidates against deterministic safety rules and produces RefusalRecords."""

    def __init__(self) -> None:
        self.refusals: list[RefusalRecord] = []

    def refuse(
        self,
        adapter: str,
        rule_id: str,
        file_path: str,
        category: RefusalCategory,
        reason: str,
        context_snippet: str | None = None,
        manual_guidance: str | None = None,
    ) -> RefusalRecord:
        """Record an explicit refusal to transform a file.

        CRITICAL TRUST GUARANTEE:
        `code_modified` is strictly False. The file on disk MUST NOT be touched.
        """
        record = RefusalRecord(
            adapter=adapter,
            rule_id=rule_id,
            file_path=file_path,
            category=category,
            reason=reason,
            code_modified=False,
            developer_review_required=True,
            context_snippet=context_snippet,
            manual_guidance=manual_guidance,
        )
        self.refusals.append(record)
        logger.info(
            "Transformation refused [%s:%s] for %s: %s",
            adapter,
            rule_id,
            file_path,
            reason,
        )
        return record

    # ── Ecosystem Pattern Detectors ──────────────────────────────────────────

    def inspect_angular_file(self, file_path: str, content: str) -> list[RefusalRecord]:
        """Check for unsupported Angular migration patterns that require human review."""
        refusals: list[RefusalRecord] = []

        # 1. Custom or third-party component decorators that extend @Component dynamically
        if re.search(r"@CustomComponent|@WrappedComponent", content):
            refusals.append(
                self.refuse(
                    adapter="angular",
                    rule_id="migrate-standalone-decorator",
                    file_path=file_path,
                    category=RefusalCategory.UNSUPPORTED_PATTERN,
                    reason="Custom component decorator abstraction detected. AST cannot deterministically synthesize standalone imports.",
                    manual_guidance="Convert custom decorator to standard @Component({ standalone: true, imports: [...] }) manually.",
                )
            )

        # 2. Dynamic NgModule compilation (e.g. Compiler.compileModuleAndAllComponentsAsync or loadChildren string syntax)
        if re.search(r"loadChildren:\s*['\"][^'\"]+#[^'\"]+['\"]", content):
            refusals.append(
                self.refuse(
                    adapter="angular",
                    rule_id="migrate-legacy-string-routing",
                    file_path=file_path,
                    category=RefusalCategory.COMPILATION_RISK,
                    reason="Legacy string-based loadChildren syntax ('path/module#Module') found. Cannot deterministically resolve module symbol.",
                    manual_guidance="Upgrade route to modern dynamic import syntax: () => import('./path').then(m => m.Module).",
                )
            )

        return refusals

    def inspect_react_file(self, file_path: str, content: str) -> list[RefusalRecord]:
        """Check for unsupported React migration patterns."""
        refusals: list[RefusalRecord] = []

        # 1. Custom history abstraction in React Router
        if "createBrowserHistory" in content and "useHistory" in content:
            refusals.append(
                self.refuse(
                    adapter="react",
                    rule_id="migrate-custom-history",
                    file_path=file_path,
                    category=RefusalCategory.UNSUPPORTED_PATTERN,
                    reason="Custom createBrowserHistory() abstraction combined with useHistory() detected. Automated transition to useNavigate() risks route sync drift.",
                    manual_guidance="Replace custom history listener with React Router v6 unstable_HistoryRouter or native useNavigate() hook.",
                )
            )

        # 2. Deprecated string refs: ref="myRef"
        if re.search(r'ref=["\'][a-zA-Z0-9_-]+["\']', content):
            refusals.append(
                self.refuse(
                    adapter="react",
                    rule_id="migrate-legacy-string-refs",
                    file_path=file_path,
                    category=RefusalCategory.COMPILATION_RISK,
                    reason="Legacy string refs ('ref=\"foo\"') detected. Automated replacement with useRef() requires component scope restructuring.",
                    manual_guidance="Refactor string refs to React.useRef() or React.createRef().",
                )
            )

        return refusals

    def inspect_python_file(self, file_path: str, content: str) -> list[RefusalRecord]:
        """Check for unsupported Python / Pydantic migration patterns."""
        refusals: list[RefusalRecord] = []

        # 1. Dynamic model construction via create_model() with dynamic keyword arguments
        if "create_model(" in content and ("**" in content or "getattr(" in content):
            refusals.append(
                self.refuse(
                    adapter="python",
                    rule_id="migrate-pydantic-dynamic-model",
                    file_path=file_path,
                    category=RefusalCategory.AMBIGUOUS_CONTRACT,
                    reason="Dynamic Pydantic model synthesis via create_model(**kwargs) detected. Field types cannot be statically verified by AST.",
                    manual_guidance="Inspect dynamic fields and manually define Pydantic v2 TypeAdapter or explicit BaseModel subclasses.",
                )
            )

        # 2. Metaclass overrides on BaseModel
        if re.search(r"class\s+\w+\s*\([^)]*metaclass\s*=", content):
            refusals.append(
                self.refuse(
                    adapter="python",
                    rule_id="migrate-pydantic-metaclass",
                    file_path=file_path,
                    category=RefusalCategory.UNSUPPORTED_PATTERN,
                    reason="Custom metaclass on BaseModel detected. Metaclass hooks conflict with Pydantic v2 ModelMetaclass.",
                    manual_guidance="Refactor metaclass logic into Pydantic v2 model_validator or root_validator equivalents.",
                )
            )

        return refusals

    def inspect_dotnet_file(self, file_path: str, content: str) -> list[RefusalRecord]:
        """Check for unsupported .NET migration patterns."""
        refusals: list[RefusalRecord] = []

        # 1. Legacy COM interop references in .csproj
        if "<COMReference" in content:
            refusals.append(
                self.refuse(
                    adapter="dotnet",
                    rule_id="migrate-com-reference",
                    file_path=file_path,
                    category=RefusalCategory.UNSUPPORTED_PATTERN,
                    reason="Legacy Windows COM interop (<COMReference>) detected in project. Cannot be deterministically migrated to cross-platform .NET 8/9.",
                    manual_guidance="Verify COM dependency compatibility or replace with modern .NET P/Invoke / interop wrappers.",
                )
            )

        # 2. Custom MSBuild targets overriding NuGet restore
        if "<Target Name=\"Restore\"" in content or "BeforeTargets=\"Restore\"" in content:
            refusals.append(
                self.refuse(
                    adapter="dotnet",
                    rule_id="migrate-cpm-custom-restore",
                    file_path=file_path,
                    category=RefusalCategory.COMPILATION_RISK,
                    reason="Custom MSBuild Restore target override detected. Central Package Management (CPM) cannot guarantee resolution order.",
                    manual_guidance="Review custom restore targets in Directory.Build.targets prior to enabling CPM.",
                )
            )

        return refusals
