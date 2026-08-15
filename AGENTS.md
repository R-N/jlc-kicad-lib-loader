# Repository Guidelines

## Project Overview

`jlc-kicad-lib-loader` is a KiCad 8+ Action Plugin (pcbnew) that searches JLCPCB/LCSC/EasyEDA Pro parts and imports them into a project-local `.elibz` library plus converted STEP 3D models. It is distributed as a KiCad PCM archive (`com.github.dsa-t.jlc-kicad-lib-loader`).

Flat, non-packaged Python: every `.py` at the repo root is copied verbatim into the archive's `plugins/` directory. There is no installable package, no dependency manifest, and no test suite.

## Architecture & Data Flow

```
__init__.py                       EasyEDALibLoaderPlugin().register()   <- KiCad entry
  easyeda_lib_loader.py           ActionPlugin, dialog wiring, worker threads, HTTP session
    easyeda_lib_loader_dialog.py  GENERATED wxFormBuilder dialog (do not edit)
    component_loader.py           ComponentLoader: Pro -> .elibz, Std -> -std.zip, + STEP models
      decryptor.py                AES-GCM + gzip for encrypted dataStrId payloads
    config_manager.py             ConfigManager (.ini) + LibraryTableManager (lib tables)
```

**Search flow** (`easyeda_lib_loader.py`): `onSearch`/`onNextPage`/`onPrevPage` -> `loadSearchPage` spawns a daemon `searchThread` running either `searchFn` (Pro: `POST https://pro.easyeda.com/api/v2/devices/search`, facet `uid`/`path` = `lcsc`/`user`) or `stdSearchFn` (Std: `POST https://easyeda.com/api/components/search` with `type=3`, `uid=user`) -> rows pushed into `m_searchResultsTree` via `wx.CallAfter`. Std rows carry `std:<uuid>` in the first column; that prefix is what routes a part to the Std path at download time. A new search interrupts the in-flight thread with a ctypes-injected `KeyboardInterrupt` (`interrupt_thread`, ~lines 60-78) then joins.

**Preview flow**: `onSearchItemSelected` fetches the detail document for the selected row and renders it into `self.webView` (a `wx.html2.WebView`, absent on Flatpak/webkit-less Linux — the pane degrades to a `wx.StaticText`). Three branches, all producing the same page: two `<figure>`s side by side, **Symbol** and **Footprint**, above a table of the part's parameters.

- **Std** (`std:` rows) — `GET /api/components/{uuid}`; figures are `<img>`s built by `thumbUrl`, which falls back to `https://image.easyeda.com/components/<uuid>.png` when the JSON `thumb` field is null (always the case for `packageDetail`). `imageMarkup` gives each `<img>` an `onerror` that hides its figure, so an unrendered document drops out without needing a probe request.
- **Pro device uuid rows** (JLC Public) — `GET https://pro.easyeda.com/api/devices/{uuid}`; Pro documents have **no thumbnail service** (`image.easyeda.com` 403s on their uuids), so `productSvgs` pulls inline SVG from `https://easyeda.com/api/products/<Cxxxx>/svgs` — the endpoint behind JLCPCB's own part preview — keyed by the `Supplier Part` attribute, taking `docType` 2 as the symbol and 4 as the footprint. An unknown or unrendered code answers `200` with `success:false` and no `result`, which yields empty markup and simply omits the figures. This call goes **last** in the `try` so a drawing failure cannot cost the "Open in EasyEDA Pro" link.
- **LCSC `C…` code rows** (JLC System) — the webview loads JLCPCB's `lcsvg/svg.html?code=…` page directly; it renders both drawings itself, so nothing is built locally.

Detail-fetch failures are logged with `warning(...)` so the pane can never go blank without a trace.

**Download flow**: `onDownload` (~139-187) reads part codes/UUIDs from `m_textCtrlParts`, resolves the target path (`lib_field` if absolute, else `$KIPRJMOD/<lib_field>`), persists the library name, prompts to register library tables, then a daemon `downloadThread` runs `ComponentLoader(...).downloadAll`:

