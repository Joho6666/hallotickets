# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Damai.com (大麦网) ticket purchasing automation system with two platform modules:

- **Mobile** (`mobile/`): **Primary/recommended** — UIAutomator2 (u2) 直连 Android 设备自动化（Python）
- **Desktop** (`desktop/`): Tauri v1 desktop app — **deprecated, blocked by official channel restrictions, do not invest time here**

Mobile follows the flow: config → driver init → navigation → ticket selection → order submission. Desktop called Damai's mtop API directly from Rust but is no longer viable.

Performance is critical — this is competitive ticket-grabbing where milliseconds matter.

## Prerequisites

- **Python**: ^3.8 + Poetry
- **Mobile**: Android device/emulator + adb（无需额外服务进程）
- **Desktop**: Node.js 20+ + Rust toolchain + Yarn (deprecated — see above)

## Commands

```bash
# === Python (Mobile module) ===
poetry install
poetry run test                              # run all tests (coverage auto-enabled, 80% threshold)
poetry run pytest tests/unit/test_mobile_damai_app.py  # single file
poetry run pytest -k "test_name"             # single test
poetry run pytest -m unit                    # by marker (unit | integration | slow)

# Mobile scripts
mobile/scripts/start_ticket_grabbing.sh --probe --yes   # safe probe run
mobile/scripts/start_ticket_grabbing.sh --yes           # live ticket grabbing
mobile/scripts/run_from_prompt.sh --mode summary --yes "prompt text"  # NLP config: preview
mobile/scripts/run_from_prompt.sh --mode apply --yes "prompt text"    # NLP config: write config
mobile/scripts/run_from_prompt.sh --mode probe --yes "prompt text"    # NLP config: write + probe
mobile/scripts/benchmark_hot_path.sh          # hot path performance benchmark

# === Desktop (Tauri app — deprecated) ===
cd desktop && yarn install && yarn tauri dev
cargo test --manifest-path desktop/src-tauri/Cargo.toml
```

## Architecture

### Mobile (`mobile/`) — Primary Module

- `damai_app.py` — `DamaiBot`: UIAutomator2 直连设备，每次操作 ~30-60ms。Uses coordinate-based gesture clicks, hot-path coordinate caching, aggressive timeout tuning
- `config.py` — Mobile config via `Config.load_config()` reading `config.jsonc`
- `item_resolver.py` — Fetches event metadata (name, venue, dates, prices) from item URLs via Damai mobile API
- `prompt_parser.py` — Parses natural-language prompts into structured intent (quantity, date, city, price) with scoring
- `prompt_runner.py` — CLI entrypoint for natural-language ticket discovery and bot invocation
- `hot_path_benchmark.py` — Performance benchmarking for the ticket-grabbing hot path
- `logger.py` — Unified logging (Shanghai timezone, console INFO+ / file DEBUG+)

### Shared (`shared/`)

- `config_validator.py` — Validates URL format, non-empty lists, positive integers (used by mobile)
- `xpath_utils.py` — XPath string literal escaping via `concat()` to handle single/double quotes

### Desktop (`desktop/`) — Deprecated

- Frontend: Vue 3 + Vuex + Vue Router + Arco Design UI (Vite)
- Backend: Rust + reqwest (Tauri 1.3) calling Damai's mtop API
- All API requests use 3s timeout, spoofed mobile Chrome UA, and anti-crawl headers

### Configuration

- Mobile: `mobile/config.jsonc` (with optional `mobile/config.local.jsonc` for dev overrides via `--config` flag or `HATICKETS_CONFIG_PATH` env var)
- Desktop: SQLite database (managed via Tauri plugin)

### Tests (`tests/`)

- `conftest.py` — Shared fixtures and **module-level mocks for uiautomator2** (injected into `sys.modules` so tests run without real device dependencies)
- Custom markers auto-applied by file path: files under `unit/` get `@pytest.mark.unit`, under `integration/` get `@pytest.mark.integration`
- Coverage: 80% threshold enforced, covers `mobile/` only

### Import Convention (Important)

- **Mobile modules** use package-qualified imports (`from mobile.config import Config`, `mobile.damai_app`) — `mobile/` is NOT added to sys.path

### CI/CD

- `.github/workflows/test.yml` — push/PR CI: pytest on Python 3.8/3.9/3.10/3.12/3.13 matrix with 80% coverage gate
- `.github/workflows/release.yml` — Tag-triggered (`v*`): creates a GitHub Release from the tag annotation (`softprops/action-gh-release` + auto-generated notes). No Tauri build and **no binary asset** — Mobile is source-only, users upgrade via `git pull` + `poetry install`. The old cross-platform Tauri build was removed with the Desktop deprecation.
- `.github/workflows/_e2e_dryrun.yml` — self-hosted-runner real-device E2E scaffold (dry-run, disabled by default)

## Key Design Decisions

- Mobile is the primary approach; Desktop is deprecated due to official channel restrictions
- Mobile uses UIAutomator2 (u2) direct connection — no server process needed, ~30-60ms per operation
- Mobile uses coordinate-based gesture clicks over element.click() for speed
- Proxy support is first-class in the Desktop module (`ProxyBuilder` wraps reqwest with HTTP/SOCKS proxy)
