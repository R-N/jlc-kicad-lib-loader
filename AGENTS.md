# Repository Guidelines

## Project Overview

`jlc-kicad-lib-loader` is a KiCad 8+ Action Plugin (pcbnew) that searches JLCPCB/LCSC/EasyEDA Pro parts and imports them into a project-local `.elibz` library plus converted STEP 3D models. It is distributed as a KiCad PCM archive (`com.github.dsa-t.jlc-kicad-lib-loader`).

Flat, non-packaged Python: every `.py` at the repo root is copied verbatim into the archive's `plugins/` directory. There is no installable package and no dependency manifest. Tests live in `tests/` and are plain `assert` scripts — **keep them out of the repo root**, or the build ships them to users.

## Architecture & Data Flow

```
__init__.py                       EasyEDALibLoaderPlugin().register()   <- KiCad entry
  easyeda_lib_loader.py           ActionPlugin, dialog wiring, worker threads, HTTP session
    easyeda_lib_loader_dialog.py  GENERATED wxFormBuilder dialog (do not edit)
    component_loader.py           ComponentLoader: Pro -> .elibz, Std -> -std.zip, + STEP models
      decryptor.py                AES-GCM + gzip for encrypted dataStrId payloads
      pro_render.py               Pro dataStr -> SVG, for the preview pane only
    config_manager.py             ConfigManager (.ini) + LibraryTableManager (lib tables)
```

