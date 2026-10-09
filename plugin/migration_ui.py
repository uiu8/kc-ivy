"""Backup, sharing and target-bound recovery in one modal workspace."""
from copy import deepcopy
from pathlib import Path
from uuid import uuid4
from qt.core import (Qt,QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QComboBox,
    QTabWidget,QLineEdit,QAbstractItemView,QTimer,QThreadPool,QDialogButtonBox)
from .theme import InkDialog as QDialog,InkFileDialog as QFileDialog,InkInputDialog as QInputDialog,InkMessageBox as QMessageBox
from .protocol import Invalid,canonical
from .transport import read,atomic_write,connected_store
from .migration import export_snapshot,validate_backup,new_session,bind_defaults,overview,progress,MigrationService
from .ui import Model,table,BookSearchProxy,Work,LABELS
from .shelf_picker import ShelfPicker,snapshot_shelves,calibre_shelves


class Choices(Model):
    def __init__(self,headers,parent=None):super().__init__(headers,parent);self.selected=set()
    def flags(self,index):
        flags=super().flags(index)
        return flags|Qt.ItemFlag.ItemIsUserCheckable if index.column()==0 else flags
    def data(self,index,role=Qt.ItemDataRole.DisplayRole):
        if index.isValid() and index.column()==0 and role==Qt.ItemDataRole.CheckStateRole:
            return Qt.CheckState.Checked if self.rows[index.row()]['id'] in self.selected else Qt.CheckState.Unchecked
        return super().data(index,role)
    def setData(self,index,value,role):
        if role!=Qt.ItemDataRole.CheckStateRole:return False
        ident=self.rows[index.row()]['id']
        if value==Qt.CheckState.Checked or value==Qt.CheckState.Checked.value:self.selected.add(ident)
        else:self.selected.discard(ident)
        self.dataChanged.emit(index,index,[role]);return True


def searchable(layout,model):
    edit=QLineEdit();edit.setClearButtonEnabled(True);edit.setPlaceholderText('搜索，空格分隔多个关键词');layout.addWidget(edit)
    proxy=BookSearchProxy(edit);proxy.setSourceModel(model);proxy.set_terms('')
    timer=QTimer(edit);timer.setSingleShot(True);timer.setInterval(180)
    edit.textChanged.connect(lambda:timer.start());timer.timeout.connect(lambda:proxy.set_terms(edit.text()))
    view=table(proxy);view.setSortingEnabled(True);view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection);layout.addWidget(view,1)
    return view


def choose(parent,title,rows,multiple=False):
    d=QDialog(parent);d.setWindowTitle(title);d.resize(850,560);layout=QVBoxLayout(d)
    model=Model(['名称','详情']);model.replace(rows);view=searchable(layout,model)
    if not multiple:view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(d.accept);buttons.rejected.connect(d.reject);layout.addWidget(buttons)
    if not d.exec():return None
    return [i.data(Qt.ItemDataRole.UserRole)['id'] for i in view.selectionModel().selectedRows()]


