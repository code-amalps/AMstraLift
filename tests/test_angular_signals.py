"""Tests for Angular Signal Inputs and Outputs Transformer."""

import pytest

from amstralift.adapters.angular_signals import (
    parse_input_decorator_args,
    parse_output_decorator_args,
    transform_angular_signals,
)


def test_parse_input_decorator_args():
    assert parse_input_decorator_args("") == {}
    assert parse_input_decorator_args("   ") == {}
    assert parse_input_decorator_args("'customName'") == {"alias": "'customName'"}
    assert parse_input_decorator_args('"customName"') == {"alias": '"customName"'}
    assert parse_input_decorator_args("{ required: true }") == {"required": True}
    assert parse_input_decorator_args("{ required: true, alias: 'custom' }") == {
        "required": True,
        "alias": "'custom'",
    }
    assert parse_input_decorator_args("{ transform: booleanAttribute }") == {
        "transform": "booleanAttribute"
    }


def test_parse_output_decorator_args():
    assert parse_output_decorator_args("") == {}
    assert parse_output_decorator_args("'onSelect'") == {"alias": "'onSelect'"}
    assert parse_output_decorator_args("{ alias: 'onSelect' }") == {"alias": "'onSelect'"}


def test_transform_simple_input_with_default():
    ts = (
        "import { Component, Input } from '@angular/core';\n\n"
        "@Component({})\n"
        "export class CardComponent {\n"
        "  @Input() title: string = 'Welcome';\n"
        "  @Input() count = 0;\n"
        "}\n"
    )
    result, count = transform_angular_signals(ts)
    assert count == 2
    assert "title = input<string>('Welcome');" in result
    assert "count = input(0);" in result
    assert "import { Component, input } from '@angular/core';" in result
    assert "Input" not in result.split("\n")[0]


def test_transform_required_input():
    ts = (
        "import { Component, Input } from '@angular/core';\n\n"
        "@Component({})\n"
        "export class CardComponent {\n"
        "  @Input({ required: true }) userId!: string;\n"
        "  @Input({ required: true, alias: 'id' }) orderId: number;\n"
        "}\n"
    )
    result, count = transform_angular_signals(ts)
    assert count == 2
    assert "userId = input.required<string>();" in result
    assert "orderId = input.required<number>({ alias: 'id' });" in result
    assert "import { Component, input } from '@angular/core';" in result


def test_transform_optional_input():
    ts = (
        "import { Component, Input } from '@angular/core';\n\n"
        "@Component({})\n"
        "export class CardComponent {\n"
        "  @Input() subtitle?: string;\n"
        "  @Input() age: number;\n"
        "}\n"
    )
    result, count = transform_angular_signals(ts)
    assert count == 2
    assert "subtitle = input<string | undefined>(undefined);" in result
    assert "age = input<number | undefined>(undefined);" in result


def test_transform_simple_output():
    ts = (
        "import { Component, Output, EventEmitter } from '@angular/core';\n\n"
        "@Component({})\n"
        "export class CardComponent {\n"
        "  @Output() itemSelected = new EventEmitter<Item>();\n"
        "  @Output() closed = new EventEmitter<void>();\n"
        "}\n"
    )
    result, count = transform_angular_signals(ts)
    assert count == 2
    assert "itemSelected = output<Item>();" in result
    assert "closed = output<void>();" in result
    assert "import { Component, output } from '@angular/core';" in result
    assert "EventEmitter" not in result.split("\n")[0]
    assert "Output" not in result.split("\n")[0]


def test_transform_output_with_alias():
    ts = (
        "import { Component, Output, EventEmitter } from '@angular/core';\n\n"
        "@Component({})\n"
        "export class CardComponent {\n"
        "  @Output('onSaved') save = new EventEmitter<User>();\n"
        "}\n"
    )
    result, count = transform_angular_signals(ts)
    assert count == 1
    assert "save = output<User>({ alias: 'onSaved' });" in result


def test_preserves_access_modifiers():
    ts = (
        "import { Component, Input, Output, EventEmitter } from '@angular/core';\n\n"
        "@Component({})\n"
        "export class CardComponent {\n"
        "  public @Input() name: string = 'User';\n"
        "  protected @Output() notify = new EventEmitter<string>();\n"
        "}\n"
    )
    result, count = transform_angular_signals(ts)
    assert count == 2
    assert "public name = input<string>('User');" in result
    assert "protected notify = output<string>();" in result