**Dialog layout** (`easyeda_lib_loader_dialog.fbp`): a search bar (`m_libSourceChoice`, `m_textCtrlSearch`, `m_searchBtn`, `m_textCtrlFilter`) over `m_splitterMain`. **Left pane** (`m_resultsPanel`) is the whole workflow: results grid `m_searchResultsTree`, the status/pagination row (`m_searchStatus`, `m_searchPage`, `m_prevPageBtn`, `m_nextPageBtn`), then the download queue — `m_queueLabel`, `m_queueList`, and Add/Paste/Remove/Clear on a single row underneath (`gSizerQueueButtons`, 1x4). **Right pane** (`m_inspectorPanel`) is only the part: `m_previewNotebook`, a `wxNotebook` whose two pages are `m_symbolPanel` ("Symbol") and `m_footprintPanel` ("Footprint"), over the details pane (`m_partTitle` on its own row, then `m_searchHyperlink1..3`, then `m_paramsList`). The queue belongs to the left pane on purpose: full-width at the bottom it squeezed the preview vertically, and in the narrower column its four columns only fit because the buttons moved below it rather than beside it. `m_partTitle` has its own row for the same reason — beside three hyperlinks in a 475px pane the links were pushed off the edge. Below the splitter, spanning the dialog: the library row (`m_textCtrlOutLibName`, `m_browseBtn`, `m_actionBtn`), the progress row (`m_progress`, `m_resultStatus`, `m_debug`, `m_closeButton`), and `m_logPane`, a `wxCollapsiblePane` that starts collapsed and auto-expands on the first WARNING (`WxTextCtrlHandler`'s `onProblem`).

**Search flow** (`easyeda_lib_loader.py`): `onSearch`/`onNextPage`/`onPrevPage` -> `loadSearchPage` spawns a daemon `searchThread` running `searchWorker`, which walks `SOURCE_SEARCHES[selection]` — a list of `(label, fn)` pairs built in `createDialog`, indexed to match `m_libSourceChoice`: **All Sources** runs both APIs, **JLC System** the Pro `lcsc` facet, **JLC Public** the Pro `user` facet, **EasyEDA Std** the Std API. Rows arrive through `addRows` -> `renderRows` (`wx.CallAfter`), which applies `m_textCtrlFilter` via `rowMatches` and the clicked column via `sortKey`, so filtering and sorting never re-query. `proRow(entry, source)` and `stdRow(entry)` build the 8 columns of `RESULT_COLUMNS`; the `Src` column comes from the facet through `PRO_FACET_SOURCE`, never from the presence of a part code. `Package` shows the `Supplier Footprint` attribute, not the footprint document's title. Std rows carry `std:<uuid>` in the `Code` column; that prefix routes a part to the Std path at download time. A new search interrupts the in-flight thread with a ctypes-injected `KeyboardInterrupt` (`interrupt_thread`) then joins.

`proSearchFn(facet, ...)` counts **only the facet it asked for**: the Pro API answers with a list per facet whatever you queried, so summing `facets` claimed thousands of parts and pages that could not be paged to, and copying every list leaked other facets' rows into the grid.
The 8 result columns are `Src, Code, Name, Description, Package, Class, Type, By`: **Name** is the device's `display_title` (the part/symbol number), **Description** is `attributes["LCSC Part Name"]` (the human description, e.g. "100k ohm ±1% 62.5mW"); there is no separate MPN column because `display_title` already carries it. Each row ends with a hidden searchable cell, built by `searchableText` from the entry's `display_title`, `title`, category `tags`, and every string `attribute` value — so the filter box matches a part number, the description, a parameter value ("100k ohm", "±1%") and a category ("resistor"). `searchWorker` also post-filters the fetched page by the search words: EasyEDA's search is fuzzy ("Resistor 100k" returns 1Ω resistors and 10µH inductors too), so it drops rows that do not match every word, but only when that leaves at least one row — a term the data stores differently must not blank the page.

**Preview flow**: `onSearchItemSelected` fetches the detail document for the selected row, fills `m_paramsList` (`setParams`) and the three hyperlinks (`setLinks`), and renders one drawing into each of `self.symbolView` and `self.footprintView` — two separate `wx.html2.WebView`s, one per notebook page, absent on Flatpak/webkit-less Linux, where each pane degrades to a `wx.StaticText`. `showDrawing(view, markup, kind)` puts the markup through `drawingPage()` so the drawing scales to its page, and shows an explanatory note when there is nothing to draw. The page is pannable and zoomable: `DRAWING_PAGE` wraps the drawing in a clipping `#vp` viewport and a transformed `#cv` canvas, and ~30 lines of vanilla JS give wheel-zoom anchored on the cursor (clamped 0.2x-40x), drag-to-pan, and double-click to reset. `transform-origin: 0 0` is load-bearing — the zoom keeps the point under the cursor fixed by solving against a top-left origin, and any other origin drifts. `mousemove`/`mouseup` are bound on `window`, not the viewport, so a drag that leaves the panel still tracks and still ends. `drawingPage()` substitutes with `str.replace`, **not** `str.format`: the stylesheet and script are mostly braces, and EasyEDA markup can contain them too. Three sources of markup:

- **Std** (`std:` rows) — `GET /api/components/{uuid}`; each panel gets an `<img>` built by `thumbUrl`, which falls back to `https://image.easyeda.com/components/<uuid>.png` when the JSON `thumb` field is null (always the case for `packageDetail`). `imageMarkup` gives the `<img>` an `onerror` that replaces it with the note, so an unrendered document does not need a probe request.
- **Pro** — `GET https://pro.easyeda.com/api/devices/{uuid}`; an LCSC `C…` row is resolved to its device uuid first through `PRO_SEARCH_BY_CODES_URL` (`/api/devices/{code}` 404s). Pro documents have **no thumbnail service** (`image.easyeda.com` 403s on their uuids), so `productSvgs` pulls inline SVG from `https://easyeda.com/api/products/<Cxxxx>/svgs` — the endpoint behind JLCPCB's own part preview — keyed by the `Supplier Part` attribute, taking `docType` 2 as the symbol and 4 as the footprint. An unknown or unrendered code answers `200` with `success:false` and no `result`, which yields empty markup. EasyEDA only renders parts carrying an LCSC code, so when that yields nothing `proDrawings` falls back to `pro_render`, which draws both documents locally (see below). Both calls go **last** in the `try` so a drawing failure cannot cost the links or the parameters.

`setParams` puts **Symbol in KiCad** and **Footprint in KiCad** first, taken from the device's `symbol.display_title` and `footprint.display_title` (Std: the document title and `packageDetail`'s `package`). Those are the names KiCad's importers use, and they are neither the part number nor the device title — one SOT-23 footprint document serves hundreds of parts, so a user searching pcbnew for `AO3401A` finds nothing. Nothing else in the UI carries that information.

