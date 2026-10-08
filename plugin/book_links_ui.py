"""Optional, explicit device-to-library mapping UI."""
from qt.core import (QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,QTableWidget,QTableWidgetItem,
                     QPushButton,QAbstractItemView,QHeaderView,Qt,QComboBox)
from .theme import InkDialog,InkMessageBox as QMessageBox,InkInputDialog as QInputDialog
from .protocol import Invalid
from .metadata import read_metadata,mappings
from .deferred import pending
from . import book_links

class BookLinksDialog(InkDialog):
    def __init__(self,host):
        super().__init__(host);self.host=host;self.api=host.library_db.new_api;self.snapshot=host.snapshot
        self.setWindowTitle('设备书籍与书库记录');self.resize(1080,730)
        layout=QVBoxLayout(self);title=QLabel('设备书籍与书库记录');title.setObjectName('heading');layout.addWidget(title)
        note=QLabel('可选：建立对应关系后，设备独有书籍也可用当前书库的同步列管理。仅在 KC++ 整理无需绑定。')
        note.setWordWrap(True);layout.addWidget(note)
        panels=QHBoxLayout();layout.addLayout(panels,1)
        self.device_search,self.device_table=self.panel(panels,'Kindle 书籍','搜索设备书名',['设备书籍','书库关联'])
        self.library_search,self.library_table=self.panel(panels,'当前 Calibre 书库','搜索书名或作者',['书名','作者','编号'])
        self.notice=QLabel('左侧选择设备书籍，右侧选择对应记录，再点击绑定。不会按同名自动匹配。')
        self.notice.setWordWrap(True);self.notice.setObjectName('status');layout.addWidget(self.notice)
        repair_bar=QHBoxLayout();layout.addLayout(repair_bar)
        self.stale=QComboBox();repair_bar.addWidget(self.stale,1)
        self.repair_button=QPushButton('改绑到左侧所选书籍');repair_bar.addWidget(self.repair_button)
        self.forget_button=QPushButton('解除此失效绑定');repair_bar.addWidget(self.forget_button)
        self.repair_button.clicked.connect(self.repair_selected);self.forget_button.clicked.connect(self.forget_stale)
        actions=QHBoxLayout();layout.addLayout(actions)
        self.bind_button=QPushButton('绑定所选记录');self.bind_button.setObjectName('primary')
        self.create_button=QPushButton('创建仅元数据记录');self.unlink_button=QPushButton('解除手动绑定')
        for button,handler in [(self.bind_button,self.bind_selected),(self.create_button,self.create_selected),(self.unlink_button,self.unlink_selected)]:
            actions.addWidget(button);button.clicked.connect(handler)
        close=QPushButton('关闭');actions.addWidget(close);close.clicked.connect(self.accept)
        self.create_button.setToolTip('在当前书库创建没有电子书文件的记录；不复制 Kindle 文件，不是电子书备份。')
        self.unlink_button.setToolTip('只解除手动对应关系，保留书库记录、列值、设备文件和收藏归属。')
        self.device_search.textChanged.connect(self.filter_tables);self.library_search.textChanged.connect(self.filter_tables)
        self.device_table.itemSelectionChanged.connect(self.update_actions);self.library_table.itemSelectionChanged.connect(self.update_actions)
        self.reload()

    def panel(self,panels,title,placeholder,headers):
        box=QVBoxLayout();panels.addLayout(box,1);box.addWidget(QLabel(title))
        search=QLineEdit();search.setPlaceholderText(placeholder);box.addWidget(search)
        table=QTableWidget(0,len(headers));table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        box.addWidget(table,1);return search,table

    def reload(self):
        self.rows=read_metadata(self.api,[])['rows'];mapped=mappings(self.snapshot,self.rows)
        row_by_uuid={r['uuid']:r for r in self.rows}
        self.associated={bid:row_by_uuid[uid]['title'] for uid,ids in mapped.items() for bid in ids}
        self.saved=book_links.links(self.api).get(book_links.device_key(self.snapshot),{})
        self.stale.clear()
        for row in book_links.link_report(self.api,self.snapshot):
            if row['reason']:self.stale.addItem(row['title']+' · '+row['reason'],row['uuid'])
        for widget in (self.stale,self.repair_button,self.forget_button):widget.setVisible(self.stale.count()>0)
        self.books=sorted(self.snapshot['books'],key=lambda b:(b['uuid'] in self.associated,b['title'] or ''))
        self.device_table.setRowCount(len(self.books));self.library_table.setRowCount(len(self.rows))
        for i,b in enumerate(self.books):
            status=('待 Kindle 索引，可先整理' if pending(b['uuid']) else '未关联')
            if b['uuid'] in self.associated:status=('手动 · ' if b['uuid'] in self.saved else '自动 · ')+self.associated[b['uuid']]
            for col,value in enumerate([b['title'] or b['uuid'],status]):
                item=QTableWidgetItem(value);item.setToolTip(value+'\n'+(b['location'] or '无本地文件路径'));self.device_table.setItem(i,col,item)
        for i,r in enumerate(self.rows):
            for col,value in enumerate([r['title'],'、'.join(r['fields']['authors']),str(r['id'])]):
                self.library_table.setItem(i,col,QTableWidgetItem(value))
        self.device_table.clearSelection();self.library_table.clearSelection();self.filter_tables();self.update_actions()

    def filter_tables(self,*args):
        for search,table in [(self.device_search,self.device_table),(self.library_search,self.library_table)]:
            words=search.text().casefold().split()
            for i in range(table.rowCount()):
                text=' '.join(table.item(i,c).text() for c in range(table.columnCount()) if table.item(i,c)).casefold()
                table.setRowHidden(i,not all(w in text for w in words))
        self.update_actions()

    def selection(self,table,rows):
        selected=table.selectionModel().selectedRows()
        return rows[selected[0].row()] if selected and not table.isRowHidden(selected[0].row()) else None

    def update_actions(self):
        book=self.selection(self.device_table,getattr(self,'books',[]));record=self.selection(self.library_table,getattr(self,'rows',[]))
        ready=bool(book and not pending(book['uuid']))
        self.bind_button.setEnabled(ready and record is not None)
        self.create_button.setEnabled(ready and book['uuid'] not in self.associated)
        self.unlink_button.setEnabled(bool(book and book['uuid'] in self.saved))
        self.repair_button.setEnabled(ready and self.stale.count()>0)

    def check_context(self):
        host=self.host
        if host.busy or not host.same_library() or host.snapshot is not self.snapshot:raise Invalid('书库或设备状态已改变，请重新打开此窗口。')
        if getattr(host.gui,'must_restart_before_config',False):raise Invalid('请先重启 Calibre，让新建列生效。')
        key=host.service.key(host.library_id,self.snapshot)
        for summary in host.service.state.job_summaries(key):
            if summary['status'] not in ('complete','closed') or host.service.state.column_pending(summary['job_id']):
                raise Invalid('请先在任务记录中处理未完成任务或待回填结果，再修改书库绑定。')
            job=host.service.state.job(summary['job_id'])
            if job.get('column_field') and job.get('result'):
                confirmed={r['op_id'] for r in job['result']['operations'] if r['status']=='confirmed'}
                changes={o['op_id'] for o in job['request']['operations'] if o['kind']!='verify_state'}
                if (confirmed & changes)-host.service.state.baseline_done(summary['job_id']):
                    raise Invalid('还有已确认结果尚未回填，请先处理后再修改书库绑定。')

    def run(self,action,message):
        try:
            self.check_context();action()
            self.host.preview=None;self.host.prepared=None;self.host.preview_ready=False
            self.host.plan_model.replace([]);self.host.update_buttons()
            self.reload();self.notice.setText(message+' 请选择同步列，按需预览导入现有归属，再生成修改预览。')
        except Exception as exc:self.notice.setText(str(exc))

    def bind_selected(self):
        b=self.selection(self.device_table,self.books);r=self.selection(self.library_table,self.rows)
        if not b or not r:return
        if QMessageBox.question(self,'确认对应书籍',f'设备：{b["title"]}\n书库：{r["title"]}（编号 {r["id"]}）\n请确认是同一本书。绑定本身不会写入列值或修改收藏夹。')!=QMessageBox.StandardButton.Yes:return
        self.run(lambda:book_links.bind(self.api,self.snapshot,b['uuid'],r['id']),'已保存绑定。')

    def create_selected(self):
        b=self.selection(self.device_table,self.books)
        if not b:return
        title,ok=QInputDialog.getText(self,'创建仅元数据记录','书名（不复制电子书文件）',text=b['title'] or '')
        if not ok:return
        authors,ok=QInputDialog.getText(self,'创建仅元数据记录','作者（可留空；多位作者用 & 分隔）')
        if not ok:return
        def create():
            book_links.create_record(self.api,self.snapshot,b['uuid'],title,[a.strip() for a in authors.split('&') if a.strip()])
            self.host.gui.library_view.model().refresh()
        self.run(create,'已创建并绑定仅元数据记录；不含电子书文件。')

    def unlink_selected(self):
        b=self.selection(self.device_table,self.books)
        if not b:return
        if QMessageBox.question(self,'解除手动绑定','仅解除对应关系；保留书库记录、列值及 Kindle 收藏归属。继续？')!=QMessageBox.StandardButton.Yes:return
        self.run(lambda:book_links.unlink(self.api,self.snapshot,b['uuid']),'已解除手动绑定。')

    def repair_selected(self):
        book=self.selection(self.device_table,self.books);old=self.stale.currentData()
        if not book or not old:return
        if QMessageBox.question(self,'确认修复绑定',f'原记录：{self.stale.currentText()}\n新设备书：{book["title"]}\n请确认是同一本书；不会自动移动收藏归属或修改列值。')!=QMessageBox.StandardButton.Yes:return
        self.run(lambda:book_links.repair(self.api,self.snapshot,old,book['uuid']),'已修复绑定。')

    def forget_stale(self):
        old=self.stale.currentData()
        if not old:return
        if QMessageBox.question(self,'解除失效绑定','保留书库记录、列值和设备收藏；只移除这条失效对应关系。继续？')!=QMessageBox.StandardButton.Yes:return
        self.run(lambda:book_links.unlink(self.api,self.snapshot,old),'已解除失效绑定。')
