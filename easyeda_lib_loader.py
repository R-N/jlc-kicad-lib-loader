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

# EasyEDA answers in well under a second when it answers at all, and hangs
# indefinitely when it does not. Without a deadline one unlucky request wedges
# whichever thread made it - which used to be the UI thread.
HTTP_TIMEOUT = (10, 30)  # (connect, read), seconds

class TimeoutSession(requests.Session):
    """A session that refuses to wait forever, unless a caller insists."""

    def request(self, *args, **kwargs):
        kwargs.setdefault("timeout", HTTP_TIMEOUT)
        return super().request(*args, **kwargs)

session = TimeoutSession()
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

def imageMarkup(url, fallback=""):
    """EasyEDA's own rendering of a Std document, as an <img>.

    Not every document has one - a standalone footprint answers 404 on
    image.easyeda.com - and the pane used to be left blank, because the handler
    hid the image's parent and said nothing. When a local drawing of the same
    document is available it takes over instead; the swap happens in the page, so
    a missing picture costs no extra request.
    """
    if not url:
        return fallback

    swap = ("this.style.display='none';"
            "var alt=document.getElementById('alt');"
            "if (alt) { alt.style.display='block';"
            " if (window.rebuildLayers) window.rebuildLayers(); }"
            "else this.parentNode.style.display='none';")

    return (f'<img src="{url}" onerror="{swap}"/>'
            + (f'<div id="alt" style="display:none">{fallback}</div>' if fallback else ""))

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

    Returns ({kind: markup}, {kind: why it is missing}). "Missing" is not one
    thing: a document that could not be read is a failure worth saying out loud,
    while a document that holds no geometry is simply how EasyEDA stores some
    parts, and the two used to be reported to the user identically.
    """
    drawings, problems = {}, {}

    for kind, uuid, render in (("symbol", symbolUuid, pro_render.symbolSvg),
                               ("footprint", footprintUuid, pro_render.footprintSvg)):
        drawings[kind] = ""

        if not uuid:
            problems[kind] = f"This part has no {kind} document."
            continue

        try:
            drawings[kind] = render(fetchDataStr(session, uuid))
        except Exception as e:
            # A missing pycryptodome or an unreadable document costs the drawing,
            # nothing else.
            warning(f"Could not fetch Pro document {uuid}: {e}")
            problems[kind] = f"The {kind} document could not be read: {e}"

    return drawings, problems

# Results per request, for both APIs.
SEARCH_PAGE_SIZE = 50

# One drawing, centred and scaled to its panel, then pannable and zoomable. Each
# drawing gets its own panel, so the page holds exactly one and needs no layout of
# its own. Substituted with str.replace, not str.format: the script below is mostly
# braces, and doubling every one of them for format() is a bug farm.
DRAWING_PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8"><style>
    html, body { height: 100%; margin: 0; overflow: hidden; }
    body { font-family: sans-serif; background: #ffffff; }
    /* The viewport clips and takes the input; the canvas is what gets transformed. */
    #vp { position: absolute; top: 0; left: 0; right: 0; bottom: 0;
          overflow: hidden; cursor: grab; }
    #vp.grabbing { cursor: grabbing; }
    #cv { position: absolute; top: 0; left: 0; right: 0; bottom: 0;
          transform-origin: 0 0;
          display: flex; align-items: center; justify-content: center; }
    /* Fill the panel: max-width alone only ever shrinks, so a 220px drawing stayed
       a stamp in a 500px panel. A viewBox keeps the aspect ratio. */
    svg { width: 100%; height: 100%; }
    img { width: 100%; height: 100%; object-fit: contain; }
    .note { color: #666; font-size: 90%; text-align: center; margin: 0 12px; }
    #hint { position: absolute; right: 6px; bottom: 4px; color: #aaa;
            font-size: 10px; pointer-events: none; user-select: none; }
    /* Hover highlight. A filter rather than a stroke override, which would repaint
       every child of the group and lose the pad colours. */
    .hot { filter: drop-shadow(0 0 2px #00a3ff) drop-shadow(0 0 6px #00a3ff); }
    #tip { position: absolute; left: 0; top: 0; pointer-events: none; display: none;
           background: #263238; color: #fff; font-size: 11px; line-height: 1.45;
           padding: 4px 7px; border-radius: 3px; white-space: pre; z-index: 3;
           box-shadow: 0 1px 4px rgba(0,0,0,.35); }
    #layers { position: absolute; left: 6px; top: 6px; z-index: 2; font-size: 11px;
              background: rgba(255,255,255,.92); border: 1px solid #d0d0d0;
              border-radius: 3px; padding: 3px 6px; max-height: 60%; overflow: auto;
              user-select: none; }
    #layers.empty { display: none; }
    #layers label { display: block; white-space: nowrap; cursor: pointer; }
    #layers input { vertical-align: -1px; margin: 0 4px 0 0; }
    #layers .sw { display: inline-block; width: 8px; height: 8px; margin-right: 4px;
                  border: 1px solid #999; }
</style></head><body>
<div id="vp"><div id="cv">__BODY__</div><div id="layers" class="empty"></div><div id="tip"></div></div>
<div id="hint">scroll: zoom &middot; drag: pan &middot; double-click: reset</div>
<script>
(function () {
    var vp = document.getElementById('vp'), cv = document.getElementById('cv');
    var k = 1, x = 0, y = 0, down = false, ox = 0, oy = 0;

    function apply() {
        cv.style.transform = 'translate(' + x + 'px,' + y + 'px) scale(' + k + ')';
    }

    vp.addEventListener('wheel', function (e) {
        e.preventDefault();
        var r = vp.getBoundingClientRect();
        var mx = e.clientX - r.left, my = e.clientY - r.top;
        // Clamp first, then derive the ratio actually applied, or the pan
        // correction drifts once zoom is pinned at a stop.
        var nk = Math.min(40, Math.max(0.2, k * Math.exp(-e.deltaY * 0.0015)));
        var f = nk / k;
        x = mx - f * (mx - x);
        y = my - f * (my - y);
        k = nk;
        apply();
    }, { passive: false });

    vp.addEventListener('mousedown', function (e) {
        down = true; ox = e.clientX - x; oy = e.clientY - y;
        vp.className = 'grabbing'; e.preventDefault();
    });

    // On window, not the viewport: a drag that leaves the panel must still track
    // and must still end, or the drawing stays glued to the pointer.
    window.addEventListener('mousemove', function (e) {
        if (down) { x = e.clientX - ox; y = e.clientY - oy; apply(); }
    });

    window.addEventListener('mouseup', function () { down = false; vp.className = ''; });
    vp.addEventListener('dblclick', function () { k = 1; x = 0; y = 0; apply(); });

    // ---- hover: what is under the cursor -------------------------------------
    var tip = document.getElementById('tip'), hot = null;

    function describe(g) {
        var d = g.dataset, out = [];

        if (d.kind === 'pin') {
            out.push('Pin ' + (d.number || '?') + (d.name ? '  ' + d.name : ''));
            if (d.type && d.type !== 'Undefined') { out.push('Type: ' + d.type); }
        } else if (d.kind === 'pad') {
            out.push('Pad ' + (d.number || '?'));
            if (d.size) { out.push('Size: ' + d.size); }
            if (d.drill) { out.push('Drill: ' + d.drill); }
            if (d.layername) { out.push(d.layername); }
        }

        return out.join('\\n');
    }

    function unhover() {
        if (hot) { hot.classList.remove('hot'); hot = null; }
        tip.style.display = 'none';
    }

    vp.addEventListener('mousemove', function (e) {
        // Panning takes precedence: a tooltip chasing the cursor mid-drag is noise.
        if (down) { unhover(); return; }

        var g = e.target.closest ? e.target.closest('g[data-kind]') : null;
        var text = g ? describe(g) : '';

        if (!g || !text) { unhover(); return; }

        if (g !== hot) { unhover(); hot = g; g.classList.add('hot'); }

        tip.textContent = text;
        tip.style.display = 'block';
        // Keep the tooltip inside the panel, or it is clipped away at the edges.
        var r = vp.getBoundingClientRect(), tw = tip.offsetWidth, th = tip.offsetHeight;
        var px = e.clientX - r.left + 12, py = e.clientY - r.top + 12;
        tip.style.left = Math.max(0, Math.min(px, r.width - tw - 2)) + 'px';
        tip.style.top = Math.max(0, Math.min(py, r.height - th - 2)) + 'px';
    });

    vp.addEventListener('mouseleave', unhover);

    // ---- footprint layers: one checkbox per layer actually drawn -------------
    // Only the drawing on screen. When EasyEDA's picture is shown, our own drawing
    // of the same document sits hidden behind it as the broken-image fallback, and
    // a panel built from that would switch layers nobody can see.
    var panel = document.getElementById('layers');

    function rebuildLayers() {
        var seen = {}, order = [];
        panel.textContent = '';
        panel.classList.add('empty');
        // Visibility decides, not position in the tree: our drawing may be sitting
        // hidden behind EasyEDA's picture as the broken-image fallback, and a panel
        // built from that would switch layers nobody can see.
        Array.prototype.forEach.call(cv.querySelectorAll('g[data-layer]'), function (g) {
            if (!g.getClientRects().length) {
                return;
            }

            var id = g.dataset.layer;

            if (!seen[id]) {
                seen[id] = { name: g.dataset.layername || ('Layer ' + id), groups: [] };
                order.push(id);
            }

            seen[id].groups.push(g);
        });

        // A symbol has no layers at all, and one lone layer is not worth a panel.
        if (order.length < 2) {
            return;
        }

        panel.classList.remove('empty');
        order.forEach(function (id) {
            var entry = seen[id];
            var label = document.createElement('label');
            var box = document.createElement('input');
            box.type = 'checkbox';
            box.checked = true;
            box.addEventListener('change', function () {
                entry.groups.forEach(function (g) {
                    g.style.display = box.checked ? '' : 'none';
                });
                unhover();
            });
            var swatch = document.createElement('span');
            swatch.className = 'sw';
            // Colour the swatch from what the layer actually drew, so it matches.
            var painted = entry.groups[0].querySelector('[fill], [stroke]');
            var colour = painted ? (painted.getAttribute('fill') !== 'none'
                                    ? painted.getAttribute('fill')
                                    : painted.getAttribute('stroke')) : '';
            swatch.style.background = colour && colour !== 'none' ? colour : 'transparent';
            label.appendChild(box);
            label.appendChild(swatch);
            label.appendChild(document.createTextNode(entry.name));
            panel.appendChild(label);
        });
    }

    // The image's onerror handler reveals the local drawing and calls this. It can
    // fire while the document is still parsing, before this script exists, so the
    // panel is built again once everything has settled.
    window.rebuildLayers = rebuildLayers;
    rebuildLayers();
    window.addEventListener('load', rebuildLayers);
})();
</script></body></html>"""


