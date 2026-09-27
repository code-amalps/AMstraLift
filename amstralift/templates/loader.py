"""Template loader and generator for CI workflow configurations."""

from pathlib import Path

GITHUB_SCHEDULED_TEMPLATE = """name: AMstraLift Scheduled Dependency Upgrade

on:
  schedule:
    # Run weekly on Monday at 04:00 UTC (staggered off-peak)
    - cron: '0 4 * * 1'
  workflow_dispatch:
    inputs:
      ecosystem:
        description: 'Target ecosystem (leave blank for auto-detection)'
        required: false
        type: choice
        default: 'auto'
        options:
          - 'auto'
          - 'angular'
          - 'python'
          - 'dotnet'
          - 'react'
      dry_run:
        description: 'Dry run mode (simulate without pushing or opening PR)'
        required: false
        type: boolean
        default: false
      publish:
        description: 'Publish reviewable PR on GitHub'
        required: false
        type: boolean
        default: true

concurrency:
  group: amstralift-${{ github.ref }}
  cancel-in-progress: false

permissions:
  contents: write
  pull-requests: write
  issues: write

jobs:
  amstralift-upgrade:
    name: AMstraLift Two-Stage Upgrade
    runs-on: ubuntu-latest
    timeout-minutes: 30

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Install uv package manager
        uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install Node.js (for Angular / React adapters)
        if: ${{ inputs.ecosystem == 'angular' || inputs.ecosystem == 'react' || inputs.ecosystem == 'auto' || github.event_name == 'schedule' }}
        uses: actions/setup-node@v4
        with:
          node-version: '20'

      - name: Set up .NET (for .NET adapter)
        if: ${{ inputs.ecosystem == 'dotnet' || inputs.ecosystem == 'auto' || github.event_name == 'schedule' }}
        uses: actions/setup-dotnet@v4
        with:
          dotnet-version: '8.0.x'

      - name: Configure Git Author Identity
        run: |
          git config user.name "amstralift-bot[bot]"
          git config user.email "amstralift-bot[bot]@users.noreply.github.com"
          git config core.autocrlf false

      - name: Execute AMstraLift Engine
        id: upgrade
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          ECO_ARG=""
          if [ "${{ inputs.ecosystem }}" != "" ] && [ "${{ inputs.ecosystem }}" != "auto" ]; then
            ECO_ARG="--ecosystem ${{ inputs.ecosystem }}"
          fi

          FLAGS="--output-bundle amstralift-bundle.json"
          if [ "${{ inputs.dry_run }}" = "true" ]; then
            FLAGS="$FLAGS --dry-run"
          fi
          if [ "${{ inputs.publish }}" = "true" ] || [ "${{ github.event_name }}" = "schedule" ]; then
            if [ "${{ inputs.dry_run }}" != "true" ]; then
              FLAGS="$FLAGS --publish"
            fi
          fi

          uvx amstralift run --repo . $ECO_ARG $FLAGS

      - name: Upload Cryptographic Advisory Bundle Artifact
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: amstralift-bundle-${{ github.run_id }}
          path: amstralift-bundle.json
          retention-days: 90
          if-no-files-found: ignore
"""

GITHUB_REUSABLE_TEMPLATE = """name: AMstraLift Reusable Upgrade Workflow

on:
  workflow_call:
    inputs:
      ecosystem:
        description: 'Target ecosystem (leave blank for auto-detection)'
        required: false
        type: string
        default: 'auto'
      target_branch:
        description: 'Base branch to upgrade against'
        required: false
        type: string
        default: 'main'
      dry_run:
        description: 'Simulate without creating branch or PR'
        required: false
        type: boolean
        default: false
      publish:
        description: 'Publish reviewable PR to GitHub'
        required: false
        type: boolean
        default: true
    secrets:
      token:
        description: 'GitHub Token with contents:write, pull-requests:write, issues:write permissions'
        required: false

jobs:
  upgrade:
    name: AMstraLift Execution
    runs-on: ubuntu-latest
    timeout-minutes: 30

    permissions:
      contents: write
      pull-requests: write
      issues: write

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4
        with:
          ref: ${{ inputs.target_branch }}
          fetch-depth: 0

      - name: Install uv
        uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Configure Git Author
        run: |
          git config user.name "amstralift-bot[bot]"
          git config user.email "amstralift-bot[bot]@users.noreply.github.com"
          git config core.autocrlf false

      - name: Run AMstraLift
        env:
          GITHUB_TOKEN: ${{ secrets.token || secrets.GITHUB_TOKEN }}
        run: |
          ECO_ARG=""
          if [ "${{ inputs.ecosystem }}" != "" ] && [ "${{ inputs.ecosystem }}" != "auto" ]; then
            ECO_ARG="--ecosystem ${{ inputs.ecosystem }}"
          fi

          FLAGS="--branch ${{ inputs.target_branch }} --output-bundle amstralift-bundle.json"
          if [ "${{ inputs.dry_run }}" = "true" ]; then
            FLAGS="$FLAGS --dry-run"
          fi
          if [ "${{ inputs.publish }}" = "true" ] && [ "${{ inputs.dry_run }}" != "true" ]; then
            FLAGS="$FLAGS --publish"
          fi

          uvx amstralift run --repo . $ECO_ARG $FLAGS

      - name: Archive Advisory Bundle
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: amstralift-bundle-${{ github.run_id }}
          path: amstralift-bundle.json
          retention-days: 90
          if-no-files-found: ignore
"""

