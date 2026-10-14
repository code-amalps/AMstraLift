"""Repository validation, discovery, and template scaffolding engine.

Handles scenarios where no Git repository or supported framework is detected in a
selected directory, offering automatic subdirectory project discovery, Git initialization,
and starter template scaffolding across Angular, React, .NET, and Python.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from amstralift.core.models import DiscoveredProject
from amstralift.core.monorepo import MonorepoScanner
from amstralift.core.workspace import run_git

logger = logging.getLogger("amstralift.core.scaffold")


def detect_repository_status(target_path: Path) -> dict[str, Any]:
    """Inspect a directory and return its Git and ecosystem status."""
    target_path = target_path.resolve()
    if not target_path.exists():
        return {
            "exists": False,
            "is_dir": False,
            "is_empty": True,
            "is_git": False,
            "git_root": None,
            "root_ecosystem": None,
            "subprojects": [],
        }

    is_dir = target_path.is_dir()
    if not is_dir:
        return {
            "exists": True,
            "is_dir": False,
            "is_empty": False,
            "is_git": False,
            "git_root": None,
            "root_ecosystem": None,
            "subprojects": [],
        }

    # Check if empty (ignoring system/hidden files like .DS_Store, Thumbs.db)
    contents = [
        p for p in target_path.iterdir()
        if p.name not in (".git", ".DS_Store", "Thumbs.db", "desktop.ini")
    ]
    is_empty = len(contents) == 0

    # Check Git repository status
    git_res = run_git(["rev-parse", "--show-toplevel"], cwd=target_path)
    is_git = git_res.returncode == 0
    git_root = Path(git_res.stdout.strip()) if is_git and git_res.stdout else None

    # Check direct ecosystem detection at root
    root_ecosystem: str | None = None
    pkg_json = target_path / "package.json"
    if pkg_json.is_file():
        try:
            data = json.loads(pkg_json.read_text(encoding="utf-8"))
            deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            if "@angular/core" in deps or (target_path / "angular.json").is_file():
                root_ecosystem = "angular"
            elif "react" in deps or "react-dom" in deps:
                root_ecosystem = "react"
        except Exception:
            pass

    if not root_ecosystem:
        if list(target_path.glob("*.csproj")) or list(target_path.glob("*.sln")):
            root_ecosystem = "dotnet"
        elif (target_path / "pyproject.toml").is_file() or (target_path / "requirements.txt").is_file():
            root_ecosystem = "python"

    # Deep scan for subprojects in subdirectories (up to 5 levels)
    subprojects: list[DiscoveredProject] = []
    if not is_empty:
        try:
            topo = MonorepoScanner.discover(target_path, max_depth=5)
            subprojects = topo.projects
        except Exception:
            pass

    return {
        "exists": True,
        "is_dir": True,
        "is_empty": is_empty,
        "is_git": is_git,
        "git_root": git_root,
        "root_ecosystem": root_ecosystem,
        "subprojects": subprojects,
    }


def scaffold_starter_project(target_path: Path, ecosystem: str = "angular") -> list[str]:
    """Initialize a Git repository and scaffold a starter project template.

    Supported ecosystems: 'angular', 'react', 'dotnet', 'python'.
    """
    target_path = target_path.resolve()
    target_path.mkdir(parents=True, exist_ok=True)
    created_files: list[str] = []

    # 1. Initialize Git if not already a repository
    git_check = run_git(["rev-parse", "--is-inside-work-tree"], cwd=target_path)
    if git_check.returncode != 0:
        run_git(["init"], cwd=target_path)
        run_git(["config", "core.autocrlf", "false"], cwd=target_path)

    # 2. Write standard .gitignore
    gitignore_file = target_path / ".gitignore"
    if not gitignore_file.exists():
        gitignore_file.write_text(
            "node_modules/\ndist/\n.angular/\n.nx/\n.vs/\nbin/\nobj/\n__pycache__/\n.venv/\n*.log\n",
            encoding="utf-8",
        )
        created_files.append(".gitignore")

    eco_clean = ecosystem.strip().lower()

    # 3. Scaffold ecosystem-specific starter files
    if eco_clean == "angular":
        pkg_file = target_path / "package.json"
        pkg_file.write_text(
            json.dumps(
                {
                    "name": "amstralift-angular-starter",
                    "version": "15.0.0",
                    "scripts": {
                        "ng": "ng",
                        "start": "ng serve",
                        "build": "ng build",
                        "test": "ng test",
                    },
                    "dependencies": {
                        "@angular/animations": "^15.2.0",
                        "@angular/common": "^15.2.0",
                        "@angular/compiler": "^15.2.0",
                        "@angular/core": "^15.2.0",
                        "@angular/forms": "^15.2.0",
                        "@angular/platform-browser": "^15.2.0",
                        "@angular/platform-browser-dynamic": "^15.2.0",
                        "@angular/router": "^15.2.0",
                        "rxjs": "~7.8.0",
                        "tslib": "^2.3.0",
                        "zone.js": "~0.12.0",
                    },
                    "devDependencies": {
                        "@angular-devkit/build-angular": "^15.2.0",
                        "@angular/cli": "^15.2.0",
                        "@angular/compiler-cli": "^15.2.0",
                        "typescript": "~4.9.5",
                    },
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        created_files.append("package.json")

        angular_json = target_path / "angular.json"
        angular_json.write_text(
            json.dumps(
                {
                    "$schema": "./node_modules/@angular/cli/lib/config/schema.json",
                    "version": 1,
                    "newProjectRoot": "projects",
                    "projects": {
                        "app": {
                            "projectType": "application",
                            "root": "",
                            "sourceRoot": "src",
                            "prefix": "app",
                            "architect": {
                                "build": {
                                    "builder": "@angular-devkit/build-angular:browser",
                                    "options": {
                                        "outputPath": "dist/app",
                                        "index": "src/index.html",
                                        "main": "src/main.ts",
                                        "polyfills": ["zone.js"],
                                        "tsConfig": "tsconfig.app.json",
                                    },
                                }
                            },
                        }
                    },
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        created_files.append("angular.json")

        tsconfig = target_path / "tsconfig.json"
        tsconfig.write_text(
            json.dumps(
                {
                    "compileOnSave": False,
                    "compilerOptions": {
                        "baseUrl": "./",
                        "outDir": "./dist/out-tsc",
                        "sourceMap": True,
                        "declaration": False,
                        "downlevelIteration": True,
                        "experimentalDecorators": True,
                        "moduleResolution": "node",
                        "importHelpers": True,
                        "target": "ES2022",
                        "module": "ES2022",
                        "lib": ["ES2022", "dom"],
                    },
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        created_files.append("tsconfig.json")

        # Starter template containing legacy directives for immediate modernization testing
        src_app = target_path / "src" / "app"
        src_app.mkdir(parents=True, exist_ok=True)
        html_file = src_app / "app.component.html"
        html_file.write_text(
            """<div *ngIf="isLoggedIn">
  <h2>Welcome to AMstraLift Angular Starter!</h2>