class MigrationCenter(QDialog):
    def __init__(self,host,ident=None):
        super().__init__(host);self.host=host;self.service=host.service;self.state=self.service.state
        self.library=str(host.gui.current_db.library_id);self.api=host.gui.current_db.new_api
        self.session=None;self.manifest=None;self.share_path=None;self.reuse={};self.title_counts={};self.busy=False;self.work=None;self.share_field=None
        self.setWindowTitle('kc-ivy 书架备份、迁移与分享');self.resize(1100,760)
        layout=QVBoxLayout(self)
        experimental=QLabel('迁移与分享为实验功能：模拟与临时 Calibre 书库检查已完成，两台真机迁移及大容量分享仍需用户自行验收。');experimental.setWordWrap(True);layout.addWidget(experimental)
        self.notice=QLabel('当前书库：'+str(getattr(host.gui.current_db,'library_path',self.library)));self.notice.setWordWrap(True);layout.addWidget(self.notice)
        self.tabs=QTabWidget();layout.addWidget(self.tabs,1)
        backup=QWidget();bl=QVBoxLayout(backup);self.tabs.addTab(backup,'书架备份')
        bl.addWidget(QLabel('来源：Kindle 已读取状态。只备份收藏关系，不含电子书；与“书库分享”的选书相互独立。'))
        self.sources=QComboBox();bl.addWidget(self.sources);self.snapshots=[]
        if host.snapshot:self.snapshots.append(host.snapshot);self.sources.addItem('当前设备 · '+host.snapshot['generated_utc'])
        for saved in self.state.workspaces('snapshot',self.library):
            s=saved['snapshot']
            if any(old['snapshot_id']==s['snapshot_id'] and old['device']==s['device'] for old in self.snapshots):continue
            self.snapshots.append(s);self.sources.addItem('已保存设备状态 · '+s['firmware']+' · '+s['generated_utc'])
        self.backup_picker=ShelfPicker(self);bl.addWidget(self.backup_picker,1)
        self.button(bl,'保存收藏关系备份（不含书籍文件）',self.save_backup,'保存勾选收藏夹及架内勾选的成员；同一本书在不同收藏夹中分别选择。')
        self.sources.currentIndexChanged.connect(self.backup_source);self.backup_source()
        restore=QWidget();rl=QVBoxLayout(restore);self.tabs.addTab(restore,'恢复到此设备')
        bar=QHBoxLayout();rl.addLayout(bar)
        self.button(bar,'打开书架备份',self.open_backup)
        self.button(bar,'保存选择',self.save_choices)
        self.button(bar,'选择收藏夹目标',self.select_target)
        self.shelf_model=Choices(['旧收藏夹','目标名称','处理方式','待加入 / 已有','待处理'])
        self.shelf_view=searchable(rl,self.shelf_model)
        self.book_model=Model(['书籍','匹配状态','目标副本数']);self.book_view=searchable(rl,self.book_model)
        tools=QHBoxLayout();rl.addLayout(tools)
        self.button(tools,'配对所选书籍',self.pair_book);self.button(tools,'跳过 / 恢复所选书籍',self.skip_books)
        self.button(tools,'预览本批恢复',self.preview)
        sharing=QWidget();sl=QVBoxLayout(sharing);self.tabs.addTab(sharing,'书库分享')
        self.share_tabs=QTabWidget();sl.addWidget(self.share_tabs)
        outgoing=QWidget();sl=QVBoxLayout(outgoing);self.share_tabs.addTab(outgoing,'导出分享包')
        incoming=QWidget();il=QVBoxLayout(incoming);self.share_tabs.addTab(incoming,'导入分享包')
        sl.addWidget(QLabel('来源：当前 Calibre 书库与下方书架列。包含电子书；不沿用“书架备份”的勾选。'))
        self.fields=QComboBox();sl.addWidget(self.fields)
        self.import_fields=QComboBox()
        from .column_io import shelf_fields,field_label
        for k in shelf_fields(self.api):
            label=field_label(self.api,k)
            self.fields.addItem(label,k);self.import_fields.addItem(label,k)
        share_bar=QHBoxLayout();sl.addLayout(share_bar)
        self.button(share_bar,'读取 / 重新选择书架',self.load_share_shelves,'从 Calibre 指定列读取收藏夹；重新读取会清空此前勾选。')
        self.button(share_bar,'载入 Calibre 当前选中书籍',self.use_calibre_selection,'用主窗口选中的书籍替换本页选择，包含这些书的全部收藏归属。')
        self.share_picker=ShelfPicker(self);sl.addWidget(self.share_picker,1)
        self.share_picker.path.setText('点击“读取 / 重新选择书架”，然后进入收藏夹选书；默认不勾选。')
        self.fields.currentIndexChanged.connect(self.share_field_changed)
        format_bar=QHBoxLayout();sl.addLayout(format_bar);format_bar.addWidget(QLabel('导出格式：'))
        self.formats=QLineEdit('EPUB,AZW3,MOBI,PDF');self.formats.setPlaceholderText('英文逗号分隔');format_bar.addWidget(self.formats,1)
        self.button(sl,'导出勾选书籍与收藏关系',self.export_books)
        il.addWidget(QLabel('打开分享包 → 选择存入哪个书库 → 检查书单 → 开始导入'))
        self.button(il,'1. 打开分享包（.kcshare.zip）',self.open_share)
        self.import_file=QLabel('尚未打开分享包。这里不接受仅含收藏关系的 .kcbookshelf.json。');self.import_file.setWordWrap(True);il.addWidget(self.import_file)
        self.import_mode=QComboBox();self.import_mode.addItems(['2. 建立独立书库（推荐）','2. 导入当前书库']);il.addWidget(self.import_mode)
        self.import_field_label=QLabel('收藏关系写入当前书库的哪一列：');il.addWidget(self.import_field_label);il.addWidget(self.import_fields)
        self.import_explanation=QLabel();self.import_explanation.setWordWrap(True);il.addWidget(self.import_explanation)
        self.import_fields.currentIndexChanged.connect(self.import_mode_changed)
        self.import_mode.currentIndexChanged.connect(self.import_mode_changed)
        self.import_model=Model(['书名','作者','格式','导入方式']);self.import_view=searchable(il,self.import_model)
        bar=QHBoxLayout();il.addLayout(bar);self.reuse_button=self.button(bar,'复用当前库已有书籍',self.reuse_book,'人工选择；只合并收藏列，不替换已有格式或元数据。')
        self.new_book_button=self.button(bar,'改为新书导入',self.reset_reuse);self.import_button=self.button(bar,'3. 开始导入',self.import_books)
        self.import_mode_changed()
        records=QWidget();hl=QVBoxLayout(records);self.tabs.addTab(records,'迁移与导入记录')
        self.history_model=Model(['类型','时间','内容 / 进度']);self.history_view=searchable(hl,self.history_model)
        bar=QHBoxLayout();hl.addLayout(bar);self.button(bar,'打开 / 继续记录',self.open_record)
        self.button(bar,'停止 / 继续迁移',self.toggle_stopped)
        self.button(bar,'查看设备任务',self.tasks)
        bar=QHBoxLayout();layout.addLayout(bar);self.cancel=QPushButton('取消当前计算');self.cancel.clicked.connect(self.cancel_work);self.cancel.hide();bar.addWidget(self.cancel)
        bar.addStretch();self.close_button=QPushButton('关闭');self.close_button.clicked.connect(self.reject);bar.addWidget(self.close_button)
        self.history()
        if ident:self.set_session(self.state.workspace(ident))
    def button(self,layout,text,fn,tip=''):
        b=QPushButton(text);b.setToolTip(tip);b.clicked.connect(lambda:self.guard(fn));layout.addWidget(b);return b
    def guard(self,fn):
        if self.busy:return
        try:
            if str(self.host.gui.current_db.library_id)!=self.library:raise Invalid('书库已切换，请重新打开窗口')
            fn()
        except Exception as e:self.notice.setText(str(e))
    def reject(self):
        if self.busy:self.notice.setText('请等待当前操作结束，或先取消计算。');return
        super().reject()
    def run(self,fn,done,device=False):
        self.busy=True;self.host.busy=True;self.host.update_buttons();self.tabs.setEnabled(False);self.cancel.setVisible(not device)
        self.notice.setText('正在处理…')
        def finish(result):
            self.busy=False;self.host.busy=False;self.work=None;self.tabs.setEnabled(True);self.cancel.hide();self.host.update_buttons()
            if result[0]:self.guard(lambda:done(result[1]))
            else:self.notice.setText(result[1])
            self.history();self.host.resume_reload()
        if device:
            def finished(job):finish((False,str(job.exception)) if job.failed else (True,job.result))
            self.host.gui.device_manager.create_job(fn,finished,'kc-ivy 迁移任务传输')
        else:
            self.work=Work(fn);self.work.signals.done.connect(finish,Qt.ConnectionType.QueuedConnection);QThreadPool.globalInstance().start(self.work)
    def cancel_work(self):
        if self.work:self.work.cancel.set()
    def snapshot(self):
        s=self.host.snapshot
        if not s:raise Invalid('请连接目标 Kindle，运行刷新后读取设备状态')
        return s
    def backup_source(self,*args):
        index=self.sources.currentIndex()
        if index<0:return
        groups,books=snapshot_shelves(self.snapshots[index]);self.backup_picker.load(groups,books,checked=True)
    def save_backup(self):
        if self.sources.currentIndex()<0:raise Invalid('尚无读取过的设备状态')
        relations=self.backup_picker.selection.relations()
        if not relations:raise Invalid('请勾选要保存的收藏夹或架内书籍')
        path,_=QFileDialog.getSaveFileName(self,'保存书架备份','Kindle书架.kcbookshelf.json','书架备份 (*.kcbookshelf.json)')
        if not path:return
        snapshot=deepcopy(self.snapshots[self.sources.currentIndex()])
        def work():
            value=export_snapshot(snapshot,set(relations),selected_relations=relations);atomic_write(path,value);return len(value['collections'])
        self.run(work,lambda n:self.notice.setText(f'已保存 {n} 个收藏夹及勾选成员；不含书籍文件。'))
    def open_backup(self):
        path,_=QFileDialog.getOpenFileName(self,'打开书架备份','','书架备份 (*.json)')
        if path:self.start_backup(read(path))
    def start_backup(self,backup):
        s=self.snapshot();validate_backup(backup)
        if self.host.intents:raise Invalid('请先处理当前普通草稿，再开始迁移')
        matches=[v for v in self.state.workspaces('migration',self.service.key(self.library,s)) if v['backup']['digest']==backup['digest']]
        self.set_session(matches[0] if matches else new_session(self.state,self.library,s,backup))
    def set_session(self,value):
        if not value:raise Invalid('迁移记录不存在')
        s=self.snapshot();self.session=MigrationService(self.service).session(value['id'],self.library,s)
        bound=bind_defaults(self.session,s)
        if bound['targets']!=self.session['targets']:self.session=self.state.save_workspace(bound,self.session['revision'])
        self.render_session();self.tabs.setCurrentIndex(1)
    def render_session(self):
        rows,books=overview(self.session,self.snapshot())
        self.shelf_model.replace([dict(r,cells=[r['name'],r['target']['name'] if r['target'] else '待选择',r['status'],f'{len(r["missing"])} / {r["existing"]}',str(r['waiting'])]) for r in rows])
        self.shelf_model.selected=set(self.session['selected'])
        self.book_model.replace([dict(r,cells=[r['title'],r['status'],str(len(r['targets']))]) for r in books])
        self.notice.setText(progress(self.session,self.snapshot())+'。目标：'+self.snapshot()['firmware']+' · '+self.session['device']['instance_id'][:8])
    def save_choices(self):
        if not self.session:raise Invalid('请先打开备份或迁移记录')
        current=self.state.workspace(self.session['id'])
        if current['revision']!=self.session['revision']:raise Invalid('记录已更新，请从迁移记录重新打开')
        if current['active_job']:raise Invalid('请先核对上一批任务，再修改迁移选择')
        self.session['selected']=sorted(self.shelf_model.selected)
        self.session=self.state.save_workspace(self.session,self.session['revision']);self.render_session()
    def current(self,view):
        i=view.currentIndex()
        if not i.isValid():raise Invalid('请先选择一行')
        return i.data(Qt.ItemDataRole.UserRole)
    def editable(self):
        if not self.session or self.session['active_job']:raise Invalid('先打开迁移记录并处理已发送任务')
    def select_target(self):
        self.editable();row=self.current(self.shelf_view);s=self.snapshot()
        items=[dict(id=c['uuid'],cells=[c['name'],'使用已有收藏夹']) for c in s['collections']]
        items.insert(0,dict(id='new',cells=['新建收藏夹','输入新名称']))
        chosen=choose(self,'为「'+row['name']+'」选择目标',items)
        if not chosen:return
        if chosen[0]=='new':
            name,ok=QInputDialog.getText(self,'新收藏夹名称','目标名称',text=row['name'])
            if not ok or not name.strip():return
            target=dict(id=str(uuid4()),name=name.strip(),new=True)
        else:
            c=next(c for c in s['collections'] if c['uuid']==chosen[0]);target=dict(id=c['uuid'],name=c['name'],new=False)
        self.session['targets'][row['id']]=target;self.session['done'].pop(row['id'],None);self.save_choices()
    def pair_book(self):
        self.editable();row=self.current(self.book_view)
        candidates=[dict(id=b['uuid'],cells=[b['title'],b['location'] or '设备书籍']) for b in self.snapshot()['books'] if not (b.get('location') or '').lower().endswith('.sh')]
        chosen=choose(self,'配对「'+row['title']+'」；可选择多个目标副本',candidates,True)
        if not chosen:return
        self.session['matches'][row['id']]=chosen
        self.session['skipped']=[b for b in self.session['skipped'] if b!=row['id']];self.save_choices()
    def skip_books(self):
        self.editable();ids={i.data(Qt.ItemDataRole.UserRole)['id'] for i in self.book_view.selectionModel().selectedRows()}
        self.session['skipped']=sorted(set(self.session['skipped'])^ids);self.save_choices()
    def preview(self):
        self.save_choices();snapshot=deepcopy(self.snapshot());ident=self.session['id']
        if self.host.intents:raise Invalid('请先处理普通草稿，迁移不混入其他编辑')
        self.run(lambda:MigrationService(self.service).prepare(self.api,self.library,snapshot,ident),self.show_plan)
    def show_plan(self,p):
        self.session=self.state.workspace(p['migration_id']);self.render_session()
        d=QDialog(self);d.setWindowTitle('迁移修改预览');d.resize(950,600);layout=QVBoxLayout(d)
        plan=p['plan'].data;names={c['uuid']:c['name'] for c in p['snapshot']['collections']}
        for op in plan['operations']:
            if op['kind']=='create_collection':names[op['collection_uuid']]=op['args']['name']
        model=Model(['操作','收藏夹','影响']);model.replace([dict(id=o['op_id'],cells=[LABELS[o['kind']],names.get(o['collection_uuid'],''),str(len(o['args'].get('members',[])))+' 本']) for o in plan['operations']])
        searchable(layout,model)
        notes=p['migration_notes']+p['issues']+[b['message'] for b in plan['blockers']]
        summary=QLabel('\n'.join(notes[:12]) if notes else '仅创建和补充归属；保留目标设备原有内容。发送后仍需在 Kindle 执行。')
        summary.setWordWrap(True);summary.setToolTip('\n'.join(notes));layout.addWidget(summary)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('发送本批到 Kindle')
        buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(bool(plan['operations']) and not plan['blockers'] and not p['issues'])
        buttons.accepted.connect(d.accept);buttons.rejected.connect(d.reject);layout.addWidget(buttons)
        if not d.exec():return
        manager=self.host.gui.device_manager
        from .mtp import execution_hint
        next_step=execution_hint(manager)
        def send():return MigrationService(self.service).submit(self.api,self.library,connected_store(manager,plan['device']),p)
        def sent(jid):
            self.session=self.state.workspace(p['migration_id']);self.render_session()
            self.notice.setText('已发送本批。'+next_step)
        self.run(send,sent,device=True)
    def share_field_changed(self,*args):
        self.share_field=None;self.share_picker.load([],[])
        self.share_picker.path.setText('书架列已改变，请重新读取后选书。')
    def load_share_shelves(self,selected=None):
        field=self.fields.currentData()
        if not field:raise Invalid('请先创建并选择保存收藏归属的多值文本列')
        def ready(value):
            self.share_field=field;self.share_picker.load(*value)
            if selected is not None:
                self.share_picker.selection.set_books(set(selected)&set(self.share_picker.selection.books),True);self.share_picker.refresh()
            self.notice.setText('已读取 Calibre 书架。双击进入收藏夹选书，或用“全选”勾选整库。')
        self.run(lambda:calibre_shelves(self.api,field),ready)
    def use_calibre_selection(self):
        ids=list(self.host.gui.library_view.get_selected_ids())
        if not ids:raise Invalid('请先在 Calibre 主窗口选中书籍')
        self.load_share_shelves(ids)
    def export_books(self):
        field=self.fields.currentData()
        if not field or self.share_field!=field:raise Invalid('请先读取本页书架，再勾选要分享的书籍')
        selection=self.share_picker.selection;ids=sorted(selection.selected_books())
        if not ids:raise Invalid('请勾选要分享的收藏夹或架内书籍')
        names={bid:[] for bid in ids}
        for gid,books in selection.relations().items():
            if selection.groups[gid].get('virtual'):continue
            for bid in books:names[bid].append(selection.groups[gid]['name'])
        from .sharing import export_share,share_inventory
        formats=[v.strip() for v in self.formats.text().upper().replace('，',',').split(',')]
        inventory=share_inventory(self.api,ids,formats)
        from .maintenance_ui import grid
        review=QDialog(self);review.setWindowTitle('确认分享范围');review.resize(850,540);box=QVBoxLayout(review)
        included=sum(bool(r['formats']) for r in inventory['rows'])
        box.addWidget(QLabel(f'选择 {len(ids)} 本：包含电子书文件 {included} 本，将跳过 {len(ids)-included} 本。'))
        box.addWidget(grid(['书名','导出格式','处理方式'],[[r['title'],', '.join(r['formats']) or '—',r['status']] for r in inventory['rows']]),1)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);box.addWidget(buttons)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(included>0)
        buttons.accepted.connect(review.accept);buttons.rejected.connect(review.reject)
        if not review.exec():return
        path,_=QFileDialog.getSaveFileName(self,'导出书籍分享包','书库分享.kcshare.zip','分享包 (*.kcshare.zip)')
        if not path:return
        self.run(lambda:export_share(self.api,self.library,ids,field,path,formats,selected_names=names,expected_inventory=inventory['fingerprint']),
            lambda r:self.notice.setText(f'已导出 {r["books"]} 本、{r["collections"]} 个收藏分类，{r["size"]/1048576:.1f} MiB；没有所选格式而跳过 {r["skipped"]} 本。'))
    def import_mode_changed(self,*args):
        current=self.import_mode.currentIndex()==1
        self.import_fields.setVisible(current);self.import_field_label.setVisible(current)
        self.import_explanation.setText(('收藏关系将合并到所选'+('原生标签，保留已有标签。' if self.import_fields.currentData()=='tags' else '自定义列，保留已有值。')+'不会自动创建或复制发送者的列。') if current else '开始导入时在新书库创建“Kindle书架”多值文本列；不复制发送者的列名称或其他自定义列。仅打开分享包不会创建书库或列。')
        self.reuse_button.setEnabled(current and self.manifest is not None);self.new_book_button.setEnabled(current and self.manifest is not None)
        self.import_button.setEnabled(self.manifest is not None);self.render_import()
    def open_share(self):
        path,_=QFileDialog.getOpenFileName(self,'打开书籍分享包','','分享包 (*.kcshare.zip *.zip)')
        if not path:return
        from .sharing import inspect_share
        def scan():
            from collections import Counter
            return inspect_share(path),dict(Counter((v or '').casefold().strip() for v in self.api.all_field_for('title',self.api.all_book_ids()).values()))
        def ready(result):
            self.title_counts=result[1];self.share_loaded(path,result[0])
        self.run(scan,ready)
    def share_loaded(self,path,manifest):
        self.share_path=path;self.manifest=manifest;self.reuse={};self.import_mode_changed();self.share_tabs.setCurrentIndex(1)
        self.import_file.setText('已打开：'+str(path)+f'（{len(manifest["backup"]["books"])} 本）')
        self.notice.setText(f'分享包：{len(manifest["backup"]["books"])} 本，{len(manifest["backup"]["collections"])} 个分类；{sum(f["size"] for f in manifest["files"])/1048576:.1f} MiB。导入前会校验文件。')
    def render_import(self):
        if not self.manifest:return
        fmts={b['id']:[] for b in self.manifest['backup']['books']}
        for f in self.manifest['files']:fmts[f['book']].append(f['format'])
        rows=[]
        for b in self.manifest['backup']['books']:
            mode='复用本地 #'+str(self.reuse[b['id']]) if b['id'] in self.reuse and self.import_mode.currentIndex()==1 else '导入新书 / 已导入则续接'
            duplicates=self.title_counts.get(b['title'].casefold().strip(),0)
            if duplicates and self.import_mode.currentIndex()==1 and b['id'] not in self.reuse:mode+=f'；同名候选 {duplicates} 本，请核对'
            rows.append(dict(id=b['id'],cells=[b['title'],'、'.join(b.get('authors',[])),','.join(fmts[b['id']]),mode]))
        self.import_model.replace(rows)
    def reuse_book(self):
        if self.import_mode.currentIndex()!=1:raise Invalid('复用已有书籍仅适用于导入当前书库')
        row=self.current(self.import_view)
        def ready(values):
            chosen=choose(self,'选择复用的本地书籍；不会覆盖其格式',values)
            if chosen:self.reuse[row['id']]=chosen[0];self.render_import()
        def records():
            ids=self.api.all_book_ids();titles=self.api.all_field_for('title',ids);authors=self.api.all_field_for('authors',ids)
            return [dict(id=i,cells=[titles[i],'、'.join(authors[i] or ())]) for i in ids]
        self.run(records,ready)
    def reset_reuse(self):
        for i in self.import_view.selectionModel().selectedRows():self.reuse.pop(i.data(Qt.ItemDataRole.UserRole)['id'],None)
        self.render_import()
    def import_books(self):
        if not self.manifest:raise Invalid('请先打开分享包并预览')
        from .sharing import import_share,new_library
        path=self.share_path;expected=self.manifest['digest'];reuse=dict(self.reuse)
        independent=self.import_mode.currentIndex()==0;target=None
        if independent:
            target=QFileDialog.getExistingDirectory(self,'选择用于新书库的空目录')
            if not target:return
            if any(Path(target).iterdir()):raise Invalid('请选择空目录；继续上次导入请切换到该书库后导入当前库')
            field='#kindlecollections'
        else:
            field=self.import_fields.currentData()
            if not field:raise Invalid('请先选择当前书库的多值文本列')
        if QMessageBox.question(self,'确认导入',f'导入 {len(self.manifest["backup"]["books"])} 本到'+('独立书库：'+target if independent else '当前书库')+'。\n原有书籍不会按同名自动覆盖；重复本包可续接。开始？')!=QMessageBox.StandardButton.Yes:return
        def work():
            if not independent:return import_share(self.api,self.library,path,field,self.state,reuse,expected)
            db=new_library(target)
            try:return import_share(db.new_api,str(db.library_id),path,field,self.state,expected=expected)
            finally:db.close()
        def done(result):
            journal,changed=result
            if not independent:self.host.gui.library_view.model().refresh()
            self.notice.setText(f'导入完成，本次处理 {len(changed)} 本。'+('在 Calibre“切换/创建书库”打开 '+target+'，再从本窗口导入记录恢复到 Kindle。' if independent else '可以先阅读，或用 Calibre 传书后从导入记录恢复收藏夹。'))
        self.run(work,done)
    def history(self):
        rows=[]
        if self.host.snapshot:
            for s in self.state.workspaces('migration',self.service.key(self.library,self.host.snapshot)):
                rows.append(dict(id=s['id'],kind='migration',cells=['书架迁移',s['created'],progress(s,self.host.snapshot)]))
        for s in self.state.workspaces('share_import',self.library):
            rows.append(dict(id=s['id'],kind='share_import',cells=['分享导入',s['created'],('已导入，可恢复到 Kindle' if s['complete'] else '未完成，可继续导入')+'；'+str(len(s['items']))+' 本']))
        self.history_model.replace(rows)
    def open_record(self):
        row=self.current(self.history_view);s=self.state.workspace(row['id'])
        if row['kind']=='migration':self.set_session(s)
        elif s['complete']:
            from .sharing import local_backup
            self.start_backup(local_backup(s))
        else:
            self.share_loaded(s['path'],s['manifest']);self.import_mode.setCurrentIndex(1)
            self.import_fields.setCurrentIndex(self.import_fields.findData(s['field']));self.tabs.setCurrentIndex(2)
    def toggle_stopped(self):
        row=self.current(self.history_view);s=self.state.workspace(row['id'])
        if row['kind']!='migration':raise Invalid('请选择迁移记录')
        if s['active_job']:raise Invalid('停止不能撤销已发送任务，请先核对原任务')
        s['stopped']=not s['stopped'];self.state.save_workspace(s,s['revision']);self.history()
    def tasks(self):self.accept();QTimer.singleShot(0,self.host.task_history)
