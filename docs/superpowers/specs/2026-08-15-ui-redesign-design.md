# UI redesign: part inspector, part queue, quiet log

Status: approved 2026-08-15. Supersedes the three-splitter layout.

## Problem

Measured on the running dialog at 1280x860, first result of a `ams1117` search selected:

| Region | Size | Share of window |
| --- | --- | --- |
| preview pane | 650x399 | 23.6% |
| results grid | 640x354 | 20.6% |
| log (empty) | 615x347 | 19.4% |
| parts textarea (empty) | 615x288 | 16.1% |
| progress gauge | 615x6 | 0.3% |

35.5% of the window is two boxes that are empty until used, the progress gauge is 6 px tall, and
the results grid clips three of its six columns at that width. For an LCSC-coded part the preview
is not ours at all: the WebView loads `jlcpcb.com/user-center/lcsvg/svg.html`, so the pane shows
their page banner and clips the footprint drawing at the pane edge.

Defects found while auditing:

1. `searchFn` sets the page count from `sum(facets.values())` while querying a single facet, so
   JLC System reports "4163 parts. Page 1/84" when that facet holds 285 parts (6 pages), and
   `low dropout regulator` reports "800 parts" with an empty grid (all 800 are in another facet).
2. `onDownload` copies the selected rows into the parts box **only when the box is empty**, and
   nothing clears the box afterwards. After one download, selecting a different part and pressing
   Download silently re-downloads the previous parts.
3. All six columns carry `COL_SORTABLE` and `GetSortColumn()` reports a sort column, but no item
   comparator is installed, so clicking a header does nothing.
4. "All Sources" searches EasyEDA Pro only; the Std library is unreachable from it.
5. `m_searchStatus2` is dead, and the Preview/JLCPCB/LCSC links are wedged into the status strip
   between the part count and the pager.
6. The grid shows the footprint *document title* (`SOT-223-3_L6.5-W3.4-P2.30-LS7.0-BR`) instead of
   the package (`Supplier Footprint` = `SOT-223`), and shows no MPN, no JLCPCB part class, and no
   symbol/footprint type for Std rows.

## Layout

```
+-------------------------------------------------------------------------------+
| [Source v][ search...                 ][Find]   [filter results...        ]   |
+----------------------------------------+--------------------------------------+
| Src Code   Name    MPN   Pkg  Cls  Typ |            Symbol                    |
| Sys C6186  AMS111. AMS1. SOT. Basic Dev|                                      |
| ...                                    +--------------------------------------+
|                                        |            Footprint                 |
|                                        |                                      |
| 285 parts           Page 1/6  [<] [>]  +--------------------------------------+
|                                        | Parameters | datasheet, JLCPCB, LCSC |
+----------------------------------------+--------------------------------------+
| Queue (3)  [Src Code Name list      ]  [Add][Remove][Clear][Paste...]         |
| Library [EasyEDA_Lib               ][Browse]              [Download parts]    |
| [progress bar                      ]  Downloaded 2 symbols, 2 footprints      |
| > Details                                              [ ] Debug  [Close]     |
+-------------------------------------------------------------------------------+
```

Two splitters, both with an obvious purpose: results | inspector, and drawings | parameters.
Down from three, one of which had its sash pinned at 0.

## Widget contract

`easyeda_lib_loader_dialog.fbp` is edited as XML and the `.py` regenerated with the official
generator, so the two cannot drift:

```bash
flatpak run --filesystem=<repo> org.wxformbuilder.wxFormBuilder -g easyeda_lib_loader_dialog.fbp
```

Names the handler code binds to:

| Widget | Kind | Role |
| --- | --- | --- |
| `m_libSourceChoice` | wxChoice | All Sources / JLC System / JLC Public / EasyEDA Std |
| `m_textCtrlSearch`, `m_searchBtn` | TextCtrl, Button | server-side query |
| `m_textCtrlFilter` | TextCtrl | client-side filter over loaded rows |
| `m_searchResultsTree` | wxTreeListCtrl | Source, Code/UUID, Name, MPN, Package, Class, Type, Contributor |
| `m_searchStatus`, `m_searchPage`, `m_prevPageBtn`, `m_nextPageBtn` | | result count and paging |
| `m_symbolPanel`, `m_footprintPanel` | wxPanel | one WebView each, created in code |
| `m_paramsList` | wxListCtrl report | Parameter / Value |
| `m_searchHyperlink1..3` | wxHyperlinkCtrl | editor / JLCPCB / LCSC, moved beside the parameters |
| `m_queueLabel`, `m_queueList` | StaticText, wxListCtrl report | Source, Code, Name |
| `m_queueAddBtn`, `m_queueRemoveBtn`, `m_queueClearBtn`, `m_queuePasteBtn` | Button | queue editing |
| `m_textCtrlOutLibName`, `m_browseBtn` | TextCtrl, Button | library location |
| `m_actionBtn` | Button | Download parts |
| `m_progress`, `m_resultStatus` | Gauge, StaticText | progress and the one-line outcome |
| `m_logPane` | wxCollapsiblePane | collapsed by default, holds `m_log` |
| `m_debug`, `m_closeButton` | CheckBox, Button | unchanged |

Removed: `m_searchStatus2` (dead), `m_textCtrlParts` and `m_staticText1` (replaced by the queue),
`m_splitter5`, `m_panel5`, `m_panel6`.

## Behaviour

**Preview.** All three sources produce two drawings, one per panel, replacing the "load JLCPCB's
page" branch: for an LCSC code the symbol and footprint SVGs come from
`easyeda.com/api/products/<code>/svgs` (docType 2 and 4) exactly as the Pro branch already does,
falling back to `pro_render` when EasyEDA published nothing; Std keeps its `image.easyeda.com`
thumbnails. Each panel holds its own WebView showing a single drawing scaled to the panel, so
resizing the splitter resizes the drawing. Parameters move out of the HTML into `m_paramsList`.
`wx.svg` is not usable here: it is nanosvg, which does not render text, and every pin name and pad
number would disappear. WebView-less environments keep today's static-text fallback.

**Queue.** `m_queueList` holds one row per part (source, code, name), deduped. A result enters it by
double-click or `Add`; `Paste...` opens a text dialog for bulk codes and UUIDs, keeping the current
power-user path. `Remove` and `Clear` edit it. On a finished download, parts that succeeded are
removed and parts that failed stay so they can be retried, with the outcome in `m_resultStatus`.
This kills defect 2: the queue is explicit state, not a text box that silently outranks selection.

**Search.** Page count and status come from the selected facet, not the sum (defect 1).
"All Sources" queries Pro (all facets) and Std, merges the rows, and enables Next while either side
has more; the Source column says which API a row came from (defect 4). An item comparator makes the
columns sort for real (defect 3). Columns carry explicit widths so one long footprint title cannot
push the rest off-screen, and expose MPN, package (`Supplier Footprint` / `c_para.package`), JLCPCB
part class, and Type (Device / Symbol / Footprint, which distinguishes a Std standalone footprint
from a symbol) (defect 6). `m_textCtrlFilter` narrows the loaded rows across every column as you
type, which is the answer to "search does not cover all relevant fields" without a round-trip.

**Log.** `m_logPane` is collapsed by default; `m_progress` and `m_resultStatus` take the freed
width. `WxTextCtrlHandler` keeps writing into `m_log` regardless, so expanding Details after a
failure shows the whole run. A warning or error auto-expands the pane, because a silent failure is
worse than a wide dialog.

## Testing

- `tests/test_offline.py` gains a section for the queue model and the row-building helpers, which
  must be pure functions to be testable: given an API entry, produce the row tuple; given a queue,
  produce the download list. No wx needed.
- `tests/smoke_preview.py` updates to the two-panel layout: assert one drawing per panel for each
  of the four cases, and that the parameters list is populated.
- `tests/smoke_download.py` unchanged; the download path is untouched.
- Manual, in KiCad: search, sort, filter, queue two parts, download, confirm the queue empties and
  Details holds the log.

## Out of scope

Relevance ranking of the upstream APIs, offline library browsing, and any change to the download
path or the on-disk library format.