Detail-fetch failures are logged with `warning(...)`, which also pops the log pane open, so the panes can never go blank without a trace.

**Local Pro rendering** (`pro_render.py`): `symbolSvg`/`footprintSvg` turn a Pro document's `dataStr` into inline SVG, reached through `component_loader.fetchDataStr` (`GET /api/v2/components/{uuid}` plus the `dataStrId` decryption fallback). Every field index and unit convention is copied from KiCad's own importers — `eeschema/sch_io/easyedapro` and `pcbnew/pcb_io/easyedapro` — so the preview agrees with what importing the part produces: **Y grows upwards** (both KiCad parsers negate it), symbol coordinates are **10-mil units**, footprint coordinates are **mils**, `FONTSTYLE` index 5 is the font size scaled by `0.62`, and pin `rotation` points from the connection tip back towards the body. `contour` mirrors `ParseContour` (`L`/`ARC`/`CARC`/`C`/`CIRCLE`/`R` tokens). Footprint drawing is restricted to `ARTWORK_LAYERS` (copper, silkscreen, outline, multi) using the palette in the document's own `LAYER` lines; mask, paste, fab, component-shape/marking, pin-soldering/floating and keepout layers are documentation that EasyEDA does not draw either, and rendering them buries the pads under opaque blocks. Pads are drawn last so silkscreen cannot cover them. A document with no geometry (empty `dataStr`, or a title-block `TABLE`/placeholder entry — common in the user library) renders to `""`, and the preview then shows the `.note` paragraph instead. **This renderer is preview-only; the download path still ships documents verbatim and converts no geometry.**

**Queue and download flow**: parts are queued explicitly, not typed into a text box. `PartQueue` (pure, no wx) holds `(source, code, name)` rows keyed by code, so it dedupes; `queueRows` fills it from the grid selection or a double-click, `addCodes` from pasted text (`onQueuePaste`), and `refreshQueue` mirrors it into `m_queueList` and gates `m_actionBtn`. `onDownload` resolves the target path (`m_textCtrlOutLibName` if absolute, else `$KIPRJMOD/<name>`), persists the library name, prompts to register library tables, then a daemon `downloadThread` runs `ComponentLoader(...).downloadAll`, which returns a summary dict `{symbols, footprints, models, skipped, failed, error}`. `onDownloadFinished` turns that into one line in `m_resultStatus` and **empties the queue on success**, keeping only the parts that failed — the old dialog left the list in place, so the next Download silently repeated it.

1. `downloadSymFp` (Pro) — `C…` codes resolved via `searchByCodes`, then `GET /api/devices/{uuid}` and `GET /api/v2/components/{uuid}` through two `ThreadPoolExecutor` pools; `extractDataStr` falls back to fetching `dataStrId` and decrypting it; writes/merges the zip `<target>/<name>.elibz` containing `device.json`, `SYMBOL/<uuid>.esym`, `FOOTPRINT/<uuid>.efoo` (pre-existing entries are merged, not clobbered). A document that cannot be read is `warning`-ed by uuid and left out, and the closing summary counts what actually reached the zip — not what was fetched, which used to report symbols the library did not contain.
   EasyEDA Pro signals an error as HTTP 200 with `{"success": false, "code": …, "message": …}`, so indexing `data["result"]` KeyErrors. Every Pro fetch goes through `proResult(data)`, which raises the API's own message (`"device not found (code 404)"`) instead of a bare `'result'`. A device or component fetch that fails is logged by uuid and counted into `downloadSymFp`'s `failed` return value, which `downloadAll` adds to `summary["failed"]` — so a part whose document 404s is reported as a failure, keeps its queue entry, and re-enables the download button, instead of silently clearing as a success.