def test_mixed_inputs_outputs_and_retained_event_emitter():
    ts = (
        "import { Component, Input, Output, EventEmitter } from '@angular/core';\n\n"
        "@Component({})\n"
        "export class CardComponent {\n"
        "  @Input() name: string = 'User';\n"
        "  @Output() notify = new EventEmitter<string>();\n"
        "  manualEmitter = new EventEmitter<boolean>();\n"
        "}\n"
    )
    result, count = transform_angular_signals(ts)
    assert count == 2
    # EventEmitter is still used by manualEmitter, so EventEmitter should remain in imports
    first_line = result.split("\n")[0]
    assert "EventEmitter" in first_line
    assert "input" in first_line
    assert "output" in first_line
    assert "Input" not in first_line
    assert "Output" not in first_line


def test_ignores_getters_and_setters():
    ts = (
        "import { Component, Input } from '@angular/core';\n\n"
        "@Component({})\n"
        "export class CardComponent {\n"
        "  @Input()\n"
        "  get value(): string { return this._val; }\n"
        "  set value(v: string) { this._val = v; }\n"
        "  private _val = '';\n"
        "}\n"
    )
    result, count = transform_angular_signals(ts)
    assert count == 0
    assert "@Input()" in result


def test_migrate_repository_signals(tmp_path):
    from amstralift.adapters.angular_signals import migrate_repository_signals

    src = tmp_path / "src" / "app"
    src.mkdir(parents=True, exist_ok=True)

    comp1 = src / "header.component.ts"
    comp1.write_text(
        "import { Component, Input, Output, EventEmitter } from '@angular/core';\n"
        "@Component({})\n"
        "export class HeaderComponent {\n"
        "  @Input() title: string = 'App';\n"
        "  @Output() logout = new EventEmitter<void>();\n"
        "}\n",
        encoding="utf-8",
    )

    spec = src / "header.component.spec.ts"
    spec.write_text("// Spec file should be skipped\n", encoding="utf-8")

    stats = migrate_repository_signals(tmp_path)
    assert "src/app/header.component.ts" in stats
    assert stats["src/app/header.component.ts"] == 2

    migrated = comp1.read_text(encoding="utf-8")
    assert "title = input<string>('App');" in migrated
    assert "logout = output<void>();" in migrated


def test_transform_view_child_and_view_children():
    ts = (
        "import { Component, ViewChild, ViewChildren, ElementRef, QueryList } from '@angular/core';\n\n"
        "@Component({})\n"
        "export class CardComponent {\n"
        "  @ViewChild('myCanvas') canvas!: ElementRef<HTMLCanvasElement>;\n"
        "  @ViewChild(UserAvatarComponent) avatar?: UserAvatarComponent;\n"
        "  @ViewChild('btn', { required: true }) button!: ElementRef;\n"
        "  @ViewChildren(ItemComponent) items!: QueryList<ItemComponent>;\n"
        "}\n"
    )
    result, count = transform_angular_signals(ts)
    assert count == 4
    assert "canvas = viewChild<ElementRef<HTMLCanvasElement>>('myCanvas');" in result
    assert "avatar = viewChild(UserAvatarComponent);" in result
    assert "button = viewChild.required<ElementRef>('btn');" in result
    assert "items = viewChildren(ItemComponent);" in result
    # Check imports updated
    first_line = result.split("\n")[0]
    assert "viewChild" in first_line
    assert "viewChildren" in first_line
    assert "ViewChild" not in first_line
    assert "ViewChildren" not in first_line
    assert "QueryList" not in first_line


def test_transform_content_child_and_content_children():
    ts = (
        "import { Component, ContentChild, ContentChildren, QueryList } from '@angular/core';\n\n"
        "@Component({})\n"
        "export class TabsComponent {\n"
        "  @ContentChild(TabLabelDirective) label!: TabLabelDirective;\n"
        "  @ContentChild('customHeader', { required: true }) header!: HeaderDirective;\n"
        "  @ContentChildren(TabComponent) tabs!: QueryList<TabComponent>;\n"
        "}\n"
    )
    result, count = transform_angular_signals(ts)
    assert count == 3
    assert "label = contentChild(TabLabelDirective);" in result
    assert "header = contentChild.required<HeaderDirective>('customHeader');" in result
    assert "tabs = contentChildren(TabComponent);" in result
    first_line = result.split("\n")[0]
    assert "contentChild" in first_line
    assert "contentChildren" in first_line
    assert "ContentChild" not in first_line
    assert "ContentChildren" not in first_line
    assert "QueryList" not in first_line