1. `downloadSymFp` (Pro) — `C…` codes resolved via `searchByCodes`, then `GET /api/devices/{uuid}` and `GET /api/v2/components/{uuid}` through two `ThreadPoolExecutor` pools; `extractDataStr` falls back to fetching `dataStrId` and decrypting it; writes/merges the zip `<target>/<name>.elibz` containing `device.json`, `SYMBOL/<uuid>.esym`, `FOOTPRINT/<uuid>.efoo` (pre-existing entries are merged, not clobbered).
2. `downloadStd` (Std) — `GET https://easyeda.com/api/components/{uuid}` returns the symbol in `result.dataStr` and the footprint in `result.packageDetail.dataStr`. Each document is wrapped verbatim in a `LIB~x~y~params~…#@$<shapes>` string (`buildStdLibShape`) and written to `<target>/<name>-std.zip` as `symbols.json` (a `docType 5` schematic list) and `footprints.json` (a `docType 3` PCB doc). This is exactly what KiCad's native Std importers enumerate — **no geometry conversion here, and none should be added**. Note the importers stop at the FIRST matching `.json` in a zip, which is why all symbols share one document and all footprints another.
3. `downloadModels(modelTasks)` — shared by both sources; `modelTasks` maps model uuid -> `(target file, fit X mm, fit Y mm)`, built by `collectProModels` (`3D Model Transform`, mils) or `collectStdModels` (layer-19 `outline3D` SVGNODE, EasyEDA units of 10 mil via `STD_UNIT_TO_MM`). Downloads raw STEP to `<KIPRJMOD>/EASYEDA_MODELS/<title>.step` (always `KIPRJMOD` — both KiCad importers hardcode that path) via `urllib.request.urlretrieve` into a `<file>_jlc` temp (pool of 8), then a **single-threaded** pool runs `pcbnew.UTILS_STEP_MODEL.LoadSTEP` -> scale to fit -> translate to origin -> `SaveSTEP`. pcbnew is not thread-safe; keep that pool at 1.

**Library tables** (`config_manager.py`): `LibraryTableManager.LIB_ENTRY_TYPES` maps `(source, table)` to the KiCad plugin type — Pro entries are `EasyEDA (JLCEDA) Pro` / `EasyEDA / JLCEDA Pro` on `<name>.elibz`, Std entries are `EasyEDA (JLCEDA) Std` / `EasyEDA / JLCEDA Std` on `<name>-std.zip` under the separate library name `<name>_Std`. Rows are inserted into `sym-lib-table`/`fp-lib-table` (created when absent) after a YES/NO `wx.MessageDialog`; `prompt_add_library(..., sources)` only prompts for the sources actually being downloaded.

## Key Directories

- repo root — all plugin source; flat by design, shipped as-is.
- `pcm/` — packaging only: `metadata.template.json`, `icon.png` (copied to `resources/`).
- `.github/workflows/` — single release workflow.
- `.out/` — build output (`.out/archive/` is gitignored; the zip and `.out/env` are **not**).

## Development Commands

```bash
./create_pcm_archive.sh              # build .out/jlc-kicad-lib-loader-<VERSION>-pcm.zip + .out/env
CI_ENV=/tmp/env ./create_pcm_archive.sh   # redirect the env-var dump
```

Archive layout:

```
metadata.json                 # template with VERSION/sha/size/url placeholders resolved or stripped
plugins/*.py  *.png  VERSION
resources/icon.png
```

No build system, no lint, no test command exists. Requires `zip`, `shasum`, `sed`.

Manual testing: copy the root `.py` files into the KiCad 3rd-party plugin dir (or install the built zip via PCM "Install from File…"), then Tools > External Plugins > Refresh in pcbnew.

## Code Conventions & Common Patterns

