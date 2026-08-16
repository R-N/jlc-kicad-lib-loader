#!/usr/bin/env python
from __future__ import annotations
from typing import Optional

import os
import math
import re
import traceback
import logging

if "darwin" in os.sys.platform:
    # SSL fix for macOS KiCad Python ---
    try:
        import certifi
        os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    except Exception:
        pass

import requests
import wx

# Read version file
__version__ = "0.0.0"
try:
    version_file = os.path.join(os.path.dirname(__file__), "VERSION")
    if os.path.exists(version_file):
        with open(version_file, "r") as f:
            version_content = f.read().strip()
            if version_content:
                __version__ = version_content
except Exception:
    pass

DEFAULT_USER_AGENT = f"jlc-kicad-lib-loader/{__version__}"

session = requests.Session()
session.headers.update({"User-Agent": DEFAULT_USER_AGENT})

def isUuid(value):
    """True for a bare 32-hex EasyEDA uuid (used to search by uuid directly)."""
    return len(value) == 32 and all(c in "0123456789abcdefABCDEF" for c in value)

def contributorOf(entry):
    """Best-effort contributor/owner nickname from a search or detail entry."""
    for key in ("creator", "owner"):
        who = entry.get(key) or {}
        if who.get("nickname"):
            return who["nickname"]
        if who.get("username"):
            return who["username"]

    return ""

def thumbUrl(result, uuid):
    """Absolute https URL of a component thumbnail.

    EasyEDA Std renders both symbol and footprint documents at
    image.easyeda.com/components/<uuid>.png, even when the JSON has no "thumb".
    """
    thumb = (result or {}).get("thumb")

    if thumb:
        return "https:" + thumb if thumb.startswith("//") else thumb

    return f"https://image.easyeda.com/components/{uuid}.png" if uuid else None

def imageMarkup(url):
    """<img> that removes itself when the source has no rendering."""
    return (f'<img src="{url}" onerror="this.parentNode.style.display=\'none\'"/>'
            if url else "")

def productSvgs(code):
    """Symbol and footprint drawings of an LCSC part, as inline SVG markup.

    Pro documents have no thumbnail service; the drawings come from the same
    endpoint the JLCPCB part preview page uses, keyed by LCSC code.
    Returns (symbol, footprint), either of which may be empty.
    """
    if not re.fullmatch(r"C\d+", code or ""):
        return "", ""

    resp = session.get(f"https://easyeda.com/api/products/{code}/svgs")
    resp.raise_for_status()

    # docType 2 = symbol, 4 = footprint
    drawings = {str(doc.get("docType")): doc.get("svg", "") for doc in resp.json().get("result") or []}

    return drawings.get("2", ""), drawings.get("4", "")

def proDrawings(symbolUuid, footprintUuid):
    """Symbol and footprint drawings rendered from the Pro documents themselves.

    The fallback for parts EasyEDA never rendered, which is every JLC Public
    part drawn from scratch instead of imported from LCSC.
    Returns (symbol, footprint), either of which may be empty.
    """
    drawings = []

    for uuid, render in ((symbolUuid, pro_render.symbolSvg),
                         (footprintUuid, pro_render.footprintSvg)):
        try:
            drawings.append(render(fetchDataStr(session, uuid)) if uuid else "")
        except Exception as e:
            # A missing pycryptodome or an unreadable document costs the drawing,
            # nothing else.
            warning(f"Could not fetch Pro document {uuid}: {e}")
            drawings.append("")

    return drawings[0], drawings[1]

# Results per request, for both APIs.
SEARCH_PAGE_SIZE = 50