def drawingPage( body ):
    """The drawing page holding one drawing. str.replace, not format: see above."""
    return DRAWING_PAGE.replace("__BODY__", body)

# Searches behind each entry of m_libSourceChoice, filled in createDialog because
# the search functions close over the dialog.
SOURCE_SEARCHES = []

# Columns of the results grid, in order. The row builders below must match.
# Symbol and Footprint are the names KiCad's choosers will show, which are neither
# the part number nor the package: one SOT-23 footprint document serves hundreds of
# parts, so seeing them before downloading is what tells you what you will get.
RESULT_COLUMNS = (("Src", 58), ("Code", 84), ("Name", 128), ("Value", 90),
                  ("Description", 150), ("Symbol", 118), ("Footprint", 150),
                  ("Package", 80), ("Class", 74), ("Type", 66), ("By", 68))

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

def valueOf(attributes):
    """The schematic value, resolved the way EasyEDA resolves it.

    A Pro device's `Name` attribute is a template naming the attribute that supplies
    the value: "={Value}" on a resistor, "={Manufacturer Part}" on an IC. Showing the
    template itself is useless, so it is substituted; a literal `Name` is used as is.
    """
    attributes = attributes or {}
    template = str(attributes.get("Name") or "").strip()
    field = re.fullmatch(r"=\{(.+)\}", template)

    if field:
        return str(attributes.get(field.group(1), "") or "")

    return template or str(attributes.get("Value", "") or "")