2. `downloadStd` (Std) — `GET https://easyeda.com/api/components/{uuid}` returns either a symbol or a standalone footprint, disambiguated by `result.dataStr.head.docType` (**2** = symbol, **4** = footprint). A symbol's footprint comes from `result.packageDetail.dataStr` (also `docType 4`, structurally identical to a standalone footprint doc); a footprint document has no `packageDetail` — it *is* the footprint. Each document is wrapped verbatim in a `LIB~x~y~params~…#@$<shapes>` string (`buildStdLibShape`) and written to `<target>/<name>-std.zip` as `symbols.json` (a `docType 5` schematic list) and `footprints.json` (a `docType 3` PCB doc). This is exactly what KiCad's native Std importers enumerate — **no geometry conversion here, and none should be added**. Note the importers stop at the FIRST matching `.json` in a zip, which is why all symbols share one document and all footprints another.
   `writeStdLibrary` merges an existing zip entry by entry, footprints first, keyed on `package` for footprints and `spiceSymbolName` for symbols. It also **drops stale misfiled symbols**: before the docType dispatch existed, footprint documents were wrapped as symbols, and the merge kept resurrecting them, so the symbol library listed footprints that could not be placed. `isMisfiledFootprint` recognises them by the signature that wrapping produced — a `package`, no `name`, and `spiceSymbolName` equal to the `package` — and each drop is `warning`-ed by name. The missing `name` is what makes this safe: a real symbol named after its own package (`CH9340`) also has `package == spiceSymbolName`, and EasyEDA always fills `name` for a symbol. Matching against `footprints.json` instead would not work, because a part whose footprint was only ever filed as a symbol has no footprint entry to match.
   Before writing, `pruneOrphans` drops every `device.json` entry whose document is not in the zip and every device pointing at one, and `uniquifyTitles` renames a second footprint sharing a title. This is not cosmetic: KiCad enumerates the library from `device.json` and then loads each entry by name, so an indexed entry with no document is a footprint that appears in the chooser and fails to load, which stops the scan and hides every other part in the library. Entries like that accumulate whenever a document cannot be read (the `extractDataStr` relative-import bug left twelve of them in one real library), so the index is pruned to what the zip actually holds on every write. `uniquifyTitles` keeps the plain name for the uuid that sorts first, so a board already referencing that name keeps resolving to the same document.
   After pruning, `addPartAliases` files every Pro device's footprint under each name the part is known by - the `Manufacturer Part`/code, the `LCSC Part Name` description, the symbol document's `display_title`, and any alias the user typed in the queue (matched by code or uuid) - each a `device.json` entry pointing at a copy of the same `.efoo`. KiCad names a footprint after its footprint document and the importer sets no description or keywords, so without this the chooser can only match the package name (`SOT-23_L2.9-…`), not the part number or description anyone actually searches for. Each alias needs its own synthetic device (copied from the real one, `Footprint` repointed at the alias uuid), because the importer reads a footprint's 3D model from whichever device references that uuid; devices are invisible to both choosers (eeschema enumerates `symbols`, pcbnew `footprints`). Aliases are rebuilt from scratch each write and marked `ALIAS_MARKER`, so a later pass drops and rebuilds them identically instead of growing (the first version self-replicated). The queue's `Alias` column is edited by double-clicking a row (`EVT_LIST_ITEM_ACTIVATED` -> `wx.TextEntryDialog`); wx.ListCtrl can only label-edit column 0, so a popup is used instead of in-place editing.
3. `downloadModels(modelTasks)` — shared by both sources; `modelTasks` maps model uuid -> `(target file, fit X mm, fit Y mm)`, built by `collectProModels` (`3D Model Transform`, mils) or `collectStdModels` (layer-19 `outline3D` SVGNODE, EasyEDA units of 10 mil via `STD_UNIT_TO_MM`). Downloads raw STEP to `<KIPRJMOD>/EASYEDA_MODELS/<title>.step` (always `KIPRJMOD` — both KiCad importers hardcode that path) via `urllib.request.urlretrieve` into a `<file>_jlc` temp (pool of 8), then a **single-threaded** pool runs `pcbnew.UTILS_STEP_MODEL.LoadSTEP` -> scale to fit -> translate to origin -> `SaveSTEP`. pcbnew is not thread-safe; keep that pool at 1.

