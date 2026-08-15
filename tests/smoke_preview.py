"""Drive the real dialog and check both preview panels for each kind of part.

    python3 tests/smoke_preview.py

Needs the network, KiCad's `pcbnew`, and a `wx` with WebView (on Linux,
`python3-wxgtk-webview4.0`); it exits cleanly when the panels degrade to static
text. Run it by hand after touching the preview.

The four rows below are the four ways a preview can be built. Symbol and footprint
live in separate panels, so each must end up holding exactly one drawing.
"""

import os
import re
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# `easyeda_lib_loader` uses relative imports, so the repository has to be
# importable as a package: give it a name via a symlink in a temp directory.
pkgroot = tempfile.mkdtemp(prefix="jlcpkgroot")
os.symlink(ROOT, os.path.join(pkgroot, "jlcpkg"))
sys.path.insert(0, pkgroot)

project = tempfile.mkdtemp(prefix="jlcpreview")
os.environ.setdefault("KIPRJMOD", project)

import pcbnew  # noqa: E402,F401  (imported for its side effects, as in KiCad)
import wx  # noqa: E402
import wx.html2  # noqa: E402

from jlcpkg import easyeda_lib_loader as ell  # noqa: E402

# 8209ba65… = "AMS1117", a Pro part cloned from LCSC C6186: EasyEDA publishes
# drawings for it, which the preview fetches as inline SVG.
PRO_CODED = "8209ba65d24940569c88b6b832f4ceb3"
# da76dbd1… = "ESP32", a Pro part drawn from scratch: no LCSC code, so EasyEDA
# publishes nothing and `pro_render` has to draw the document itself.
PRO_CODELESS = "da76dbd12af14461a059de2fae304c81"
# 636dcb04… = "regulator", a Pro part whose symbol document is a title-block
# table with no geometry: nothing can draw it, so the pane must explain itself.
PRO_EMPTY = "636dcb04ffc646ac9fa6cd7b27e8768e"
# An EasyEDA Std part, previewed with EasyEDA's own rendered thumbnails.
STD_PART = "std:4c0dae4e58984c06b7812642e521e379"

app = wx.App()
app.SetAssertMode(wx.APP_ASSERT_SUPPRESS)
plugin = ell.EasyEDALibLoaderPlugin()
dialog = plugin.createDialog()
dialog.SetSize((1200, 800))
dialog.Show()
tree = dialog.m_searchResultsTree

if not isinstance(plugin.symbolView, wx.html2.WebView):
    print("SKIP: no WebView in this environment")
    sys.exit(0)


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        wx.Yield()
        time.sleep(0.05)

# Cleared the first time a simulated click fails to reach the tree; after that
# the handler is called directly and the click is not retried.
clicked = True


class StubSelection:
    """What the selection handler reads off a wx TreeListEvent."""

    def __init__(self, item):
        self.item = item

    def GetItem(self):
        return self.item


def readPanels():
    panels = {}

    for name, view in (("symbol", plugin.symbolView), ("footprint", plugin.footprintView)):
        ok, html = view.RunScript("document.body.innerHTML")
        assert ok, f"could not read the {name} panel back out of its WebView"
        panels[name] = html or ""

    return panels


def selectRow(code):
    """Put one row in the tree and select it, so the real handler runs on it."""
    tree.DeleteAllItems()
    item = tree.AppendItem(tree.GetRootItem(), "")
    # Column 1 is Code/UUID; the handler reads the code from there, not from the tree label.
    tree.SetItemText(item, 1, code)
    pump(0.4)

    global clicked
    panels = {"symbol": "", "footprint": ""}

    if clicked:
        # A simulated click is the honest path, but it only lands when the window
        # holds focus, which is not true on every desktop or under a nested or
        # headless X server.
        view = tree.GetDataView()
        view.SetFocus()
        pump(0.2)
        origin = view.ClientToScreen(wx.Point(0, 0))
        sim = wx.UIActionSimulator()
        # The first ~45px of the data view is the column header, so aim below it.
        sim.MouseMove(origin.x + 60, origin.y + 55)
        pump(0.2)
        sim.MouseClick(wx.MOUSE_BTN_LEFT)
        pump(4.0)
        panels = readPanels()

    if not any("<svg" in html or "<img" in html or "note" in html for html in panels.values()):
        clicked = False
        tree.Select(item)
        plugin.onSearchItemSelected(StubSelection(item))
        pump(4.5)
        panels = readPanels()

    return panels


def drawings(html):
    return html.count("<svg") + html.count("<img")


def report(label, panels):
    print(f"{label:14} -> symbol: {drawings(panels['symbol'])} drawing,"
          f" footprint: {drawings(panels['footprint'])} drawing")


panels = selectRow(PRO_CODED)
report("pro, coded", panels)
for name, html in panels.items():
    assert drawings(html) == 1, f"{name} panel holds {drawings(html)} drawings, expected 1"
    assert "<svg" in html, f"{name} should be EasyEDA's own inline SVG"
    assert 'class="note"' not in html, f"{name} panel shows a note despite having a drawing"

# The viewer links are wx hyperlinks beside the parameters, not part of the HTML.
link = dialog.m_searchHyperlink1
assert "pro.easyeda.com/editor" in link.GetURL(), f"editor link: {link.GetURL()}"
assert link.GetLabel() == "Open in EasyEDA Pro", f"link label: {link.GetLabel()}"
assert dialog.m_searchHyperlink2.GetLabel() == "JLCPCB", "JLCPCB link missing for a coded part"
assert dialog.m_paramsList.GetItemCount() > 3, "parameters list is empty"

panels = selectRow(PRO_CODELESS)
report("pro, codeless", panels)
for name, html in panels.items():
    assert drawings(html) == 1, f"{name} panel holds {drawings(html)}, expected a local render"
    assert 'class="note"' not in html, f"{name} panel shows a note despite being rendered"
assert not dialog.m_searchHyperlink2.IsShown(), "a codeless part has no JLCPCB page"

panels = selectRow(PRO_EMPTY)
report("pro, no data", panels)
for name, html in panels.items():
    assert drawings(html) == 0, f"{name} panel drew something for an empty document"
    assert 'class="note"' in html, f"{name} panel is blank with no explanation"

panels = selectRow(STD_PART)
report("std", panels)
for name, html in panels.items():
    assert drawings(html) == 1, f"{name} panel holds {drawings(html)} thumbnails, expected 1"
    assert "<img" in html, f"{name} should be an EasyEDA thumbnail"
assert "easyeda.com/component/" in link.GetURL(), f"Std viewer link: {link.GetURL()}"
assert link.GetLabel() == "Open in EasyEDA Std", f"link label: {link.GetLabel()}"

print("PREVIEW SMOKE OK",
      "(driven by simulated clicks)" if clicked
      else "(window would not take focus; handler called directly)")
