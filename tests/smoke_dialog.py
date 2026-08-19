"""Drive the real dialog end to end: search, filter, sort, queue, download.

    python3 tests/smoke_dialog.py

Needs the network, KiCad's `pcbnew`, and `wx`. It writes into a throwaway
`KIPRJMOD`, so it never touches a real project. Roughly two minutes, most of it
waiting on EasyEDA. Run it by hand after touching the dialog.

Covers the defects the redesign fixed, so each assertion is a regression guard:
the page count that summed every facet, a source column derived from the part
code instead of the facet, an unreadable footprint document title in the Package
column, dead sort headers, a Std library unreachable from "All Sources", and a
part queue that survived a successful download and silently re-downloaded.
"""
import os
import sys
import tempfile
import time

# `easyeda_lib_loader` imports its siblings relatively, the way KiCad loads it,
# so the repository needs a package name before it can be imported at all.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pkgroot = tempfile.mkdtemp(prefix="jlcpkgroot")
os.symlink(ROOT, os.path.join(pkgroot, "jlcpkg"))
sys.path.insert(0, pkgroot)

project = tempfile.mkdtemp(prefix="jlcdialog")
os.environ["KIPRJMOD"] = project

import pcbnew  # noqa: F401,E402  (imported for its side effects, as inside KiCad)
import wx  # noqa: E402

from jlcpkg import easyeda_lib_loader as ell  # noqa: E402

# Registering the library tables asks with a modal wx.MessageDialog, which nothing
# is there to answer, so an unattended run hung there forever. The tables are
# `smoke_download.py`'s subject; here the download itself is. Record the request
# instead of showing it, and check the rows separately below.
prompted = []
ell.LibraryTableManager.prompt_add_library = (
    lambda self, parent, lib_name, lib_path, sources=("pro",):
        prompted.append((lib_name, tuple(sources))))

# A JLC System part with a code, a JLC part reachable only by search text, and an
# EasyEDA Std symbol: one of each download path.
PRO_CODE = "C2040"
STD_PART = "std:4c0dae4e58984c06b7812642e521e379"

app = wx.App()
app.SetAssertMode(wx.APP_ASSERT_SUPPRESS)
plugin = ell.EasyEDALibLoaderPlugin()
dlg = plugin.createDialog()
dlg.SetSize((1180, 820))
dlg.Show()
tree = dlg.m_searchResultsTree


def pump(seconds):
    """Run the event loop: the search and download run on worker threads."""
    end = time.time() + seconds

    while time.time() < end:
        wx.Yield()
        time.sleep(0.05)


def rows():
    """The grid as the user sees it, after filtering and sorting."""
    out, item = [], tree.GetFirstItem()

    while item.IsOk():
        out.append(tuple(tree.GetItemText(item, column)
                         for column in range(len(ell.RESULT_COLUMNS))))
        item = tree.GetNextItem(item)

    return out


def search(selection, text, seconds):
    dlg.m_libSourceChoice.SetSelection(selection)
    dlg.m_textCtrlSearch.SetValue(text)
    plugin.onSearch(None)
    pump(seconds)

    return rows()


class StubSelection:
    """What the selection handler reads off a wx TreeListEvent."""

    def __init__(self, item):
        self.item = item

    def GetItem(self):
        return self.item


pump(1.0)

# --- one facet, one count --------------------------------------------------------
found = search(1, "ams1117", 10.0)
status, page = dlg.m_searchStatus.GetLabel(), dlg.m_searchPage.GetLabel()
print(f"JLC System: {len(found)} rows | {status} | {page}")
assert found, "a search for a stock part returned nothing"
# The API answers with a list per facet whatever you asked for; counting all of
# them claimed thousands of parts and dozens of pages that could not be paged to.
count = int(status.split()[0])
assert count == 285, f"the count is not the queried facet's: {status}"
assert page.endswith(f"/{-(-count // ell.SEARCH_PAGE_SIZE)}", ), f"page count wrong: {page}"
assert {row[0] for row in found} == {"System"}, \
    f"rows leaked in from another facet: {sorted({row[0] for row in found})}"

first = found[0]
print("first row:", first)
assert first[1].startswith("C"), f"no part code in the code column: {first[1]}"
# The footprint document is titled SOT-223-3_L6.5-W3.4-P2.30-LS7.0-BR; the package
# a person recognises is SOT-223. Column 7 is Package, column 6 the document title.
assert first[7] and "_L" not in first[7], f"package column is a document title: {first[7]}"
assert first[6], "no footprint name, which is what pcbnew will call it"
assert first[5], "no symbol name, which is what eeschema will call it"
assert first[3], "no value"

