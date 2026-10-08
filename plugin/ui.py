"""Native virtual Qt views; device I/O is serialized by Calibre DeviceManager.

Metadata, persistent source claims, 修改预览 and device tasks share one pipeline.
Firmware writes remain gated by the explicit device probe.
"""
import copy
import threading
from uuid import uuid4
from pathlib import Path
from qt.core import (Qt, QSize, QObject, pyqtSignal, QRunnable, QThreadPool, QTimer,
    QAbstractTableModel, QModelIndex, QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QMessageBox, QTableView, QListView, QStackedWidget,
    QTabWidget, QWidget, QHeaderView, QAbstractItemView, QInputDialog, QFileDialog,
    QDialogButtonBox, QTextEdit, QStyledItemDelegate, QColor, QPen,QMenu,QScrollArea,
    QCheckBox,QComboBox,QSortFilterProxyModel,QSplitter,QGroupBox,QGridLayout,QIcon,QPixmap,QProgressBar)
from calibre.gui2.actions import InterfaceAction
from calibre.gui2.device import device_signals
from calibre.utils.config import JSONConfig
from .protocol import Invalid, canonical, digest, loads, cancellable
from .planner import Catalog, intent, move, plan, request
from .legacy import import_collections
from .transport import connected_store, read
from .pages import Pages
from .theme import InkDialog as QDialog, InkInputDialog as QInputDialog, InkMessageBox as QMessageBox, InkFileDialog as QFileDialog
from .theme import NoticeLabel

LABELS = dict(create_collection='新建收藏夹', add_members='加入书籍', remove_members='移除书籍',
              rename_collection='改名', delete_collection='删除收藏夹', verify_state='只读核验')
EDIT_HELP=[
    '新建一个收藏夹草稿，可以立即向其中加入书籍。预览并发送后，还需在 Kindle 执行。',
    '修改所选收藏夹的名称，保留其中书籍及其他收藏归属。当前只生成待同步草稿。',
    '删除所选收藏夹及其收藏关系；保留书籍文件，也保留书籍在其他收藏夹中的归属。先生成待同步草稿，可撤销，预览确认后才发送。',
    '把书籍加入目标收藏夹，保留所有原有收藏归属。一本书可以同时属于多个收藏夹。当前只生成待同步草稿。',
    '只将所选书籍移出指定收藏夹，不删除书籍文件，也不影响其他收藏归属。在全部书籍模式下，需要先选择从哪个收藏夹移除。',
    '先加入目标收藏夹，确认成功后再从指定原收藏夹移除。其他收藏归属和书籍文件保留；若加入未成功，不会继续移除。移动草稿的加入与移除会一起撤销。']

def is_script(book):
    return (book.get('location') or book.get('title') or '').strip().casefold().endswith('.sh')


class Model(QAbstractTableModel):
    def __init__(self, headers, parent=None):
        super().__init__(parent)
        self.headers, self.rows = headers, []
    def replace(self, rows):
        self.beginResetModel(); self.rows = rows; self.endResetModel()
    def rowCount(self, parent=QModelIndex()): return 0 if parent.isValid() else len(self.rows)
    def columnCount(self, parent=QModelIndex()): return len(self.headers)
    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid(): return None
        row = self.rows[index.row()]
        if role == Qt.ItemDataRole.UserRole: return row
        if role == Qt.ItemDataRole.ToolTipRole and 'tooltip' in row:return row['tooltip']
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return row['cells'][index.column()]
    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.headers[section]


def table(model):
    view = QTableView()
    view.setModel(model)
    view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    view.verticalHeader().setVisible(False)
    view.verticalHeader().setDefaultSectionSize(32)
    view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    view.horizontalHeader().setStretchLastSection(True)
    view.setColumnWidth(0,300)
    return view


class Signals(QObject):
    done = pyqtSignal(object)


class BookSearchProxy(QSortFilterProxyModel):
    def set_terms(self,text):
        self.terms=text.casefold().split();self.invalidateFilter()
    def filterAcceptsRow(self, row, parent):
        value=self.sourceModel().rows[row]
        if getattr(self,'only_ungrouped',False) and not value.get('unclassified',False):return False
        haystack=' '.join(str(v) for v in value['cells']).casefold()
        return all(word in haystack for word in getattr(self,'terms',[]))


class BookPicker(QDialog):
    """One source model and a proxy; no per-book widgets or ebook reads."""
    def __init__(self, books, parent=None, calibre_order=None):
        super().__init__(parent)
        books=[b for b in books if not is_script(b)]
        self.setWindowTitle('选择加入的设备书籍');self.resize(760,550)
        layout=QVBoxLayout(self);bar=QHBoxLayout();layout.addLayout(bar)
        bar.addWidget(QLabel('搜索书籍：'));self.search=QLineEdit();self.search.setClearButtonEnabled(True);self.search.setPlaceholderText('输入书名关键词，空格分隔多个词');bar.addWidget(self.search,1)
        self.ungrouped=QCheckBox('只看未归类');bar.addWidget(self.ungrouped)
        self.order=QComboBox();self.order.addItems(['书名升序','书名降序','设备 UUID 升序']);bar.addWidget(self.order)
        if calibre_order is not None:
            self.order.addItem('Calibre 当前列表顺序')
            rank={value:i for i,value in enumerate(calibre_order)}
            books=sorted(books,key=lambda b:rank.get(b.get('calibre_uuid'),len(rank)))
            self.order.setCurrentIndex(3)
        layout.addWidget(QLabel('已隐藏 .sh 脚本及本收藏夹已有或待加入的书；其他收藏夹中的书仍可选择。'))
        if calibre_order is not None:
            layout.addWidget(QLabel('Calibre 顺序：当前列表在前；被筛选隐藏及未匹配的设备书籍在后。'))
        self.model=Model(['书名','设备 UUID','已有收藏归属'],self)
        self.model.replace([dict(id=b['uuid'],unclassified=not b.get('collection_count',0),cells=[b['title'] or '','待设备识别' if b['uuid'].startswith('kc-new-') else b['uuid'],b.get('collection_names','')]) for b in books])
        self.proxy=BookSearchProxy(self);self.proxy.setSourceModel(self.model)
        self.proxy.setFilterKeyColumn(-1);self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.proxy.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.view=table(self.proxy);self.view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        layout.addWidget(self.view)
        self.picked=set();self.changing=False
        self.count=QLabel();layout.addWidget(self.count)
        self.view.selectionModel().selectionChanged.connect(self.capture)
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(180)
        self.search.textChanged.connect(lambda:self.timer.start());self.timer.timeout.connect(self.refilter)
        self.order.currentIndexChanged.connect(self.refilter)
        self.ungrouped.toggled.connect(self.refilter)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
        self.refilter()
    def capture(self,*args):
        if self.changing:return
        visible={self.proxy.index(i,0).data(Qt.ItemDataRole.UserRole)['id'] for i in range(self.proxy.rowCount())}
        self.picked.difference_update(visible)
        self.picked.update(i.data(Qt.ItemDataRole.UserRole)['id'] for i in self.view.selectionModel().selectedRows())
        self.count.setText(f'显示 {self.proxy.rowCount()} 本；已选 {len(self.picked)} 本（包含搜索隐藏的选择）')
    def refilter(self,*args):
        self.changing=True
        self.proxy.only_ungrouped=self.ungrouped.isChecked()
        self.proxy.set_terms(self.search.text().strip())
        self.proxy.sort(-1 if self.order.currentIndex()==3 else (1 if self.order.currentIndex()==2 else 0),
                        Qt.SortOrder.DescendingOrder if self.order.currentIndex()==1 else Qt.SortOrder.AscendingOrder)
        selection=self.view.selectionModel();selection.clearSelection()
        for row in range(self.proxy.rowCount()):
            index=self.proxy.index(row,0)
            if index.data(Qt.ItemDataRole.UserRole)['id'] in self.picked:
                selection.select(index,selection.SelectionFlag.Select|selection.SelectionFlag.Rows)
        self.changing=False
        self.count.setText(f'显示 {self.proxy.rowCount()} 本；已选 {len(self.picked)} 本（包含搜索隐藏的选择）')


class FolderDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index): return QSize(174,122)
    def paint(self, painter, option, index):
        painter.save()
        selected=bool(option.state & type(option.state).State_Selected)
        box=option.rect.adjusted(4,4,-4,-4)
        painter.setPen(QPen(QColor('#a34838' if selected else '#d5cfc3'),1))
        painter.setBrush(QColor('#f0e6d9' if selected else '#faf8f1'))
        painter.drawRoundedRect(box,4,4)
        painter.setPen(QPen(QColor('#9e4939' if selected else '#6b665b'),1))
        painter.setBrush(QColor('#f5f0e6'))
        painter.drawRoundedRect(box.left()+13,box.top()+12,15,6,2,2)
        painter.drawRoundedRect(box.left()+13,box.top()+16,27,18,2,2)
        painter.setPen(QColor('#302d29'))
        row=index.data(Qt.ItemDataRole.UserRole)
        title=option.fontMetrics.elidedText(row.get('name',''),Qt.TextElideMode.ElideRight,max(10,(box.width()-24)*2-25))
        painter.drawText(box.adjusted(12,41,-10,-29),Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignTop|Qt.TextFlag.TextWordWrap,title)
        painter.setPen(QColor('#706657'))
        summary=row.get('summary',str(row.get('count',0))+' 本')+(' · '+row['marker'] if row.get('marker') else '')
        painter.drawText(box.adjusted(12,box.height()-26,-10,-6),Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,
                         option.fontMetrics.elidedText(summary,Qt.TextElideMode.ElideRight,box.width()-24))
        painter.restore()


