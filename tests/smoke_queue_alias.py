"""The queue's Alias column, and the aliases reaching the loader.

Drives the real dialog and the real onDownload, with two things stubbed: the
library-table prompt, a modal wx.MessageDialog that nothing clicks in an unattended
run, and ComponentLoader itself, because what is under test is the wiring rather
than another download. Needs wx and a display; no network.
"""
import os, sys, tempfile, types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
project = tempfile.mkdtemp(prefix="aliaswiring")
os.environ["KIPRJMOD"] = project

pkgroot = tempfile.mkdtemp(prefix="jlcpkgroot")
os.symlink(ROOT, os.path.join(pkgroot, "jlcpkg"))
sys.path.insert(0, pkgroot)
sys.path.insert(0, ROOT)

import wx
from jlcpkg import easyeda_lib_loader as ell
from jlcpkg import config_manager as cm

captured = {}


class FakeLoader:
    def __init__(self, **kwargs):
        captured["init"] = kwargs

    def downloadAll(self, components, aliases=None):
        captured["components"] = list(components)
        captured["aliases"] = aliases
        return {"symbols": len(components), "footprints": len(components),
                "models": 0, "skipped": 0, "failed": 0, "error": None}


# No modal: the prompt is a YES/NO dialog and this run has nobody to answer it.
cm.LibraryTableManager.prompt_add_library = lambda self, *a, **k: captured.setdefault("prompted", True)
ell.ComponentLoader = FakeLoader

app = wx.App()
app.SetAssertMode(wx.APP_ASSERT_SUPPRESS)
plugin = ell.EasyEDALibLoaderPlugin()
dlg = plugin.createDialog()

plugin.queue.addCodes("C15127\nC6186\nstd:4c0dae4e58984c06b7812642e521e379\n")
plugin.queue.setAlias("C15127", "MY-MOSFET")
plugin.queue.setAlias("std:4c0dae4e58984c06b7812642e521e379", "FUEL-GAUGE")
plugin.refreshQueue()

assert dlg.m_queueList.GetItemCount() == 3, "queue list does not show the parts"
assert dlg.m_queueList.GetColumnCount() == 4, \
    f"queue has {dlg.m_queueList.GetColumnCount()} columns, expected an Alias column"
assert dlg.m_queueList.GetColumn(3).GetText() == "Alias", "the fourth column is not Alias"
aliasCells = [dlg.m_queueList.GetItemText(i, 3) for i in range(3)]
print("alias column:", aliasCells)
assert aliasCells == ["MY-MOSFET", "", "FUEL-GAUGE"], f"alias column shows {aliasCells}"

plugin.onDownload(None)
for _ in range(200):
    wx.Yield()
    if plugin.downloadThread is None and dlg.m_resultStatus.GetLabel():
        break
    import time; time.sleep(0.05)

print("prompted for library tables:", captured.get("prompted"))
print("components:", captured.get("components"))
print("aliases   :", captured.get("aliases"))
assert captured.get("components") == ["C15127", "C6186", "std:4c0dae4e58984c06b7812642e521e379"], \
    "the queued codes did not reach the loader"
assert captured.get("aliases") == {"C15127": "MY-MOSFET",
                                   "std:4c0dae4e58984c06b7812642e521e379": "FUEL-GAUGE"}, \
    f"the aliases did not reach the loader: {captured.get('aliases')}"
print("result:", dlg.m_resultStatus.GetLabel())
assert len(plugin.queue) == 0, "a clean run must empty the queue"

# The Alias cell is edited by double-clicking the row, which wx reports as ITEM_ACTIVATED.
plugin.queue.addCodes("C2040\n")
plugin.refreshQueue()
plugin.queue.setAlias("C2040", "TYPED-IN-DIALOG")
plugin.refreshQueue()
assert dlg.m_queueList.GetItemText(0, 3) == "TYPED-IN-DIALOG", "the edited alias is not shown"
print("\nWIRING OK: queue aliases reach ComponentLoader.downloadAll and show in the column")
dlg.Destroy()
