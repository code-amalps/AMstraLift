"""Unit tests for Angular self-closing tags modernization."""

from pathlib import Path
from amstralift.adapters.angular_self_closing import (
    migrate_repository_self_closing,
    transform_angular_self_closing,
    transform_inline_templates_in_ts,
)


def test_basic_empty_component_tag():
    html = "<app-header></app-header>"
    res, count = transform_angular_self_closing(html)
    assert count == 1
    assert res == "<app-header />"


def test_empty_component_tag_with_attributes():
    html = '<app-header [title]="title" (action)="onAction()"></app-header>'
    res, count = transform_angular_self_closing(html)
    assert count == 1
    assert res == '<app-header [title]="title" (action)="onAction()" />'


def test_multiline_attributes():
    html = """<app-header
  [title]="title"
  (action)="onAction()">
</app-header>"""
    expected = """<app-header
  [title]="title"
  (action)="onAction()" />"""
    res, count = transform_angular_self_closing(html)
    assert count == 1
    assert res == expected


def test_attribute_expression_with_comparison_operator():
    html = '<app-counter [count]="count > 0 ? count : 0"></app-counter>'
    res, count = transform_angular_self_closing(html)
    assert count == 1
    assert res == '<app-counter [count]="count > 0 ? count : 0" />'


def test_standard_html_tags_not_converted():
    html = '<div class="box"></div><span></span><p></p><button type="button"></button>'
    res, count = transform_angular_self_closing(html)
    assert count == 0
    assert res == html


def test_non_empty_tags_not_converted():
    html = '<app-card><span>Card Content</span></app-card><app-button>Click me</app-button>'
    res, count = transform_angular_self_closing(html)
    assert count == 0
    assert res == html


def test_already_self_closing_tag_preserved():
    html = '<app-header [title]="pageTitle" />'
    res, count = transform_angular_self_closing(html)
    assert count == 0
    assert res == html


def test_angular_builtin_tags():
    html = '<router-outlet></router-outlet>\n<ng-container></ng-container>\n<ng-content></ng-content>'
    expected = '<router-outlet />\n<ng-container />\n<ng-content />'
    res, count = transform_angular_self_closing(html)
    assert count == 3
    assert res == expected


def test_nested_tags_conversion():
    html = '<app-outer><app-inner></app-inner></app-outer>'
    expected = '<app-outer><app-inner /></app-outer>'
    res, count = transform_angular_self_closing(html)
    assert count == 1
    assert res == expected


def test_html_comments_skipped():
    html = '<!-- <app-header></app-header> -->\n<app-footer></app-footer>'
    expected = '<!-- <app-header></app-header> -->\n<app-footer />'
    res, count = transform_angular_self_closing(html)
    assert count == 1
    assert res == expected


def test_script_and_style_blocks_skipped():
    html = """<script>
  const x = '<app-dummy></app-dummy>';
</script>
<app-main></app-main>"""
    expected = """<script>
  const x = '<app-dummy></app-dummy>';
</script>
<app-main />"""
    res, count = transform_angular_self_closing(html)
    assert count == 1
    assert res == expected


def test_inline_templates_in_ts_backticks():
    ts = """import { Component } from '@angular/core';

@Component({
  selector: 'app-root',
  standalone: true,
  template: `
    <app-header></app-header>
    <main>
      <router-outlet></router-outlet>
    </main>
    <app-footer></app-footer>
  `,
})
export class AppComponent {}
"""
    expected = """import { Component } from '@angular/core';

@Component({
  selector: 'app-root',
  standalone: true,
  template: `
    <app-header />
    <main>
      <router-outlet />
    </main>
    <app-footer />
  `,
})
export class AppComponent {}
"""
    res, count = transform_inline_templates_in_ts(ts)
    assert count == 3
    assert res == expected


def test_inline_templates_in_ts_quotes():
    ts = """@Component({
  selector: 'app-card',
  template: '<app-icon [name]="icon"></app-icon>',
})
export class CardComponent {}"""
    expected = """@Component({
  selector: 'app-card',
  template: '<app-icon [name]="icon" />',
})
export class CardComponent {}"""
    res, count = transform_inline_templates_in_ts(ts)
    assert count == 1
    assert res == expected


def test_migrate_repository_self_closing(tmp_path: Path):
    # Setup html template
    html_file = tmp_path / "src" / "app" / "test.component.html"
    html_file.parent.mkdir(parents=True)
    html_file.write_text(
        '<app-sidebar></app-sidebar>\n<router-outlet></router-outlet>',
        encoding="utf-8",
    )

    # Setup ts file with inline template
    ts_file = tmp_path / "src" / "app" / "inline.component.ts"
    ts_file.write_text(
        '@Component({\n  template: `<app-nav></app-nav>`\n})\nexport class NavComponent {}',
        encoding="utf-8",
    )

    # Run migration
    stats = migrate_repository_self_closing(tmp_path)

    assert len(stats) == 2
    assert sum(stats.values()) == 3

    assert html_file.read_text(encoding="utf-8") == "<app-sidebar />\n<router-outlet />"
    assert (
        ts_file.read_text(encoding="utf-8")
        == "@Component({\n  template: `<app-nav />`\n})\nexport class NavComponent {}"
    )
