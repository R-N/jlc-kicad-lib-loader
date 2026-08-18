"""The dialog's layout, asserted rather than eyeballed.

Right pane holds the part and nothing else: the Symbol/Footprint notebook over the
details table. Everything else - search results, queue, library row, progress, log -
belongs to the left pane. Also checks the window resizes, offers a maximize button,
and that the queue is wide enough for its Alias column.

Containment is asked structurally (GetParent) and order with parent-relative
GetPosition, never with screen coordinates: a widget nested in a wxSplitterWindow
reports a stale screen origin, and the window manager may move the dialog between
two measurements.

Needs wx and a display. No network, no pcbnew. Writes two screenshots to /tmp.
"""
import ast, os, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import wx
try:
    import wx.html2
    HAS_WEBVIEW = True
except ImportError:
    HAS_WEBVIEW = False

from easyeda_lib_loader_dialog import EasyEdaLibLoaderDialog

# The real drawing page, without importing the package (it uses relative imports).
_module = ast.parse(open(os.path.join(ROOT, "easyeda_lib_loader.py")).read())
PAGE = next(n.value.value for n in _module.body
            if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "DRAWING_PAGE")
COLUMNS = next(n.value for n in _module.body
               if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "RESULT_COLUMNS")
COLUMNS = [(e.elts[0].value, e.elts[1].value) for e in COLUMNS.elts]

app = wx.App()
app.SetAssertMode(wx.APP_ASSERT_SUPPRESS)
dlg = EasyEdaLibLoaderDialog(None)
dlg.Show()


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        wx.Yield()
        time.sleep(0.05)


tree = dlg.m_searchResultsTree
for title, width in COLUMNS:
    tree.AppendColumn(title, width=width, flags=wx.COL_RESIZABLE | wx.COL_SORTABLE)

ROWS = [
    ("System", "C6186", "AMS1117-3.3_C6186", "AMS1117-3.3", "1A 3.3V LDO",
     "AMS1117-3.3", "SOT-223-3_L6.5-W3.4-P2.30-LS7.0-BR", "SOT-223", "Basic", "Device", "LCSC"),
    ("System", "C15127", "AO3401A_C15127", "AO3401A", "P-ch 30V 4A MOSFET",
     "AO3401A", "SOT-23_L2.9-W1.3-P1.90-LS2.4-BR", "SOT-23", "Basic", "Device", "LCSC"),
    ("System", "C25804", "0402WGF1003TCE", "100k\u03a9", "100k \u00b11% 62.5mW",
     "0402WGF1003TCE", "R0402", "0402", "Basic", "Device", "LCSC"),
    ("Std", "std:4c0dae4e", "Adafruit Max17048", "", "fuel gauge breakout",
     "Adafruit Max17048", "MAX17048_BREAKOUT", "MAX17048_BREAKOUT", "", "Symbol", "adafruit"),
]
for row in ROWS:
    assert len(row) == len(COLUMNS), f"fixture row has {len(row)} cells for {len(COLUMNS)} columns"
    item = tree.AppendItem(tree.GetRootItem(), row[0])
    for column, value in enumerate(row[1:], start=1):
        tree.SetItemText(item, column, value)

for title, width in (("Parameter", 150), ("Value", 240)):
    dlg.m_paramsList.AppendColumn(title, width=width)
for name, value in (("Symbol in KiCad", "AMS1117-3.3"),
                    ("Footprint in KiCad", "SOT-223-3_L6.5-W3.4-P2.30-LS7.0-BR"),
                    ("Manufacturer", "Advanced Monolithic Systems"),
                    ("Manufacturer Part", "AMS1117-3.3"), ("Supplier Part", "C6186"),
                    ("Package", "SOT-223"), ("JLCPCB Part Class", "Basic Part")):
    row = dlg.m_paramsList.InsertItem(dlg.m_paramsList.GetItemCount(), name)
    dlg.m_paramsList.SetItem(row, 1, value)

for title, width in (("Source", 62), ("Code / UUID", 210), ("Name", 190), ("Alias", 150)):
    dlg.m_queueList.AppendColumn(title, width=width)