</div>
<ul>
  <li *ngFor="let item of items">{{ item }}</li>
</ul>
""",
            encoding="utf-8",
        )
        created_files.append("src/app/app.component.html")

        ts_file = src_app / "app.component.ts"
        ts_file.write_text(
            """import { Component } from '@angular/core';

@Component({
  selector: 'app-root',
  templateUrl: './app.component.html',
})
export class AppComponent {
  isLoggedIn = true;
  items = ['Ecosystem Adapters', '6-Gate Verification', 'AST Modernization'];
}
""",
            encoding="utf-8",
        )
        created_files.append("src/app/app.component.ts")

    elif eco_clean == "react":
        pkg_file = target_path / "package.json"
        pkg_file.write_text(
            json.dumps(
                {
                    "name": "amstralift-react-starter",
                    "version": "1.0.0",
                    "scripts": {
                        "build": "tsc",
                    },
                    "dependencies": {
                        "react": "^17.0.2",
                        "react-dom": "^17.0.2",
                    },
                    "devDependencies": {
                        "@types/react": "^17.0.2",
                        "@types/react-dom": "^17.0.2",
                        "typescript": "^4.9.5",
                    },
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        created_files.append("package.json")

        tsconfig = target_path / "tsconfig.json"
        tsconfig.write_text(
            json.dumps(
                {
                    "compilerOptions": {
                        "target": "es5",
                        "module": "commonjs",
                        "jsx": "react",
                        "strict": True,
                        "esModuleInterop": True,
                    }
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        created_files.append("tsconfig.json")

        src_dir = target_path / "src"
        src_dir.mkdir(parents=True, exist_ok=True)
        app_file = src_dir / "App.tsx"
        app_file.write_text(
            """import React from 'react';

export const App = () => {
  return <h1>AMstraLift React Starter</h1>;
};
""",
            encoding="utf-8",
        )
        created_files.append("src/App.tsx")

    elif eco_clean in ("dotnet", ".net"):
        csproj_file = target_path / "MyApplication.csproj"
        csproj_file.write_text(
            """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>net6.0</TargetFramework>
    <ImplicitUsings>enable</ImplicitUsings>
    <Nullable>enable</Nullable>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Newtonsoft.Json" Version="12.0.3" />
  </ItemGroup>
</Project>
""",
            encoding="utf-8",
        )
        created_files.append("MyApplication.csproj")

        prog_file = target_path / "Program.cs"
        prog_file.write_text(
            """Console.WriteLine("AMstraLift .NET Starter Application");
""",
            encoding="utf-8",
        )
        created_files.append("Program.cs")

    elif eco_clean == "python":
        req_file = target_path / "requirements.txt"
        req_file.write_text("requests==2.25.1\nurllib3==1.26.5\n", encoding="utf-8")
        created_files.append("requirements.txt")

        main_file = target_path / "main.py"
        main_file.write_text(
            """def main():
    print("AMstraLift Python Starter")

if __name__ == "__main__":
    main()
""",
            encoding="utf-8",
        )
        created_files.append("main.py")

    # 4. Commit starter files
    run_git(["add", "."], cwd=target_path)
    run_git(
        ["commit", "--no-verify", "-m", f"Initial commit: AMstraLift {ecosystem} starter scaffold"],
        cwd=target_path,
    )

    return created_files