class Work(QRunnable):
    def __init__(self, fn):
        super().__init__(); self.fn = fn; self.signals = Signals(); self.cancel=threading.Event()
    def run(self):
        try:
            with cancellable(self.cancel): result = (True,self.fn())
        except Exception as exc: result = (False,str(exc))
        self.signals.done.emit(result)


class KCPlusAction(InterfaceAction):
    name = 'KC++'
    action_spec = ('KC++', None, '整理 Kindle 收藏夹并预览编辑', None)
    def genesis(self):
        self.window = None
        pixmap = QPixmap()
        pixmap.loadFromData(self.load_resources(['images/kcpp.svg'])['images/kcpp.svg'], 'SVG')
        self.qaction.setIcon(QIcon(pixmap))
        self.qaction.triggered.connect(self.open_manager)
        menu=QMenu(self.gui)
        from .theme import apply_theme
        apply_theme(menu)
        entries=[('新建收藏夹','create_collection'),('预览修改','calculate_preview'),
            ('书架与同步设置','configure_rules'),('查看设备报告','show_device_report'),('整理收藏夹','show_collections'),
            ('把 Kindle 书架导入 Calibre 列','import_column'),('恢复 Calibre 列备份','restore_column'),
            ('首次使用','first_run_guide'),('关于 KC++','about')]
        for label,method in entries:
            action=menu.addAction(label)
            def invoke(checked=False,name=method):
                self.open_manager(action=name)
            action.triggered.connect(invoke)
        self.qaction.setMenu(menu)
        device_signals.device_metadata_available.connect(self.metadata_ready)
        device_signals.device_connection_changed.connect(self.connection_changed)
    def ensure_window(self):
        if self.window is not None and not self.window.same_library():
            self.window.retire()
            self.window=None
        if self.window is None:
            self.window = Manager(self.gui)
    def library_changed(self, db):
        if self.window is not None:
            self.window.retire()
            self.window=None
    def open_manager(self, checked=False, action=None):
        self.ensure_window()
        self.window.show(); self.window.raise_(); self.window.activateWindow()
        if action in ('first_run_guide','about') and not self.window.busy:
            getattr(self.window,action)();return
        self.window.after_load_action=action
        self.window.load()
    def metadata_ready(self, *args):
        # Connecting a device alone must not instantiate KC++ or scan books.
        # An existing session may receive confirmed results without opening UI.
        if self.window is None or not self.window.same_library():return
        if not self.window.busy:self.window.load(automatic=not self.window.isVisible())
        else:self.window.reload_requested=True
    def connection_changed(self, connected):
        if not connected and self.window is not None:
            self.window.invalidate('设备已断开，连接后会自动刷新。')