for source, code, name, alias in (("System", "C6186", "AMS1117-3.3_C6186", ""),
                                  ("System", "C15127", "AO3401A_C15127", "MY-MOSFET"),
                                  ("Std", "std:4c0dae4e58984c06b781", "Adafruit Max17048", "")):
    row = dlg.m_queueList.InsertItem(dlg.m_queueList.GetItemCount(), source)
    dlg.m_queueList.SetItem(row, 1, code)
    dlg.m_queueList.SetItem(row, 2, name)
    dlg.m_queueList.SetItem(row, 3, alias)

SYM = ('<svg viewBox="0 0 220 120"><g data-kind="pin" data-number="1" data-name="GND">'
       '<line x1="10" y1="45" x2="40" y2="45" stroke="#8a1c1c" stroke-width="2"/>'
       '<line x1="10" y1="45" x2="40" y2="45" stroke="#000" stroke-opacity="0"'
       ' stroke-width="6" pointer-events="stroke"/></g>'
       '<rect x="40" y="25" width="140" height="70" fill="none" stroke="#8a1c1c" stroke-width="2"/>'
       '<text x="110" y="65" font-size="14" text-anchor="middle" fill="#8a1c1c">AMS1117</text></svg>')
FP = ('<svg viewBox="0 0 220 120">'
      '<g data-layer="3" data-layername="Top Silkscreen Layer">'
      '<rect x="60" y="20" width="100" height="80" fill="none" stroke="#c8a415" stroke-width="1.5"/></g>'
      '<g data-layer="1" data-layername="Top Layer">'
      '<g data-kind="pad" data-number="1" data-size="2.500 x 1.100 mm" data-layername="Top Layer">'
      '<rect x="30" y="34" width="30" height="16" fill="#12a0a0"/></g></g></svg>')

views = {}
for panel, markup in ((dlg.m_symbolPanel, SYM), (dlg.m_footprintPanel, FP)):
    sizer = wx.BoxSizer(wx.VERTICAL)

    if HAS_WEBVIEW:
        try:
            view = wx.html2.WebView.New(panel)
            view.SetPage(PAGE.replace("__BODY__", markup), "")
            views[panel] = view
        except NotImplementedError:
            view = wx.StaticText(panel, label="no webview")
    else:
        view = wx.StaticText(panel, label="no webview")

    sizer.Add(view, 1, wx.EXPAND)
    panel.SetSizer(sizer)
    panel.Layout()

dlg.m_queueLabel.SetLabel("Queue: 3 parts")
dlg.m_textCtrlOutLibName.SetValue("EasyEDA_Lib")
dlg.m_partTitle.SetLabel("AMS1117-3.3_C6186")
dlg.m_searchHyperlink1.SetLabelText("Open in EasyEDA Pro")
dlg.m_searchHyperlink1.SetURL("https://pro.easyeda.com")
dlg.m_searchHyperlink2.SetLabelText("JLCPCB")
dlg.m_searchHyperlink2.SetURL("https://jlcpcb.com")
dlg.m_searchHyperlink3.SetLabelText("LCSC")
dlg.m_searchHyperlink3.SetURL("https://lcsc.com")
dlg.m_searchStatus.SetLabel("285 parts")
dlg.m_searchPage.SetLabel("Page 1/6")
dlg.m_progress.SetValue(45)
dlg.m_resultStatus.SetLabel("Downloaded 2 symbols, 2 footprints, 2 models")
dlg.m_actionBtn.Enable()
dlg.Layout()
# A child window with its own sizer needs its own Layout, or a resized StaticText
# grows over its neighbours.
dlg.m_detailsPanel.Layout()
dlg.m_resultsPanel.Layout()
pump(1.5)


def shot(name):
    pump(0.9)
    size = dlg.GetClientSize()
    bmp = wx.Bitmap(size.width, size.height)
    mem = wx.MemoryDC(bmp)
    mem.Blit(0, 0, size.width, size.height, wx.WindowDC(dlg), 0, 0)
    mem.SelectObject(wx.NullBitmap)
    bmp.SaveFile(f"/tmp/{name}.png", wx.BITMAP_TYPE_PNG)
    print(f"wrote /tmp/{name}.png {size.width}x{size.height}")