GITLAB_CI_TEMPLATE = """# AMstraLift Scheduled Dependency Upgrade for GitLab CI
stages:
  - upgrade

variables:
  UV_CACHE_DIR: "$CI_PROJECT_DIR/.cache/uv"
  AMSTRALIFT_DRY_RUN: "false"
  AMSTRALIFT_PUBLISH: "true"

cache:
  paths:
    - .cache/uv

amstralift:upgrade:
  stage: upgrade
  image: python:3.12-slim
  rules:
    - if: $CI_PIPELINE_SOURCE == "schedule"
    - if: $CI_PIPELINE_SOURCE == "web"
  before_script:
    - apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates
    - curl -LsSf https://astral.sh/uv/install.sh | sh
    - export PATH="$HOME/.local/bin:$PATH"
    - git config --global user.name "amstralift-bot"
    - git config --global user.email "amstralift-bot@gitlab.internal"
    - git config --global --add safe.directory "$CI_PROJECT_DIR"
  script:
    - |
      FLAGS="--output-bundle amstralift-bundle.json"
      if [ "$AMSTRALIFT_DRY_RUN" = "true" ]; then
        FLAGS="$FLAGS --dry-run"
      fi
      if [ "$AMSTRALIFT_PUBLISH" = "true" ] && [ "$AMSTRALIFT_DRY_RUN" != "true" ]; then
        FLAGS="$FLAGS --publish"
      fi

      uvx amstralift run --repo . $FLAGS
  artifacts:
    name: "amstralift-bundle-$CI_JOB_ID"
    when: always
    expire_in: 90 days
    paths:
      - amstralift-bundle.json
"""


def get_template_content(provider: str = "github", variant: str = "scheduled") -> str:
    """Retrieve CI template content by provider and variant."""
    prov = provider.lower()
    var = variant.lower()

    if prov == "github":
        if var == "reusable":
            return GITHUB_REUSABLE_TEMPLATE
        return GITHUB_SCHEDULED_TEMPLATE
    elif prov == "gitlab":
        return GITLAB_CI_TEMPLATE
    raise ValueError(f"Unsupported provider '{provider}'. Supported providers: 'github', 'gitlab'.")


def generate_ci_template(
    target_dir: Path,
    provider: str = "github",
    variant: str = "scheduled",
    overwrite: bool = False,
) -> Path:
    """Generate turnkey CI configuration file in the target repository directory."""
    target_dir = target_dir.resolve()
    content = get_template_content(provider=provider, variant=variant)

    if provider.lower() == "github":
        workflows_dir = target_dir / ".github" / "workflows"
        workflows_dir.mkdir(parents=True, exist_ok=True)
        filename = "amstralift-reusable.yml" if variant.lower() == "reusable" else "amstralift-scheduled.yml"
        out_file = workflows_dir / filename
    elif provider.lower() == "gitlab":
        out_file = target_dir / ".gitlab-ci-amstralift.yml"
    else:
        raise ValueError(f"Unknown provider '{provider}'")

    if out_file.exists() and not overwrite:
        raise FileExistsError(f"CI template already exists at {out_file}. Set overwrite=True to overwrite.")

    out_file.write_text(content.strip() + "\n", encoding="utf-8")
    return out_file