def proRow(entry, source=SOURCE_PUBLIC):
    """A results row for an EasyEDA Pro device, as the search API returns it."""
    attributes = entry.get("attributes") or {}
    code = entry.get("product_code") or entry.get("uuid", "")
    name = entry.get("display_title") or entry.get("title", "")
    description = attributes.get("LCSC Part Name", "")
    footprintName = (entry.get("footprint") or {}).get("display_title", "")

    searchable = " ".join(filter(None, [
        name,
        entry.get("title", ""),
        tagText(entry.get("tags")),
        searchableText(attributes),
    ]))

    return (source,
            code,
            name,
            valueOf(attributes),
            description,
            # What eeschema and pcbnew will call them, which is the footprint
            # document's own title, not the package and not the part number.
            (entry.get("symbol") or {}).get("display_title", ""),
            footprintName,
            # The footprint document title is unreadable ("SOT-223-3_L6.5-W3.4-P2.30-LS7.0-BR");
            # the supplier's package name is what a person recognises.
            attributes.get("Supplier Footprint") or footprintName,
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
    title = entry.get("title", "")
    package = params.get("package", "")

    return (SOURCE_STD,
            STD_PREFIX + entry["uuid"],
            title,
            # Std documents keep the schematic value under "name".
            params.get("name", ""),
            "",
            # A Std symbol is imported under its own title; a footprint document is
            # imported under its package, which is also the only package it names.
            title if kind == "Symbol" else "",
            package,
            package,
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

        self.entries[code] = (source, code, name, "")

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
        return [code for _, code, _, _ in self.entries.values()]

    def setAlias(self, code, alias):
        """Set a custom footprint alias for a queued part; empty clears it."""
        if code in self.entries:
            source, _, name, _ = self.entries[code]
            self.entries[code] = (source, code, name, (alias or "").strip())

    def aliases(self):
        return {code: alias for _, code, _, alias in self.entries.values() if alias}

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
from . import std_render
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

            for source, code, name, alias in self.queue.rows():
                row = dlg.m_queueList.InsertItem(dlg.m_queueList.GetItemCount(), source)
                dlg.m_queueList.SetItem(row, 1, code)
                dlg.m_queueList.SetItem(row, 2, name)
                dlg.m_queueList.SetItem(row, 3, alias)

            dlg.m_queueLabel.SetLabel(
                f"Queue: {len(self.queue)} part{'' if len(self.queue) == 1 else 's'}"
                if self.queue else
                "Download queue is empty. Double-click a result to add it.")
            dlg.m_actionBtn.Enable(bool(self.queue) and not self.downloadThread)
            dlg.m_queueRemoveBtn.Enable(bool(self.queue))
            dlg.m_queueClearBtn.Enable(bool(self.queue))
        def onQueueAlias( event ):
            item = event.GetIndex()

            if item < 0:
                return

            code = dlg.m_queueList.GetItemText(item, 1)
            current = self.queue.aliases().get(code, "")

            dialog = wx.TextEntryDialog(dlg,
                                        f"Footprint alias for {code} (searchable name, empty clears it):",
                                        "Set footprint alias", current)

            if dialog.ShowModal() == wx.ID_OK:
                self.queue.setAlias(code, dialog.GetValue())
                refreshQueue()

            dialog.Destroy()


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
                summary = loader.downloadAll(components, self.queue.aliases())
                wx.CallAfter(onDownloadFinished, summary, components)

            setResult(f"Downloading {len(components)} part{'' if len(components) == 1 else 's'}…")
            dlg.m_actionBtn.Disable()
            self.downloadThread = Thread(target = threadedFn, daemon=True)
            self.downloadThread.start()

        def onDownloadFinished( summary, requested ):
            self.downloadThread = None
            parts = [f"{summary['symbols']} symbol{'' if summary['symbols'] == 1 else 's'}",
                     f"{summary['footprints']} footprint{'' if summary['footprints'] == 1 else 's'}",
                     f"{summary['models']} model{'' if summary['models'] == 1 else 's'}"]

            if summary["skipped"]:
                parts.append(f"{summary['skipped']} without a STEP model")

            # Whatever landed leaves the queue; whatever did not stays, so a retry
            # repeats only the parts that need it instead of the whole list.
            stillFailing = set(summary.get("failedItems") or [])
            self.queue.remove([code for code in requested if code not in stillFailing])
            failed = summary["failed"] or summary["error"]

            if failed:
                kept = (f" {len(stillFailing)} part{'' if len(stillFailing) == 1 else 's'}"
                        f" left in the queue." if stillFailing else "")
                setResult(f"Finished with problems: {summary['error'] or str(summary['failed']) + ' failed'}."
                          f" Downloaded {', '.join(parts)}.{kept} See Details.")
                dlg.m_logPane.Expand()
                dlg.Layout()
            else:
                setResult(f"Downloaded {', '.join(parts)}. Restart pcbnew to use new footprints.")

            refreshQueue()

        def pageSize():
            """Results per request, as chosen in the search bar.

            Read at call time rather than captured, so changing it takes effect on the
            next search without rebuilding the search functions.
            """
            try:
                return int(dlg.m_pageSizeChoice.GetStringSelection()) or SEARCH_PAGE_SIZE
            except (ValueError, AttributeError):
                return SEARCH_PAGE_SIZE

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
                "pageSize": pageSize(),
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
                "pageSize": pageSize(),
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

            return count, math.ceil(count / pageSize())

        def uuidSearch( term ):
            """Resolve a pasted uuid against both APIs.

            Neither EasyEDA keyword search indexes uuids - not a device uuid, not a
            Std document uuid, not a model uuid - so pasting one returned nothing at
            all even for a part that exists. Both APIs answer a direct lookup, so a
            uuid is looked up instead of searched.
            """
            uuid = term.strip()

            if uuid.lower().startswith(STD_PREFIX):
                uuid = uuid[len(STD_PREFIX):]

            try:
                device = proResult(session.get(PRO_DEVICE_URL.format(uuid=uuid)).json())
                # A direct lookup does not say which facet the device came from; LCSC
                # parts are the ones carrying a supplier code.
                source = SOURCE_SYSTEM if (device.get("attributes") or {}).get(
                    "Supplier Part") else SOURCE_PUBLIC
                addRows([proRow(device, source)])

                return 1, 1
            except Exception as e:
                debug(f"{uuid} is not an EasyEDA Pro device: {e}")

            try:
                result = session.get(STD_COMPONENT_URL.format(uuid=uuid)).json()

                if result.get("success") and result.get("result"):
                    addRows([stdRow(result["result"])])

                    return 1, 1
            except Exception as e:
                debug(f"{uuid} is not an EasyEDA Std component: {e}")

            # A 3D model file uuid resolves on neither: it is not a part at all, and
            # is downloaded with whichever part references it.
            try:
                if session.head(MODEL_FILE_URL.format(uuid=uuid)).status_code == 200:
                    warning(f"{uuid} is a 3D model file, not a part. Search for the part"
                            f" that uses it (by name or LCSC code); its model is"
                            f" downloaded automatically.")

                    return 0, 1
            except Exception as e:
                debug(f"{uuid} is not a model file either: {e}")

            warning(f"Nothing found for {uuid}. Codes and names are searched; a uuid is"
                    f" looked up directly, and this one is neither a Pro device nor a"
                    f" Std document.")

            return 0, 1

        def searchWorker(sourceId, words, page):
            setStatus("Searching…")
            wx.CallAfter(dlg.m_prevPageBtn.Disable)
            wx.CallAfter(dlg.m_nextPageBtn.Disable)

            try:
                counts = []
                totalPages = 1

                # A uuid is not searchable text; it is looked up on both APIs instead.
                searches = SOURCE_SEARCHES[sourceId]

                if re.fullmatch(r"(?:std:)?[0-9a-fA-F]{32}", words.strip()):
                    searches = [("", lambda term, page: uuidSearch(term))]

                # "All Sources" means both APIs, not just Pro's three facets.
                for label, search in searches:
                    try:
                        count, pages = search(words, page)
                        totalPages = max(totalPages, pages)
                        # "1 part", "2 parts", "1 JLC part" - the label names the
                        # source, the noun is pluralised against the count.
                        counts.append(f"{count} {label + ' ' if label else ''}"
                                      f"part{'' if count == 1 else 's'}")
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

        def showDrawing( view, markup, caption, problem="" ):
            """Put one drawing in its own panel, or say why it is empty."""
            if not wx_html2_available or not isinstance(view, wx.html2.WebView):
                # Degraded pane: the static text already explains itself.
                return

            if markup:
                view.SetPage(drawingPage(markup), "")
            else:
                view.SetPage(drawingPage(
                    f'<p class="note">No {caption} drawing available.<br/>'
                    # The reason can be an API message: it goes in as text, not markup.
                    + pro_render._escape(problem or
                                         "The EasyEDA document holds no geometry for it.")
                    + '</p>'), "")

        # One selection's worth of network work, cached: a part is asked for once per
        # session, so arrow-keying up and down a page of results stops re-fetching.
        previewCache = {}
        previewSeq = [0]
        LOADING_NOTE = '<p class="note">Loading\u2026</p>'

        def fetchPreview( itemCode ):
            """Everything one preview needs. Runs on a worker thread, never raises.

            A failed fetch still has to leave the panes and the parameter table in a
            defined state, so failures are logged and returned as empty markup.
            """
            # "has" is what the part owns, which is not the same as what could be
            # drawn: a document that exists but fails to render still means the part
            # has one, and the tab must not run away from it.
            # "local*" is our own drawing of the document, kept beside whatever the
            # pane shows by default. For Std that default is EasyEDA's picture, so the
            # two are different and the Render button switches between them.
            data = {"title": "", "attributes": {}, "links": [], "symbol": "", "footprint": "",
                    "localSymbol": "", "localFootprint": "",
                    "hasSymbol": False, "hasFootprint": False, "problems": {}}

            if itemCode.startswith(STD_PREFIX):
                stdUuid = itemCode[len(STD_PREFIX):]
                data["links"] = [("Open in EasyEDA Std",
                                  f"https://easyeda.com/component/{stdUuid}")]

                try:
                    comp_info = session.get(STD_COMPONENT_URL.format(uuid=stdUuid))
                    comp_info.raise_for_status()
                    debug("std component info: " + json.dumps(comp_info.json(), indent=4))
                    result = comp_info.json()["result"]

                    head = (result.get("dataStr") or {}).get("head") or {}
                    attributes = dict(head.get("c_para") or {})
                    data["title"] = result.get("title", "")

                    if str(head.get("docType")) == "4":
                        # A standalone footprint document *is* the footprint - it has no
                        # symbol whatsoever. Drawing it on the Symbol tab promised a
                        # symbol the library will not contain.
                        data["hasFootprint"] = True
                        data["localFootprint"] = std_render.footprintSvg(result.get("dataStr"))
                        data["footprint"] = imageMarkup(thumbUrl(result, stdUuid),
                                                        data["localFootprint"])
                        data["problems"]["symbol"] = "This document is a footprint on its own."
                        kicadNames = {"Footprint in KiCad": attributes.get("package")
                                      or result.get("title", "")}
                    else:
                        data["hasSymbol"] = True
                        data["localSymbol"] = std_render.symbolSvg(result.get("dataStr"))
                        data["symbol"] = imageMarkup(thumbUrl(result, stdUuid),
                                                     data["localSymbol"])
                        kicadNames = {"Symbol in KiCad": attributes.get("spiceSymbolName")
                                      or result.get("title", "")}
                        named = attributes.get("package")
                        # A Std symbol names its footprint in `c_para.package` and the
                        # document only sometimes comes with it. The part still claims
                        # a footprint, so the Footprint tab stays worth being on - the
                        # pane says what is missing instead of the tab running away.
                        data["hasFootprint"] = bool(named)
                        data["problems"]["footprint"] = (
                            f"This symbol references footprint \u201c{named}\u201d,"
                            " which EasyEDA did not send with it."
                            " It may not be published as a part of its own."
                            if named else "This symbol has no footprint attached.")

                        if result.get("packageDetail"):
                            package = result["packageDetail"]
                            data["hasFootprint"] = True
                            data["problems"].pop("footprint", None)
                            attributes["Footprint"] = package.get("title", "")
                            kicadNames["Footprint in KiCad"] = (
                                (package.get("dataStr") or {}).get("head", {})
                                .get("c_para", {}).get("package") or package.get("title", ""))
                            data["localFootprint"] = std_render.footprintSvg(package.get("dataStr"))
                            data["footprint"] = imageMarkup(thumbUrl(package, package.get("uuid")),
                                                            data["localFootprint"])

                    data["attributes"] = {**kicadNames, **attributes}
                except Exception as e:
                    traceback.print_exc()
                    warning(f"Failed to load component info for {stdUuid}: {e}")

                return data

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
                data["title"] = device.get('display_title') or device.get('title', '')
                code = attributes.get('Supplier Part', '')

                # First, because they are what a person needs and cannot guess: KiCad
                # names a symbol after its symbol document and a footprint after its
                # footprint document, and neither is the part number. One SOT-23
                # footprint document is shared by hundreds of parts, so searching
                # pcbnew for "AO3401A" finds nothing.
                attributes = {"Symbol in KiCad": (device.get("symbol") or {}).get("display_title", ""),
                              "Footprint in KiCad": (device.get("footprint") or {}).get("display_title", ""),
                              **attributes}
                data["attributes"] = attributes
                data["hasSymbol"] = bool(attributes.get('Symbol'))
                data["hasFootprint"] = bool(attributes.get('Footprint'))

                if attributes.get('Symbol') or attributes.get('Footprint'):
                    # https://pro.easyeda.com/editor#tab=*!{sym_uuid}(device){dev_uuid}|!{fp_uuid}(device){dev_uuid}
                    tabList = [f"!{attributes[key]}(device){device['uuid']}"
                               for key in ("Symbol", "Footprint") if attributes.get(key)]
                    data["links"].append(("Open in EasyEDA Pro",
                                          f"https://pro.easyeda.com/editor#tab=*{'|'.join(tabList)}"))

                if re.fullmatch(r"C\d+", code or ""):
                    data["links"].append(("JLCPCB", f"https://jlcpcb.com/partdetail/{code}"))
                    data["links"].append(("LCSC", f"https://www.lcsc.com/product-detail/{code}.html"))

                # Last: the drawings are a bonus, the links above must survive their
                # failure. Our own render comes first even when EasyEDA has one: only
                # markup we generate carries the pin/pad/layer metadata the preview
                # hovers and toggles on, and it is the geometry the importer produces.
                drawings, problems = proDrawings(
                    attributes.get('Symbol'), attributes.get('Footprint'))
                data["symbol"], data["footprint"] = drawings["symbol"], drawings["footprint"]
                data["localSymbol"], data["localFootprint"] = data["symbol"], data["footprint"]
                data["problems"] = problems

                if not data["symbol"] and not data["footprint"]:
                    # Nothing drawable in the documents: fall back to the flat SVG
                    # EasyEDA renders for LCSC-coded parts.
                    data["symbol"], data["footprint"] = productSvgs(code)

                    for kind in ("symbol", "footprint"):
                        if data[kind]:
                            data["problems"].pop(kind, None)
            except Exception as e:
                traceback.print_exc()
                warning(f"Failed to load device info for {itemCode}: {e}")

            return data

        # The notebook's page order, so the rule below can index it.
        SYMBOL_PAGE, FOOTPRINT_PAGE = 0, 1

        # Sticky across selections: someone who wants drawings they can hover wants
        # them for the next part too, not once.
        renderLocal = [False]

        def paneMarkup( data ):
            """(symbol, footprint) markup for the mode the user is in."""
            if not renderLocal[0]:
                return data["symbol"], data["footprint"]

            return (data["localSymbol"] or data["symbol"],
                    data["localFootprint"] or data["footprint"])

        def refreshRenderButton( data ):
            """The button offers the drawing that is not currently on screen."""
            local = bool(data["localSymbol"] or data["localFootprint"])
            # EasyEDA's own picture is only worth offering back when it differs from
            # our drawing, which is exactly the Std case.
            picture = (data["symbol"] != data["localSymbol"]
                       or data["footprint"] != data["localFootprint"])
            dlg.m_renderBtn.SetLabel("Show EasyEDA image" if renderLocal[0]
                                     else "Render locally")
            dlg.m_renderBtn.Enable(local and picture)
            dlg.m_drawingsPanel.Layout()

        def applyPreview( itemCode, data ):
            """Put one fetched preview on screen."""
            dlg.m_partTitle.SetLabel(data["title"] or itemCode)
            setLinks(*data["links"])
            setParams(data["attributes"])
            symbol, footprint = paneMarkup(data)
            showDrawing(self.symbolView, symbol, "symbol",
                        data["problems"].get("symbol", ""))
            showDrawing(self.footprintView, footprint, "footprint",
                        data["problems"].get("footprint", ""))
            refreshRenderButton(data)
            self.previewData = data
            self.previewCode = itemCode

            # Leave the chosen tab alone unless this part cannot fill it at all. Keyed
            # on the documents the part *owns*, never on what was drawn: a footprint
            # that failed to render is still a footprint, and browsing footprints used
            # to get yanked to the Symbol tab by every part whose footprint 404'd.
            owns = {SYMBOL_PAGE: data["hasSymbol"], FOOTPRINT_PAGE: data["hasFootprint"]}
            here = dlg.m_previewNotebook.GetSelection()
            other = FOOTPRINT_PAGE if here == SYMBOL_PAGE else SYMBOL_PAGE

            if not owns.get(here, True) and owns.get(other):
                dlg.m_previewNotebook.SetSelection(other)

            dlg.m_detailsPanel.Layout()

        def onRenderToggle( event ):
            """Switch between EasyEDA's picture and our own drawing of the document.

            Nothing is fetched: both were built when the part was selected, so this
            is a repaint. EasyEDA has no drawing for a Pro document, and ours has the
            pin, pad and layer metadata theirs cannot carry.
            """
            renderLocal[0] = not renderLocal[0]
            data = getattr(self, "previewData", None)

            if data:
                applyPreview(getattr(self, "previewCode", ""), data)

        def onSearchItemSelected( event ):
            itemCode = dlg.m_searchResultsTree.GetItemText(event.GetItem(), 1)
            previewSeq[0] += 1
            seq = previewSeq[0]

            if itemCode in previewCache:
                applyPreview(itemCode, previewCache[itemCode])
                return

            def finish( data ):
                previewCache[itemCode] = data

                while len(previewCache) > 64:
                    previewCache.pop(next(iter(previewCache)))

                # A wx window is falsy once its C++ object is gone: the dialog can be
                # closed while a fetch is still in flight, and painting into a dead
                # window is a segfault, not an exception.
                if not dlg:
                    return

                # A slower part selected earlier must not overwrite the current one,
                # but its result is worth keeping: selecting it again is then free.
                if seq == previewSeq[0]:
                    applyPreview(itemCode, data)

            def worker():
                data = fetchPreview(itemCode)
                wx.CallAfter(finish, data)

            # Up to four EasyEDA round trips: fast on a good day, unbounded on a bad
            # one. On the UI thread that froze all of pcbnew, mid-click, for as long
            # as EasyEDA felt like taking.
            dlg.m_partTitle.SetLabel(f"{itemCode} \u2026")
            setLinks()
            setParams({})
            showDrawing(self.symbolView, LOADING_NOTE, "symbol")
            showDrawing(self.footprintView, LOADING_NOTE, "footprint")
            dlg.m_detailsPanel.Layout()
            Thread(target=worker, daemon=True).start()

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

        for title, width in RESULT_COLUMNS:
            dlg.m_searchResultsTree.AppendColumn(title, width=width,
                                                 flags=wx.COL_RESIZABLE | wx.COL_SORTABLE)

        for title, width in (("Parameter", 170), ("Value", 320)):
            dlg.m_paramsList.AppendColumn(title, width=width)

        for title, width in (("Source", 90), ("Code / UUID", 300), ("Name", 300), ("Alias", 200)):
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
        # A new page size changes what a page even is, so re-ask from page one.
        dlg.m_pageSizeChoice.Bind(wx.EVT_CHOICE, onSearch)
        dlg.m_debug.Bind(wx.EVT_CHECKBOX, onDebugCheckbox)
        dlg.m_queueAddBtn.Bind(wx.EVT_BUTTON, onQueueAdd)
        dlg.m_queuePasteBtn.Bind(wx.EVT_BUTTON, onQueuePaste)
        dlg.m_queueRemoveBtn.Bind(wx.EVT_BUTTON, onQueueRemove)
        dlg.m_queueClearBtn.Bind(wx.EVT_BUTTON, onQueueClear)
        dlg.m_queueList.Bind(wx.EVT_LIST_ITEM_ACTIVATED, onQueueAlias)
        dlg.m_browseBtn.Bind(wx.EVT_BUTTON, onBrowse)
        dlg.m_renderBtn.Bind(wx.EVT_BUTTON, onRenderToggle)

        # Reachable for tests: a simulated click only lands when the window really
        # holds focus, which is not true in every environment tests run in.
        self.onSearchItemSelected = onSearchItemSelected
        self.onRenderToggle = onRenderToggle
        self.onSearch = onSearch
        self.onDownload = onDownload
        self.onDownloadFinished = onDownloadFinished
        self.addRows = addRows
        self.renderRows = renderRows
        self.refreshQueue = refreshQueue

        # SOURCE_SEARCHES needs the closures above, so it is built here rather than
        # at module level: index matches m_libSourceChoice's order.
        SOURCE_SEARCHES.clear()
        SOURCE_SEARCHES.extend([
            [("JLC", lambda words, page: proSearchFn(None, words, page)),
             ("EasyEDA Std", stdSearchFn)],
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