def ancestors(w):
    out = []
    while w is not None and w is not dlg:
        w = w.GetParent()
        out.append(w)
    return out


def box(w):
    """Parent-relative rect. Only ever compared with siblings of the same parent."""
    o, s = w.GetPosition(), w.GetSize()
    return wx.Rect(o.x, o.y, s.width, s.height)


nb = dlg.m_previewNotebook
tabs = [nb.GetPageText(i) for i in range(nb.GetPageCount())]
print("notebook pages:", tabs)
assert tabs == ["Symbol", "Footprint"], f"tabs are {tabs}"
assert dlg.m_symbolPanel.GetParent() is nb, "symbol panel is not a notebook page"
assert dlg.m_footprintPanel.GetParent() is nb, "footprint panel is not a notebook page"
assert nb.GetSelection() == 0, "Symbol is not the tab shown first"

# --- the right pane holds the part, and nothing else ----------------------------
assert dlg.m_inspectorPanel in ancestors(nb), "notebook is not in the right pane"
assert dlg.m_inspectorPanel in ancestors(dlg.m_paramsList), "params list is not in the right pane"

for name, w in (("queue list", dlg.m_queueList), ("queue label", dlg.m_queueLabel),
                ("add button", dlg.m_queueAddBtn), ("paste button", dlg.m_queuePasteBtn),
                ("remove button", dlg.m_queueRemoveBtn), ("clear button", dlg.m_queueClearBtn),
                ("results grid", tree), ("page buttons", dlg.m_nextPageBtn),
                ("library box", dlg.m_textCtrlOutLibName), ("browse", dlg.m_browseBtn),
                ("download", dlg.m_actionBtn), ("progress", dlg.m_progress),
                ("result status", dlg.m_resultStatus), ("debug", dlg.m_debug),
                ("close", dlg.m_closeButton), ("log pane", dlg.m_logPane)):
    assert dlg.m_inspectorPanel not in ancestors(w), f"{name} is in the right pane"
    assert dlg.m_resultsPanel in ancestors(w), f"{name} is not on the left pane"
print("left pane owns: results, queue, library row, progress row, log pane")

# --- order within each pane -----------------------------------------------------
grid, ql, add = box(tree), box(dlg.m_queueList), box(dlg.m_queueAddBtn)
lib, logp = box(dlg.m_textCtrlOutLibName), box(dlg.m_logPane)
assert ql.y >= grid.GetBottom() - 2, f"queue (y {ql.y}) is not below the grid ({grid.GetBottom()})"
assert add.y >= ql.GetBottom() - 2, f"queue buttons (y {add.y}) are not below the list"
assert lib.y >= add.y - 2, "the library row is not below the queue"
assert logp.y >= lib.y - 2, "the log pane is not at the bottom"
draw, det = box(dlg.m_drawingsPanel), box(dlg.m_detailsPanel)
assert draw.GetBottom() <= det.y + 8, "drawings are not above the details table"
print(f"left: grid y {grid.y}-{grid.GetBottom()}, queue {ql.y}, buttons {add.y}, "
      f"library {lib.y}, log {logp.y}")

# --- the queue must be wide enough to show the Alias column ---------------------
cols = sum(dlg.m_queueList.GetColumnWidth(i) for i in range(dlg.m_queueList.GetColumnCount()))
print(f"queue list {ql.width}px wide, columns need {cols}px")
assert ql.width >= cols, f"queue list ({ql.width}px) is narrower than its columns ({cols}px)"
rows = ql.height // max(1, dlg.m_queueList.GetItemRect(0).height)
assert rows >= 3, f"queue only shows {rows} rows"