# --- the filter narrows the page without another round trip ---------------------
dlg.m_textCtrlFilter.SetValue("5.0")
pump(1.0)
filtered = rows()
print(f"filter '5.0': {len(filtered)} of {len(found)} | {dlg.m_searchPage.GetLabel()}")
assert 0 < len(filtered) < len(found), f"the filter did nothing: {len(filtered)}"
# The grid only shows RESULT_COLUMNS, but a row carries one more cell: the flattened
# attributes and category tags the filter also searches. Re-checking the match against
# the truncated visible row would call a legitimate hit on a hidden value a failure,
# so the full row is looked up by code.
full = {row[1]: row for row in plugin.searchRows}
assert all(ell.rowMatches(full[row[1]], "5.0") for row in filtered), \
    "a non-matching row survived: " + str(next(row for row in filtered
                                               if not ell.rowMatches(full[row[1]], "5.0")))
assert any(len(full[row[1]]) > len(ell.RESULT_COLUMNS) for row in filtered), \
    "rows carry no hidden searchable cell, so the filter cannot see attributes"
assert "shown" in dlg.m_searchPage.GetLabel(), "the filter does not say how much it hid"
dlg.m_textCtrlFilter.SetValue("")
pump(1.0)
assert len(rows()) == len(found), "clearing the filter did not restore the page"
assert dlg.m_searchPage.GetLabel() == page, \
    f"clearing the filter left its count behind: {dlg.m_searchPage.GetLabel()}"

# --- the sort headers reorder the grid -------------------------------------------
tree.SetSortColumn(2, True)
plugin.renderRows()
pump(0.5)
names = [row[2] for row in rows()]
assert names == sorted(names, key=lambda name: ell.sortKey((None, None, name), 2)), \
    "clicking a column header does not sort"
print("sorted by Name:", names[:2])
tree.SetSortColumn(1, True)
plugin.renderRows()
pump(0.3)

# --- selecting a row fills the inspector ----------------------------------------
item = tree.GetFirstItem()
tree.Select(item)
plugin.onSearchItemSelected(StubSelection(item))
pump(7.0)
print("title:", dlg.m_partTitle.GetLabel(), "| params:", dlg.m_paramsList.GetItemCount())
assert dlg.m_paramsList.GetItemCount() > 3, "the parameters list is empty"
assert dlg.m_searchHyperlink2.IsShown() and dlg.m_searchHyperlink2.GetLabel() == "JLCPCB", \
    "a coded part must link to its JLCPCB page"

# --- All Sources reaches the Std library too ------------------------------------
found = search(0, "max17048", 14.0)
sources = sorted({row[0] for row in found})
print(f"All Sources: {len(found)} rows | {sources} | {dlg.m_searchStatus.GetLabel()}")
assert "Std" in sources, "All Sources still queries only the Pro API"
assert {"System", "Public"} & set(sources), "All Sources lost the Pro rows"
kinds = sorted({row[9] for row in found})
print("types:", kinds)
assert "Symbol" in kinds or "Footprint" in kinds, "Std rows do not say what they are"

# --- queue, download, and the queue must empty itself ---------------------------
assert not dlg.m_actionBtn.IsEnabled(), "Download is enabled with an empty queue"
plugin.queue.addCodes(f"{PRO_CODE}\n{STD_PART}\n")
plugin.refreshQueue()
pump(0.5)
print("queued:", plugin.queue.codes(), "| Download enabled:", dlg.m_actionBtn.IsEnabled())
assert dlg.m_actionBtn.IsEnabled(), "Download stays disabled with parts queued"
assert dlg.m_queueList.GetItemCount() == 2, "the queue list does not show the queued parts"

plugin.onDownload(None)

for _ in range(150):
    pump(1.0)

    if plugin.downloadThread is None and dlg.m_resultStatus.GetLabel():
        break

result = dlg.m_resultStatus.GetLabel()
print("result:", result)
print("queue after:", len(plugin.queue), "| log pane expanded:", dlg.m_logPane.IsExpanded())
assert result.startswith("Downloaded"), f"the download did not report success: {result!r}"
assert "2 symbols" in result and "2 footprints" in result, f"miscounted: {result!r}"
# The old dialog kept the part list around, so the next Download silently repeated it.
assert len(plugin.queue) == 0, "a clean run must empty the queue"
assert dlg.m_progress.GetValue() == dlg.m_progress.GetRange(), "the progress bar did not finish"

target = os.path.join(project, "EasyEDA_Lib")
elibz = os.path.join(target, "EasyEDA_Lib.elibz")
stdzip = os.path.join(target, "EasyEDA_Lib-std.zip")
print("elibz:", os.path.exists(elibz), "| std zip:", os.path.exists(stdzip))
assert os.path.exists(elibz), "the Pro part did not reach a .elibz"
assert os.path.exists(stdzip), "the Std part did not reach a -std.zip"

# Both sources were downloaded, so both were offered for registration - a Std part
# whose table row is never proposed imports into a library KiCad cannot see.
print("prompted:", prompted)
assert prompted, "the library tables were never offered"
assert prompted[-1] == ("EasyEDA_Lib", ("pro", "std")), \
    f"both sources must be offered for a mixed download: {prompted}"

print("DIALOG SMOKE OK")
