# -*- coding: utf-8 -*-

###########################################################################
## Python code generated with wxFormBuilder (version 4.2.1-0-g80c4cb6-dirty)
## http://www.wxformbuilder.org/
##
## PLEASE DO *NOT* EDIT THIS FILE!
###########################################################################

import wx
import wx.xrc
import wx.dataview
import wx.adv

###########################################################################
## Class EasyEdaLibLoaderDialog
###########################################################################

class EasyEdaLibLoaderDialog ( wx.Dialog ):

	def __init__( self, parent ):
		wx.Dialog.__init__ ( self, parent, id = wx.ID_ANY, title = u"JLCPCB/LCSC Library Loader. Unofficial, use at your own risk.", pos = wx.DefaultPosition, size = wx.Size( 1180,820 ), style = wx.DEFAULT_DIALOG_STYLE|wx.RESIZE_BORDER )

		self.SetSizeHints( wx.Size( 900,640 ), wx.DefaultSize )

		bSizerMain = wx.BoxSizer( wx.VERTICAL )

		bSizerSearchBar = wx.BoxSizer( wx.HORIZONTAL )

		m_libSourceChoiceChoices = [ u"All Sources", u"JLC System", u"JLC Public", u"EasyEDA Std" ]
		self.m_libSourceChoice = wx.Choice( self, wx.ID_ANY, wx.DefaultPosition, wx.DefaultSize, m_libSourceChoiceChoices, 0 )
		self.m_libSourceChoice.SetSelection( 0 )
		self.m_libSourceChoice.SetToolTip( u"Which library to search" )

		bSizerSearchBar.Add( self.m_libSourceChoice, 0, wx.ALIGN_CENTER_VERTICAL|wx.ALL, 5 )

		self.m_textCtrlSearch = wx.TextCtrl( self, wx.ID_ANY, wx.EmptyString, wx.DefaultPosition, wx.DefaultSize, wx.TE_PROCESS_ENTER )
		self.m_textCtrlSearch.SetToolTip( u"Keyword, LCSC code or UUID. Enter searches." )

		bSizerSearchBar.Add( self.m_textCtrlSearch, 3, wx.ALIGN_CENTER_VERTICAL|wx.BOTTOM|wx.TOP, 5 )

		self.m_searchBtn = wx.Button( self, wx.ID_ANY, u"Find", wx.DefaultPosition, wx.DefaultSize, 0 )

		self.m_searchBtn.SetDefault()
		bSizerSearchBar.Add( self.m_searchBtn, 0, wx.ALIGN_CENTER_VERTICAL|wx.ALL, 5 )

		self.m_filterLabel = wx.StaticText( self, wx.ID_ANY, u"Filter:", wx.DefaultPosition, wx.DefaultSize, 0 )
		self.m_filterLabel.Wrap( -1 )

		bSizerSearchBar.Add( self.m_filterLabel, 0, wx.ALIGN_CENTER_VERTICAL|wx.LEFT, 10 )

		self.m_textCtrlFilter = wx.TextCtrl( self, wx.ID_ANY, wx.EmptyString, wx.DefaultPosition, wx.DefaultSize, 0 )
		self.m_textCtrlFilter.SetToolTip( u"Narrows the results below across every column" )

		bSizerSearchBar.Add( self.m_textCtrlFilter, 2, wx.ALIGN_CENTER_VERTICAL|wx.ALL, 5 )


		bSizerMain.Add( bSizerSearchBar, 0, wx.EXPAND|wx.LEFT|wx.RIGHT, 5 )

		self.m_splitterMain = wx.SplitterWindow( self, wx.ID_ANY, wx.DefaultPosition, wx.DefaultSize, wx.SP_3D|wx.SP_LIVE_UPDATE )
		self.m_splitterMain.SetSashGravity( 0.4 )
		self.m_splitterMain.Bind( wx.EVT_IDLE, self.m_splitterMainOnIdle )
		self.m_splitterMain.SetMinimumPaneSize( 220 )

		self.m_resultsPanel = wx.Panel( self.m_splitterMain, wx.ID_ANY, wx.DefaultPosition, wx.DefaultSize, wx.TAB_TRAVERSAL )
		bSizerResults = wx.BoxSizer( wx.VERTICAL )

		self.m_searchResultsTree = wx.dataview.TreeListCtrl( self.m_resultsPanel, wx.ID_ANY, wx.DefaultPosition, wx.DefaultSize, wx.dataview.TL_MULTIPLE )
		self.m_searchResultsTree.SetToolTip( u"Double-click a row to queue it for download" )
		self.m_searchResultsTree.SetMinSize( wx.Size( 420,240 ) )


		bSizerResults.Add( self.m_searchResultsTree, 1, wx.EXPAND|wx.ALL, 5 )

		bSizerResultStatus = wx.BoxSizer( wx.HORIZONTAL )

		self.m_searchStatus = wx.StaticText( self.m_resultsPanel, wx.ID_ANY, wx.EmptyString, wx.DefaultPosition, wx.DefaultSize, 0 )
		self.m_searchStatus.Wrap( -1 )

		bSizerResultStatus.Add( self.m_searchStatus, 0, wx.ALIGN_CENTER_VERTICAL|wx.ALL, 5 )


		bSizerResultStatus.Add( ( 0, 0), 1, wx.EXPAND, 5 )

		self.m_searchPage = wx.StaticText( self.m_resultsPanel, wx.ID_ANY, wx.EmptyString, wx.DefaultPosition, wx.DefaultSize, 0 )
		self.m_searchPage.Wrap( -1 )

		bSizerResultStatus.Add( self.m_searchPage, 0, wx.ALIGN_CENTER_VERTICAL|wx.ALL, 5 )

		self.m_prevPageBtn = wx.Button( self.m_resultsPanel, wx.ID_ANY, u"  <  ", wx.DefaultPosition, wx.DefaultSize, wx.BU_EXACTFIT )
		self.m_prevPageBtn.Enable( False )
		self.m_prevPageBtn.SetToolTip( u"Previous page" )

		bSizerResultStatus.Add( self.m_prevPageBtn, 0, wx.ALIGN_CENTER_VERTICAL|wx.BOTTOM|wx.TOP, 5 )

		self.m_nextPageBtn = wx.Button( self.m_resultsPanel, wx.ID_ANY, u"  >  ", wx.DefaultPosition, wx.DefaultSize, wx.BU_EXACTFIT )
		self.m_nextPageBtn.Enable( False )
		self.m_nextPageBtn.SetToolTip( u"Next page" )

		bSizerResultStatus.Add( self.m_nextPageBtn, 0, wx.ALIGN_CENTER_VERTICAL|wx.BOTTOM|wx.RIGHT|wx.TOP, 5 )


		bSizerResults.Add( bSizerResultStatus, 0, wx.EXPAND, 5 )


		self.m_resultsPanel.SetSizer( bSizerResults )
		self.m_resultsPanel.Layout()
		bSizerResults.Fit( self.m_resultsPanel )
		self.m_inspectorPanel = wx.Panel( self.m_splitterMain, wx.ID_ANY, wx.DefaultPosition, wx.DefaultSize, wx.TAB_TRAVERSAL )
		bSizerInspector = wx.BoxSizer( wx.VERTICAL )

		self.m_splitterInspector = wx.SplitterWindow( self.m_inspectorPanel, wx.ID_ANY, wx.DefaultPosition, wx.DefaultSize, wx.SP_3D|wx.SP_LIVE_UPDATE )
		self.m_splitterInspector.SetSashGravity( 1 )
		self.m_splitterInspector.Bind( wx.EVT_IDLE, self.m_splitterInspectorOnIdle )
		self.m_splitterInspector.SetMinimumPaneSize( 150 )

		self.m_drawingsPanel = wx.Panel( self.m_splitterInspector, wx.ID_ANY, wx.DefaultPosition, wx.DefaultSize, wx.TAB_TRAVERSAL )
		bSizerDrawings = wx.BoxSizer( wx.VERTICAL )

		bSizerSymbol = wx.StaticBoxSizer( wx.StaticBox( self.m_drawingsPanel, wx.ID_ANY, u"Symbol" ), wx.VERTICAL )

		self.m_symbolPanel = wx.Panel( bSizerSymbol.GetStaticBox(), wx.ID_ANY, wx.DefaultPosition, wx.DefaultSize, wx.TAB_TRAVERSAL )
		self.m_symbolPanel.SetMinSize( wx.Size( 220,120 ) )

		bSizerSymbol.Add( self.m_symbolPanel, 1, wx.EXPAND|wx.ALL, 2 )


		bSizerDrawings.Add( bSizerSymbol, 1, wx.EXPAND|wx.ALL, 4 )

		bSizerFootprint = wx.StaticBoxSizer( wx.StaticBox( self.m_drawingsPanel, wx.ID_ANY, u"Footprint" ), wx.VERTICAL )

		self.m_footprintPanel = wx.Panel( bSizerFootprint.GetStaticBox(), wx.ID_ANY, wx.DefaultPosition, wx.DefaultSize, wx.TAB_TRAVERSAL )
		self.m_footprintPanel.SetMinSize( wx.Size( 220,120 ) )

		bSizerFootprint.Add( self.m_footprintPanel, 1, wx.EXPAND|wx.ALL, 2 )


		bSizerDrawings.Add( bSizerFootprint, 1, wx.EXPAND|wx.ALL, 4 )


		self.m_drawingsPanel.SetSizer( bSizerDrawings )
		self.m_drawingsPanel.Layout()
		bSizerDrawings.Fit( self.m_drawingsPanel )
		self.m_detailsPanel = wx.Panel( self.m_splitterInspector, wx.ID_ANY, wx.DefaultPosition, wx.DefaultSize, wx.TAB_TRAVERSAL )
		bSizerDetails = wx.BoxSizer( wx.VERTICAL )

		bSizerLinks = wx.BoxSizer( wx.HORIZONTAL )

		self.m_partTitle = wx.StaticText( self.m_detailsPanel, wx.ID_ANY, wx.EmptyString, wx.DefaultPosition, wx.DefaultSize, 0 )
		self.m_partTitle.Wrap( -1 )

		bSizerLinks.Add( self.m_partTitle, 0, wx.ALIGN_CENTER_VERTICAL|wx.ALL, 5 )


		bSizerLinks.Add( ( 0, 0), 1, wx.EXPAND, 5 )

		self.m_searchHyperlink1 = wx.adv.HyperlinkCtrl( self.m_detailsPanel, wx.ID_ANY, wx.EmptyString, wx.EmptyString, wx.DefaultPosition, wx.DefaultSize, wx.adv.HL_ALIGN_LEFT|wx.adv.HL_CONTEXTMENU )
		bSizerLinks.Add( self.m_searchHyperlink1, 0, wx.ALIGN_CENTER_VERTICAL|wx.ALL, 5 )

		self.m_searchHyperlink2 = wx.adv.HyperlinkCtrl( self.m_detailsPanel, wx.ID_ANY, wx.EmptyString, wx.EmptyString, wx.DefaultPosition, wx.DefaultSize, wx.adv.HL_ALIGN_LEFT|wx.adv.HL_CONTEXTMENU )
		bSizerLinks.Add( self.m_searchHyperlink2, 0, wx.ALIGN_CENTER_VERTICAL|wx.ALL, 5 )

		self.m_searchHyperlink3 = wx.adv.HyperlinkCtrl( self.m_detailsPanel, wx.ID_ANY, wx.EmptyString, wx.EmptyString, wx.DefaultPosition, wx.DefaultSize, wx.adv.HL_ALIGN_LEFT|wx.adv.HL_CONTEXTMENU )
		bSizerLinks.Add( self.m_searchHyperlink3, 0, wx.ALIGN_CENTER_VERTICAL|wx.ALL, 5 )


		bSizerDetails.Add( bSizerLinks, 0, wx.EXPAND, 5 )

		self.m_paramsList = wx.ListCtrl( self.m_detailsPanel, wx.ID_ANY, wx.DefaultPosition, wx.DefaultSize, wx.LC_HRULES|wx.LC_REPORT|wx.LC_SINGLE_SEL|wx.LC_VRULES )
		self.m_paramsList.SetToolTip( u"Parameters of the selected part" )
		self.m_paramsList.SetMinSize( wx.Size( 260,120 ) )

		bSizerDetails.Add( self.m_paramsList, 1, wx.EXPAND|wx.BOTTOM|wx.LEFT|wx.RIGHT, 5 )


		self.m_detailsPanel.SetSizer( bSizerDetails )
		self.m_detailsPanel.Layout()
		bSizerDetails.Fit( self.m_detailsPanel )
		self.m_splitterInspector.SplitHorizontally( self.m_drawingsPanel, self.m_detailsPanel, 400 )
		bSizerInspector.Add( self.m_splitterInspector, 1, wx.EXPAND, 5 )


		self.m_inspectorPanel.SetSizer( bSizerInspector )
		self.m_inspectorPanel.Layout()
		bSizerInspector.Fit( self.m_inspectorPanel )
		self.m_splitterMain.SplitVertically( self.m_resultsPanel, self.m_inspectorPanel, 700 )
		bSizerMain.Add( self.m_splitterMain, 1, wx.EXPAND, 5 )

		bSizerQueue = wx.BoxSizer( wx.HORIZONTAL )

		bSizerQueueList = wx.BoxSizer( wx.VERTICAL )

		self.m_queueLabel = wx.StaticText( self, wx.ID_ANY, u"Download queue is empty. Double-click a result to add it.", wx.DefaultPosition, wx.DefaultSize, 0 )
		self.m_queueLabel.Wrap( -1 )

		bSizerQueueList.Add( self.m_queueLabel, 0, wx.LEFT|wx.TOP, 5 )

		self.m_queueList = wx.ListCtrl( self, wx.ID_ANY, wx.DefaultPosition, wx.DefaultSize, wx.LC_HRULES|wx.LC_REPORT )
		self.m_queueList.SetToolTip( u"Parts that will be downloaded" )
		self.m_queueList.SetMinSize( wx.Size( 400,76 ) )

		bSizerQueueList.Add( self.m_queueList, 1, wx.EXPAND|wx.ALL, 5 )


		bSizerQueue.Add( bSizerQueueList, 1, wx.EXPAND, 5 )

		gSizerQueueButtons = wx.GridSizer( 2, 2, 0, 0 )

		self.m_queueAddBtn = wx.Button( self, wx.ID_ANY, u"Add selected", wx.DefaultPosition, wx.DefaultSize, 0 )
		self.m_queueAddBtn.SetToolTip( u"Queue the parts selected in the results" )

		gSizerQueueButtons.Add( self.m_queueAddBtn, 0, wx.ALL|wx.EXPAND, 2 )

		self.m_queuePasteBtn = wx.Button( self, wx.ID_ANY, u"Paste codes…", wx.DefaultPosition, wx.DefaultSize, 0 )
		self.m_queuePasteBtn.SetToolTip( u"Queue LCSC codes or UUIDs, one per line" )

		gSizerQueueButtons.Add( self.m_queuePasteBtn, 0, wx.ALL|wx.EXPAND, 2 )

		self.m_queueRemoveBtn = wx.Button( self, wx.ID_ANY, u"Remove", wx.DefaultPosition, wx.DefaultSize, 0 )
		self.m_queueRemoveBtn.SetToolTip( u"Remove the selected queue entries" )

		gSizerQueueButtons.Add( self.m_queueRemoveBtn, 0, wx.ALL|wx.EXPAND, 2 )

		self.m_queueClearBtn = wx.Button( self, wx.ID_ANY, u"Clear", wx.DefaultPosition, wx.DefaultSize, 0 )
		self.m_queueClearBtn.SetToolTip( u"Empty the queue" )

		gSizerQueueButtons.Add( self.m_queueClearBtn, 0, wx.ALL|wx.EXPAND, 2 )


		bSizerQueue.Add( gSizerQueueButtons, 0, wx.ALIGN_BOTTOM, 5 )


		bSizerMain.Add( bSizerQueue, 0, wx.EXPAND|wx.LEFT|wx.RIGHT, 5 )

		bSizerLibrary = wx.BoxSizer( wx.HORIZONTAL )

		self.m_staticText2 = wx.StaticText( self, wx.ID_ANY, u"Library:", wx.DefaultPosition, wx.DefaultSize, 0 )
		self.m_staticText2.Wrap( -1 )

		bSizerLibrary.Add( self.m_staticText2, 0, wx.ALIGN_CENTER_VERTICAL|wx.ALL, 5 )

		self.m_textCtrlOutLibName = wx.TextCtrl( self, wx.ID_ANY, wx.EmptyString, wx.DefaultPosition, wx.DefaultSize, 0 )
		self.m_textCtrlOutLibName.SetToolTip( u"Library name inside the project, or an absolute path" )

		bSizerLibrary.Add( self.m_textCtrlOutLibName, 1, wx.ALIGN_CENTER_VERTICAL|wx.BOTTOM|wx.EXPAND|wx.TOP, 5 )

		self.m_browseBtn = wx.Button( self, wx.ID_ANY, u"Browse…", wx.DefaultPosition, wx.DefaultSize, 0 )
		self.m_browseBtn.SetToolTip( u"Choose the folder the library is written to" )

		bSizerLibrary.Add( self.m_browseBtn, 0, wx.ALIGN_CENTER_VERTICAL|wx.ALL, 5 )

		self.m_actionBtn = wx.Button( self, wx.ID_ANY, u"Download parts", wx.DefaultPosition, wx.DefaultSize, 0 )
		self.m_actionBtn.Enable( False )
		self.m_actionBtn.SetToolTip( u"Download everything in the queue" )

		bSizerLibrary.Add( self.m_actionBtn, 0, wx.ALIGN_CENTER_VERTICAL|wx.BOTTOM|wx.LEFT|wx.TOP, 10 )


		bSizerMain.Add( bSizerLibrary, 0, wx.EXPAND|wx.LEFT|wx.RIGHT, 5 )

		bSizerProgress = wx.BoxSizer( wx.HORIZONTAL )

		self.m_progress = wx.Gauge( self, wx.ID_ANY, 100, wx.DefaultPosition, wx.DefaultSize, wx.GA_HORIZONTAL )
		self.m_progress.SetValue( 0 )
		self.m_progress.SetMinSize( wx.Size( -1,18 ) )

		bSizerProgress.Add( self.m_progress, 1, wx.ALIGN_CENTER_VERTICAL|wx.ALL, 5 )

		self.m_resultStatus = wx.StaticText( self, wx.ID_ANY, wx.EmptyString, wx.DefaultPosition, wx.DefaultSize, 0 )
		self.m_resultStatus.Wrap( -1 )

		bSizerProgress.Add( self.m_resultStatus, 2, wx.ALIGN_CENTER_VERTICAL|wx.ALL, 5 )

		self.m_debug = wx.CheckBox( self, wx.ID_ANY, u"Debug", wx.DefaultPosition, wx.DefaultSize, 0 )
		self.m_debug.SetToolTip( u"Log every request and document" )

		bSizerProgress.Add( self.m_debug, 0, wx.ALIGN_CENTER_VERTICAL|wx.ALL, 5 )

		self.m_closeButton = wx.Button( self, wx.ID_CANCEL, u"Close dialog", wx.DefaultPosition, wx.DefaultSize, 0 )
		bSizerProgress.Add( self.m_closeButton, 0, wx.ALIGN_CENTER_VERTICAL|wx.ALL, 5 )


		bSizerMain.Add( bSizerProgress, 0, wx.EXPAND|wx.LEFT|wx.RIGHT, 5 )

		self.m_logPane = wx.CollapsiblePane( self, wx.ID_ANY, u"Details", wx.DefaultPosition, wx.DefaultSize, wx.CP_DEFAULT_STYLE )
		self.m_logPane.Collapse( True )

		self.m_logPane.SetToolTip( u"Full log of the last run" )

		bSizerLogPane = wx.BoxSizer( wx.VERTICAL )

		self.m_log = wx.TextCtrl( self.m_logPane.GetPane(), wx.ID_ANY, wx.EmptyString, wx.DefaultPosition, wx.DefaultSize, wx.TE_BESTWRAP|wx.TE_MULTILINE|wx.TE_READONLY )
		self.m_log.SetMinSize( wx.Size( 400,150 ) )

		bSizerLogPane.Add( self.m_log, 1, wx.EXPAND|wx.ALL, 2 )


		self.m_logPane.GetPane().SetSizer( bSizerLogPane )
		self.m_logPane.GetPane().Layout()
		bSizerLogPane.Fit( self.m_logPane.GetPane() )
		bSizerMain.Add( self.m_logPane, 0, wx.EXPAND|wx.LEFT|wx.RIGHT, 5 )


		self.SetSizer( bSizerMain )
		self.Layout()

		self.Centre( wx.BOTH )

	def __del__( self ):
		pass

	def m_splitterMainOnIdle( self, event ):
		self.m_splitterMain.SetSashPosition( 700 )
		self.m_splitterMain.Unbind( wx.EVT_IDLE )

	def m_splitterInspectorOnIdle( self, event ):
		self.m_splitterInspector.SetSashPosition( 400 )
		self.m_splitterInspector.Unbind( wx.EVT_IDLE )