# --- the details header must neither overlap nor clip ---------------------------
title = box(dlg.m_partTitle)
panelWidth = dlg.m_detailsPanel.GetClientSize().width
for name, w in (("hyperlink 1", dlg.m_searchHyperlink1), ("hyperlink 2", dlg.m_searchHyperlink2),
                ("hyperlink 3", dlg.m_searchHyperlink3)):
    if w.IsShown():
        assert not title.Intersects(box(w)), f"part title overlaps {name}"
        assert box(w).GetRight() <= panelWidth + 1, \
            f"{name} is clipped: ends at {box(w).GetRight()} of {panelWidth}px"
print(f"details header fits inside {panelWidth}px")

# --- the window must resize and offer a maximize button -------------------------
assert dlg.GetWindowStyle() & wx.RESIZE_BORDER, "dialog has no resize border"
assert dlg.GetWindowStyle() & wx.MAXIMIZE_BOX, "dialog offers no maximize button"
size, hint = dlg.GetSize(), dlg.GetMinSize()
best = dlg.GetEffectiveMinSize()
print(f"size {size.width}x{size.height} | min hint {hint.width}x{hint.height} "
      f"| layout minimum {best.width}x{best.height}")
assert best.width <= size.width and best.height <= size.height, \
    f"the layout's own minimum {best.width}x{best.height} exceeds the window"
dlg.SetSize(wx.Size(hint.width, hint.height))
pump(0.8)
shrunk = dlg.GetSize()
print(f"shrunk to {shrunk.width}x{shrunk.height}")
assert shrunk.width < size.width and shrunk.height < size.height, "the dialog would not shrink"
dlg.SetSize(size)
pump(0.8)

# --- items per page -------------------------------------------------------------
options = [dlg.m_pageSizeChoice.GetString(i) for i in range(dlg.m_pageSizeChoice.GetCount())]
print("per page:", options, "selected", dlg.m_pageSizeChoice.GetStringSelection())
assert options == ["10", "25", "50", "100", "200"], f"page size options are {options}"
assert dlg.m_pageSizeChoice.GetStringSelection() == "50", "page size does not default to 50"
assert "Narrow" in dlg.m_filterLabel.GetLabel(), \
    f"the filter still reads like a second search box: {dlg.m_filterLabel.GetLabel()!r}"

total = dlg.GetClientSize().width * dlg.GetClientSize().height
for label, widget in (("results grid", tree), ("queue", dlg.m_queueList),
                      ("preview notebook", nb), ("params", dlg.m_paramsList)):
    sz = widget.GetSize()
    print(f"{label:18} {sz.width:5}x{sz.height:4} = {100.0 * sz.width * sz.height / total:5.1f}%")

# --- the drawings really are interactive ----------------------------------------
for tab, (panel, view) in enumerate(views.items()):
    nb.SetSelection(tab)
    pump(0.9)
    ok, value = view.RunScript(
        "(function(){var c=document.querySelectorAll('#cv svg').length;"
        "var o=getComputedStyle(document.getElementById('cv')).transformOrigin;"
        "var hot=document.querySelectorAll('g[data-kind]').length;"
        "var lay=document.querySelectorAll('#layers label').length;"
        "return c+'|'+o+'|'+hot+'|'+lay;})()")
    print(f"tab {nb.GetPageText(tab)}: ok={ok} -> {value}")
    assert ok, f"could not run script in the {nb.GetPageText(tab)} view"
    count, origin, hover, layers = value.split("|")
    assert count == "1", f"{nb.GetPageText(tab)} page holds {count} drawings"
    assert origin.startswith("0px 0px"), f"transform origin is {origin}, not the top left"
    assert int(hover) >= 1, f"{nb.GetPageText(tab)} page has nothing hoverable"

    if nb.GetPageText(tab) == "Footprint":
        assert int(layers) == 2, f"footprint layer panel lists {layers} layers, expected 2"
    else:
        assert int(layers) == 0, "a symbol must not get a layer panel"

nb.SetSelection(0)
pump(1.0)
shot("layout_symbol")
nb.SetSelection(1)
pump(1.0)
assert nb.GetSelection() == 1, "could not switch to the Footprint tab"
shot("layout_footprint")
print("\nLAYOUT OK: right pane is the part only, everything else on the left, "
      "window resizes, drawings interactive")
dlg.Destroy()