**Library tables** (`config_manager.py`): `LibraryTableManager.LIB_ENTRY_TYPES` maps `(source, table)` to the KiCad plugin type — Pro entries are `EasyEDA (JLCEDA) Pro` / `EasyEDA / JLCEDA Pro` on `<name>.elibz`, Std entries are `EasyEDA (JLCEDA) Std` / `EasyEDA / JLCEDA Std` on `<name>-std.zip` under the separate library name `<name>_Std`. Rows are inserted into `sym-lib-table`/`fp-lib-table` (created when absent) after a YES/NO `wx.MessageDialog`; `prompt_add_library(..., sources)` only prompts for the sources actually being downloaded.

**A running pcbnew never picks up library changes.** `PCB_IO_EASYEDA::GetLibraryTimestamp()` returns a constant `0` (KiCad 10, `pcbnew/pcb_io/easyeda/pcb_io_easyeda_plugin.cpp`), and `FOOTPRINT_LIST_IMPL::ReadFootprintFiles` early-returns when `GenerateTimestamp() == m_list_timestamp`. So when only an EasyEDA library changes, the aggregate timestamp is unchanged and KiCad skips the rescan — freshly downloaded footprints are missing from the footprint chooser for the rest of the session. At startup `m_list_timestamp` is `0`, the aggregate is non-zero, and everything is rescanned; that is why **restarting pcbnew** is the only fix. `downloadAll` therefore ends with `warnRescanNeeded`. Do NOT try to fix this by deleting `fp-info-cache`: that file is a KiCad 6/7/8 artifact and is not read by KiCad 10 at all.

## Key Directories

- repo root — all plugin source; flat by design, shipped as-is.
- `tests/` — `assert` scripts and captured fixtures; never shipped, because only root `.py` files are.
- `pcm/` — packaging only: `metadata.template.json`, `icon.png` (copied to `resources/`).
- `.github/workflows/` — single release workflow.
- `out/` — build output, gitignored along with `jlc-kicad-lib-loader-*.zip`. Not `.out/`: KiCad's "Install from File…" picker cannot see dot-directories.
- `docs/` — design notes, never shipped; `docs/superpowers/specs/` holds the UI redesign spec.

## Development Commands

```bash
./create_pcm_archive.sh              # build out/jlc-kicad-lib-loader-<VERSION>-pcm.zip + out/env
CI_ENV=/tmp/env ./create_pcm_archive.sh   # redirect the env-var dump
```

Archive layout:

```
metadata.json                 # template with VERSION/sha/size/url placeholders resolved or stripped
plugins/*.py  *.png  VERSION
resources/icon.png
```

No build system, no lint, and no test command exists. Requires `zip`, `shasum`, `sed`.

Regenerating the dialog after editing `easyeda_lib_loader_dialog.fbp`:

```bash
flatpak install --user -y flathub org.wxformbuilder.wxFormBuilder   # once
flatpak run --filesystem="$PWD" org.wxformbuilder.wxFormBuilder -g easyeda_lib_loader_dialog.fbp
```

The flatpak is the 4.2.1 generator the file was written with: regenerating an untouched `.fbp` reproduces the committed `.py` byte for byte apart from the build-string comment. The Ubuntu `.deb` on the release page is missing shared libraries and does not run. `wxPanel` styles go in `window_style`, not `style`, or the generator warns and drops them.

Manual testing: copy the root `.py` files into the KiCad 3rd-party plugin dir (or install the built zip via PCM "Install from File…"), then Tools > External Plugins > Refresh in pcbnew.

## Code Conventions & Common Patterns

