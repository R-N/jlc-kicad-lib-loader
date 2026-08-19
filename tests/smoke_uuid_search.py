"""Pasting a uuid into Find must resolve it, or say what it is.

Neither EasyEDA keyword search indexes uuids, so a pasted one used to return
nothing at all - even for a part that exists and that both APIs will hand over on
a direct lookup. A 3D model file uuid resolves on neither and has to say so: it is
not a part, and it arrives with whichever part references it.

Needs wx, pcbnew and the network. ~15s.
"""
import os, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pkgroot = tempfile.mkdtemp(prefix="jlcpkgroot")
os.symlink(ROOT, os.path.join(pkgroot, "jlcpkg"))
sys.path.insert(0, pkgroot)
os.environ.setdefault("KIPRJMOD", tempfile.mkdtemp(prefix="jlcuuid"))

import pcbnew, wx  # noqa
from jlcpkg import easyeda_lib_loader as ell

app = wx.App()
app.SetAssertMode(wx.APP_ASSERT_SUPPRESS)
plugin = ell.EasyEDALibLoaderPlugin()
dlg = plugin.createDialog()
dlg.Show()

CASES = [
    ("a Pro device uuid", "f58385f66b144586baef3753ba84f65d", 1),
    ("a Std document uuid", "7e897495bbaf42c9a1e64c55011a8529", 1),
    ("the same, prefixed", "std:191f82fa4cdb4362ace6b365bebb2565", 1),
    ("a 3D model file uuid", "86c9a28785b84e52859c2af3e4e264a5", 0),
    ("a uuid that is nothing", "00000000000000000000000000000000", 0),
    ("an LCSC code still searches", "C631727", 1),
]

for label, term, expected in CASES:
    dlg.m_log.Clear()
    dlg.m_textCtrlSearch.SetValue(term)
    plugin.onSearch(None)
    end = time.time() + 60

    while time.time() + 0 < end and (plugin.searchThread is not None):
        wx.Yield()
        time.sleep(0.05)

    for _ in range(20):
        wx.Yield()
        time.sleep(0.05)

    rows = len(plugin.searchRows)
    grid = dlg.m_searchResultsTree.GetFirstItem()
    shown = dlg.m_searchResultsTree.GetItemText(grid, 1) if grid.IsOk() else "-"
    name = dlg.m_searchResultsTree.GetItemText(grid, 2) if grid.IsOk() else ""
    note = [line for line in dlg.m_log.GetValue().splitlines() if "WARNING" in line]
    print(f"{label:30} rows={rows} status={dlg.m_searchStatus.GetLabel()!r:18} "
          f"first={shown!r} {name[:26]!r}")

    if note:
        print(f"{'':30} log: {note[0].split('WARNING')[-1].strip()[:120]}")

    assert rows >= expected, f"{label}: {rows} rows, expected at least {expected}"

    if expected == 0:
        assert note, f"{label}: nothing found and nothing said"

print("\nUUID SEARCH OK")
dlg.Destroy()
