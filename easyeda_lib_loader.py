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
    def __init__(self, ctrl: wx.TextCtrl):
        logging.Handler.__init__(self)
        self.ctrl = ctrl

    def emit(self, record):
        s = self.format(record) + '\n'
        wx.CallAfter(self.ctrl.AppendText, s)

class EasyEDALibLoaderPlugin(ActionPlugin):
    dialog: Optional[EasyEdaLibLoaderDialog] = None
    downloadThread: Optional[Thread] = None
    searchThread: Optional[Thread] = None
    searchPage = 1
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

        handler = WxTextCtrlHandler(dlg.m_log)
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

        def progressHandler( current, total ):
            wx.CallAfter(dlg.m_progress.SetRange, total)
            wx.CallAfter(dlg.m_progress.SetValue, current)

        def onDebugCheckbox( event: wx.CommandEvent ):
            logging.getLogger().setLevel( logging.DEBUG if event.IsChecked() else logging.INFO )

        def onDownload( event ):
            dlg.m_log.Clear()

            if not dlg.m_textCtrlParts.GetValue().strip():
                for sel in dlg.m_searchResultsTree.GetSelections():
                    dlg.m_textCtrlParts.AppendText(dlg.m_searchResultsTree.GetItemText(sel) + "\n")

            components = dlg.m_textCtrlParts.GetValue().splitlines()

            if not components:
                error( "No parts to download." )
                return

            kiprjmod = os.getenv("KIPRJMOD") or ""

            if not kiprjmod:
                error( "KIPRJMOD is not set properly." )
                return
            
            lib_field = dlg.m_textCtrlOutLibName.GetValue()
            
            if os.path.isabs(lib_field):
                target_path = lib_field
            else:
                target_path = os.path.join(kiprjmod, lib_field)

            target_name = os.path.basename(target_path);
            
            # Save library name to config
            if config_manager:
                config_manager.set_library_name(lib_field)
            
            # Check if library exists in tables and prompt to add if not
            if library_manager:
                proComponents, stdComponents = splitSources(components)
                sources = (["pro"] if proComponents else []) + (["std"] if stdComponents else [])
                library_manager.prompt_add_library(dlg, target_name, target_path, sources)

            def threadedFn():
                loader = ComponentLoader(kiprjmod=kiprjmod, target_path=target_path, target_name=target_name, progress=progressHandler, session=session)
                loader.downloadAll(components)

                wx.CallAfter(dlg.m_actionBtn.Enable)

            dlg.m_actionBtn.Disable()
            self.downloadThread = Thread(target = threadedFn, daemon=True)
            self.downloadThread.start()

        def stdSearchFn(words, page):
            def setStatus( status ):
                wx.CallAfter(dlg.m_searchStatus.SetLabel, status)
                wx.CallAfter(dlg.m_statusPanel.Layout)

            def appendItem( data ):
                treeItem = dlg.m_searchResultsTree.AppendItem( dlg.m_searchResultsTree.GetRootItem(), data[0] )

                for i in range(1, len(data)):
                    dlg.m_searchResultsTree.SetItemText(treeItem, i, data[i]);

            setStatus("Searching...")
            wx.CallAfter(dlg.m_searchResultsTree.DeleteAllItems)
            wx.CallAfter(dlg.m_prevPageBtn.Disable)
            wx.CallAfter(dlg.m_nextPageBtn.Disable)

            try:
                if isUuid(words.strip()):
                    # Direct uuid search: fetch the single component and show it as one result
                    resp = session.get(STD_COMPONENT_URL.format(uuid=words.strip()))
                    resp.raise_for_status()
                    found = resp.json()

                    debug(json.dumps(found, indent=4))

                    if not found.get("success") or not found.get("result"):
                        raise Exception(f"Unable to fetch component: {found}")

                    result = {"page": 1, "totalPage": 1, "facets": {"user": 1},
                              "lists": {"user": [found["result"]]}}
                else:
                    resp = session.post( STD_SEARCH_URL, data={
                        "type": 3,
                        "uid": "user",
                        "wd": words,
                        "page": page,
                        "pageSize": 50,
                        "returnListStyle": "classifyarr"
                    } )
                    resp.raise_for_status()
                    found = resp.json()

                    debug(json.dumps(found, indent=4))

                    if not found.get("success") or not found.get("result"):
                        raise Exception(f"Unable to search: {found}")

                    result = found["result"]

                for entry in result["lists"].get("user", []):
                    c_para = (entry.get("dataStr") or {}).get("head", {}).get("c_para") or {}

                    wx.CallAfter(appendItem, [
                        STD_PREFIX + entry["uuid"],
                        entry.get("title", ""),
                        c_para.get("Manufacturer") or c_para.get("BOM_Manufacturer", ""),
                        c_para.get("name", ""),
                        c_para.get("package", ""),
                        contributorOf(entry)
                    ])

                curPage = int(result["page"])
                totalPages = int(result["totalPage"])

                if(curPage > 1):
                    wx.CallAfter(dlg.m_prevPageBtn.Enable)

                if(curPage < totalPages):
                    wx.CallAfter(dlg.m_nextPageBtn.Enable)

                setStatus(f"{result['facets'].get('user', 0)} parts.")
                wx.CallAfter(dlg.m_searchPage.SetLabel, f"Page {curPage}/{totalPages}")
                wx.CallAfter(dlg.m_statusPanel.Layout)

            except KeyboardInterrupt:
                print("KeyboardInterrupt.")
            except Exception as e:
                traceback.print_exc()
                setStatus(f"Failed to search parts: {e}")

            finally:
                self.searchThread = None

        def searchFn(facet, words, page):
            def setStatus( status ):
                wx.CallAfter(dlg.m_searchStatus.SetLabel, status)
                wx.CallAfter(dlg.m_statusPanel.Layout)

            def setPageText( pageText ):
                wx.CallAfter(dlg.m_searchPage.SetLabel, pageText)
                wx.CallAfter(dlg.m_statusPanel.Layout)

            def clearItems():
                wx.CallAfter(dlg.m_searchResultsTree.DeleteAllItems)

            def appendItem( data ):
                treeItem = dlg.m_searchResultsTree.AppendItem( dlg.m_searchResultsTree.GetRootItem(), data[0] )

                for i in range(1, len(data)):
                    dlg.m_searchResultsTree.SetItemText(treeItem, i, data[i]);

            def addItem( item ):
                wx.CallAfter(appendItem, item)


            setStatus("Searching...")
            clearItems()

            wx.CallAfter(dlg.m_prevPageBtn.Disable)
            wx.CallAfter(dlg.m_nextPageBtn.Disable)

            try:
                pageSize = 50

                reqData={
                    "page": page,
                    "pageSize": pageSize,
                    "wd": words,
                    "returnListStyle": "classifyarr"
                }

                if facet:
                    reqData |= {
                        "uid": facet,
                        "path": facet,
                    }

                resp = session.post( "https://pro.easyeda.com/api/v2/devices/search", data=reqData )
                resp.raise_for_status()
                found = resp.json()

                debug(json.dumps(found, indent=4))

                if not found.get("success") or not found.get("result"):
                    raise Exception(f"Unable to search: {found}")

                totalDevices = sum(found["result"]["facets"].values())

                for facet in found["result"]["lists"].values():
                    for entry in facet:
                        addItem([
                            entry.get("product_code", entry["uuid"]),
                            entry["display_title"],
                            entry["attributes"].get("Manufacturer", ""),
                            entry["symbol"]["display_title"] if entry.get("symbol") else "",
                            entry["footprint"]["display_title"] if entry.get("footprint") else "",
                            contributorOf(entry)
                        ])

                curPage = int(found['result']['page'])
                totalPages = math.ceil(totalDevices / pageSize)

                if(curPage > 1):
                    wx.CallAfter(dlg.m_prevPageBtn.Enable)

                if(curPage < totalPages):
                    wx.CallAfter(dlg.m_nextPageBtn.Enable)

                setStatus(f"{totalDevices} parts.")
                setPageText(f"Page {curPage}/{totalPages}")

            except KeyboardInterrupt:
                print("KeyboardInterrupt.")
            except Exception as e:
                traceback.print_exc()
                setStatus(f"Failed to search parts: {e}")

            finally:
                self.searchThread = None

        def loadSearchPage( facetId, words, page ):
            if self.searchThread:
                interrupt_thread(self.searchThread)
                self.searchThread.join()

            # Choice order: All Sources, JLC System, JLC Public, EasyEDA Std Public
            if facetId == 3:
                self.searchThread = Thread(target = stdSearchFn,
                                     daemon=True,
                                     args=(words, page))
            else:
                self.searchThread = Thread(target = searchFn, 
                                     daemon=True, 
                                     args=([None, "lcsc", "user"][facetId], words, page))

            self.searchThread.start()

        def onSearch( event ):
            self.searchPage = 1
            loadSearchPage(dlg.m_libSourceChoice.GetSelection(), dlg.m_textCtrlSearch.GetValue(), self.searchPage)

        def onNextPage( event ):
            self.searchPage += 1
            loadSearchPage(dlg.m_libSourceChoice.GetSelection(), dlg.m_textCtrlSearch.GetValue(), self.searchPage)
        
        def onPrevPage( event ):
            self.searchPage -= 1
            loadSearchPage(dlg.m_libSourceChoice.GetSelection(), dlg.m_textCtrlSearch.GetValue(), self.searchPage)

        def onSearchItemActivated( event ):
            if dlg.m_textCtrlParts.GetValue() and not dlg.m_textCtrlParts.GetValue().endswith("\n"):
                dlg.m_textCtrlParts.AppendText("\n")

            dlg.m_textCtrlParts.AppendText(dlg.m_searchResultsTree.GetItemText(event.GetItem()) + "\n")

        def onSearchItemSelected( event ):
            itemCode = dlg.m_searchResultsTree.GetItemText(event.GetItem())
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

                    if result.get("packageDetail"):
                        package = result["packageDetail"]
                        attributes["Footprint"] = package.get("title", "")
                        preview_footprint = imageMarkup(thumbUrl(package, package.get("uuid")))
                except Exception as e:
                    traceback.print_exc()
                    warning(f"Failed to load component info for {stdUuid}: {e}")

                dlg.m_searchHyperlink1.SetLabelText( "Open in EasyEDA Std" )
                dlg.m_searchHyperlink1.SetURL( f"https://easyeda.com/component/{stdUuid}" )
                dlg.m_searchHyperlink1.Show()

                dlg.m_searchHyperlink2.Hide()
                dlg.m_searchHyperlink3.Hide()
            elif itemCode.startswith("C"):
                dlg.m_searchHyperlink1.SetLabelText( f"{itemCode} Preview" )
                dlg.m_searchHyperlink1.SetURL( f"https://jlcpcb.com/user-center/lcsvg/svg.html?code={itemCode}" )
                dlg.m_searchHyperlink1.Show()

                dlg.m_searchHyperlink2.SetLabelText( f"JLCPCB" )
                dlg.m_searchHyperlink2.SetURL( f"https://jlcpcb.com/partdetail/{itemCode}" )
                dlg.m_searchHyperlink2.Show()

                dlg.m_searchHyperlink3.SetLabelText( f"LCSC" )
                dlg.m_searchHyperlink3.SetURL( f"https://www.lcsc.com/product-detail/{itemCode}.html" )
                dlg.m_searchHyperlink3.Show()
            else:
                easyedaLink = None

                try:
                    dev_info = session.get(f"https://pro.easyeda.com/api/devices/{itemCode}")
                    dev_info.raise_for_status()
                    debug("device info: " + json.dumps(dev_info.json(), indent=4))
                    device = dev_info.json()["result"]
                    attributes = device['attributes']
                    preview_title = device.get('display_title') or device.get('title', '')

                    if attributes.get('Symbol') or attributes.get('Footprint'):
                        # https://pro.easyeda.com/editor#tab=*!{sym_uuid}(device){dev_uuid}|!{fp_uuid}(device){dev_uuid}
                        tabList = []

                        if attributes.get('Symbol'):
                            tabList.append(f"!{attributes['Symbol']}(device){itemCode}")

                        if attributes.get('Footprint'):
                            tabList.append(f"!{attributes['Footprint']}(device){itemCode}")

                        easyedaLink = f"https://pro.easyeda.com/editor#tab=*{'|'.join(tabList)}"

                    # Last: the drawings are a bonus, the link above must survive their failure
                    preview_symbol, preview_footprint = productSvgs(attributes.get('Supplier Part', ''))
                except Exception as e:
                    traceback.print_exc()
                    warning(f"Failed to load device info for {itemCode}: {e}")

                if easyedaLink:
                    dlg.m_searchHyperlink1.SetLabelText( f"Open in EasyEDA Pro" )
                    dlg.m_searchHyperlink1.SetURL( easyedaLink )
                    dlg.m_searchHyperlink1.Show()
                else:
                    dlg.m_searchHyperlink1.Hide()

                dlg.m_searchHyperlink2.Hide()
                dlg.m_searchHyperlink3.Hide()

            dlg.m_statusPanel.Layout()

            global wx_html2_available
            if wx_html2_available:
                self.webView.Hide()

                if itemCode.startswith("C"):
                    self.webView.LoadURL( f"https://jlcpcb.com/user-center/lcsvg/svg.html?code={itemCode}" )
                    self.webView.SetZoomFactor(0.8)
                else:
                    table_rows = ''.join(
                        f"""<tr>
                            <td><b>{key}</b></td>
                            <td>
                            {value if not (isinstance(value, str) and value.startswith(('http://', 'https://'))) else f'<a href="{value}" target="_blank">{value}</a>'}
                            </td>
                        </tr>"""
                        for key, value in attributes.items()
                    )
                    
                    style = """
                        body {
                            font-family: sans-serif;
                        }
                        table {
                            border:1px solid #CCC;
                            border-collapse:collapse;
                        }
                        td {
                            border:1px solid #CCC;
                            padding: 2px;
                        }
                        figure {
                            display: inline-block;
                            margin: 0 12px 4px 0;
                            text-align: center;
                        }
                        figure img, figure svg {
                            max-width: 220px;
                            max-height: 220px;
                            height: auto;
                        }
                        figcaption {
                            color: #666;
                            font-size: 90%;
                        }
                        .note {
                            color: #666;
                            margin: 0 0 6px 0;
                        }
                    """
                    heading = preview_title or f"Device UUID: {itemCode}"

                    def figure( label, drawing ):
                        return f'<figure>{drawing}<figcaption>{label}</figcaption></figure>' if drawing else ""

                    image_html = figure("Symbol", preview_symbol) + figure("Footprint", preview_footprint)

                    if not image_html:
                        # Pro devices drawn from scratch have no LCSC code, and EasyEDA
                        # publishes no rendering of their documents. Say so, rather than
                        # leaving a blank space that looks like a failure.
                        image_html = ('<p class="note">No drawing published for this part.'
                                      ' EasyEDA only renders parts that carry an LCSC part number;'
                                      ' open it in the editor, or import it and view it in KiCad.</p>')

                    html_content = f"""
                    <html>
                    <head>
                        <style>
                        {style}
                        </style>
                    </head>
                    <body>
                        {image_html}
                        <p><b>{heading}</b></p>
                        <table>
                            {table_rows}
                        </table>
                    </body>
                    </html>
                    """
                    self.webView.SetPage(html_content, "")
                    self.webView.SetZoomFactor(1.0)

        def onWebviewLoaded( event ):
            self.webView.Show()

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

        dlg.m_searchResultsTree.AppendColumn("Code/UUID", width=wx.COL_WIDTH_AUTOSIZE, flags=wx.COL_RESIZABLE | wx.COL_SORTABLE )
        dlg.m_searchResultsTree.AppendColumn("Name", width=wx.COL_WIDTH_AUTOSIZE, flags=wx.COL_RESIZABLE | wx.COL_SORTABLE)
        dlg.m_searchResultsTree.AppendColumn("Manufacturer", width=wx.COL_WIDTH_AUTOSIZE, flags=wx.COL_RESIZABLE | wx.COL_SORTABLE)
        dlg.m_searchResultsTree.AppendColumn("Symbol", width=wx.COL_WIDTH_AUTOSIZE, flags=wx.COL_RESIZABLE | wx.COL_SORTABLE)
        dlg.m_searchResultsTree.AppendColumn("Footprint", width=wx.COL_WIDTH_AUTOSIZE, flags=wx.COL_RESIZABLE | wx.COL_SORTABLE)
        dlg.m_searchResultsTree.AppendColumn("Contributor", width=wx.COL_WIDTH_AUTOSIZE, flags=wx.COL_RESIZABLE | wx.COL_SORTABLE)

        # Load library name from config or use default
        default_lib_name = "EasyEDA_Lib"
        if config_manager:
            default_lib_name = config_manager.get_library_name(default_lib_name)
        dlg.m_textCtrlOutLibName.SetValue(default_lib_name);

        global wx_html2_available
        if wx_html2_available:
            try:
                self.webView = wx.html2.WebView.New(dlg.m_webViewPanel)
                self.webView.Bind(wx.html2.EVT_WEBVIEW_LOADED, onWebviewLoaded)
                self.webView.Bind(wx.html2.EVT_WEBVIEW_NEWWINDOW, onWebviewNewWindow)
            except NotImplementedError as err:
                self.webView = wx.StaticText(dlg.m_webViewPanel, style=wx.ALIGN_CENTER_VERTICAL|wx.ALIGN_CENTRE_HORIZONTAL)
                self.webView.SetLabel("Preview is not supported in this wxPython environment.")
                dlg.m_webViewPanel.SetMinSize( wx.Size(20, 20) )
                wx_html2_available = False
        else:
            self.webView = wx.StaticText(dlg.m_webViewPanel, style=wx.ALIGN_CENTER_VERTICAL|wx.ALIGN_CENTRE_HORIZONTAL)
            self.webView.SetLabel("wx.html2 is not available. Install python3-wxgtk-webview4.0 (Debian/Ubuntu)")
            dlg.m_webViewPanel.SetMinSize( wx.Size(20, 20) )

        dlg.m_webViewPanel.GetSizer().Add(self.webView, 1, wx.EXPAND)
        dlg.m_webViewPanel.Layout()

        dlg.SetEscapeId(wx.ID_CANCEL)
        dlg.Bind(wx.EVT_WINDOW_DESTROY, onDestroy)
        
        dlg.m_searchResultsTree.Bind(wx.dataview.EVT_TREELIST_ITEM_ACTIVATED, onSearchItemActivated)
        dlg.m_searchResultsTree.Bind(wx.dataview.EVT_TREELIST_SELECTION_CHANGED, onSearchItemSelected)
        dlg.m_actionBtn.Bind(wx.EVT_BUTTON, onDownload)
        dlg.m_searchBtn.Bind(wx.EVT_BUTTON, onSearch)
        dlg.m_prevPageBtn.Bind(wx.EVT_BUTTON, onPrevPage)
        dlg.m_nextPageBtn.Bind(wx.EVT_BUTTON, onNextPage)
        dlg.m_textCtrlSearch.Bind(wx.EVT_TEXT_ENTER, onSearch)
        dlg.m_libSourceChoice.Bind(wx.EVT_CHOICE, onSearch)
        dlg.m_debug.Bind(wx.EVT_CHECKBOX, onDebugCheckbox)

        dlg.m_textCtrlSearch.SetFocus()
        return dlg
