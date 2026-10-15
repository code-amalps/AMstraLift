# Contributing to AMstraLift

Thank you for your interest in contributing to **AMstraLift**! We welcome contributions from developers, security researchers, and maintainers across Angular, React, .NET, and Python ecosystems.

---

## Code of Conduct

Please maintain a collaborative, respectful, and constructive environment. We value technical rigor, honest assessments, and empathetic code reviews.

---

## Development Setup

AMstraLift uses modern Python tooling powered by **[uv](https://docs.astral.sh/uv/)**.

### 1. Prerequisites
- Python 3.11+
- Git 2.30+
- Node.js 18+ (for testing Angular and React adapters)
- .NET 6.0+ SDK (for testing .NET adapters)
- `uv` package manager (`pip install uv` or `curl -LsSf https://astral.sh/uv/install.sh | sh`)

### 2. Clone and Setup
```bash
git clone https://github.com/code-amalps/AMstraLift.git
cd AMstraLift

# Synchronize virtual environment with development dependencies
uv sync --group dev
```

### 3. Running the Test Suite
AMstraLift maintains a comprehensive test suite covering CLI, adapters, cryptographic attestation, monorepos, and sandbox gates:

```bash
# Run all tests
uv run pytest

# Run specific test modules
uv run pytest tests/test_cli.py -v
uv run pytest tests/test_modernize.py -v
uv run pytest tests/test_crypto.py -v
```

### 4. Code Quality & Formatting
We enforce formatting and static analysis using **Ruff**:

```bash
# Lint checks
uv run ruff check .

# Automated formatting
uv run ruff format .
```

---

## Architectural Guidelines

When contributing to AMstraLift, keep the following core invariants in mind:

1. **Deterministic Execution:** Do not introduce LLM calls or nondeterministic heuristics into transformation routines. Every AST transformation must produce identical output given identical input.
2. **The Refusal Pattern:** If an AST codemod or migration pattern encounters ambiguous syntax or unsupported user overrides, it **must refuse cleanly** rather than generating broken code. A refused transformation preserves the original file byte-for-byte.
3. **Sandbox Isolation:** All file mutations and compiler executions belong in **Stage A**. **Stage B** is read-only with respect to the working tree and is strictly responsible for branch creation, cryptographic verification, and PR publishing.
4. **Preserve Documentation Integrity:** Maintain existing comments, type annotations, and docstrings unless your PR explicitly refactors that functionality.

---

## Submitting Pull Requests

1. **Create a topic branch:**
   ```bash
   git checkout -b feat/my-new-codemod
   ```
2. **Add unit tests:** Every new feature, adapter method, or bugfix must include automated unit tests under `tests/`.
3. **Verify locally:** Ensure `uv run pytest` and `uv run ruff check .` pass cleanly before pushing.
4. **Open a PR:** Open a Pull Request targeting the `Dev` branch with a clear description of the problem solved, testing methodology, and relevant issue links.

---

## License & Attribution Notice

AMstraLift is licensed under the **[GNU Affero General Public License v3.0 (AGPL-3.0-or-later)](LICENSE)** with Section 7 author attribution. By contributing code to AMstraLift, you agree that your contributions will be licensed under the project's AGPL-3.0 license.