- **Naming**: modules and locals are `snake_case`; functions and methods are `camelCase` (`downloadSymFp`, `getUuidFirstPart`, `onDownload`, `createDialog`). Classes are `PascalCase`. Match the surrounding file — do not "fix" the camelCase methods.
- **Widgets**: generated controls are `m_`-prefixed (`m_log`, `m_logPane`, `m_progress`, `m_actionBtn`, `m_searchResultsTree`, `m_queueList`, `m_paramsList`, `m_symbolPanel`, `m_footprintPanel`, `m_textCtrlSearch`, `m_textCtrlFilter`, `m_textCtrlOutLibName`, `m_libSourceChoice`, `m_debug`).
- **Generated code**: `easyeda_lib_loader_dialog.py` is wxFormBuilder 4.2.1 output from `easyeda_lib_loader_dialog.fbp` — never hand-edit; change the `.fbp` and regenerate (command above). Only the splitter `EVT_IDLE` handlers are generated; **all functional handlers are bound externally** in `easyeda_lib_loader.py`, at the end of `createDialog`. The handlers are closures over `dlg`; the ones tests need are also published on the plugin (`self.onSearch`, `self.onDownload`, `self.onSearchItemSelected`, `self.addRows`, `self.renderRows`, `self.refreshQueue`), because a simulated click only lands when the window really holds focus.
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
| `pro_render.py` | `symbolSvg` / `footprintSvg`: Pro documents -> inline SVG for the preview |
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

No framework, no `conftest.py`, no linter config, no CI test step: four `assert` scripts under `tests/`, each run directly.

```bash
python3 tests/test_offline.py       # always; no network, ~0.5s
python3 tests/smoke_queue_alias.py  # after touching the queue or aliases; wx, no network, ~2s
python3 tests/smoke_download.py     # after touching the download path; network + pcbnew
python3 tests/smoke_preview.py      # after touching the preview; network + wx WebView
python3 tests/smoke_dialog.py       # after touching the dialog; network + pcbnew + wx
```

- **`tests/test_offline.py`** — everything that is pure logic: `pro_render` against a captured real document (`tests/fixtures/pro_ams1117.json`, AMS1117/C6186) plus synthetic documents for the Y flip, unit scaling, pin direction, font ratio, pad shapes, contour tokens, layer filtering and empty/broken input; `component_loader`'s Std format, docType dispatch, merge and 3D unit conversion, driven through a fake session so no network is touched; `config_manager`'s library-table rows; and the dialog's pure pieces — `proRow`/`stdRow` column mapping, `rowMatches`, `sortKey` and `PartQueue`. Sections needing `requests`/`pcbnew`/`wx` skip themselves rather than fail. Prints a per-section report and a check count.
- **`tests/smoke_queue_alias.py`** — the queue's `Alias` column and the path from it to the loader: the column exists and shows what was typed, `onDownload` hands `PartQueue.aliases()` to `ComponentLoader.downloadAll`, and a clean run empties the queue. `LibraryTableManager.prompt_add_library` and `ComponentLoader` are both stubbed — the prompt is modal, and the wiring is the subject, not another download. It needs a display but no network, so it is the cheap check after touching the queue.
- **`tests/smoke_download.py`** — downloads a real Pro part and a real Std part into a temp project, then makes **KiCad's own importers** enumerate and load the result (pad counts, extents) and checks the merge keeps existing entries. This is the contract that has actually broken before; the offline suite cannot cover it.
- **`tests/smoke_preview.py`** — opens the real dialog and checks all four preview branches (Pro with an LCSC code, Pro drawn from scratch, a document with no geometry, and Std), asserting **one drawing per panel** now that symbol and footprint have their own WebView. It clicks with `wx.UIActionSimulator` when the window takes focus and otherwise calls `plugin.onSearchItemSelected` directly, so it works on a desktop and under a nested X server alike.
- **`tests/smoke_dialog.py`** — the whole dialog against the live APIs in a throwaway `KIPRJMOD`: a faceted search (the count must be the queried facet's, and no other facet's rows may appear), the filter and its restore, the sort headers, the inspector, "All Sources" reaching both APIs, then a real queued download that must report what landed, finish the progress bar and empty the queue. Every assertion there is a defect the redesign fixed. Re-checking the filter needs the **full** row from `plugin.searchRows`, not the row read back out of the grid: the grid only holds `RESULT_COLUMNS`, while the row carries one more cell (`searchableText`) that the filter also matches, so a legitimate hit on a hidden attribute value looks like a non-matching row that survived. This run is dominated by STEP conversion — budget tens of minutes on a slow machine, not the two minutes it takes on a fast one.