# One drawing, centred and scaled to its panel. Each drawing gets its own panel, so
# the page holds exactly one and needs no layout of its own.
DRAWING_PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8"><style>
    html, body {{ height: 100%; margin: 0; }}
    body {{ display: flex; align-items: center; justify-content: center;
            font-family: sans-serif; background: #ffffff; }}
    /* Fill the panel: max-width alone only ever shrinks, so a 220px drawing stayed
       a stamp in a 500px panel. A viewBox keeps the aspect ratio. */
    svg {{ width: 100%; height: 100%; }}
    img {{ width: 100%; height: 100%; object-fit: contain; }}
    .note {{ color: #666; font-size: 90%; text-align: center; margin: 0 12px; }}
</style></head><body>{body}</body></html>"""

# Searches behind each entry of m_libSourceChoice, filled in createDialog because
# the search functions close over the dialog.
SOURCE_SEARCHES = []

# Columns of the results grid, in order. The row builders below must match.
# Eight columns share the results pane, so the headings are short enough not to be
# truncated themselves; the widths fit "Extended", "Footprint" and a supplier name.
RESULT_COLUMNS = (("Src", 64), ("Code", 90), ("Name", 118), ("Description", 160),
                  ("Package", 84), ("Class", 88), ("Type", 74), ("By", 72))

# Source labels, also what the Source column shows.
SOURCE_SYSTEM = "System"
SOURCE_PUBLIC = "Public"
SOURCE_STD = "Std"

# Which source each EasyEDA Pro search facet is. The facet a row came from is the
# truth; an LCSC code is not, because JLC's own catalogue holds parts with no code
# yet, which used to be labelled as user-contributed.
PRO_FACET_SOURCE = {"lcsc": SOURCE_SYSTEM, "user": SOURCE_PUBLIC, "mine": SOURCE_PUBLIC}

def tagText(tags):
    """Category names from a Pro device's tags, for the search blob."""
    out = []
    for key in ("parent_tag", "child_tag"):
        name = ((tags or {}).get(key) or {}).get("name") or ""
        if name:
            out.append(name)
    return " ".join(out)

def proRow(entry, source=SOURCE_PUBLIC):
    """A results row for an EasyEDA Pro device, as the search API returns it."""
    attributes = entry.get("attributes") or {}
    code = entry.get("product_code") or entry.get("uuid", "")
    name = entry.get("display_title") or entry.get("title", "")
    description = attributes.get("LCSC Part Name", "")

    searchable = " ".join(filter(None, [
        name,
        entry.get("title", ""),
        tagText(entry.get("tags")),
        searchableText(attributes),
    ]))

    return (source,
            code,
            name,
            description,
            # The footprint document title is unreadable ("SOT-223-3_L6.5-W3.4-P2.30-LS7.0-BR");
            # the supplier's package name is what a person recognises.
            attributes.get("Supplier Footprint")
            or ((entry.get("footprint") or {}).get("display_title", "")),
            (attributes.get("JLCPCB Part Class") or "").replace(" Part", ""),
            "Device",
            contributorOf(entry),
            searchable)

def stdRow(entry):
    """A results row for an EasyEDA Std component.

    `head.docType` 2 is a symbol and 4 a standalone footprint; the grid says which,
    because a footprint-only entry is otherwise indistinguishable from a symbol.
    """
    dataStr = entry.get("dataStr") or {}
    head = dataStr.get("head") or {}
    params = head.get("c_para") or {}
    kind = "Footprint" if str(head.get("docType")) == "4" else "Symbol"

    return (SOURCE_STD,
            STD_PREFIX + entry["uuid"],
            entry.get("title", ""),
            "",
            params.get("package", ""),
            "",
            kind,
            contributorOf(entry),
            searchableText(params))

def searchableText(attributes):
    """The parameter key/value pairs flattened into one string, so the filter box
    matches parameter values ("100k ohm", "±1%") and not just the grid columns."""
    return " ".join(f"{key} {value}" for key, value in (attributes or {}).items()
                    if isinstance(value, str) and value)

def rowMatches(row, query):
    """True when every whitespace-separated term appears somewhere in the row."""
    if not query or not query.strip():
        return True

    haystack = " ".join(str(cell) for cell in row).casefold()

    return all(term in haystack for term in query.casefold().split())

def sortKey(row, column):
    """Sort key for one column: numeric where the text is numeric, else case-folded."""
    value = str(row[column]) if column < len(row) else ""
    digits = re.fullmatch(r"[A-Za-z]*(\d+)", value.strip())

    return (0, int(digits.group(1)), "") if digits else (1, 0, value.casefold())

class PartQueue:
    """The parts to download: ordered, de-duplicated, keyed by code.

    Deliberately not a text box. The old free-text field outranked the search
    selection whenever it held anything, so a stale line silently re-downloaded
    the previous parts.
    """

    def __init__(self):
        self.entries = {}

    def add(self, code, source="", name=""):
        """True when the part was new to the queue."""
        code = (code or "").strip()

        if not code or code in self.entries:
            return False

        self.entries[code] = (source, code, name)

        return True

    def addRows(self, rows):
        """Queue result rows; returns how many were new."""
        return sum(self.add(row[1], row[0], row[2]) for row in rows)

    def addCodes(self, text):
        """Queue pasted codes and uuids, one per line; returns how many were new."""
        added = 0

        for line in (text or "").splitlines():
            code = line.strip()

            if code:
                source = SOURCE_STD if code.startswith(STD_PREFIX) else (
                    SOURCE_SYSTEM if re.fullmatch(r"C\d+", code) else SOURCE_PUBLIC)
                added += self.add(code, source, "")

        return added

    def remove(self, codes):
        for code in list(codes):
            self.entries.pop(code, None)

    def clear(self):
        self.entries.clear()

    def rows(self):
        return list(self.entries.values())

    def codes(self):
        return [code for _, code, _ in self.entries.values()]

    def __len__(self):
        return len(self.entries)

wx_html2_available = True
try: 
    import wx.html2
except ImportError as e:
    wx_html2_available = False

from threading import Lock, Thread
from logging import info, warning, debug, error, critical
from io import StringIO

import wx.dataview

from .component_loader import *
from . import pro_render
from .easyeda_lib_loader_dialog import EasyEdaLibLoaderDialog
from .config_manager import ConfigManager, LibraryTableManager

from pcbnew import *
import ctypes

log_stream = StringIO()    
logging.basicConfig(stream=log_stream, level=logging.INFO)

def interrupt_thread(thread):
    print("interrupt_thread")
    if not thread.is_alive():
        return

    exc = ctypes.py_object(KeyboardInterrupt)
    res = ctypes.pythonapi.PyThreadState_SetAsyncExc(
        ctypes.c_long(thread.ident), exc)

    if res == 0:
        print("nonexistent thread id")
        return False
    elif res > 1:
        # """if it returns a number greater than one, you're in trouble,
        # and you should call it again with exc=NULL to revert the effect"""
        ctypes.pythonapi.PyThreadState_SetAsyncExc(thread.ident, None)
        print("PyThreadState_SetAsyncExc failed")

        return False
    
    print("interrupt_thread success")
    return True

class WxTextCtrlHandler(logging.Handler):
    """Writes the log into the dialog's Details pane.

    `onProblem` is called for anything at WARNING or above, so a collapsed pane can
    open itself: a silent failure is worse than a taller dialog.
    """

    def __init__(self, ctrl: wx.TextCtrl, onProblem=None):
        logging.Handler.__init__(self)
        self.ctrl = ctrl
        self.onProblem = onProblem

    def emit(self, record):
        s = self.format(record) + '\n'
        wx.CallAfter(self.ctrl.AppendText, s)

        if self.onProblem and record.levelno >= logging.WARNING:
            wx.CallAfter(self.onProblem)

class EasyEDALibLoaderPlugin(ActionPlugin):
    dialog: Optional[EasyEdaLibLoaderDialog] = None
    downloadThread: Optional[Thread] = None
    searchThread: Optional[Thread] = None
    searchPage = 1
    # What the paging label shows when no filter is narrowing the page.
    pageLabel = ""
    components = []
    
    def defaults(self):
        self.name = "EasyEDA (LCEDA) Library Loader"
        self.category = "3D data loader"
        self.description = "Load library parts from EasyEDA (LCEDA)"
        self.show_toolbar_button = True
        self.icon_file_name = os.path.join(os.path.dirname(__file__), 'easyeda_lib_loader.png')

    def Run(self):
        if(self.dialog is None):
            self.dialog = self.createDialog()

        self.dialog.Show()
        self.dialog.Raise()

    def createDialog(self):
        frame = wx.FindWindowByName("PcbFrame")
    
        dlg = EasyEdaLibLoaderDialog(frame)
        dlg.SetTitle(dlg.GetTitle() + f" Version {__version__}")

        def openLogOnProblem():
            if not dlg.m_logPane.IsExpanded():
                dlg.m_logPane.Expand()
                dlg.Layout()

        handler = WxTextCtrlHandler(dlg.m_log, openLogOnProblem)
        logging.getLogger().handlers.clear();
        logging.getLogger().addHandler(handler)
        FORMAT = "%(levelname)s: %(message)s"
        handler.setFormatter(logging.Formatter(FORMAT))
        logging.getLogger().setLevel(level=logging.INFO)
        
        # Get KIPRJMOD early to initialize config
        kiprjmod = os.getenv("KIPRJMOD") or ""
        config_manager = None
        library_manager = None
        
        if kiprjmod:
            config_manager = ConfigManager(kiprjmod)
            library_manager = LibraryTableManager(kiprjmod)

        # Every row the last search returned, unfiltered and unsorted. The grid is a
        # view of this: filtering and sorting rebuild it without touching the network.
        self.searchRows = []
        self.queue = PartQueue()

        def progressHandler( current, total ):
            wx.CallAfter(dlg.m_progress.SetRange, total)
            wx.CallAfter(dlg.m_progress.SetValue, current)

        def onDebugCheckbox( event: wx.CommandEvent ):
            logging.getLogger().setLevel( logging.DEBUG if event.IsChecked() else logging.INFO )

        def setResult( text ):
            wx.CallAfter(dlg.m_resultStatus.SetLabel, text)

        def selectedRows():
            """Result rows the user has selected, in grid order."""
            codes = {dlg.m_searchResultsTree.GetItemText(item, 1)
                     for item in dlg.m_searchResultsTree.GetSelections()}

            return [row for row in self.searchRows if row[1] in codes]

        def refreshQueue():
            dlg.m_queueList.DeleteAllItems()

            for source, code, name in self.queue.rows():
                row = dlg.m_queueList.InsertItem(dlg.m_queueList.GetItemCount(), source)
                dlg.m_queueList.SetItem(row, 1, code)
                dlg.m_queueList.SetItem(row, 2, name)

            dlg.m_queueLabel.SetLabel(
                f"Queue: {len(self.queue)} part{'' if len(self.queue) == 1 else 's'}"
                if self.queue else
                "Download queue is empty. Double-click a result to add it.")
            dlg.m_actionBtn.Enable(bool(self.queue) and not self.downloadThread)
            dlg.m_queueRemoveBtn.Enable(bool(self.queue))
            dlg.m_queueClearBtn.Enable(bool(self.queue))

        def queueRows( rows ):
            added = self.queue.addRows(rows)
            refreshQueue()

            if rows and not added:
                setResult("Already queued.")

        def onQueueAdd( event ):
            rows = selectedRows()

            if not rows:
                setResult("Select a result first.")
                return

            queueRows(rows)

        def onQueuePaste( event ):
            dialog = wx.TextEntryDialog(dlg, "One LCSC code or UUID per line."
                                        " Prefix an EasyEDA Std uuid with 'std:'.",
                                        "Queue parts by code", style=wx.TE_MULTILINE | wx.OK | wx.CANCEL)
            dialog.SetSize((460, 320))

            if dialog.ShowModal() == wx.ID_OK:
                added = self.queue.addCodes(dialog.GetValue())
                refreshQueue()
                setResult(f"Queued {added} part{'' if added == 1 else 's'}.")

            dialog.Destroy()

        def onQueueRemove( event ):
            codes = []
            row = dlg.m_queueList.GetFirstSelected()

            while row != -1:
                codes.append(dlg.m_queueList.GetItemText(row, 1))
                row = dlg.m_queueList.GetNextSelected(row)

            self.queue.remove(codes or self.queue.codes()[-1:])
            refreshQueue()

        def onQueueClear( event ):
            self.queue.clear()
            refreshQueue()

        def onBrowse( event ):
            start = dlg.m_textCtrlOutLibName.GetValue()

            if not os.path.isabs(start):
                start = os.path.join(os.getenv("KIPRJMOD") or "", start)

            dialog = wx.DirDialog(dlg, "Library folder", defaultPath=start,
                                  style=wx.DD_DEFAULT_STYLE)

            if dialog.ShowModal() == wx.ID_OK:
                chosen = dialog.GetPath()
                kiprjmod = os.getenv("KIPRJMOD") or ""

                # Keep it project-relative when it is inside the project, so the
                # library table entry stays portable.
                if kiprjmod and os.path.commonpath([chosen, kiprjmod]) == kiprjmod:
                    chosen = os.path.relpath(chosen, kiprjmod)

                dlg.m_textCtrlOutLibName.SetValue(chosen)

            dialog.Destroy()

        def onDownload( event ):
            dlg.m_log.Clear()
            components = self.queue.codes()

            if not components:
                setResult("Nothing queued.")
                return

            kiprjmod = os.getenv("KIPRJMOD") or ""

            if not kiprjmod:
                error( "KIPRJMOD is not set properly." )
                setResult("No KiCad project: open a board first.")
                return

            lib_field = dlg.m_textCtrlOutLibName.GetValue()
            target_path = lib_field if os.path.isabs(lib_field) else os.path.join(kiprjmod, lib_field)
            target_name = os.path.basename(target_path)

            if config_manager:
                config_manager.set_library_name(lib_field)

            if library_manager:
                proComponents, stdComponents = splitSources(components)
                sources = (["pro"] if proComponents else []) + (["std"] if stdComponents else [])
                library_manager.prompt_add_library(dlg, target_name, target_path, sources)

            def threadedFn():
                loader = ComponentLoader(kiprjmod=kiprjmod, target_path=target_path,
                                        target_name=target_name, progress=progressHandler,
                                        session=session)
                summary = loader.downloadAll(components)
                wx.CallAfter(onDownloadFinished, summary)

            setResult(f"Downloading {len(components)} part{'' if len(components) == 1 else 's'}…")
            dlg.m_actionBtn.Disable()
            self.downloadThread = Thread(target = threadedFn, daemon=True)
            self.downloadThread.start()

        def onDownloadFinished( summary ):
            self.downloadThread = None
            parts = [f"{summary['symbols']} symbol{'' if summary['symbols'] == 1 else 's'}",
                     f"{summary['footprints']} footprint{'' if summary['footprints'] == 1 else 's'}",
                     f"{summary['models']} model{'' if summary['models'] == 1 else 's'}"]

            if summary["skipped"]:
                parts.append(f"{summary['skipped']} without a STEP model")

            failed = summary["failed"] or summary["error"]

            if failed:
                # The queue keeps its parts so the run can be retried once the cause
                # is fixed, and the log opens itself because something needs reading.
                setResult(f"Finished with problems: {summary['error'] or str(summary['failed']) + ' failed'}."
                          f" Downloaded {', '.join(parts)}. See Details.")
                dlg.m_logPane.Expand()
                dlg.Layout()
            else:
                self.queue.clear()
                setResult(f"Downloaded {', '.join(parts)}. Restart pcbnew to use new footprints.")

            refreshQueue()

        def stdSearchFn(words, page, append=False):
            """EasyEDA Std search. Returns (count, totalPages)."""
            if isUuid(words.strip()):
                # Direct uuid lookup: one component, shown as a single result.
                resp = session.get(STD_COMPONENT_URL.format(uuid=words.strip()))
                resp.raise_for_status()
                found = resp.json()

                debug(json.dumps(found, indent=4))

                if not found.get("success") or not found.get("result"):
                    raise Exception(f"Unable to fetch component: {found}")

                addRows([stdRow(found["result"])])

                return 1, 1

            resp = session.post( STD_SEARCH_URL, data={
                "type": 3,
                "uid": "user",
                "wd": words,
                "page": page,
                "pageSize": SEARCH_PAGE_SIZE,
                "returnListStyle": "classifyarr"
            } )
            resp.raise_for_status()
            found = resp.json()

            debug(json.dumps(found, indent=4))

            if not found.get("success") or not found.get("result"):
                raise Exception(f"Unable to search: {found}")

            result = found["result"]
            addRows([stdRow(entry) for entry in result["lists"].get("user", [])])

            return int(result["facets"].get("user", 0)), int(result["totalPage"])

        def proSearchFn(facet, words, page):
            """EasyEDA Pro search. Returns (count, totalPages)."""
            reqData = {
                "page": page,
                "pageSize": SEARCH_PAGE_SIZE,
                "wd": words,
                "returnListStyle": "classifyarr"
            }

            if facet:
                reqData |= {"uid": facet, "path": facet}

            resp = session.post( PRO_SEARCH_URL, data=reqData )
            resp.raise_for_status()
            found = resp.json()

            debug(json.dumps(found, indent=4))

            if not found.get("success") or not found.get("result"):
                raise Exception(f"Unable to search: {found}")

            result = found["result"]
            facets = result["facets"]
            # Count the facet that was actually queried. Summing every facet reported
            # thousands of parts for a facet holding hundreds, and paged into nothing.
            count = int(facets.get(facet, 0)) if facet else int(sum(facets.values()))

            # The response carries a list per facet whatever was asked for, so a
            # JLC System search used to show user-contributed rows it had not counted.
            wanted = [facet] if facet else list(result["lists"].keys())

            for key in wanted:
                source = PRO_FACET_SOURCE.get(key, SOURCE_PUBLIC)
                addRows([proRow(entry, source) for entry in result["lists"].get(key) or []])

            return count, math.ceil(count / SEARCH_PAGE_SIZE)

        def searchWorker(sourceId, words, page):
            setStatus("Searching…")
            wx.CallAfter(dlg.m_prevPageBtn.Disable)
            wx.CallAfter(dlg.m_nextPageBtn.Disable)

            try:
                counts = []
                totalPages = 1

                # "All Sources" means both APIs, not just Pro's three facets.
                for label, search in SOURCE_SEARCHES[sourceId]:
                    try:
                        count, pages = search(words, page)
                        totalPages = max(totalPages, pages)
                        counts.append(f"{count} {label}" if label else f"{count} parts")
                    except KeyboardInterrupt:
                        raise
                    except Exception as e:
                        traceback.print_exc()
                        counts.append(f"{label or 'search'} failed")
                        warning(f"Search failed: {e}")

                # EasyEDA's search is fuzzy: "Resistor 100k" also returns 1Ω resistors
                # and 10µH inductors. Narrow the page to rows matching every word the
                # user typed, but only when that keeps a row - a term the data stores
                # differently (a Chinese description, say) must not blank the page.
                if words.strip():
                    matches = [row for row in self.searchRows if rowMatches(row, words)]
                    if matches:
                        self.searchRows = matches
                        wx.CallAfter(renderRows)

                if page > 1:
                    wx.CallAfter(dlg.m_prevPageBtn.Enable)

                if page < totalPages:
                    wx.CallAfter(dlg.m_nextPageBtn.Enable)

                setStatus(", ".join(counts))
                self.pageLabel = f"Page {page}/{totalPages}"
                setPage(self.pageLabel)
            except KeyboardInterrupt:
                print("KeyboardInterrupt.")
            except Exception as e:
                traceback.print_exc()
                setStatus(f"Failed to search parts: {e}")
            finally:
                self.searchThread = None

        def setStatus( status ):
            wx.CallAfter(dlg.m_searchStatus.SetLabel, status)
            wx.CallAfter(dlg.m_resultsPanel.Layout)

        def setPage( text ):
            # A StaticText resizes itself around the new text but keeps its old
            # position, so without a re-layout it grows over the paging buttons.
            wx.CallAfter(dlg.m_searchPage.SetLabel, text)
            wx.CallAfter(dlg.m_resultsPanel.Layout)

        def addRows( rows ):
            """Collect rows from a worker thread and show them through the filter."""
            self.searchRows.extend(rows)
            wx.CallAfter(renderRows)

        def renderRows():
            tree = dlg.m_searchResultsTree
            query = dlg.m_textCtrlFilter.GetValue()
            rows = [row for row in self.searchRows if rowMatches(row, query)]
            sorted_, column, ascending = tree.GetSortColumn()

            if sorted_:
                rows.sort(key=lambda row: sortKey(row, column), reverse=not ascending)

            tree.DeleteAllItems()

            for row in rows:
                item = tree.AppendItem(tree.GetRootItem(), row[0])

                # The last cell is the searchable parameter text, not a column.
                for column, value in enumerate(row[1:len(RESULT_COLUMNS)], start=1):
                    tree.SetItemText(item, column, value)

            # While filtering, the paging label counts what survived the filter;
            # clearing the filter has to put the page number back.
            setPage(f"{len(rows)} of {len(self.searchRows)} shown"
                    if query and self.searchRows else self.pageLabel)

        def loadSearchPage( sourceId, words, page ):
            if self.searchThread:
                interrupt_thread(self.searchThread)
                self.searchThread.join()

            self.searchRows = []
            wx.CallAfter(dlg.m_searchResultsTree.DeleteAllItems)

            self.searchThread = Thread(target = searchWorker, daemon=True,
                                       args=(sourceId, words, page))
            self.searchThread.start()

        def onSearch( event ):
            self.searchPage = 1
            loadSearchPage(dlg.m_libSourceChoice.GetSelection(), dlg.m_textCtrlSearch.GetValue(),
                           self.searchPage)

        def onNextPage( event ):
            self.searchPage += 1
            loadSearchPage(dlg.m_libSourceChoice.GetSelection(), dlg.m_textCtrlSearch.GetValue(),
                           self.searchPage)

        def onPrevPage( event ):
            self.searchPage -= 1
            loadSearchPage(dlg.m_libSourceChoice.GetSelection(), dlg.m_textCtrlSearch.GetValue(),
                           self.searchPage)

        def onFilter( event ):
            renderRows()

        def onColumnSorted( event ):
            renderRows()

        def onSearchItemActivated( event ):
            code = dlg.m_searchResultsTree.GetItemText(event.GetItem(), 1)
            queueRows([row for row in self.searchRows if row[1] == code])

        def setParams( attributes ):
            dlg.m_paramsList.DeleteAllItems()

            for key, value in (attributes or {}).items():
                if value in (None, ""):
                    continue

                row = dlg.m_paramsList.InsertItem(dlg.m_paramsList.GetItemCount(), str(key))
                dlg.m_paramsList.SetItem(row, 1, str(value))

        def showDrawing( view, markup, caption ):
            """Put one drawing in its own panel, or say why it is empty."""
            if not wx_html2_available or not isinstance(view, wx.html2.WebView):
                # Degraded pane: the static text already explains itself.
                return

            if markup:
                view.SetPage(DRAWING_PAGE.format(body=markup), "")
            else:
                view.SetPage(DRAWING_PAGE.format(
                    body=f'<p class="note">No {caption} drawing available.'
                         ' The EasyEDA document holds no geometry for it.</p>'), "")

        def onSearchItemSelected( event ):
            itemCode = dlg.m_searchResultsTree.GetItemText(event.GetItem(), 1)
            attributes = {}
            preview_title = ""
            preview_symbol = ""
            preview_footprint = ""

            if itemCode.startswith(STD_PREFIX):
                stdUuid = itemCode[len(STD_PREFIX):]

                try:
                    comp_info = session.get(STD_COMPONENT_URL.format(uuid=stdUuid))
                    comp_info.raise_for_status()
                    debug("std component info: " + json.dumps(comp_info.json(), indent=4))
                    result = comp_info.json()["result"]

                    attributes = dict((result.get("dataStr") or {}).get("head", {}).get("c_para") or {})
                    preview_title = result.get("title", "")
                    preview_symbol = imageMarkup(thumbUrl(result, stdUuid))
                    kicadNames = {"Symbol in KiCad": attributes.get("spiceSymbolName")
                                  or result.get("title", "")}

                    if result.get("packageDetail"):
                        package = result["packageDetail"]
                        attributes["Footprint"] = package.get("title", "")
                        kicadNames["Footprint in KiCad"] = (
                            (package.get("dataStr") or {}).get("head", {})
                            .get("c_para", {}).get("package") or package.get("title", ""))
                        preview_footprint = imageMarkup(thumbUrl(package, package.get("uuid")))

                    attributes = {**kicadNames, **attributes}
                except Exception as e:
                    traceback.print_exc()
                    warning(f"Failed to load component info for {stdUuid}: {e}")

                setLinks(("Open in EasyEDA Std", f"https://easyeda.com/component/{stdUuid}"))
            else:
                easyedaLink = None
                links = []

                try:
                    # The device endpoint takes a uuid; a JLC System row carries an LCSC
                    # code, which resolves through the same call the download path uses.
                    deviceUuid = itemCode

                    if re.fullmatch(r"C\d+", itemCode):
                        byCode = session.post(PRO_SEARCH_BY_CODES_URL, data={"codes[]": [itemCode]})
                        byCode.raise_for_status()
                        entries = byCode.json().get("result") or []

                        if not entries:
                            raise Exception(f"No EasyEDA Pro device for {itemCode}")

                        deviceUuid = entries[0]["uuid"]

                    dev_info = session.get(PRO_DEVICE_URL.format(uuid=deviceUuid))
                    dev_info.raise_for_status()
                    debug("device info: " + json.dumps(dev_info.json(), indent=4))
                    device = proResult(dev_info.json())
                    attributes = dict(device["attributes"])
                    preview_title = device.get('display_title') or device.get('title', '')
                    code = attributes.get('Supplier Part', '')

                    # First, because they are what a person needs and cannot guess: KiCad
                    # names a symbol after its symbol document and a footprint after its
                    # footprint document, and neither is the part number. One SOT-23
                    # footprint document is shared by hundreds of parts, so searching
                    # pcbnew for "AO3401A" finds nothing.
                    attributes = {"Symbol in KiCad": (device.get("symbol") or {}).get("display_title", ""),
                                  "Footprint in KiCad": (device.get("footprint") or {}).get("display_title", ""),
                                  **attributes}

                    if attributes.get('Symbol') or attributes.get('Footprint'):
                        # https://pro.easyeda.com/editor#tab=*!{sym_uuid}(device){dev_uuid}|!{fp_uuid}(device){dev_uuid}
                        tabList = [f"!{attributes[key]}(device){device['uuid']}"
                                   for key in ("Symbol", "Footprint") if attributes.get(key)]
                        easyedaLink = f"https://pro.easyeda.com/editor#tab=*{'|'.join(tabList)}"

                    if easyedaLink:
                        links.append(("Open in EasyEDA Pro", easyedaLink))

                    if re.fullmatch(r"C\d+", code or ""):
                        links.append(("JLCPCB", f"https://jlcpcb.com/partdetail/{code}"))
                        links.append(("LCSC", f"https://www.lcsc.com/product-detail/{code}.html"))

                    # Last: the drawings are a bonus, the links above must survive their failure
                    preview_symbol, preview_footprint = productSvgs(code)

                    if not preview_symbol and not preview_footprint:
                        # EasyEDA only renders parts with an LCSC code, so draw the
                        # documents ourselves for the ones it never rendered.
                        preview_symbol, preview_footprint = proDrawings(
                            attributes.get('Symbol'), attributes.get('Footprint'))
                except Exception as e:
                    traceback.print_exc()
                    warning(f"Failed to load device info for {itemCode}: {e}")

                setLinks(*links)

            dlg.m_partTitle.SetLabel(preview_title or itemCode)
            setParams(attributes)
            showDrawing(self.symbolView, preview_symbol, "symbol")
            showDrawing(self.footprintView, preview_footprint, "footprint")
            dlg.m_detailsPanel.Layout()

        def setLinks( *links ):
            """Fill the link row beside the parameters; unused links hide."""
            for control, link in zip((dlg.m_searchHyperlink1, dlg.m_searchHyperlink2,
                                      dlg.m_searchHyperlink3), list(links) + [None, None, None]):
                if link:
                    control.SetLabelText(link[0])
                    control.SetURL(link[1])
                    control.Show()
                else:
                    control.Hide()

        def onWebviewNewWindow( event ):
            wx.LaunchDefaultBrowser( event.GetURL() )

        def onDestroy( event ):
            if self.searchThread:
                interrupt_thread(self.searchThread)
                self.searchThread.join( 5 )

            if self.downloadThread:
                interrupt_thread(self.downloadThread)
                self.downloadThread.join( 5 )

            event.Skip()

        for title, width in RESULT_COLUMNS:
            dlg.m_searchResultsTree.AppendColumn(title, width=width,
                                                 flags=wx.COL_RESIZABLE | wx.COL_SORTABLE)

        for title, width in (("Parameter", 170), ("Value", 320)):
            dlg.m_paramsList.AppendColumn(title, width=width)

        for title, width in (("Source", 90), ("Code / UUID", 300), ("Name", 300)):
            dlg.m_queueList.AppendColumn(title, width=width)

        # Load library name from config or use default
        default_lib_name = "EasyEDA_Lib"
        if config_manager:
            default_lib_name = config_manager.get_library_name(default_lib_name)
        dlg.m_textCtrlOutLibName.SetValue(default_lib_name);

        # One WebView per drawing, so each is scaled to its own panel instead of
        # sharing one page. wx.svg cannot replace them: it is nanosvg, which draws
        # no text, and every pin name and pad number would vanish.
        self.symbolView = None
        self.footprintView = None

        for panel, attribute in ((dlg.m_symbolPanel, "symbolView"),
                                 (dlg.m_footprintPanel, "footprintView")):
            sizer = wx.BoxSizer(wx.VERTICAL)
            view = None

            global wx_html2_available
            if wx_html2_available:
                try:
                    view = wx.html2.WebView.New(panel)
                    view.Bind(wx.html2.EVT_WEBVIEW_NEWWINDOW, onWebviewNewWindow)
                except NotImplementedError:
                    wx_html2_available = False

            if view is None:
                view = wx.StaticText(panel, style=wx.ALIGN_CENTRE_HORIZONTAL)
                view.SetLabel("Preview needs wx.html2.\n"
                              "Install python3-wxgtk-webview4.0 (Debian/Ubuntu).")

            setattr(self, attribute, view)
            sizer.Add(view, 1, wx.EXPAND)
            panel.SetSizer(sizer)
            panel.Layout()

        dlg.SetEscapeId(wx.ID_CANCEL)
        dlg.Bind(wx.EVT_WINDOW_DESTROY, onDestroy)

        dlg.m_searchResultsTree.Bind(wx.dataview.EVT_TREELIST_ITEM_ACTIVATED, onSearchItemActivated)
        dlg.m_searchResultsTree.Bind(wx.dataview.EVT_TREELIST_SELECTION_CHANGED, onSearchItemSelected)
        dlg.m_searchResultsTree.Bind(wx.dataview.EVT_TREELIST_COLUMN_SORTED, onColumnSorted)
        dlg.m_actionBtn.Bind(wx.EVT_BUTTON, onDownload)
        dlg.m_searchBtn.Bind(wx.EVT_BUTTON, onSearch)
        dlg.m_prevPageBtn.Bind(wx.EVT_BUTTON, onPrevPage)
        dlg.m_nextPageBtn.Bind(wx.EVT_BUTTON, onNextPage)
        dlg.m_textCtrlSearch.Bind(wx.EVT_TEXT_ENTER, onSearch)
        dlg.m_textCtrlFilter.Bind(wx.EVT_TEXT, onFilter)
        dlg.m_libSourceChoice.Bind(wx.EVT_CHOICE, onSearch)
        dlg.m_debug.Bind(wx.EVT_CHECKBOX, onDebugCheckbox)
        dlg.m_queueAddBtn.Bind(wx.EVT_BUTTON, onQueueAdd)
        dlg.m_queuePasteBtn.Bind(wx.EVT_BUTTON, onQueuePaste)
        dlg.m_queueRemoveBtn.Bind(wx.EVT_BUTTON, onQueueRemove)
        dlg.m_queueClearBtn.Bind(wx.EVT_BUTTON, onQueueClear)
        dlg.m_browseBtn.Bind(wx.EVT_BUTTON, onBrowse)

        # Reachable for tests: a simulated click only lands when the window really
        # holds focus, which is not true in every environment tests run in.
        self.onSearchItemSelected = onSearchItemSelected
        self.onSearch = onSearch
        self.onDownload = onDownload
        self.addRows = addRows
        self.renderRows = renderRows
        self.refreshQueue = refreshQueue

        # SOURCE_SEARCHES needs the closures above, so it is built here rather than
        # at module level: index matches m_libSourceChoice's order.
        SOURCE_SEARCHES.clear()
        SOURCE_SEARCHES.extend([
            [("JLC parts", lambda words, page: proSearchFn(None, words, page)),
             ("EasyEDA Std parts", stdSearchFn)],
            [("", lambda words, page: proSearchFn("lcsc", words, page))],
            [("", lambda words, page: proSearchFn("user", words, page))],
            [("", stdSearchFn)],
        ])

        refreshQueue()
        setLinks()
        showDrawing(self.symbolView, "", "symbol")
        showDrawing(self.footprintView, "", "footprint")

        dlg.m_textCtrlSearch.SetFocus()
        return dlg