- **Naming**: modules and locals are `snake_case`; functions and methods are `camelCase` (`downloadSymFp`, `getUuidFirstPart`, `onDownload`, `createDialog`). Classes are `PascalCase`. Match the surrounding file — do not "fix" the camelCase methods.
- **Widgets**: generated controls are `m_`-prefixed (`m_log`, `m_progress`, `m_actionBtn`, `m_searchResultsTree`, `m_textCtrlParts`, `m_textCtrlOutLibName`, `m_libSourceChoice`, `m_debug`).
- **Generated code**: `easyeda_lib_loader_dialog.py` is wxFormBuilder 4.2.1 output from `easyeda_lib_loader_dialog.fbp` — never hand-edit; change the `.fbp` in wxFormBuilder and regenerate. Only the splitter `EVT_IDLE` handlers are generated; **all functional handlers are bound externally** in `easyeda_lib_loader.py` (~454-465).
- **Threading**: long work runs on daemon `threading.Thread`; all UI updates go through `wx.CallAfter`. Never touch wx or pcbnew from a worker.
- **Logging**: standard `logging`; `createDialog` clears existing handlers and installs `WxTextCtrlHandler` (~84-91) that writes into `m_log`. Log, do not `print`.
- **HTTP**: one module-level `requests.Session` with User-Agent `jlc-kicad-lib-loader/<version>`. No retries and no timeouts anywhere — adding a timeout is an improvement, not a regression.
- **Errors**: per-item `try/except` with counters inside batch loops so one bad part does not abort the run; user-facing failures surface via the log pane or `wx.MessageDialog`.
- **Optional deps**: `decryptor.py` raises at import with pip instructions when `pycryptodome` is missing. Keep optional dependencies lazily imported with actionable messages.
- **State**: `ConfigManager` persists `[Library] name` to `<KIPRJMOD>/jlc-kicad-lib-loader.ini` via `configparser`. No globals beyond the session and `MODELS_DIR`.

## Important Files

| Path | Role |
|---|---|
| `__init__.py` | `EasyEDALibLoaderPlugin().register()` — plugin registration |
| `easyeda_lib_loader.py` | ActionPlugin, dialog wiring, threads, session, version (`VERSION` file, fallback `0.0.0`) |
| `component_loader.py` | `ComponentLoader.downloadAll` / `downloadSymFp` / `downloadModels` |
| `config_manager.py` | `ConfigManager`, `LibraryTableManager` |
| `decryptor.py` | `decryptDataStrIdData` (AES-GCM, tag = last 16 bytes, then gzip) |
| `easyeda_lib_loader_dialog.py` / `.fbp` | generated dialog / its wxFormBuilder source |
| `create_pcm_archive.sh` | build; `VERSION` is **hardcoded** (currently `1.0.11`) |
| `pcm/metadata.template.json` | PCM v1 metadata; `kicad_version` min `8.0.7` |
| `.github/workflows/release.yml` | build on push/tag/dispatch; draft Release on tags, artifact otherwise |

## Runtime/Tooling Preferences

- Runs inside **KiCad's bundled Python** (KiCad >= 8.0.7); `pcbnew` and `wx` come from KiCad, never from pip.
- No package manager, no virtualenv, no `pyproject.toml`/`requirements.txt`. Do not add one without cause — the plugin ships as loose files.
- Optional runtime deps documented in `README.md`: `pycryptodome` (encrypted part data), `certifi` (macOS SSL), `python3-wxgtk-webview4.0` (Linux preview pane).
- UI edits require **wxFormBuilder 4.2.1**; icons come from `easyeda_lib_loader.svg` (Inkscape export).

## Testing & QA

There is no test framework, no `conftest.py`, no linter config, and no CI test step — verification is manual inside KiCad. When changing behavior:

1. Build with `./create_pcm_archive.sh` and confirm the zip contents.
2. Load the plugin in pcbnew, search a known part (e.g. `C25804`), download it, and confirm `<lib>.elibz` plus `EASYEDA_MODELS/*.step` appear and the library shows up in `sym-lib-table`/`fp-lib-table`.
3. For pure-logic changes (decryption, transform math, `.elibz` merge), a standalone `assert`-based script run under a plain Python 3 interpreter is acceptable; keep `pcbnew` imports out of that path.

## Gotchas

- Bumping the release version means editing `VERSION="…"` in `create_pcm_archive.sh` — the git tag does not drive it.
- `.out/*.zip` and `.out/env` are not gitignored; do not commit them.
- STEP models always land under `$KIPRJMOD/EASYEDA_MODELS`, independent of the chosen library path.
- `pcm/icon.png` is copied into `resources/` but `metadata.template.json` has no `icon` key referencing it.
