"""Tests for Angular Constructor DI to inject() Transformer."""

import pytest

from amstralift.adapters.angular_inject import (
    parse_parameter_property,
    split_constructor_params,
    transform_angular_inject,
)


def test_split_constructor_params():
    assert split_constructor_params("") == []
    assert split_constructor_params("private http: HttpClient, protected route: ActivatedRoute") == [
        "private http: HttpClient",
        "protected route: ActivatedRoute",
    ]
    # Nested commas in generics / decorators
    raw = "@Inject(API_TOKEN) private api: Map<string, number>, private auth: AuthService"
    params = split_constructor_params(raw)
    assert len(params) == 2
    assert params[0] == "@Inject(API_TOKEN) private api: Map<string, number>"
    assert params[1] == "private auth: AuthService"


def test_parse_parameter_property():
    prop = parse_parameter_property("private http: HttpClient")
    assert prop is not None
    assert prop["name"] == "http"
    assert prop["type"] == "HttpClient"
    assert prop["token"] == "HttpClient"
    assert prop["modifiers"] == ["private"]
    assert prop["options"] == {}

    # Non-property parameter
    assert parse_parameter_property("http: HttpClient") is None


def test_parse_parameter_with_inject_and_optional():
    raw = "@Optional() @Inject(CONFIG_TOKEN) public config: AppConfig"
    prop = parse_parameter_property(raw)
    assert prop is not None
    assert prop["name"] == "config"
    assert prop["token"] == "CONFIG_TOKEN"
    assert prop["modifiers"] == ["public"]
    assert prop["options"] == {"optional": "true"}


def test_transform_empty_constructor_removed():
    ts = (
        "import { Component, OnInit } from '@angular/core';\n"
        "import { HttpClient } from '@angular/common/http';\n\n"
        "@Component({})\n"
        "export class UserComponent {\n"
        "  constructor(\n"
        "    private http: HttpClient,\n"
        "    protected route: ActivatedRoute,\n"
        "  ) {}\n"
        "}\n"
    )
    result, count = transform_angular_inject(ts)
    assert count == 2
    assert "private http = inject(HttpClient);" in result
    assert "protected route = inject(ActivatedRoute);" in result
    assert "constructor" not in result
    assert "import { Component, OnInit, inject } from '@angular/core';" in result


def test_transform_constructor_with_body_preserved():
    ts = (
        "import { Component } from '@angular/core';\n\n"
        "@Component({})\n"
        "export class UserComponent {\n"
        "  constructor(private http: HttpClient) {\n"
        "    this.init();\n"
        "  }\n"
        "}\n"
    )
    result, count = transform_angular_inject(ts)
    assert count == 1
    assert "private http = inject(HttpClient);" in result
    assert "constructor() {\n    this.init();\n  }" in result


def test_transform_with_decorators_and_import_cleanup():
    ts = (
        "import { Component, Inject, Optional } from '@angular/core';\n\n"
        "@Component({})\n"
        "export class UserComponent {\n"
        "  constructor(\n"
        "    @Inject(API_URL) private api: string,\n"
        "    @Optional() private logger?: LoggerService,\n"
        "  ) {}\n"
        "}\n"
    )
    result, count = transform_angular_inject(ts)
    assert count == 2
    assert "private api = inject(API_URL);" in result
    assert "private logger = inject(LoggerService, { optional: true });" in result
    first_line = result.split("\n")[0]
    assert "inject" in first_line
    assert "Inject" not in first_line
    assert "Optional" not in first_line


def test_transform_retains_non_property_parameters():
    ts = (
        "import { Component } from '@angular/core';\n\n"
        "@Component({})\n"
        "export class SubComponent extends BaseComponent {\n"
        "  constructor(private http: HttpClient, parentId: string) {\n"
        "    super(parentId);\n"
        "  }\n"
        "}\n"
    )
    result, count = transform_angular_inject(ts)
    assert count == 1
    assert "private http = inject(HttpClient);" in result
    assert "constructor(parentId: string) {" in result
    assert "super(parentId);" in result


def test_migrate_repository_inject(tmp_path):
    from amstralift.adapters.angular_inject import migrate_repository_inject

    src = tmp_path / "src" / "app"
    src.mkdir(parents=True, exist_ok=True)

    comp = src / "app.component.ts"
    comp.write_text(
        "import { Component } from '@angular/core';\n"
        "@Component({})\n"
        "export class AppComponent {\n"
        "  constructor(private router: Router) {}\n"
        "}\n",
        encoding="utf-8",
    )

    stats = migrate_repository_inject(tmp_path)
    assert "src/app/app.component.ts" in stats
    assert stats["src/app/app.component.ts"] == 1

    content = comp.read_text(encoding="utf-8")
    assert "private router = inject(Router);" in content
    assert "constructor" not in content