Assertions must not compute their expectation from the constant they are testing — pin the number KiCad's importer produces (`font-size="6.200"`, not `10 * FONT_CAP_RATIO`). Fixtures are captured from the live API; the shape counts they contain are quoted in the comments, so a swapped fixture shows up as a mismatch rather than a silent pass.

Still manual, because no script can judge it: build with `./create_pcm_archive.sh`, install the zip in KiCad, download a part, and look at the symbol and footprint in eeschema and pcbnew.

## Gotchas

- Bumping the release version means editing `VERSION="…"` in `create_pcm_archive.sh` — the git tag does not drive it.
- A new `.py` at the repo root is shipped to users — put test and scratch scripts in `tests/`.
- Relative imports inside function bodies (`from . import decryptor`) break when the modules are imported flat, e.g. by a test harness; fall back to the absolute import.
- `easyeda_lib_loader` imports its siblings relatively, so it can only be imported as a package. Tests symlink the repo into a temp directory under a package name (`jlcpkg`) rather than adding absolute-import fallbacks to the plugin.
- A `wx.StaticText` resizes itself around a new label but keeps its position, so `SetLabel` without a `Layout()` on the containing panel lets it grow over its neighbours. Both status labels go through `setStatus`/`setPage`, which re-lay out the row.
- `wx.dataview.TreeListEvent` cannot be synthesized and `tree.Select()` fires no event; drive selection handlers directly or through `wx.UIActionSimulator` with real focus.
- Capture screenshots from the window's own `wx.WindowDC`, not a `wx.ScreenDC` blit of `GetScreenRect()` — the latter grabs whatever the window manager has on top.
- **wx GUI tests die on this host** with `Gtk:ERROR ... ensure_surface_for_gicon: Failed to load /usr/share/icons/breeze-dark/status/16/image-missing.svg`. Two host faults combine: the session sets `gtk-icon-theme-name=breeze-dark`, which is not installed, and librsvg 2.62 no longer ships a gdk-pixbuf SVG loader (`/usr/lib/gdk-pixbuf-2.0/2.10.0/loaders/` holds only `libopenraw`), so even the `image-missing` fallback cannot be decoded. It is not a plugin bug. Shim the missing theme onto an installed **PNG** one instead of touching the system: `mkdir -p /var/tmp/jlcgui/icons && ln -sfn /usr/share/icons/AdwaitaLegacy /var/tmp/jlcgui/icons/breeze-dark`, then run with `XDG_DATA_HOME=/var/tmp/jlcgui`. `AdwaitaLegacy` is the theme that still has `image-missing.png`; `Adwaita`, `Breeze_Light` and the Oxygen themes are SVG-only and fail the same way. `settings.ini`/`GTK_THEME` alone do **not** help — a running session's XSETTINGS outranks them. Use `/var/tmp`, not `/tmp`: the shim gets cleaned out from under long runs.
- Screen coordinates lie for a widget nested inside `wxSplitterWindow`: `GetScreenPosition()` on the notebook and the params list came back offset by 259px from their own containing pane, and the window manager may also move the dialog between two measurements. Assert layout with `GetPosition()` (parent-relative) between **siblings of the same parent**, and assert containment by walking `GetParent()`, never by comparing pixels across panes.
- A `wx.html2.WebView` can blit **black** into a `wx.MemoryDC` even when the page is fine — its surface is not always in the window DC when you capture. Prove drawing content with `view.RunScript(...)` (count `#cv svg`, read `transformOrigin`) and treat the screenshot as appearance only.
- STEP models always land under `$KIPRJMOD/EASYEDA_MODELS`, independent of the chosen library path.
- `pcm/icon.png` is copied into `resources/` but `metadata.template.json` has no `icon` key referencing it.