class Manager(QDialog, Pages):
    def __init__(self, gui):
        super().__init__(gui)
        self.setProperty('kc_main',True)
        self.gui = gui
        self.library_db=gui.current_db
        self.library_id=str(gui.current_db.library_id)
        self.retired=False
        self.after_load_action=None
        self.service=None
        self.prepared=None
        self.preview_ready=False
        if hasattr(gui.current_db,'new_api'):
            from calibre.constants import config_dir
            from .service import Service
            self.service=Service(Path(config_dir)/'plugins'/'kc-plus-state')
        self.snapshot = self.catalog = self.preview = None
        self.intents, self.scope, self.issues = [], set(), []
        self.active = None
        self.pending_ops=[]
        self.busy, self.work = False, None
        self.generation = 0
        self.reload_requested=False
        self.pool = QThreadPool(self); self.pool.setMaxThreadCount(1)
        from . import KCPlus
        self.setWindowTitle('KC++ '+'.'.join(map(str,KCPlus.version))+' · 收藏夹管理')
        self.resize(1440,900)
        outer=QHBoxLayout(self);outer.setContentsMargins(0,0,0,0);outer.setSpacing(0)
        navigation=QWidget();navigation.setObjectName('navigation');navigation.setFixedWidth(154);outer.addWidget(navigation)
        nav_layout=QVBoxLayout(navigation);nav_layout.setContentsMargins(10,18,10,12)
        brand=QLabel('KC++');brand.setObjectName('heading');nav_layout.addWidget(brand)
        self.navigation_layout=nav_layout;self.navigation_buttons=[]
        panel=QWidget();outer.addWidget(panel,1);root=QVBoxLayout(panel);root.setContentsMargins(18,12,18,12);root.setSpacing(10)
        heading=QLabel('我的收藏夹');self.heading=heading;heading.setObjectName('heading');root.addWidget(heading)
        subtitle=QHBoxLayout();root.addLayout(subtitle);subtitle.addWidget(QLabel('整理 → 预览 → 发送'));subtitle.addStretch()
        self.mode_summary=QLabel('读取设备后选择整理方式',self);self.mode_summary.setMaximumWidth(340);subtitle.addWidget(self.mode_summary)

        bar = QHBoxLayout(); root.addLayout(bar)
        self.refresh = QPushButton('读取设备状态');self.refresh.setToolTip('读取 Kindle 上次导出的收藏状态、任务结果，以及 Calibre 新传入的书籍；新书无需先拔插。若你在 Kindle 上另行修改过收藏，请先运行 KC刷新收藏夹，再连接读取。'); self.refresh.clicked.connect(self.load); bar.addWidget(self.refresh)
        self.scan_new=QCheckBox('扫描新增文件');self.scan_new.setChecked(True);bar.addWidget(self.scan_new)
        self.scan_new.setToolTip('关闭后只读取快照和回执，速度较快；发送修改前需开启并重新读取。扫描可取消。')
        self.import_button = QPushButton('导入旧 collection(s).json 并预览')
        self.import_button.clicked.connect(self.import_legacy);self.import_button.hide()
        self.clear = QPushButton('放弃草稿'); self.clear.clicked.connect(self.clear_draft); bar.addWidget(self.clear)
        bar.addStretch()
        self.start_button=QPushButton('首次使用');self.start_button.clicked.connect(self.first_run_guide);bar.addWidget(self.start_button)
        self.history_button=QPushButton('任务与执行结果');self.history_button.clicked.connect(self.task_history);bar.addWidget(self.history_button)
        self.draft_toggle=QPushButton('草稿（0）');self.draft_toggle.setCheckable(True);bar.addWidget(self.draft_toggle)
        self.workflow_status=QLabel('当前进度：等待读取设备',self);self.workflow_status.hide()
        self.read_feedback=QWidget();feedback=QHBoxLayout(self.read_feedback);feedback.setContentsMargins(0,0,0,0)
        self.read_message=QLabel();self.read_message.setWordWrap(True);feedback.addWidget(self.read_message,1)
        self.read_indicator=QProgressBar();self.read_indicator.setRange(0,0);self.read_indicator.setFixedWidth(120);feedback.addWidget(self.read_indicator)
        self.cancel_read=QPushButton('取消扫描');self.cancel_read.clicked.connect(lambda:self._scan_event.set());feedback.addWidget(self.cancel_read)
        self.read_feedback.hide();root.addWidget(self.read_feedback)
        notice_bar=QHBoxLayout();root.addLayout(notice_bar)
        self.status = NoticeLabel('打开 KC++ 后读取 Kindle 状态；仅连接设备不会弹出插件窗口。');self.status.setMaximumHeight(82);notice_bar.addWidget(self.status,1)
        self.notice_details=QPushButton('提示详情');self.notice_details.clicked.connect(self.show_notice_details);notice_bar.addWidget(self.notice_details)
        self.dismiss_notice_button=QPushButton('忽略本条');self.dismiss_notice_button.setToolTip('仅隐藏当前提示，不更改任务结果。新的状态或错误仍会显示。');self.dismiss_notice_button.clicked.connect(self.dismiss_notice);notice_bar.addWidget(self.dismiss_notice_button)
        self.main_splitter=QSplitter(Qt.Orientation.Horizontal);root.addWidget(self.main_splitter,1)
        self.tabs = QTabWidget();self.tabs.tabBar().hide(); self.main_splitter.addWidget(self.tabs)
        page = QWidget(); layout = QVBoxLayout(page); self.tabs.addTab(page,'收藏夹')
        tools = QHBoxLayout(); layout.addLayout(tools)
        self.view_mode=QComboBox();self.view_mode.addItems(['收藏夹模式','全部书籍']);tools.addWidget(self.view_mode)
        self.view_mode.currentIndexChanged.connect(self.change_view_mode)
        self.book_order=QComboBox();self.book_order.addItems(['书名升序','书名降序','设备编号顺序']);self.book_order.currentIndexChanged.connect(self.browse);self.book_order.hide()
        self.back = QPushButton('所有收藏夹'); self.back.clicked.connect(self.go_back); tools.addWidget(self.back)
        self.search = QLineEdit(); self.search.setPlaceholderText('搜索收藏夹名称或当前架内书名'); tools.addWidget(self.search,1)
        self.folder_order=QComboBox()
        self.folder_order.addItems(['收藏夹名称升序','收藏夹名称降序','书籍数量从多到少','设备编号顺序'])
        self.folder_order.setToolTip('设备编号顺序按 UUID 排列，不代表 Kindle 屏幕顺序。当前快照没有最近使用时间和 Kindle 排序设置。')
        tools.addWidget(self.folder_order);tools.addWidget(self.book_order);self.folder_order.currentIndexChanged.connect(self.browse)
        self.timer = QTimer(self); self.timer.setSingleShot(True); self.timer.setInterval(180)
        self.search.textChanged.connect(lambda: self.timer.start()); self.timer.timeout.connect(self.browse)
        self.stack = QStackedWidget(); layout.addWidget(self.stack,1)
        self.folders = Model(['收藏夹'],self)
        self.grid = QListView(); self.grid.setModel(self.folders)
        self.grid.setViewMode(QListView.ViewMode.IconMode)
        self.grid.setResizeMode(QListView.ResizeMode.Adjust)
        self.grid.setLayoutMode(QListView.LayoutMode.SinglePass);self.grid.setMovement(QListView.Movement.Static);self.grid.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop);self.grid.setWrapping(True);self.grid.setFlow(QListView.Flow.LeftToRight)
        self.grid.setGridSize(QSize(174,122)); self.grid.setUniformItemSizes(True)
        self.grid.setWordWrap(True); self.grid.setSpacing(4)
        self.grid.setItemDelegate(FolderDelegate(self.grid))
        self.grid.activated.connect(self.open_folder); self.stack.addWidget(self.grid)
        self.books = Model(['书籍','整理后归属（含草稿）','对应 Calibre','待执行变化'],self)
        self.book_view = table(self.books); self.book_view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection); self.stack.addWidget(self.book_view)
        self.book_view.setColumnWidth(0,260);self.book_view.setColumnWidth(1,260);self.book_view.setColumnWidth(2,135)
        self.grid.selectionModel().selectionChanged.connect(self.update_edit_buttons)
        self.book_view.selectionModel().selectionChanged.connect(self.update_edit_buttons)
        self.edit_buttons=[]
        edits=QHBoxLayout(); layout.addLayout(edits)
        for text,slot in [('新建收藏夹',self.create_collection),('改名',self.rename_collection),
                          ('删除收藏夹',self.delete_collection),('加入书籍',self.add_books),
                          ('移除所选书籍',self.remove_books),('移动所选书籍',self.move_books)]:
            button=QPushButton(text); button.clicked.connect(slot); edits.addWidget(button); self.edit_buttons.append(button)
        self.draft_sidebar=QWidget();self.draft_sidebar.setMinimumWidth(280)
        draft_layout=QVBoxLayout(self.draft_sidebar);draft_layout.setContentsMargins(8,0,0,0)
        draft_head=QHBoxLayout();draft_layout.addLayout(draft_head)
        draft_head.addWidget(QLabel('待同步草稿'));draft_head.addStretch()
        hide_drafts=QPushButton('收起');hide_drafts.clicked.connect(lambda:self.draft_toggle.setChecked(False));draft_head.addWidget(hide_drafts)
        hint=QLabel('选择草稿可撤销；悬停查看完整书单。移动的加入和移除会一起撤销。');hint.setWordWrap(True);draft_layout.addWidget(hint)
        self.draft_model=Model(['操作','收藏夹','涉及书籍 / 修改内容'],self)
        self.draft_view=table(self.draft_model)
        self.draft_view.setColumnWidth(0,85);self.draft_view.setColumnWidth(1,95);self.draft_view.setColumnWidth(2,150)
        self.draft_view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        draft_layout.addWidget(self.draft_view,1)
        draft_bar=QHBoxLayout();draft_layout.addLayout(draft_bar)
        self.undo_selected=QPushButton('撤销所选草稿');self.undo_selected.clicked.connect(self.undo_drafts);draft_bar.addWidget(self.undo_selected)
        self.undo_last=QPushButton('撤销上一步');self.undo_last.clicked.connect(lambda:self.undo_drafts(last=True));draft_bar.addWidget(self.undo_last)
        self.draft_groups={}
        self.main_splitter.addWidget(self.draft_sidebar)
        self.main_splitter.setStretchFactor(0,1);self.main_splitter.setStretchFactor(1,0)
        self.main_splitter.setCollapsible(0,False);self.main_splitter.setCollapsible(1,False)
        self.main_splitter.setSizes([780,340]);self.draft_sidebar.hide()
        self.draft_toggle.toggled.connect(self.draft_sidebar.setVisible)
        preview_page=QWidget(); preview_layout=QVBoxLayout(preview_page); self.tabs.addTab(preview_page,'修改预览')
        self.changes_only=QCheckBox('只显示变动');self.changes_only.setToolTip('隐藏无需变动的核验行，不影响安全检查或实际发送内容。')
        self.changes_only.setChecked(True);preview_layout.addWidget(self.changes_only)
        self.changes_only.toggled.connect(self.render_plan)
        self.resolutions={}
        self.resolve_button=QPushButton('处理加入 / 移除冲突');preview_layout.addWidget(self.resolve_button)
        self.resolve_button.clicked.connect(self.resolve_conflicts)
        self.plan_model=Model(['操作','收藏夹','影响','原因'],self)
        self.plan_view=table(self.plan_model);preview_layout.addWidget(self.plan_view,1)
        self.plan_ops=[]
        self.effect_label=QLabel('选择一项操作查看涉及的书籍。');preview_layout.addWidget(self.effect_label)
        self.effect_model=Model(['涉及书籍','设备文件（区分格式及副本）','设备 UUID'],self)
        effect_view=table(self.effect_model);effect_view.hideColumn(2);effect_view.setMaximumHeight(175);preview_layout.addWidget(effect_view)
        choose_copies=QPushButton('选择列同步副本…');choose_copies.setToolTip('一本书的多个设备格式是不同副本；可在书籍范围中只选需要同步的副本。手动草稿仍以选书结果为准。')
        choose_copies.clicked.connect(lambda:self.configure_rules(scope_tab=True));preview_layout.addWidget(choose_copies)
        self.plan_view.selectionModel().currentRowChanged.connect(self.show_effect)
        self.blockers=QTextEdit();self.blockers.setObjectName('preview_notice'); self.blockers.setReadOnly(True); self.blockers.setMaximumHeight(170); preview_layout.addWidget(self.blockers)
        workflow_page=QWidget(); workflow_layout=QVBoxLayout(workflow_page)
        workflow_scroll=QScrollArea();workflow_scroll.setWidgetResizable(True);workflow_scroll.setWidget(workflow_page)
        self.tabs.addTab(workflow_scroll,'规则、指定列与设置')
        sections=[
            ('日常设置', [('书架与同步设置（指定列、范围、规则、保护）',self.configure_rules),('设备书籍与书库记录',self.manage_book_links),('收藏夹与书籍报告',self.show_device_report)]),
            ('列值导入与回填', [('Kindle 收藏归属导入指定列',self.import_column),('回填已确认的执行结果',self.backfill_result),('校准已有书架的初始状态',self.calibrate_baseline),('从列备份恢复',self.restore_column)]),
            ('备份、迁移与分享', [('书架备份与新机迁移 / 书库分享',self.migration_center)]),
            ('旧插件迁移与其他导入', [('读取设备旧收藏文件',self.import_legacy),('导入旧 collection(s).json / .full',self.import_external_legacy),('导入原 Kindle Collections 配置',self.migrate_settings),('导入为单值文本 / 长文本 / 布尔摘要',self.import_summary)]),
            ('设备安装与维护', [('安装 / 升级配套 KC',self.install_device),('实验性 MTP 传输',self.mtp_settings),('准备真机编辑能力测试',self.probe_device),('撤销未执行的能力测试',self.cancel_device_probe),('恢复未完成快照',self.recover_snapshot),('恢复设备启动入口',self.rollback_device),('设备版本管理',self.runtime_manager),('空间占用与安全清理',self.storage_manager)]),
            ('帮助', [('首次使用',self.first_run_guide),('关于 KC++',self.about)])]
        self.column_only_buttons=[]
        for title,actions in sections:
            group=QGroupBox(title);grid=QGridLayout(group)
            explanations={'日常设置':'日常：选择哪一列管理收藏夹；加入保留其他归属，移动会退出原收藏夹。','列值导入与回填':'首次导入：把 Kindle 归属写入列。校准：只记录当前列与设备为起点，不修改二者。回填：处理已确认的执行结果。','旧插件迁移与其他导入':'仅迁移旧配置时使用，日常同步无需手动复制 JSON。','设备安装与维护':'首次安装或故障时使用；正常使用无需反复运行能力测试。','帮助':'日常顺序：整理 → 预览 → 发送 → Kindle 执行 → 重连接收结果。'}
            hint=QLabel(explanations.get(title,'备份收藏夹，或把选中的电子书和分类一起分享；接收后可独立建库。'));hint.setWordWrap(True);grid.addWidget(hint,0,0,1,2)
            for i,(text,slot) in enumerate(actions):
                button=QPushButton(text);button.clicked.connect(slot);grid.addWidget(button,1+i//2,i%2)
                if slot in (self.backfill_result,self.calibrate_baseline):self.column_only_buttons.append(button)
            workflow_layout.addWidget(group)
            if title=='旧插件迁移与其他导入':
                group.hide();advanced=QPushButton('旧插件迁移与高级导入 ▸');advanced.setCheckable(True);advanced.toggled.connect(group.setVisible);workflow_layout.addWidget(advanced)
        workflow_layout.addStretch()
        for title,index in [('设备安装与维护',3),('帮助',4)]:
            container=QWidget();section_layout=QVBoxLayout(container)
            for group in workflow_page.findChildren(QGroupBox):
                if group.title()==title:
                    workflow_layout.removeWidget(group);section_layout.addWidget(group)
            section_layout.addStretch();self.tabs.addTab(container,title)
        for text,index in [('收藏夹',0),('修改预览',1),('任务记录',None),('同步设置',2),('设备维护',3),('帮助',4)]:
            button=QPushButton(text);button.setObjectName('nav');button.setCheckable(index is not None)
            if index is None:button.clicked.connect(self.task_history)
            else:button.clicked.connect(lambda checked=False,i=index:self.tabs.setCurrentIndex(i))
            self.navigation_buttons.append((button,index));nav_layout.addWidget(button)
        nav_layout.addStretch();nav_layout.addWidget(QLabel('先预览，再发送'))
        def select_navigation(index):
            for button,target in self.navigation_buttons:
                if target is not None:button.setChecked(target==index)
            heading.setText(['我的收藏夹','修改预览','同步设置','设备维护','帮助'][index])
            if index==0 and self.catalog:self.browse()
            if hasattr(self,'report'):self.update_buttons()
        self.tabs.currentChanged.connect(select_navigation);select_navigation(0)
        bottom=QHBoxLayout(); root.addLayout(bottom)
        self.calculate=QPushButton('生成修改预览');self.calculate.setObjectName('primary'); self.calculate.clicked.connect(self.calculate_preview); bottom.addWidget(self.calculate)
        self.send=QPushButton('发送到 Kindle'); self.send.clicked.connect(self.send_plan); bottom.addWidget(self.send)
        self.report=QPushButton('保存预览报告'); self.report.clicked.connect(self.save_report); bottom.addWidget(self.report)
        self.cancel_button=QPushButton('取消计算'); self.cancel_button.clicked.connect(self.cancel_work); bottom.addWidget(self.cancel_button)
        bottom.addStretch(); close=QPushButton('关闭'); close.clicked.connect(self.reject); bottom.addWidget(close)
        self.update_buttons()

        self.view_preferences=JSONConfig('plugins/kc-plus-view')
        for key,combo in self.view_controls():
            value=self.view_preferences.get(key,0)
            if type(value) is not int or not 0<=value<combo.count():value=0
            combo.blockSignals(True);combo.setCurrentIndex(value);combo.blockSignals(False)
            combo.currentIndexChanged.connect(self.save_view_preferences)
        self.heading.setText('所有书籍' if self.view_mode.currentIndex()==1 else '我的收藏夹')

    def view_controls(self):
        return [('view_mode',self.view_mode),('folder_order',self.folder_order),('book_order',self.book_order)]

    def save_view_preferences(self,*args):
        with self.view_preferences:
            for key,combo in self.view_controls():self.view_preferences[key]=combo.currentIndex()

    def same_library(self):
        return not self.retired and self.library_db is self.gui.current_db and self.library_id==str(self.gui.current_db.library_id)

    def retire(self):
        # Keep the bound DB alive for an already-running job; discard its UI callbacks.
        self.persist_draft()
        self.retired=True;self.generation+=1
        self.reload_requested=False;self.after_load_action=None
        self.hide()

    def update_buttons(self):
        loaded=self.snapshot is not None and not self.busy
        self.refresh.setEnabled(not self.busy)
        self.calculate.setEnabled(loaded and (bool(self.intents) or self.service is not None))
        self.import_button.setEnabled(loaded)
        self.clear.setEnabled(not self.busy and bool(self.intents))
        self.undo_selected.setEnabled(not self.busy and bool(self.intents))
        self.undo_last.setEnabled(not self.busy and bool(self.intents))
        self.send.setEnabled(loaded and self.preview is not None and self.preview_ready
                             and bool(self.plan_ops) and not self.issues)
        self.report.setEnabled(not self.busy and self.preview is not None)
        self.report.setVisible(self.tabs.currentIndex()==1 and self.preview is not None)
        self.cancel_button.setEnabled(self.busy and self.work is not None and getattr(self.work,'can_cancel',True))
        self.cancel_button.setVisible(self.busy and self.work is not None and getattr(self.work,'can_cancel',True))
        self.update_edit_buttons()
        self.scan_new.setEnabled(not self.busy)
        if not getattr(self,'_last_scan_full',True):self.calculate.setEnabled(False);self.send.setEnabled(False)

    def update_edit_buttons(self,*args):
        if not hasattr(self,'edit_buttons'):return
        loaded=self.snapshot is not None and self.catalog is not None and not self.busy
        cid=self.selected_collection() if loaded else None
        selected=bool(self.book_view.selectionModel().selectedRows()) if loaded and self.stack.currentIndex()==1 else False
        enabled=[loaded,loaded and bool(cid),loaded and bool(cid),loaded and (bool(cid) or selected),loaded and selected,loaded and selected]
        hints=['请先读取设备状态。','请先选择收藏夹。','请先选择收藏夹。','请先选择收藏夹，或在全部书籍中选择书籍。','请先进入书籍列表并选书。','请先进入书籍列表并选书。']
        for b,ok,hint,detail in zip(self.edit_buttons,enabled,hints,EDIT_HELP):
            b.setEnabled(ok);b.setToolTip('<qt><b>'+b.text()+'</b><p>'+detail+'</p>'+('' if ok else '<p>'+hint+'</p>')+'</qt>');b.setToolTipDuration(18000)

    def refresh_mode_summary(self):
        from .usability import uses_column
        p=self.service.profile(self.library_id,self.snapshot) if self.service and self.snapshot else {}
        if uses_column(p):
            count=sum(len(b['copies']) for b in p['column_baseline'].values())
            scope='全部已匹配书籍（自动包含新书）' if p.get('scope_mode','selected')=='all' else f'指定书籍（{count} 个设备副本）'
            name=self.library_db.new_api.field_metadata.get(p['field'],{}).get('name',p['field'])
            title='联合整理 · '+('原生标签' if p['field']=='tags' else name)
            detail=f'同步列：{name}（{p["field"]}）\n列同步范围：{scope}\n已确认的收藏整理结果会回填此列。'
        elif p.get('sync_mode')=='column':
            title='联合整理 · 尚未选择同步列';detail='请在同步设置选择专用书架列、原生标签，或仅在 KC++ 整理。'
        else:
            title='仅 KC++ 整理';detail='收藏归属来自设备快照和草稿，无需创建或选择 Calibre 列。'
        self.mode_summary.setText(self.mode_summary.fontMetrics().elidedText(title,Qt.TextElideMode.ElideRight,330))
        self.mode_summary.setToolTip(title+'\n'+detail)
        for button in self.column_only_buttons:
            button.setEnabled(uses_column(p));button.setToolTip('列同步模式使用；纯 KC++ 模式无需此步骤' if not uses_column(p) else '')

    def notice_key(self):
        return digest([self.library_id,(self.snapshot or {}).get('device'),self.status.text()])

    def dismiss_notice(self):
        from calibre.utils.config import JSONConfig
        settings=JSONConfig('plugins/kc-plus-notices')
        keys=list(settings.get('dismissed',[]));key=self.notice_key()
        settings['dismissed']=(keys if key in keys else keys+[key])[-50:]
        self.status.hide()

    def show_notice(self,text,level='info',summary=None):
        self.status.show_message(text,level,summary)
        from calibre.utils.config import JSONConfig
        settings=JSONConfig('plugins/kc-plus-notices')
        self.status.setVisible(self.notice_key() not in settings.get('dismissed',[]))

    def show_notice_details(self):
        from .notice_ui import notice_dialog
        notice_dialog(self).exec()

    def invalidate(self, message):
        self.generation += 1
        self.snapshot=self.catalog=self.preview=None
        self.pending_ops=[]
        self.prepared=None
        self.folders.replace([]); self.books.replace([]); self.plan_model.replace([])
        self.show_notice(message,'warning');self.workflow_status.setText('当前进度：设备状态待刷新'); self.update_buttons()

    def error(self, exc):
        self._check_job_id=None
        self.after_load_action=None
        self.show_notice(str(exc),'error' if isinstance(exc,Exception) else 'warning')

    def background(self, fn, done, cancelable=True):
        if self.busy or not self.same_library(): return
        self.busy=True; self.update_buttons()
        generation=self.generation
        self.work=Work(fn)
        self.work.can_cancel=cancelable
        self.update_buttons()
        def finished(result):
            self.busy=False
            if generation == self.generation:
                if result[0]:
                    try:done(result[1])
                    except Exception as exc:self.error(exc)
                else: self.error(result[1])
            self.work=None; self.update_buttons()
            self.resume_reload()
        self.work.signals.done.connect(finished,Qt.ConnectionType.QueuedConnection)
        self.pool.start(self.work)

    def cancel_work(self):
        if self.work is not None and self.work.can_cancel:
            self.work.cancel.set()
            self.status.setText('正在取消计算…')

    def device_job(self, fn, done, description,read_progress=False,automatic=False):
        if self.busy or not self.same_library(): return
        self.busy=True; self.update_buttons()
        generation=self.generation
        timer=None
        if read_progress:
            self._scan_event=threading.Event();self._scan_message='正在读取设备快照…'
            self._scan_full=not automatic and self.scan_new.isChecked()
            self.read_message.setText(self._scan_message);self.cancel_read.setEnabled(True)
            self.read_feedback.setVisible(not automatic)
            def update_progress():
                self.read_message.setText(self._scan_message)
                self.cancel_read.setEnabled(not self._scan_message.startswith('扫描完成'))
            timer=QTimer(self);timer.timeout.connect(update_progress);timer.start(200)
        def finished(job):
            if timer:timer.stop();timer.deleteLater();self.read_feedback.hide()
            self.busy=False
            if generation == self.generation:
                if job.failed: self.error(job.exception or '设备任务失败')
                else:
                    try:
                        if read_progress:self._last_scan_full=self._scan_full
                        done(job.result)
                    except Exception as exc:self.error(exc)
            self.update_buttons()
            self.resume_reload()
        def bound_work(*args,**kwargs):
            if not self.same_library():raise Invalid('书库已切换，请在当前书库重新打开 KC++')
            return fn(*args,**kwargs)
        self.gui.device_manager.create_job(bound_work,finished,description)

    def read_snapshot(self,store):
        from .protocol import reporting,checkpoint
        event=getattr(self,'_scan_event',threading.Event())
        with cancellable(event),reporting(lambda message:setattr(self,'_scan_message',message)):
            value=store.snapshot(scan_new=getattr(self,'_scan_full',True));checkpoint()
        self._scan_message='扫描完成，正在接收结果与处理回填…'
        return value

    def resume_reload(self):
        if not self.same_library():return
        if self.reload_requested and not self.busy:
            self.reload_requested=False
            QTimer.singleShot(0,lambda:self.load(automatic=not self.isVisible()))
        elif self.after_load_action and not self.busy:
            QTimer.singleShot(0,self.run_loaded_action)

    def run_loaded_action(self):
        if not self.same_library() or self.busy:return
        name=self.after_load_action;self.after_load_action=None
        if not name:return
        if not self.snapshot and name not in ('first_run_guide','about'):
            self.error('请先连接 Kindle 并读取设备状态。');return
        getattr(self,name)()

    def load(self,checked=False,automatic=False):
        if not self.same_library():return
        # Keep an unseen draft intact; opening the manager performs a full read.
        if automatic and self.intents:return
        if self.busy:self.reload_requested=True;return
        if self.intents:
            self.preview=None; self.update_buttons()
            old=self.snapshot
            def append_books():
                store=connected_store(self.gui.device_manager);value=self.read_snapshot(store)
                if value['device']!=old['device'] or value['collections']!=old['collections'] or value['relations']!=old['relations'] or value['capabilities']!=old['capabilities'] or value['policy']!=old['policy']:
                    raise Invalid('设备收藏状态已变化；请先保存或放弃草稿，再重新读取')
                current={b['uuid']:b for b in value['books']}
                from .deferred import draft_book_bindings,pending
                mapping=draft_book_bindings(old,value,store)
                for b in old['books']:
                    target=current.get(mapping.get(b['uuid'],b['uuid']))
                    if target!=b and not (pending(b['uuid']) and target and target['location']==b['location'] and (b['uuid'] in mapping or target['uuid']==b['uuid'])):
                        raise Invalid('已有书籍状态已变化，请先处理草稿再读取')
                return value,mapping
            def appended(result):
                value,mapping=result
                for op in self.intents:
                    if 'members' in op['args']:op['args']['members']=[mapping.get(u,u) for u in op['args']['members']]
                self.resolutions={};self.prepared=None;self.preview_ready=False
                self.snapshot=value;self.catalog=Catalog(value);self.browse()
                self.render_drafts();self.persist_draft()
                added=len(value['books'])-len(old['books'])
                self.status.setText(f'已发现 {added} 本新增书籍，原草稿保留；请重新生成预览。' if added else '未发现新增书籍，原草稿保留。')
            self.device_job(append_books,appended,'读取新传入书籍',read_progress=True)
            return
        manager=self.gui.device_manager
        def work():
            store=connected_store(manager)
            snapshot=self.read_snapshot(store)
            receipts=store.receipts()
            messages=[]
            changed=[]
            if self.service:
                key=self.service.key(self.library_id,snapshot);saved=self.service.state.draft(key)
                if saved and saved.get('book_anchors'):
                    from .deferred import draft_book_bindings
                    mapping=draft_book_bindings(saved['book_anchors'],snapshot,store)
                    if mapping:
                        for op in saved['intents']:
                            if 'members' in op['args']:op['args']['members']=[mapping.get(u,u) for u in op['args']['members']]
                        saved['resolutions']={};self.service.state.save_draft(key,saved)
                _,messages=self.service.receive(self.library_id,snapshot,receipts)
                changed,notes=self.service.backfill_confirmed(self.library_db.new_api,self.library_id,snapshot)
                messages.extend(notes)
                repaired=self.service.state.reconcile_pending(self.service.key(self.library_id,snapshot),snapshot,getattr(store,'new_books',[]))
                if repaired:messages.append('已校正本地待识别书籍编号；书籍和收藏归属未修改。请重新预览。')
                from .retention import automatic_retention
                try:messages.append(automatic_retention(store,self.service.state,self.service.key(self.library_id,snapshot)))
                except Exception as e:messages.append('历史清理暂未完成，已保留恢复记录：'+str(e))
            return snapshot,receipts,Catalog(snapshot),messages,changed
        self.device_job(work,self.loaded,'KC++ 读取设备快照与回执',read_progress=True,automatic=automatic)

    def loaded(self, result):
        self.snapshot,receipts,self.catalog=result[:3]
        self._read_jobs=([self.service.state.job(j['job_id']) for j in self.service.state.job_summaries(self.service.key(self.library_id,self.snapshot))] if self.service else [])
        self.active=None; self.preview=None; self.issues=[]
        self.refresh_mode_summary();self.workflow_status.setText('当前进度：已读取设备快照 → 可整理草稿 → 预览 → 发送 → Kindle 执行 → 重连确认')
        self.status.setText(f'状态生成于 {self.snapshot["generated_utc"]}；{len(self.catalog.books)} 本，'
                            f'{len(self.catalog.collections)} 个收藏夹；已读取 {len(receipts)} 份回执。')
        waiting=sum(bid.startswith('kc-new-') for bid in self.catalog.books)
        if waiting:self.status.setText(self.status.text()+f'\n含 {waiting} 个文件尚未对应设备快照中的书籍（不代表刚刚传入）。可建立草稿；设备执行时仍需确认索引与格式支持。')
        self.refresh_pending();self.browse()
        if len(result)>3 and result[3]:self.status.setText(self.status.text()+'\n'+'\n'.join(result[3]))
        if len(result)>4 and result[4]:
            model=self.gui.library_view.model()
            if hasattr(model,'refresh_ids'):model.refresh_ids(result[4])
            else:model.refresh()

        summary=f'已读取设备：{len(self.catalog.books)} 本书，{len(self.catalog.collections)} 个收藏夹。';level='info'
        if self.service:
            from .usability import task_status
            summaries=self.service.state.job_summaries(self.service.key(self.library_id,self.snapshot))
            if summaries:
                state,next_step=task_status(self.service.state.job(summaries[0]['job_id']),self.service.state)
                self.status.setText(self.status.text()+'\n最近任务：'+state+'。下一步：'+next_step)
                self.workflow_status.setText('最近任务：'+state+' ｜ 下一步：'+next_step)
                level='success' if state in ('已完成（无需列回填）','执行及列值处理完成') else 'warning'
                summary=('上次任务已完成，可继续整理。' if level=='success' else state+'；'+next_step)
                if state=='已关闭（历史结果）':level='info';summary='已读取设备，可继续整理；历史任务结果保留在任务记录中。'
        if len(result)>3 and any(any(word in str(note) for word in ('失败','冲突','暂未','待处理','无法','待回填')) for note in result[3]):
            summary='已读取设备，有待处理提示，请查看详情。';level='warning'
        elif self.service:
            profile=self.service.profile(self.library_id,self.snapshot)
            if profile.get('sync_mode')=='column' and not profile.get('field'):
                summary='尚未选择同步列：请在“同步设置”选择专用书架列、原生标签，或仅 KC++ 整理。';level='warning'
        self.show_notice(self.status.text(),level,summary)
        if self.service:
            from .readiness import attention_jobs
            jobs=[self.service.state.job(s['job_id']) for s in self.service.state.job_summaries(self.service.key(self.library_id,self.snapshot))]
            count=len(attention_jobs(jobs,self.service.state))
            self.history_button.setText(f'任务与执行结果（待处理 {count}）' if count else '任务与执行结果')
            for button,index in self.navigation_buttons:
                if index is None:button.setText(f'任务记录 · {count}' if count else '任务记录')
            if count and level=='success':self.show_notice(self.status.text(),'warning',f'设备已读取，仍有 {count} 项任务需要处理；请查看任务记录。')
        if not getattr(self,'_last_scan_full',True):self.show_notice('已快速读取收藏状态；编辑发送前请勾选“扫描新增文件”并重新读取。','info')
        self.restore_saved_draft()
        selected=getattr(self,'_check_job_id',None)
        if selected:
            self._check_job_id=None
            QTimer.singleShot(0,lambda:self.task_history(selected))

    def refresh_pending(self):
        self.pending_ops=[]
        if not self.service or not self.snapshot:return
        summaries=self.service.state.job_summaries(self.service.key(self.library_id,self.snapshot))
        if summaries:
            from .usability import pending_operations
            self.pending_ops=pending_operations(self.service.state.job(summaries[0]['job_id']),self.snapshot)

    def draft_catalog(self):
        key=(id(self.catalog),tuple(o['intent_id'] for o in self.intents))
        if getattr(self,'_draft_catalog_key',None)==key:return self._draft_catalog
        cat=copy.copy(self.catalog)
        cat.collections={k:dict(v) for k,v in self.catalog.collections.items()}
        cat.members=copy.deepcopy(self.catalog.members)
        cat.names=copy.deepcopy(self.catalog.names)
        for op in self.intents:
            cid=op['collection_uuid'];kind=op['kind'];args=op['args']
            if kind=='create_collection':
                cat.collections[cid]=dict(uuid=cid,name=args['name'],complete=True,draft=True)
                cat.members[cid]=set();cat.names[args['name']].append(cid)
            elif kind=='rename_collection' and cid in cat.collections:cat.collections[cid]['name']=args['name']
            elif kind=='delete_collection':cat.collections.pop(cid,None)
            elif kind=='add_members':cat.members[cid].update(args['members'])
            elif kind=='remove_members':cat.members[cid].difference_update(args['members'])
        self._draft_catalog_key=key;self._draft_catalog=cat
        return cat

    def browse(self):
        if not self.catalog: return
        cat=self.draft_catalog()
        if self.active is not None and self.active not in cat.collections:self.active=None
        query=self.search.text().casefold().strip()
        all_books=self.view_mode.currentIndex()==1 and self.active is None
        if self.tabs.currentIndex()==0:self.heading.setText('所有书籍' if all_books else cat.collections[self.active]['name'] if self.active else '我的收藏夹')
        listing=all_books or self.active is not None
        self.folder_order.setVisible(not listing);self.book_order.setVisible(listing)
        self.search.setPlaceholderText('搜索书名或收藏夹归属' if listing else '搜索收藏夹名称')
        if self.active is None and not all_books:
            rows=[dict(id=c['uuid'],name=c['name'],count=len(cat.members[c['uuid']]),marker='待同步 · 新建' if c.get('draft') else '含未知关系' if not c['complete'] else '',tooltip=c['name']+'\nUUID: '+c['uuid'],cells=[c['name']+(' ['+c['uuid'][:8]+']' if len(cat.names[c['name']])>1 else '')+'\n\n'+str(len(cat.members[c['uuid']]))+' 本'+
                  (' · 新建草稿' if c.get('draft') else ' · 含未知关系' if not c['complete'] else '')],search=c['name'].casefold())
                  for c in cat.collections.values() if query in c['name'].casefold()]
            selected=self.selected_collection()
            mode=self.folder_order.currentIndex()
            for row in rows:
                cid=row['id'];before=self.catalog.members.get(cid,set());after=cat.members[cid]
                notes=[]
                if cid in self.catalog.collections and cat.collections[cid]['name']!=self.catalog.collections[cid]['name']:notes.append('待改名')
                if after-before:notes.append('待加入 '+str(len(after-before))+' 本')
                if before-after:notes.append('待移除 '+str(len(before-after))+' 本')
                if before!=after:row['summary']=f'{len(before)} → {len(after)} 本'
                if notes:
                    row['tooltip']+='\n尚未发送：'+'；'.join(notes)
                    row['marker']='待同步'+(' · 含未知关系' if not cat.collections[cid]['complete'] else '')
                if cid in self.catalog.collections and cat.collections[cid]['name']!=self.catalog.collections[cid]['name']:
                    row['tooltip']+='\n原名：'+self.catalog.collections[cid]['name']
            by_id={r['id']:r for r in rows}
            for op in self.intents:
                cid=op['collection_uuid']
                if op['kind']=='delete_collection' and cid not in by_id and cid in self.catalog.collections:
                    name=self.catalog.collections[cid]['name']
                    if query not in name.casefold():continue
                    row=dict(id=cid,name=name,count=len(self.catalog.members[cid]),marker='待同步 · 删除',pending_only=True,draft_deleted=True,
                             tooltip='待同步：删除收藏夹「'+name+'」，保留书籍文件。可在草稿侧栏撤销。',cells=[name+'\n待同步 · 删除'],search=name.casefold())
                    rows.append(row);by_id[cid]=row
            for op in self.pending_ops:
                cid=op['collection_uuid'];row=by_id.get(cid)
                if row is None and op['kind']=='create_collection' and cid not in cat.collections and query in op['args']['name'].casefold():
                    name=op['args']['name'];row=dict(id=cid,name=name,count=0,marker=op['label'],summary='尚未读取到',pending_only=True,tooltip=name,cells=[name+'\n'+op['label']],search=name.casefold())
                    rows.append(row);by_id[cid]=row
                if row is not None:
                    if op['label'] not in row['marker']:row['marker']=(row['marker']+' · ' if row['marker'] else '')+op['label']
                    row['tooltip']+='\n'+op['label']+'：'+LABELS[op['kind']]+(' → '+op['args']['name'] if op['kind']=='rename_collection' else '')
            if mode<2:rows.sort(key=lambda r:(r['search'],r['id']),reverse=mode==1)
            elif mode==2:rows.sort(key=lambda r:(-r['count'],r['search'],r['id']))
            self.folders.replace(rows); self.stack.setCurrentIndex(0)
            for i,row in enumerate(rows):
                if row['id']==selected:self.grid.setCurrentIndex(self.folders.index(i,0));break
        else:
            memberships={}
            changes={}
            for cid in {o['collection_uuid'] for o in self.intents}:
                before=self.catalog.members.get(cid,set())
                after=cat.members.get(cid,set()) if cid in cat.collections else set()
                name=cat.collections.get(cid,self.catalog.collections.get(cid,{})).get('name',cid)
                for bid in after-before:changes.setdefault(bid,[]).append('待加入「'+name+'」')
                for bid in before-after:changes.setdefault(bid,[]).append('待退出「'+name+'」')
                old_name=self.catalog.collections.get(cid,{}).get('name')
                if cid in cat.collections and old_name and old_name!=name:
                    for bid in after:changes.setdefault(bid,[]).append('待改名「'+old_name+'」→「'+name+'」')
            for op in self.pending_ops:
                cid=op['collection_uuid'];name=self.catalog.collections.get(cid,{}).get('name',op['args'].get('name',cid))
                for bid in op['args'].get('members',self.catalog.members.get(cid,())):
                    changes.setdefault(bid,[]).append(op['label']+'：'+LABELS[op['kind']]+'「'+name+'」')
            for cid,ids in cat.members.items():
                if cid in cat.collections:
                    for bid in ids:memberships.setdefault(bid,[]).append(cat.collections[cid]['name'])
            ids=list(cat.books) if all_books else list(cat.members[self.active])
            rows=[]
            draft_books={bid for op in self.intents for bid in op['args'].get('members',())}
            for bid in ids:
                book=cat.books.get(bid)
                if all_books and book and is_script(book):continue
                title=(book['title'] or bid) if book else '未知成员：'+str(bid)
                names='、'.join(memberships.get(bid,[])) or '未归类'
                if not all(word in (title+' '+names).casefold() for word in query.split()):continue
                details='；'.join(changes.get(bid,[]))
                if details and bid in draft_books:details='待同步：'+details
                from .readiness import book_status
                state,hint=book_status(book or dict(uuid=bid),getattr(self,'_read_jobs',[]))
                rows.append(dict(id=bid,cells=[title,names,state,details or '设备已读取状态'],tooltip=hint+'\n'+((book or {}).get('location') or '')))
            mode=self.book_order.currentIndex()
            if mode<2:rows.sort(key=lambda r:(r['cells'][0].casefold(),r['id']),reverse=mode==1)
            else:rows.sort(key=lambda r:r['id'])
            self.books.replace(rows); self.stack.setCurrentIndex(1)
        self.update_edit_buttons()

    def change_view_mode(self,*args):
        self.active=None;self.search.clear();self.browse()

    def open_folder(self,index):
        if self.folders.rows[index.row()].get('pending_only'):
            text='此收藏夹待删除，可在草稿侧栏撤销；设备尚未修改。' if self.folders.rows[index.row()].get('draft_deleted') else '此收藏夹正在等待设备结果；请在“任务与执行结果”中查看进度。'
            self.show_notice(text,'warning');return
        self.active=self.folders.rows[index.row()]['id']; self.search.clear(); self.browse()
    def show_collections(self):
        self.tabs.setCurrentIndex(0);self.go_back()
    def go_back(self): self.active=None;self.view_mode.setCurrentIndex(0); self.search.clear(); self.browse()
    def selected_collection(self):
        if self.active: return self.active
        if self.view_mode.currentIndex()==1:return None
        index=self.grid.currentIndex()
        if index.isValid() and index.row()<len(self.folders.rows) and self.folders.rows[index.row()].get('pending_only'):return None
        return self.folders.rows[index.row()]['id'] if index.isValid() else None
    def selected_books(self):
        return [self.books.rows[i.row()]['id'] for i in self.book_view.selectionModel().selectedRows()]
    def queue(self, values):
        if not self.snapshot: return
        # A batch import/recovery is not a single user action. Only pair the
        # add/remove dependency of a move, including restored moves.
        creates={v['collection_uuid']:v['intent_id'] for v in self.intents if v['kind']=='create_collection'}
        for v in values:
            if v['kind']=='create_collection':creates[v['collection_uuid']]=v['intent_id']
            elif v['collection_uuid'] in creates and creates[v['collection_uuid']] not in v['depends_on']:
                v['depends_on'].append(creates[v['collection_uuid']])
        known={v['intent_id']:v for v in self.intents}
        for v in values:
            iid=v['intent_id'];self.draft_groups[iid]=iid
            if v['kind']=='remove_members':
                for dep in v.get('depends_on',[]):
                    predecessor=known.get(dep)
                    if predecessor and predecessor['kind']=='add_members' and predecessor['collection_uuid']!=v['collection_uuid']:
                        self.draft_groups[iid]=self.draft_groups.get(dep,dep)
            known[iid]=v
        self.intents.extend(values); self.scope.update(v['collection_uuid'] for v in values if v['kind']!='create_collection')
        self.preview=None; self.plan_model.replace([])
        self.prepared=None
        self.status.setText(f'已记录 {len(self.intents)} 项草稿；设备与 Calibre 均未修改。')
        self.workflow_status.setText(f'当前进度：草稿 {len(self.intents)} 项 → 请生成修改预览 → 发送 → Kindle 执行 → 重连确认')
        self.render_drafts()
        self.persist_draft()
        self.browse()
        self.update_buttons()
    def render_drafts(self):
        was_empty=self.draft_model.rowCount()==0
        names={c['uuid']:c['name'] for c in (self.snapshot or {}).get('collections',[])}
        books={b['uuid']:b['title'] or b['uuid'] for b in (self.snapshot or {}).get('books',[])}
        rows=[]
        for op in self.intents:
            cid=op['collection_uuid'];args=op['args'];name=names.get(cid,cid)
            if op['kind']=='create_collection':name=args['name'];names[cid]=name
            members=args.get('members',[])
            titles=[books.get(b,b) for b in members]
            detail=(str(len(members))+' 本：'+'、'.join(titles[:3])+('…' if len(titles)>3 else '')) if members else args.get('name','删除收藏夹，保留书籍文件')
            if op['kind']=='rename_collection':detail=name+' → '+args['name'];names[cid]=args['name']
            rows.append(dict(id=op['intent_id'],cells=[LABELS[op['kind']],name,detail],tooltip=detail+'\n'+'\n'.join(titles)))
        self.draft_model.replace(rows)
        self.draft_toggle.setText(f'草稿（{len(rows)}）')
        if not rows:self.draft_toggle.setChecked(False)
    def undo_drafts(self,checked=False,last=False):
        if self.busy or not self.intents:return
        ids={self.intents[-1]['intent_id']} if last else {self.draft_model.rows[i.row()]['id'] for i in self.draft_view.selectionModel().selectedRows()}
        if not ids:self.error('请先选择要撤销的草稿。');return
        # Keep a move atomic; also remove dependents of an undone create or edit.
        while True:
            groups={self.draft_groups.get(i,i) for i in ids}
            created={o['collection_uuid'] for o in self.intents if o['intent_id'] in ids and o['kind']=='create_collection'}
            expanded=ids|{o['intent_id'] for o in self.intents if self.draft_groups.get(o['intent_id'],o['intent_id']) in groups or set(o.get('depends_on',[]))&ids or o['collection_uuid'] in created}
            if expanded==ids:break
            ids=expanded
        self.intents=[o for o in self.intents if o['intent_id'] not in ids]
        self.draft_groups={k:v for k,v in self.draft_groups.items() if k not in ids}
        self.scope={o['collection_uuid'] for o in self.intents if o['kind']!='create_collection'}
        self.resolutions={k:v for k,v in self.resolutions.items() if k.split('/')[0] not in ids}
        self.preview=None;self.prepared=None;self.preview_ready=False
        self.plan_model.replace([]);self.effect_model.replace([]);self.blockers.clear()
        self.render_drafts();self.persist_draft();self.browse();self.update_buttons()
        self.status.setText(f'已撤销 {len(ids)} 项草稿；请重新生成修改预览。')
        self.workflow_status.setText(f'当前进度：剩余草稿 {len(self.intents)} 项 → 请重新预览')
    def clear_draft(self):
        self.resolutions={}
        self.intents=[]; self.scope=set(); self.issues=[]; self.preview=None
        self.draft_groups={};self.render_drafts();self.persist_draft()
        self.prepared=None
        self.plan_model.replace([]); self.blockers.clear(); self.update_buttons()
        self.load()
    def create_collection(self):
        name,ok=QInputDialog.getText(self,'新建收藏夹','名称')
        if ok and name.strip():
            cid=str(uuid4());self.queue([intent('create_collection',cid,dict(name=name.strip()))])
            self.active=cid;self.search.clear();self.tabs.setCurrentIndex(0);self.browse()
            self.status.setText('已打开新建收藏夹草稿。点击“加入书籍”搜索加书，完成后统一预览发送。')
    def rename_collection(self):
        cid=self.selected_collection()
        if not cid: self.error('请先选择收藏夹。'); return
        name,ok=QInputDialog.getText(self,'原地改名','新名称',text=self.draft_catalog().collections[cid]['name'])
        if ok and name.strip(): self.queue([intent('rename_collection',cid,dict(name=name.strip()))])
    def delete_collection(self):
        cid=self.selected_collection()
        if not cid: self.error('请先选择收藏夹。'); return
        self.queue([intent('delete_collection',cid)])
    def remove_books(self):
        selected=self.selected_books()
        source=self.active or self.pick_source(selected)
        if source and selected:self.queue([intent('remove_members',source,dict(members=[b for b in selected if b in self.draft_catalog().members[source]]))])
    def move_books(self):
        if not self.selected_books():self.error('请先选择书籍。');return
        source=self.active or self.pick_source(self.selected_books())
        if not source:return
        selected=[b for b in self.selected_books() if b in self.draft_catalog().members[source] and not is_script(self.catalog.books.get(b,{}))]
        if not selected:self.error('所选项目均为 .sh 脚本，不参与移动书籍。');return
        options=[(c['uuid'],c['name']+' ['+c['uuid'][:8]+']') for c in self.draft_catalog().collections.values() if c['uuid']!=source]
        if not options:self.error('没有其他目标收藏夹，请先新建收藏夹。');return
        label,ok=QInputDialog.getItem(self,'移动书籍','目标收藏夹',[v for _,v in options],0,False)
        if ok: self.queue(move(selected,source,next(k for k,v in options if v==label)))
    def pick_source(self,selected):
        if not selected:self.error('请先选择书籍。');return None
        cat=self.draft_catalog();options=[(cid,c['name']+' ['+cid[:8]+']') for cid,c in cat.collections.items() if set(selected)&cat.members[cid]]
        if not options:self.error('所选书籍尚未归类。');return None
        label,ok=QInputDialog.getItem(self,'选择来源收藏夹','只移除所选来源中的关系；其他归属保留。',[v for _,v in options],0,False)
        return next(k for k,v in options if v==label) if ok else None

    def add_books(self):
        cid=self.selected_collection()
        if self.view_mode.currentIndex()==1 and not self.active:
            selected=self.selected_books()
            if not selected:self.error('请先在书籍列表选择要加入的书。');return
            options=[(k,c['name']+' ['+k[:8]+']') for k,c in self.draft_catalog().collections.items()]
            if not options:self.error('请先新建收藏夹。');return
            label,ok=QInputDialog.getItem(self,'加入收藏夹','选择目标收藏夹；保留所有原归属。',[v for _,v in options],0,False)
            if ok:self.queue([intent('add_members',next(k for k,v in options if v==label),dict(members=selected))])
            return
        if not cid: self.error('请先选择收藏夹。'); return
        excluded=set(self.draft_catalog().members[cid])
        for draft in self.intents:
            if draft['collection_uuid']==cid and draft['kind']=='add_members':
                excluded.update(draft['args']['members'])
        candidates=[b for b in self.snapshot['books'] if b['uuid'] not in excluded and not is_script(b)]
        if not candidates:self.error('没有可加入的设备书籍：本收藏夹已有或草稿中已选择这些书。');return
        order=None
        try:
            model=self.gui.library_view.model()
            ids=[model.id(row) for row in range(model.rowCount())]
            uuids=self.library_db.new_api.all_field_for('uuid',ids)
            order=[uuids[i] for i in ids if uuids.get(i)]
        except (AttributeError,KeyError,TypeError):
            pass
        cat=self.draft_catalog()
        memberships={}
        for collection,members in cat.members.items():
            if collection not in cat.collections:continue
            for bid in members:memberships.setdefault(bid,[]).append(cat.collections[collection]['name'])
        candidates=[dict(b,collection_names='、'.join(memberships.get(b['uuid'],[])),collection_count=len(memberships.get(b['uuid'],[]))) for b in candidates]
        dialog=BookPicker(candidates,self,calibre_order=order)
        if dialog.exec():
            selected=sorted(dialog.picked)
            if selected: self.queue([intent('add_members',cid,dict(members=selected))])

    def calculate_preview(self):
        if not self.snapshot: return
        snapshot,values,scope=self.snapshot,copy.deepcopy(self.intents),set(self.scope)
        library=self.library_id
        if self.service:
            api=self.library_db.new_api
            def done(prepared):
                previous=getattr(self,'workflow_issues',[])
                self.issues=[x for x in self.issues if x not in previous]
                self.workflow_issues=list(prepared['issues'])
                self.prepared=prepared;self.issues.extend(self.workflow_issues);self.show_preview(prepared['plan'])
                if prepared['warnings']:self.blockers.append('\n'.join(map(str,prepared['warnings'])))
            resolutions=dict(self.resolutions)
            self.background(lambda:self.service.preview(api,library,snapshot,values,resolutions),done)
        else:self.background(lambda:plan(snapshot,values,scope,library),self.show_preview)
    def resolve_conflicts(self):
        if self.busy:return
        prepared=getattr(self,'prepared',None)
        conflicts=prepared.get('conflicts',[]) if prepared else []
        if not conflicts:self.error('请先生成预览；当前没有可处理的加入 / 移除冲突。');return
        choices=['以手动移除为准，确认执行后记住例外','保留来源要求，取消这条移除','暂不处理，保持阻断']
        for c in conflicts:
            name=self.catalog.collections.get(c['collection_uuid'],{}).get('name',c['collection_uuid'])
            title=self.catalog.books.get(c['book_uuid'],{}).get('title') or c['book_uuid']
            present=c['book_uuid'] in self.catalog.members.get(c['collection_uuid'],set())
            state='设备快照：这本书已在此收藏夹。' if present else '设备快照：这本书不在此收藏夹。'
            value,ok=QInputDialog.getItem(self,'同一本书的两项要求不一致',f'收藏夹：{name}\n书籍：{title}\n{state}\n列值或分类规则：要求加入。手动草稿：要求移除。\n以手动为准：本次移除，设备确认后记住例外。\n以来源为准：本次不执行这项移除。其他收藏夹归属保留。\n选择只改变预览，仍需发送并在 Kindle 执行。',choices,2,False)
            if not ok:continue
            if value==choices[2]:self.resolutions.pop(c['key'],None)
            else:self.resolutions[c['key']]='manual' if value==choices[0] else 'source'
        self.persist_draft()
        self.calculate_preview()
    def show_preview(self,value):
        self.preview=value; p=value.data
        self.preview_ready=not p['blockers']
        self.render_plan()
        messages=[self.catalog.collections.get(x['collection_uuid'],{}).get('name',x['collection_uuid'])+'：'+x['message'] for x in p['blockers']]+[str(x) for x in self.issues]
        self.blockers.setPlainText('\n'.join(messages) if messages else '预览可发送；尚未在 Kindle 执行。')
        self.blockers.setProperty('blocked',bool(messages));self.blockers.style().unpolish(self.blockers);self.blockers.style().polish(self.blockers)
        self.tabs.setCurrentIndex(1)
        changed=sum(o['kind']!='verify_state' for o in p['operations'])
        self.show_notice(f'变动 {changed} 项；只读核验 {len(p["operations"])-changed} 项；阻断 {len(messages)} 项。'+('请先处理下方问题，尚未发送。' if messages else '预览检查通过，尚未在 Kindle 执行。'),'warning' if messages else 'success')
        self.workflow_status.setText('当前进度：预览需处理问题' if messages else '当前进度：预览完成 → 可发送 → Kindle 执行 → 重连确认')
    def render_plan(self,*args):
        if not self.preview:return
        self.plan_ops=[o for o in self.preview.data['operations'] if not self.changes_only.isChecked() or o['kind']!='verify_state']
        from .usability import plan_rows
        visible={o['op_id'] for o in self.plan_ops}
        self.plan_model.replace([r for r in plan_rows(self.preview.data['operations'],self.catalog,LABELS) if r['op_id'] in visible])
        self.effect_model.replace([])
        if self.plan_ops:self.plan_view.setCurrentIndex(self.plan_model.index(0,0))
    def show_effect(self,index,previous=None):
        if not index.isValid() or index.row()>=len(self.plan_ops) or not self.catalog:
            self.effect_model.replace([]);return
        op=self.plan_ops[index.row()]
        members=op['args'].get('members',sorted(self.catalog.members[op['collection_uuid']],key=lambda v:v or ''))
        self.effect_label.setText(f'涉及 {len(members)} 个设备副本（同书不同格式分别计数）；最多展示前 500 个。保存报告可保留完整清单。')
        self.effect_model.replace([dict(cells=[self.catalog.books.get(bid,{}).get('title') or '未知书籍',self.catalog.books.get(bid,{}).get('location') or '无本地路径',str(bid)],tooltip=(self.catalog.books.get(bid,{}).get('location') or '')+'\n设备编号：'+str(bid)) for bid in members[:500]])
    def import_legacy(self):
        manager=self.gui.device_manager
        def work():
            store=connected_store(manager)
            candidates=[store.mount/'system/collections.json',store.mount/'collections.json',store.mount/'collection.json',
                store.mount/'documents/calibre_collections_sync/collections.json']
            existing=[p for p in candidates if p.is_file()]
            if len(existing)!=1:raise Invalid('未找到唯一的旧收藏文件；如需导入外部备份，请在设置页选择“外部旧文件”。')
            return import_collections(read(existing[0]),store.snapshot())
        def done(result):
            self.issues.extend(result['issues']);self.queue(result['intents']);self.scope.update(result['scope'])
            QTimer.singleShot(0,self.calculate_preview)
        self.device_job(work,done,'KC++ 自动导入设备旧收藏文件')
    def import_external_legacy(self):
        path,_=QFileDialog.getOpenFileName(self,'导入外部旧收藏夹文件','','JSON / full (*.json *.full *.backup);;所有文件 (*)')
        if not path: return
        snapshot=self.snapshot
        def done(result):
            self.issues.extend(result['issues']); self.queue(result['intents']); self.scope.update(result['scope'])
            # Background callback finishes before starting the next job.
            QTimer.singleShot(0,self.calculate_preview)
        self.background(lambda:import_collections(read(path),snapshot),done)
    def send_plan(self):
        if not self.preview or not self.preview_ready or self.issues: return
        preview=self.preview; inputs_digest=digest(self.intents)
        if not self.same_library() or self.library_id!=preview.data['library_uuid']:
            self.error('当前书库已改变，请重新预览。'); return
        from .mtp import execution_hint
        next_step=execution_hint(self.gui.device_manager)
        if QMessageBox.question(self,'发送已预览任务','发送后：'+next_step+'\n是否发送当前预览？')!=QMessageBox.StandardButton.Yes: return
        manager=self.gui.device_manager
        api=getattr(self.library_db,'new_api',None)
        prepared=self.prepared
        manual=copy.deepcopy(self.intents)
        resolutions=dict(self.resolutions)
        def work():
            store=connected_store(manager,preview.data['device'])
            if self.service and prepared:
                return self.service.submit(api,preview.data['library_uuid'],store,prepared,manual,resolutions)
            req=request(preview,store.snapshot(),inputs_digest)
            store.send(req)
            return req['job_id']
        def sent(job):
            self.resolutions={}
            self.intents=[]; self.scope=set(); self.preview=None
            self.draft_groups={};self.render_drafts()
            self.prepared=None
            self.pending_ops=[dict(o,label='等待设备执行') for o in preview.data['operations'] if o['kind']!='verify_state']
            self.persist_draft();self.browse()
            self.show_notice('任务已发送：'+job+'\n'+next_step,'warning','等待设备执行：'+next_step)
            self.workflow_status.setText('当前进度：已发送 → 等待 Kindle 执行 → 重连确认；发送成功不代表执行成功')
        self.device_job(work,sent,'KC++ 自动发送已预览任务')
    def save_report(self):
        if not self.preview: return
        path,_=QFileDialog.getSaveFileName(self,'保存诊断预览（不是日常传输步骤）','KC++-preview.json','JSON (*.json)')
        if path:
            value=self.preview.data;catalog=self.catalog;issues=list(self.issues)
            def write_report():
                affected={o['op_id']:[dict(uuid=bid,title=catalog.books.get(bid,{}).get('title')) for bid in o['args'].get('members',sorted(catalog.members[o['collection_uuid']],key=lambda v:v or ''))] for o in value['operations']}
                Path(path).write_bytes(canonical(dict(plan=value,import_issues=issues,affected_books=affected)))
            self.background(write_report,lambda _:self.show_notice('预览和完整影响清单已保存。','success'),cancelable=False)
    def persist_draft(self):
        if not self.service or not self.snapshot:return
        key=self.service.key(self.library_id,self.snapshot)
        # Staging a task clears its draft transactionally. Do not resurrect it on close.
        if self.intents and any(j.get('inputs',{}).get('manual')==self.intents for j in self.service.state.jobs(key) if j['status']=='staged'):return
        self.service.state.save_draft(key,dict(intents=self.intents,scope=sorted(self.scope),resolutions=self.resolutions,
            issues=self.issues,snapshot_digest=digest(self.snapshot),book_anchors=dict(device=self.snapshot['device'],mapping=self.snapshot['mapping'],books=[b for b in self.snapshot['books'] if b['uuid'].startswith('kc-new-')])) )

    def restore_saved_draft(self):
        if not self.service or not self.snapshot:return
        key=self.service.key(self.library_id,self.snapshot)
        saved=self.service.state.draft(key)
        if not saved or not saved.get('intents'):return
        self.resolutions=saved.get('resolutions',{});self.issues=saved.get('issues',[])
        self.queue(copy.deepcopy(saved['intents']));self.scope.update(saved.get('scope',[]))
        changed=saved.get('snapshot_digest')!=digest(self.snapshot)
        self.preview=None;self.prepared=None;self.preview_ready=False
        self.status.setText('已恢复自动保存的草稿。'+('设备快照已变化，' if changed else '')+'请重新预览，尚未发送。')
        self.workflow_status.setText(f'当前进度：已恢复草稿 {len(self.intents)} 项 → 请重新预览；尚未发送')

    def reject(self):
        if self.busy: self.status.setText('请等待当前操作完成后关闭。'); return
        self.persist_draft()
        super().reject()
