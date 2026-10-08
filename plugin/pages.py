"""Native settings and column workflows, backed by the same persistent service."""
import copy
from qt.core import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QComboBox,
    QDialogButtonBox,QMessageBox,QFileDialog,QInputDialog,QTableWidget,QTableWidgetItem,Qt,QCheckBox,QTextEdit)
from .theme import InkDialog as QDialog, InkInputDialog as QInputDialog, InkMessageBox as QMessageBox, InkFileDialog as QFileDialog
from .protocol import Invalid
from .metadata import read_metadata,mappings
from .transport import connected_store,read,atomic_write
from . import column_io
from .rules import default_rule,migrate

class Pages:
    def manage_book_links(self):
        if self.busy:return
        try:
            if self.snapshot is None:raise Invalid('请先读取设备状态。')
            from .book_links_ui import BookLinksDialog
            BookLinksDialog(self).exec()
        except Exception as exc:self.error(exc)

    def migration_center(self,ident=None):
        if self.busy:return
        from .migration_ui import MigrationCenter
        dialog=MigrationCenter(self,ident if isinstance(ident,str) else None)
        dialog.exec()
    def recover_snapshot(self):
        if self.busy:return
        if QMessageBox.question(self,'恢复未完成快照','只归档 latest.json.partial，不删除任务、回执、备份或正式快照。归档后需弹出 Kindle，运行“KC刷新收藏夹”。继续？')!=QMessageBox.StandardButton.Yes:return
        from .transport import archive_snapshot_partial
        self.device_job(lambda:archive_snapshot_partial(connected_store(self.gui.device_manager)),
            lambda path:self.error('已归档：'+path+'；请弹出 Kindle 后运行刷新收藏夹。' if path else '没有未完成的快照文件，无需处理。'),
            'KC++ 归档未完成快照')

    def task_history(self,selected_job_id=None):
        try:
            p=self.require_profile();summaries=self.service.state.job_summaries(self.service.key(p['library_uuid'],self.snapshot))
            from .task_ui import TaskHistoryDialog
            jobs=[self.service.state.job(summary['job_id']) for summary in summaries]
            TaskHistoryDialog(self,jobs,selected_job_id).exec()
        except Exception as exc:self.error(exc)

    def select_recovery(self,values,snapshot,job):
        from .usability import recovery_items,choose_recovery
        from .ui import LABELS
        items=recovery_items(values,snapshot,job['snapshot'])
        dialog=QDialog(self);dialog.setWindowTitle('逐项恢复未完成操作');dialog.resize(950,600);layout=QVBoxLayout(dialog)
        confirmed=sum(r['status']=='confirmed' for r in job.get('result',{}).get('operations',[]))
        layout.addWidget(QLabel(f'已确认成功 {confirmed} 项，已排除。勾选要恢复的操作；取消不会关闭原任务。'))
        table=QTableWidget(len(items),3);table.setHorizontalHeaderLabels(['恢复','操作 / 收藏夹','涉及书籍 / 失效项目']);layout.addWidget(table)
        names={c['uuid']:c['name'] for c in job['snapshot']['collections']}
        for i,item in enumerate(items):
            v=item['intent'];box=QTableWidgetItem('');box.setFlags(Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsUserCheckable);box.setCheckState(Qt.CheckState.Checked);table.setItem(i,0,box)
            table.setItem(i,1,QTableWidgetItem(LABELS[v['kind']]+' / '+names.get(v['collection_uuid'],v['args'].get('name',v['collection_uuid']))))
            text=str(len(v['args'].get('members',[])))+' 本'
            if item['missing']:text+='；失效：'+'、'.join(b['title']+' ['+b['uuid']+']' for b in item['missing'])
            table.setItem(i,2,QTableWidgetItem(text));table.item(i,2).setToolTip(text)
        table.resizeColumnsToContents();table.setColumnWidth(2,520)
        skip=QCheckBox('明确排除所选操作中的失效书籍（不会猜测或替换为其他书）');layout.addWidget(skip)
        warning=QLabel('移动请一起保留加入与移除；新架加书须保留新建操作。');warning.setWordWrap(True);layout.addWidget(warning)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);layout.addWidget(buttons)
        chosen=[]
        def accept():
            try:
                selected={items[i]['intent']['intent_id'] for i in range(len(items)) if table.item(i,0).checkState()==Qt.CheckState.Checked}
                result=choose_recovery(items,selected,skip.isChecked())
                if not result:raise Invalid('没有可恢复项，请取消或重新选择')
                chosen.extend(result);dialog.accept()
            except Exception as e:warning.setText(str(e))
        buttons.accepted.connect(accept);buttons.rejected.connect(dialog.reject)
        return chosen if dialog.exec() else None

    def runtime_manager(self):
        if self.busy:return
        try:
            from .runtime_versions import preview,cleanup
            def scan():return preview(connected_store(self.gui.device_manager))
            def show(plan):
                from .maintenance_ui import runtime_dialog
                dialog,button=runtime_dialog(self,plan)
                def run():
                    dialog.accept()
                    self.device_job(lambda:cleanup(connected_store(self.gui.device_manager),plan),lambda n:self.error(f'已清理 {n} 个旧运行版本及对应过期入口备份；任务数据保留。'),'KC++ 清理旧运行版本')
                button.clicked.connect(run)
                dialog.exec()
            self.device_job(scan,show,'KC++ 读取设备运行版本')
        except Exception as e:self.error(e)

    def storage_manager(self):
        if self.busy:return
        try:
            p=self.require_profile();key=self.service.key(p['library_uuid'],self.snapshot)
            def scan():
                from .storage import inventory,cleanup_plan
                store=connected_store(self.gui.device_manager,p['device'])
                usage=inventory(store,self.service.state.directory)
                try:items=cleanup_plan(store,self.service.state.jobs(key));note=''
                except Invalid as e:items=[];note=str(e)
                return usage,items,note
            def show(value):
                usage,items,note=value
                from .maintenance_ui import storage_dialog
                dialog,button=storage_dialog(self,usage,items,note)
                def run():
                    from .storage import cleanup
                    dialog.accept()
                    self.device_job(lambda:cleanup(connected_store(self.gui.device_manager,p['device']),self.service.state.jobs(key),items),lambda count:self.error(f'已清理 {count} 个旧诊断文件；任务和备份已保留。'),'KC++ 清理已预览的诊断文件')
                button.clicked.connect(run)
                dialog.exec()
            self.device_job(scan,show,'KC++ 统计空间与生成清理预览')
        except Exception as e:self.error(e)

    def global_settings(self):
        try:p=self.require_profile()
        except Exception as e:self.error(e);return
        dialog=QDialog(self);dialog.setWindowTitle('全局规则设置');layout=QVBoxLayout(dialog)
        case=QCheckBox('匹配 / 规则改名时忽略大小写');case.setChecked(p['settings']['ignore_case']);layout.addWidget(case)
        layout.addWidget(QLabel('不允许修改或删除的名称（每行一个；正则以 re: 开头）'))
        patterns=QTextEdit();patterns.setPlainText('\n'.join(p['settings']['ignore_all']));layout.addWidget(patterns)
        layout.addWidget(QLabel('保留未纳管关系；缺席不代表删除。改名按 UUID 绑定。\n旧版自动重启、重置时间、JSON 差分和手选机型不直接执行，改用能力检测和精确核验。'))
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);layout.addWidget(buttons);buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject)
        if dialog.exec():
            try:
                p['settings'].update(ignore_case=case.isChecked(),ignore_all=[x for x in patterns.toPlainText().splitlines() if x])
                self.service.state.save(self.service.key(p['library_uuid'],self.snapshot),p,p['revision']);self.preview=None;self.prepared=None;self.update_buttons()
            except Exception as e:self.error(e)
    def show_device_report(self):
        if not self.snapshot:return
        from .ui import Model,table
        dialog=QDialog(self);dialog.setWindowTitle('设备收藏夹与书籍');dialog.resize(1120,650);layout=QVBoxLayout(dialog)
        choices=QComboBox();choices.addItems(['所有收藏夹','所有书籍','未归类书籍','未匹配书籍','未知收藏关系']);layout.addWidget(choices)
        model=Model(['名称 / 书名','身份','归属 / 状态','路径 / 匹配'],dialog);layout.addWidget(table(model))
        def display():
            choice=choices.currentIndex();rows=[]
            if choice==0:
                rows=[dict(cells=[c['name'],c['uuid'],str(len(self.catalog.members[c['uuid']]))+' 本','完整' if c['complete'] else '含未知关系']) for c in self.snapshot['collections']]
            elif choice==4:
                rows=[dict(cells=[str(r['collection_uuid']),str(r['book_uuid']),str(r['member_type']),str(r['member_key'])]) for r in self.snapshot['relations'] if r['book_uuid'] not in self.catalog.books or r['collection_uuid'] not in self.catalog.collections]
            else:
                for b in self.snapshot['books']:
                    shelves=self.catalog.book_collections[b['uuid']]
                    if choice==2 and shelves:continue
                    if choice==3 and b['calibre_uuid']:continue
                    rows.append(dict(cells=[b['title'] or '',b['uuid'],' | '.join(self.catalog.collections[c]['name'] for c in shelves if c in self.catalog.collections),(b['location'] or '')+' / '+('已匹配' if b['calibre_uuid'] else '设备独有或未匹配')]))
            model.replace(sorted(rows,key=lambda r:r['cells'][0]))
        choices.currentIndexChanged.connect(display);display()
        save=QPushButton('导出当前报告 CSV');layout.addWidget(save)
        def export():
            path,_=QFileDialog.getSaveFileName(dialog,'保存报告','KC++-report.csv','CSV (*.csv)')
            if path:
                import csv
                with open(path,'w',encoding='utf-8-sig',newline='') as stream:
                    writer=csv.writer(stream);writer.writerow(model.headers);writer.writerows(r['cells'] for r in model.rows)
        save.clicked.connect(export);dialog.exec()
    def about(self):
        from . import KCPlus
        from .install import VERSION
        QMessageBox.information(self,'关于 KC++','KC++ '+'.'.join(map(str,KCPlus.version))+' 正式版 / KC '+VERSION+'\n主要使用环境：Windows / Calibre 9.15+；历史真机记录：PW5、5.17.1.0.3、USB 磁盘。\n其他型号与固件的理论范围见随包“Kindle 运行环境与理论支持范围”，不代表实测通过。\nMTP、迁移分享及其他平台保留实验标识；新增型号与实验功能仍需真机验收。\n无需安装原 Kindle Collections；修改需预览、设备执行和确认。')
    def rollback_device(self):
        try:
            from .mtp import is_mtp
            if is_mtp(self.gui.device_manager):return self.rollback_mtp_device()
            from .install import rollback
            store=connected_store(self.gui.device_manager)
            backups=sorted(store.path('upgrades').glob('*/manifest.json'))
            if not backups:raise Invalid('没有入口备份')
            labels=[p.parent.name for p in backups]
            label,ok=QInputDialog.getItem(self,'恢复设备入口','保留 KC 数据。回退后旧追加同步可能再次加入旧输入的归属，请勿继续运行旧输入。',labels,len(labels)-1,False)
            if not ok:return
            self.device_job(lambda:rollback(self.gui.device_manager,backups[labels.index(label)].parent),lambda n:self.error(f'已恢复 {n} 个入口；设备数据保留。'),'KC++ 回退启动入口')
        except Exception as e:self.error(e)
    def require_profile(self):
        if not self.same_library():raise Invalid('书库已切换，请重新打开 KC++')
        if self.busy:raise Invalid('请等待当前任务完成')
        if not self.snapshot or not self.service:raise Invalid('请先连接设备并读取状态')
        library=self.library_id
        return self.service.profile(library,self.snapshot)
    def select_column(self,summary=False):
        api=self.library_db.new_api
        columns=[(key,meta.get('name',key)) for key,meta in api.field_metadata.items()
                 if (key.startswith('#') and meta.get('datatype') in ('text','comments','bool') and not meta.get('is_multiple') if summary else column_io.is_shelf_field(key,meta))]
        if not columns:raise Invalid('请先在 Calibre 添加一个多值文本自定义列，例如 Kindle书架')
        labels=[label+' ('+key+')' if summary else column_io.field_label(api,key) for key,label in columns]
        label,ok=QInputDialog.getItem(self,'指定往返列','选择用于收藏夹归属的多值列',labels,0,False)
        return columns[labels.index(label)][0] if ok else None
    def setup_column(self):
        try:
            p=self.require_profile()
            if p['field']:
                if QMessageBox.question(self,'采用新增书籍','已采用列 '+p['field']+'。是否将本次新匹配的书籍纳入范围？已有书籍的新副本不自动扩权。')==QMessageBox.StandardButton.Yes:
                    self.background(lambda:self.service.adopt_new_books(self.library_db.new_api,p['library_uuid'],self.snapshot),lambda n:self.error(f'已采用 {n} 本新增书籍；请生成 修改预览。'))
                return
            field=self.select_column()
            if not field:return
            api=self.library_db.new_api;snapshot=self.snapshot;library=p['library_uuid']
            def ready(metadata):
                copies=mappings(snapshot,metadata['rows'])
                multiple=sum(len(v)>1 for v in copies.values())
                from .ui import Model,table
                from qt.core import QAbstractItemView
                dialog=QDialog(self);dialog.setWindowTitle('审阅并选择采用的设备副本');dialog.resize(1000,620);layout=QVBoxLayout(dialog)
                layout.addWidget(QLabel(f'{len(copies)} 本匹配书籍，{multiple} 本有多个副本。默认全选；可用 Ctrl / Shift 选择需要纳管的行。\n两端一致的归属记为初始状态；只在 Calibre 存在的值继续预览为新增。'))
                books={b['uuid']:b for b in snapshot['books']};model=Model(['Calibre 书名','设备副本 UUID','设备路径'],dialog)
                model.replace([dict(uid=row['uuid'],bid=bid,cells=[row['title'],bid,books[bid]['location'] or '']) for row in metadata['rows'] for bid in copies.get(row['uuid'],[])])
                view=table(model);view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection);layout.addWidget(view);view.selectAll()
                buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);layout.addWidget(buttons);buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject)
                if not dialog.exec():return
                selected={}
                for index in view.selectionModel().selectedRows():
                    row=model.rows[index.row()];selected.setdefault(row['uid'],[]).append(row['bid'])
                if not selected:raise Invalid('没有选中设备副本，未采用任何范围')
                def work():return self.service.adopt(api,library,snapshot,field,selected,include_current=True)
                from qt.core import QTimer
                QTimer.singleShot(0,lambda:self.background(work,lambda _:self.error('已采用 '+field+'；现在可生成 修改预览。')))
            self.background(lambda:read_metadata(api,[field]),ready)
        except Exception as e:self.error(e)
    def calibrate_baseline(self):
        try:
            p=self.require_profile();api=self.library_db.new_api;snapshot=self.snapshot
            def ready(value):
                from .ui import Model,table
                dialog=QDialog(self);dialog.setWindowTitle('校准已有书架：仅采用两端一致的归属');dialog.resize(850,550)
                layout=QVBoxLayout(dialog)
                layout.addWidget(QLabel(f'{len(value["rows"])} 条归属将记为初始状态。\n不改书架或列值；保留两端差异、手动草稿和既有排除。规则仍独立生效。'))
                model=Model(['书名','Calibre 列值','Kindle 原名','收藏夹 UUID'],dialog);model.replace([dict(cells=[r['title'],r['collection'],r['device_name'],r['collection_uuid']]) for r in value['rows']]);layout.addWidget(table(model))
                buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);layout.addWidget(buttons);buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject)
                if dialog.exec():
                    QTimer.singleShot(0,lambda:self.background(lambda:self.service.baseline_apply(api,p['library_uuid'],snapshot,value),lambda n:(self.error(f'已校准 {n} 条初始归属，请重新生成 修改预览。'),setattr(self,'preview',None),setattr(self,'prepared',None)),cancelable=False))
            from qt.core import QTimer
            self.background(lambda:self.service.baseline_preview(api,p['library_uuid'],snapshot),ready)
        except Exception as e:self.error(e)
    def preview_column_rows(self,rows,field,title='回填预览',transaction=None,summary=False,align_import=False):
        from .ui import Model,table
        dialog=QDialog(self);dialog.setWindowTitle(title);dialog.resize(920,600);layout=QVBoxLayout(dialog)
        changed=[r for r in rows if not column_io.equal(r['before'],r['after'])]
        layout.addWidget(QLabel(f'{len(changed)} 本将修改。写入前重新核对身份和原值，并保存备份。'))
        model=Model(['书籍','当前值','写入后'],dialog)
        def display(value):return ' | '.join(value) if isinstance(value,(tuple,list)) else '空' if value is None else '是' if value is True else '否' if value is False else str(value)
        model.replace([dict(cells=[r['title'],display(r['before']),display(r['after'])]) for r in changed]);layout.addWidget(table(model))
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);layout.addWidget(buttons)
        buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject)
        if not dialog.exec():return
        api=self.library_db.new_api;library=self.library_id
        def work():
            if transaction:
                if transaction['library_uuid']!=library:raise Invalid('书库已改变')
                if not self.service.state.column_pending(transaction['job_id']):self.service.state.stage_column(transaction)
            result=column_io.apply(api,library,field,changed,self.service.state.directory/'column-backups',summary=summary)
            if transaction:
                p=self.service.profile(library,self.snapshot)
                if p['field']!=field:raise Invalid('回填列已改变，停止更新记录')
                job=self.service.state.job(transaction['job_id'])
                rows=column_io.receipt_plan(p,job,job['result'],read_metadata(api,[field]),set(transaction['operations']))
                self.service.state.accept_column_baseline(self.service.key(library,self.snapshot),
                    column_io.accept_backfill(p,rows),transaction['job_id'],transaction['operations'])
                self.service.state.finish_column(transaction)
            if align_import:
                # If interrupted between column write and profile save, rerunning
                # import or explicit calibration safely completes this step.
                value=self.service.baseline_preview(api,library,self.snapshot,legacy_names=False)
                self.service.baseline_apply(api,library,self.snapshot,value)
            return result
        def done(result):
            self.preview=None;self.prepared=None
            model=self.gui.library_view.model()
            if hasattr(model,'refresh_ids'):model.refresh_ids([r['id'] for r in changed])
            else:model.refresh()
            self.error(f'已写入 {result[1]} 本。备份：{result[0] or "无变化"}')
        from qt.core import QTimer
        QTimer.singleShot(0,lambda:self.background(work,done,cancelable=False))
    def import_column(self):
        try:
            p=self.require_profile();field=p['field'] or self.select_column()
            if not field:return
            api=self.library_db.new_api;snapshot=self.snapshot
            def work():
                if p['field']:self.service.baseline_preview(api,p['library_uuid'],snapshot)
                return column_io.import_plan(snapshot,read_metadata(api,[field]),field,bindings=p['bindings'])
            self.background(work,lambda rows:self.preview_column_rows(rows,field,'导入 Kindle 归属到 Calibre（合并并登记初始归属）',align_import=bool(p['field'])))
        except Exception as e:self.error(e)
    def import_summary(self):
        try:
            self.require_profile();field=self.select_column(summary=True)
            if not field:return
            api=self.library_db.new_api;snapshot=self.snapshot
            self.background(lambda:column_io.import_summary_plan(snapshot,read_metadata(api,[]),api,field),
                lambda rows:self.preview_column_rows(rows,field,'摘要导入：仅在 Calibre 展示，不用于往返归属',summary=True))
        except Exception as e:self.error(e)
    def backfill_result(self):
        try:
            p=self.require_profile()
            if not p['field']:raise Invalid('请先指定往返列')
            key=self.service.key(p['library_uuid'],self.snapshot)
            jobs=[j for j in self.service.state.job_summaries(key) if j['status'] in ('complete','pending','closed')]
            if not jobs:raise Invalid('没有已接收的本地任务结果')
            labels=[j['job_id']+' / '+j['status'] for j in jobs]
            label,ok=QInputDialog.getItem(self,'回填执行结果','选择任务',labels,len(labels)-1,False)
            if not ok:return
            job=self.service.state.job(jobs[labels.index(label)]['job_id']);api=self.library_db.new_api
            if not job.get('result'):raise Invalid('此任务没有可回填的设备结果')
            if job.get('column_field')=='':
                self.show_notice('此任务使用纯 KC++ 模式，已确认的结果无需列回填。需要展示到列时，可另行预览导入 Kindle 当前归属。','success');return
            if job.get('column_field',p['field'])!=p['field']:raise Invalid('原任务书架列与当前设置不同，不能回填到其他列')
            def work():
                pending=self.service.state.column_pending(job['request']['job_id'])
                if pending:
                    value=copy.deepcopy(pending)
                    for row in value['rows']:
                        current=sorted(api.field_for(p['field'],row['id']) or ())
                        if current not in (sorted(row['before']),sorted(row['after'])):raise Invalid('未完成回填后发生了新编辑，请先保留备份并解决冲突')
                        row['before']=current
                    return value
                done=self.service.state.column_done(job['request']['job_id'])
                remaining={r['op_id'] for r in job['result']['operations'] if r['status']=='confirmed'}-done
                if not remaining:raise Invalid('此任务已确认的操作均已回填，不会再次覆盖后续编辑')
                rows=column_io.receipt_plan(p,job,job['result'],read_metadata(api,[p['field']]),remaining)
                return dict(job_id=job['request']['job_id'],result_digest=job['result']['result_digest'],
                    operations=sorted(remaining),library_uuid=p['library_uuid'],rows=rows,field=p['field'])
            self.background(work,lambda t:self.preview_column_rows(t['rows'],p['field'],'已确认操作回填（保留后来编辑）',t))
        except Exception as e:self.error(e)
    def restore_column(self):
        path,_=QFileDialog.getOpenFileName(self,'恢复列备份',str(self.service.state.directory/'column-backups'),'JSON (*.json)')
        if not path:return
        try:
            value=read(path);from .protocol import digest
            body={k:v for k,v in value.items() if k!='digest'}
            if value.get('digest')!=digest(body) or value.get('library_uuid')!=self.library_id:raise Invalid('备份摘要或书库不匹配')
            api=self.library_db.new_api;rows=[]
            for row in value['rows']:
                current=api.field_for(value['field'],row['id'])
                if not any(column_io.equal(current,v) for v in (row['before'],row['after'])):raise Invalid('备份后存在新编辑，停止覆盖')
                rows.append(dict(row,before=current,after=row['before']))
            self.preview_column_rows(rows,value['field'],'恢复列备份预览',summary=value.get('schema')=='kc-summary-backup/v1')
        except Exception as e:self.error(e)
    def configure_rules(self,checked=False,scope_tab=False):
        try:
            p=self.require_profile()
            if self.intents and p.get('field'):raise Invalid('请先执行或放弃手动草稿，再修改同步设置')
            api=self.library_db.new_api
            fields=column_io.shelf_fields(api)
            self.background(lambda:read_metadata(api,fields),lambda m:self.open_sync_settings(p,m,fields,scope_tab))
        except Exception as e:self.error(e)
    def open_sync_settings(self,p,metadata,fields,scope_tab=False):
        dialog=QDialog(self);dialog.setWindowTitle('书架与同步设置');dialog.resize(1000,720)
        from qt.core import QWidget,QTabWidget,QScrollArea,QHeaderView
        root=QVBoxLayout(dialog);root.setContentsMargins(22,18,22,18);root.setSpacing(12)
        heading=QLabel('书架与同步设置');heading.setObjectName('heading');root.addWidget(heading)
        root.addWidget(QLabel('选择整理方式，按需调整范围、分类与保护。保存后重新预览生效。'))
        tabs=QTabWidget();tabs.setObjectName('sync_settings_tabs');root.addWidget(tabs,1)
        sections=[]
        for title in ('同步方式','书籍范围','分类规则','保护设置'):
            scroll=QScrollArea();scroll.setWidgetResizable(True);page=QWidget();box=QVBoxLayout(page)
            box.setContentsMargins(16,18,16,18);box.setSpacing(12);scroll.setWidget(page);tabs.addTab(scroll,title);sections.append(box)
        layout=sections[0]
        from .ui import Model,table
        from qt.core import QTimer
        from qt.core import QAbstractItemView
        column=QComboBox();column.setObjectName('bookshelf_column')
        column.addItem('请选择已有的多值文本列…','')
        for field in fields:
            if field.startswith('#'):column.addItem(column_io.field_label(self.library_db.new_api,field),field)
        column.setCurrentIndex(max(0,column.findData(p['field'])))
        from .usability import uses_column
        mode=QComboBox();mode.setObjectName('sync_mode')
        for label,value in [('专用书架列（推荐）','custom'),('使用原生标签','tags'),('仅在 KC++ 整理','manual')]:mode.addItem(label,value)
        selected_mode=('tags' if p['field']=='tags' else 'custom') if uses_column(p) or p.get('sync_mode')=='column' else 'manual'
        mode.setCurrentIndex(mode.findData(selected_mode))
        def selected_field():
            if mode.currentData()=='manual':return ''
            if mode.currentData()=='tags':return 'tags' if 'tags' in fields else ''
            return column.currentData()
        layout.addWidget(QLabel('如何管理收藏归属'))
        mode.setToolTip('专用列和原生标签均与 KC++ 联合整理；仅 KC++ 模式不需要列。切换不会清空已有列值或 Kindle 收藏关系。')
        layout.addWidget(mode)
        column_label=QLabel('选择专用书架列');layout.addWidget(column_label)
        column.setToolTip('可选任意多值文本自定义列，名称不限。每个值对应一个收藏夹；一本书可以有多个值。')
        layout.addWidget(column)
        tags_hint=QLabel('标签将用于收藏分类，整理结果也会回填标签。');tags_hint.setObjectName('tags_writeback_notice');tags_hint.setWordWrap(True)
        layout.addWidget(tags_hint)
        column_help=QPushButton('新建书架列…');column_help.setObjectName('create_column_help')
        column_help.setToolTip('已有合适列可直接选择，无须新建。查找名称与列标题均可自定。')
        column_help.clicked.connect(self.column_creation_help)
        help_bar=QHBoxLayout();help_bar.addWidget(column_help);help_bar.addStretch();layout.addLayout(help_bar)
        column_notice=QLabel('尚未选择同步列。请选择已有列；也可新建书架列，或改用原生标签 / 仅 KC++ 整理。')
        column_notice.setObjectName('missing_column_notice');column_notice.setWordWrap(True);layout.addWidget(column_notice)
        layout=sections[1]
        from .scope import available_books
        copies=available_books(self.snapshot,metadata);books={b['uuid']:b for b in self.snapshot['books']}
        range_mode=QComboBox();range_mode.setObjectName('scope_mode')
        range_mode.addItems(['全部已匹配书籍（包含以后新加入的书）','只同步我选中的书籍'])
        range_mode.setCurrentIndex(0 if p.get('scope_mode','all' if not p['field'] else 'selected')=='all' else 1)
        layout.addWidget(QLabel('列同步范围（不限制 KC++ 手动整理）：'))
        layout.addWidget(range_mode)
        range_hint=QLabel('全部模式只包含能可靠对应当前 Calibre 书库的 Kindle 书籍；新书在生成预览时自动加入。空列不会清空 Kindle 已有归属。')
        range_hint.setWordWrap(True);layout.addWidget(range_hint)
        from qt.core import QWidget
        scope_panel=QWidget();scope_layout=QVBoxLayout(scope_panel);scope_layout.setContentsMargins(0,0,0,0);layout.addWidget(scope_panel)
        scope=Model(['书名 / 已匹配副本数','设备文件（每行一个副本）'],dialog)
        scope.replace([dict(uid=r['uuid'],bid=bid,cells=[r['title']+f' · {len(copies[r["uuid"]])} 个副本',books[bid]['location'] or bid]) for r in metadata['rows'] for bid in copies.get(r['uuid'],[])])
        scope_view=table(scope);scope_view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        scope_view.setObjectName('sync_scope_books');scope_view.setMinimumHeight(180);scope_layout.addWidget(scope_view,1)
        scope_view.setToolTip('Ctrl / Shift 可多选；完整设备路径可横向滚动查看。移出范围只停止列同步，不会移除 Kindle 归属。')
        selection=scope_view.selectionModel()
        for i,r in enumerate(scope.rows):
            if not p['field'] or r['bid'] in p['column_baseline'].get(r['uid'],{}).get('copies',[]):
                selection.select(scope.index(i,0),selection.SelectionFlag.Select|selection.SelectionFlag.Rows)
        scope_bar=QHBoxLayout();scope_layout.addLayout(scope_bar)
        for text,slot in [('全选',scope_view.selectAll),('全不选',scope_view.clearSelection)]:
            button=QPushButton(text);button.clicked.connect(slot);scope_bar.addWidget(button)
        scope_layout.addWidget(QLabel('Ctrl / Shift 多选。移出范围只停止列同步，不删除 Kindle 原有归属。'))
        layout.addStretch()
        layout=sections[0]
        existing=QLabel();existing.setObjectName('active_bookshelf_rule');existing.setWordWrap(True);existing.setStyleSheet('padding:14px; background:#ece9e1; border-radius:6px;')
        layout.addWidget(existing);layout.addStretch()
        def show_existing():
            field=selected_field()
            if mode.currentData()=='manual':text='仅在 KC++ 整理，无需创建或选择列。'
            elif not field:text='联合整理 · 尚未选择同步列'
            else:
                state='当前使用' if field==p['field'] and uses_column(p) else '保存后使用'
                name=self.library_db.new_api.field_metadata[field].get('name',field)
                text=state+'：'+name+'\nCalibre 列与 KC++ 共同管理收藏归属。'
            existing.setText(text)
        layout=sections[2]
        extra_label=QLabel('额外分类（可选） · 按作者、丛书、标签等生成收藏夹。')
        extra_label.setToolTip('只读取分类来源，不把收藏夹名称回填到来源字段。上方已选择的书架同步列无需重复配置规则。')
        extra_label.setWordWrap(True);layout.addWidget(extra_label)
        rule_hint=QLabel('没有额外规则也能正常同步书架列。复杂条件可横向滚动编辑。');rule_hint.setWordWrap(True);layout.addWidget(rule_hint)
        keys=['field','action','prefix','suffix','minimum','ignore','include','rename_from','rename_to','split']
        labels=['来源字段','动作','前缀','后缀','最少书数','忽略','仅包含','改名正则','替换为','分隔正则']
        grid=QTableWidget(len(p['rules']),len(keys));grid.setObjectName('classification_rules');grid.setHorizontalHeaderLabels(labels);layout.addWidget(grid,1)
        grid.setMinimumHeight(220);grid.setAlternatingRowColors(True);grid.verticalHeader().setDefaultSectionSize(44)
        grid.setToolTip('多个匹配条件每行一个；正则表达式以 re: 开头。停用规则将动作设为“不使用”。')
        grid.setColumnWidth(0,200);grid.setColumnWidth(1,130)
        def fill(row,rule):
            for col,key in enumerate(keys):grid.setItem(row,col,QTableWidgetItem('\n'.join(rule[key]) if isinstance(rule[key],list) else str(rule[key])))
            from .metadata import BUILTINS
            fields=QComboBox()
            for key,meta in self.library_db.new_api.field_metadata.items():
                if key in BUILTINS or key.startswith('#'):fields.addItem((meta.get('name') or key)+' ('+key+')',key)
            fields.addItem('用户分类 (User Categories)','user_categories')
            index=fields.findData(rule['field'])
            if index<0:fields.addItem(rule['field'],rule['field']);index=fields.count()-1
            fields.setCurrentIndex(index);grid.setCellWidget(row,0,fields)
            action=QComboBox()
            for text,value in [('不使用','none'),('生成 / 更新收藏夹','create'),('删除匹配收藏夹','delete')]:action.addItem(text,value)
            action.setToolTip('生成：按来源值建架；删除：删除匹配的整个收藏夹，不删除书籍文件；停用不会直接删除收藏夹。')
            action.setCurrentIndex(action.findData(rule['action']));grid.setCellWidget(row,1,action)
        for row,r in enumerate(p['rules']):fill(row,r)
        add=QPushButton('添加规则');rule_bar=QHBoxLayout();rule_bar.addWidget(add);rule_bar.addStretch();layout.addLayout(rule_bar)
        def addrow():row=grid.rowCount();grid.insertRow(row);fill(row,default_rule('tags'))
        add.clicked.connect(addrow)
        layout.addWidget(QLabel('规则不再使用时，把动作改为“不使用”。最少书数不足不会自动删除收藏夹。'))
        case=QCheckBox('匹配分类规则时忽略英文大小写');case.setChecked(p['settings']['ignore_case']);layout.addWidget(case)
        layout=sections[3]
        layout.addWidget(QLabel('保护 Kindle 已有收藏关系'))
        preserve=QCheckBox('保留未纳管的收藏夹及归属（始终启用）');preserve.setChecked(True);preserve.setEnabled(False);layout.addWidget(preserve)
        layout.addWidget(QLabel('KC++ 不得修改或删除的收藏夹名称：每行一个，正则以 re: 开头'))
        patterns=QTextEdit();patterns.setObjectName('protected_patterns');patterns.setMinimumHeight(160);patterns.setPlaceholderText('每行一个收藏夹名称，例如：待读\n也可填写以 re: 开头的正则表达式');patterns.setPlainText('\n'.join(p['settings']['ignore_all']));layout.addWidget(patterns)
        protected=set(self.snapshot['policy']['protected_collections'])
        names=[c['name'] for c in self.snapshot['collections'] if c['uuid'] in protected]
        layout.addWidget(QLabel('设备端额外保护（本窗口不能解除）：'+('、'.join(names) or '无')))
        layout.addStretch()
        def mode_changed():
            enabled=mode.currentData()!='manual';custom=mode.currentData()=='custom'
            column_notice.setVisible(custom and not selected_field())
            for widget in (column_label,column,column_help):widget.setVisible(custom)
            tags_hint.setVisible(mode.currentData()=='tags')
            for widget in (column,scope_view,grid,add,extra_label):widget.setEnabled(enabled)
            tabs.setTabEnabled(1,enabled);tabs.setTabEnabled(2,enabled)
            range_mode.setEnabled(enabled)
            scope_panel.setVisible(enabled and range_mode.currentIndex()==1)
            range_hint.setText(('新匹配的书籍会自动加入；新书的空列不会清空原归属。' if range_mode.currentIndex()==0 else '仅同步下方选中的书籍，新书不会自动加入。') if enabled else '纯 KC++ 模式不使用列同步范围。')
            for i in range(scope_bar.count()):scope_bar.itemAt(i).widget().setEnabled(enabled)
            show_existing()
        mode.currentIndexChanged.connect(mode_changed);range_mode.currentIndexChanged.connect(mode_changed);column.currentIndexChanged.connect(mode_changed);mode_changed()
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);root.addWidget(buttons)
        def accept_settings():
            if getattr(self.gui,'must_restart_before_config',False):
                tabs.setCurrentIndex(0);column_notice.setText('新列或列设置等待重启生效。请关闭此窗口并重启 Calibre，再选择书架列保存。');column_notice.show();return
            if mode.currentData()!='manual' and not selected_field():
                tabs.setCurrentIndex(0);column_notice.show();column.setFocus();return
            dialog.accept()
        buttons.accepted.connect(accept_settings);buttons.rejected.connect(dialog.reject)
        if scope_tab:tabs.setCurrentIndex(1)
        if not dialog.exec():return
        try:
            settings=copy.deepcopy(p['settings'])
            settings.update(ignore_case=case.isChecked(),ignore_all=[v for v in patterns.toPlainText().splitlines() if v],keep_kindle_only=True)
            if mode.currentData()=='manual':
                if QMessageBox.question(self,'使用纯 KC++ 整理', '后续任务只采用手动草稿；原书架列和规则配置保留但暂停使用，不清空列值。\n切回列模式时需重新预览；已在 Kindle 确认的手动移除不会被盲目加回。\n保存此设置？')!=QMessageBox.StandardButton.Yes:return
                def saved_manual(value):
                    self.preview=None;self.prepared=None;self.preview_ready=False;self.plan_model.replace([])
                    self.refresh_mode_summary();self.show_notice('已切换为纯 KC++ 整理；无需列回填，请重新预览。','success');self.update_buttons()
                QTimer.singleShot(0,lambda:self.background(lambda:self.service.configure_manual(p['library_uuid'],self.snapshot,p['revision'],settings),saved_manual))
                return
            if not selected_field():raise Invalid('列同步需要多值文本列；可先在 Calibre 创建，或选择纯 KC++ 模式')
            rules=[]
            for row in range(grid.rowCount()):
                r=copy.deepcopy(p['rules'][row]) if row<len(p['rules']) else default_rule('tags')
                for col,key in enumerate(keys):
                    if col in (0,1):r[key]=grid.cellWidget(row,col).currentData();continue
                    value=grid.item(row,col).text();r[key]=int(value) if key=='minimum' else value.splitlines() if key in ('ignore','include') else value
                if r['action'] not in ('none','create','delete'):raise Invalid('动作只能为 none/create/delete')
                rules.append(r)
            selected={}
            for index in scope_view.selectionModel().selectedRows():
                row=scope.rows[index.row()];selected.setdefault(row['uid'],[]).append(row['bid'])
            scope_mode='all' if range_mode.currentIndex()==0 else 'selected'
            if scope_mode=='all':selected=copies
            field=selected_field();settings=copy.deepcopy(p['settings'])
            settings.update(ignore_case=case.isChecked(),ignore_all=[v for v in patterns.toPlainText().splitlines() if v],keep_kindle_only=True)
            old_count=sum(len(b['copies']) for b in p['column_baseline'].values());new_count=sum(map(len,selected.values()))
            if QMessageBox.question(self,'确认设置变更',f'书架列：{p["field"] or "未设置"} → {field}\n列同步范围：{range_mode.currentText()}\n同步范围内记录：{old_count} → {new_count}（含待识别项）\n分类规则：{len(p["rules"])} → {len(rules)} 条\n保存不会写入 Kindle 或 Calibre 列。移出范围保留原归属；之后必须重新预览。')!=QMessageBox.StandardButton.Yes:return
            snapshot=self.snapshot;api=self.library_db.new_api
            # Reuse the captured metadata fingerprint for the chosen field.
            from .metadata import read_metadata
            def work():
                current=read_metadata(api,fields)
                if current['fingerprint']!=metadata['fingerprint']:raise Invalid('书库已改变，请重新打开设置')
                one=read_metadata(api,[field])
                return self.service.configure(api,p['library_uuid'],snapshot,field,selected,rules,settings,p['revision'],one['fingerprint'],scope_mode=scope_mode)
            def saved(value):
                self.preview=None;self.prepared=None;self.preview_ready=False;self.plan_model.replace([])
                self.refresh_mode_summary();self.show_notice('书架与同步设置已保存；请重新生成修改预览。','success');self.update_buttons()
            QTimer.singleShot(0,lambda:self.background(work,saved))
        except Exception as e:self.error(e)
    def migrate_settings(self):
        path,_=QFileDialog.getOpenFileName(self,'导入原 Kindle Collections 配置','','JSON (*.json)')
        if not path:return
        try:
            p=self.require_profile();value=migrate(read(path),p['library_uuid'],p['device']['storage_uuid'])
            text=f'读取 {len(value["rules"])} 条规则。\n'+'\n'.join(value['warnings'])+'\n迁移设置？原文件保留。'
            if QMessageBox.question(self,'迁移预览',text)!=QMessageBox.StandardButton.Yes:return
            p.update(rules=value['rules'],settings=value['settings'],legacy_original=value['original'])
            self.service.state.save(self.service.key(p['library_uuid'],self.snapshot),p,p['revision']);self.preview=None;self.error('已迁移；请审阅规则再预览。')
        except Exception as e:self.error(e)
    def mtp_settings(self):
        from calibre.utils.config import JSONConfig
        from .mtp import PREFERENCE,can_enable
        settings=JSONConfig('plugins/kc-plus-transport')
        dialog=QDialog(self);dialog.setWindowTitle('实验性 MTP 传输');layout=QVBoxLayout(dialog)
        option=QCheckBox('启用实验性 MTP（KPW6 / 2024 款 / Scribe 等）')
        option.setChecked(bool(settings.get(PREFERENCE,False)));layout.addWidget(option)
        if not can_enable(self.gui.device_manager) and not option.isChecked():option.setEnabled(False)
        note=QLabel('尚无 KPW6 真机验证。开启仅允许尝试传输，不表示设备已具备编辑能力。\n'
            '只有连接到 MTP Kindle 才能开启；开关不会安装文件，也不会改变普通 USB 设备的传输方式。\n'
            '安装后断开连接，在 Kindle 运行带 -MTP 的刷新 / 测试 / 执行入口；重连读取结果。\n'
            '支持安装、读取快照与回执、发送和重试任务、能力测试及保护策略。\n'
            'KC 0.6.15 起支持刚传入的新书：传书完成后读取设备状态，即可整理并发送；断开后执行。\n'
            '首次发现和发送前需读取新书文件校验，大文件会增加等待时间。\n'
            '可以撤下已核验的实验入口；自动清理暂未开放。关闭开关不会撤销已发送任务。\n'
            '断线后使用任务记录中的「核对 / 重试原任务」，不要另建重复任务。')
        note.setWordWrap(True);layout.addWidget(note)
        retry=QPushButton('核对 / 继续上一次 MTP 传输（含能力测试）');layout.addWidget(retry)
        retry.setEnabled(bool(settings.get(PREFERENCE,False)))
        def resume():
            dialog.accept()
            def work():
                store=connected_store(self.gui.device_manager)
                if not getattr(store,'experimental_mtp',False):raise Invalid('当前连接不是已启用的实验性 MTP')
                return store.retry_transfer()
            self.device_job(work,self.error,'KC++ 核对原 MTP 传输')
        retry.clicked.connect(resume)
        withdraw=QPushButton('撤下实验入口并关闭 MTP');layout.addWidget(withdraw)
        withdraw.setEnabled(can_enable(self.gui.device_manager))
        def remove_entries():
            dialog.reject();self.rollback_mtp_device()
        withdraw.clicked.connect(remove_entries)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
        if dialog.exec():
            if option.isChecked() and not bool(settings.get(PREFERENCE,False)) and not can_enable(self.gui.device_manager):
                self.error('请先连接 Calibre 已识别的 MTP Kindle，才能开启实验功能。');return
            settings[PREFERENCE]=option.isChecked()
            self.error('实验性 MTP 已开启；先安装配套 KC，再断开连接运行刷新。' if option.isChecked() else '实验性 MTP 已关闭；设备文件保留。')

    def rollback_mtp_device(self):
        from .mtp import rollback_mtp,PREFERENCE
        from .install import FILES
        from calibre.utils.config import JSONConfig
        if QMessageBox.question(self,'撤下 MTP 实验入口',
            '仅删除核验一致的三个 -MTP 启动入口，保留原入口、运行文件、书籍、收藏夹和全部任务记录。\n'
            '这不会撤销已经执行的收藏夹修改，也不会将 MTP 设备变成普通 USB 磁盘。\n'
            '有未完成任务或入口内容不符时将停止；关闭开关后仍可使用此回退入口。是否继续？')!=QMessageBox.StandardButton.Yes:return
        resources=self.gui.iactions['KC++'].load_resources(['runtime/'+name for name in FILES])
        manager=self.gui.device_manager
        def done(count):
            JSONConfig('plugins/kc-plus-transport')[PREFERENCE]=False
            self.error(f'已撤下 {count} 个实验入口并关闭 MTP；原入口及全部数据保留。没有原入口的新设备需要另行安装可用的 KC。')
        self.device_job(lambda:rollback_mtp(manager,resources),done,'KC++ 撤下 MTP 实验入口')

    def install_device(self):
        try:
            from .install import install,FILES
            resources=self.gui.iactions['KC++'].load_resources(['runtime/'+name for name in FILES])
            from .install import VERSION
            from .mtp import is_mtp
            mtp=is_mtp(self.gui.device_manager)
            detail='实验性 MTP 将新增三个 -MTP 入口，保留原入口；尚无 KPW6 真机验证。可在实验设置中撤下入口。' if mtp else '当前为普通 USB 模式，与 MTP 开关无关；此次手动安装会升级 KC，并备份启动入口，可从“恢复设备启动入口”回退。'
            if QMessageBox.question(self,'安装配套 KC','安装 KC '+VERSION+' 运行文件。'+detail+'是否安装？')!=QMessageBox.StandardButton.Yes:return
            def installed(path):
                if mtp:
                    self.error(path)
                    return
                if self.snapshot:
                    hint='已有设备状态，本次升级无需先刷新收藏夹。若任务已发送，安全弹出后直接运行「KC执行收藏夹任务」，无需重发。'
                else:
                    hint='首次使用或设备状态尚未初始化时，安全弹出后先运行「KC刷新收藏夹」；已有状态且任务已发送时，可直接运行「KC执行收藏夹任务」，无需重发。'
                self.error('已安装 KC '+VERSION+'。'+hint+'入口备份：'+path)
            self.device_job(lambda:install(self.gui.device_manager,resources),installed,'KC++ 安装配套 KC')
        except Exception as e:self.error(e)
    def device_settings(self):
        try:
            from .policy import build,send
            self.require_profile();dialog=QDialog(self);dialog.setWindowTitle('受保护收藏夹');layout=QVBoxLayout(dialog)
            layout.addWidget(QLabel('勾选后禁止业务任务修改该收藏夹。此策略单独发送，不混入业务编辑。限流保持保守默认值。'))
            grid=QTableWidget(len(self.snapshot['collections']),1);grid.setHorizontalHeaderLabels(['保护收藏夹']);layout.addWidget(grid)
            protected=set(self.snapshot['policy']['protected_collections'])
            for i,c in enumerate(self.snapshot['collections']):
                item=QTableWidgetItem(c['name']+' ['+c['uuid']+']');item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable);item.setCheckState(Qt.CheckState.Checked if c['uuid'] in protected else Qt.CheckState.Unchecked);grid.setItem(i,0,item)
            buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);layout.addWidget(buttons);buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject)
            if not dialog.exec():return
            selected=[c['uuid'] for i,c in enumerate(self.snapshot['collections']) if grid.item(i,0).checkState()==Qt.CheckState.Checked]
            task=build(self.snapshot,selected)
            from .mtp import execution_hint
            next_step=execution_hint(self.gui.device_manager)
            self.device_job(lambda:send(connected_store(self.gui.device_manager,task['device']),task),lambda _:self.error('保护策略已发送；'+next_step),'KC++ 发送独立保护策略')
        except Exception as e:self.error(e)
    def probe_device(self):
        try:
            from .probe import prepare_probe
            from .deferred import pending
            self.require_profile();available=[b for b in self.snapshot['books'] if not pending(b['uuid']) and b['collection_count']==0 and not self.catalog.book_collections[b['uuid']]]
            if not available:raise Invalid('请先在 Kindle 准备一本不属于任何收藏夹的测试书，再刷新状态')
            labels=[(b['title'] or b['uuid'])+' ['+b['uuid']+']' for b in available]
            label,ok=QInputDialog.getItem(self,'真机编辑能力测试','选择一本文档作为测试书（不修改书籍文件）',labels,0,False)
            if not ok:return
            from .mtp import execution_hint
            next_step=execution_hint(self.gui.device_manager,probe=True)
            if QMessageBox.question(self,'确认六步真机测试','此测试会新建测试架、原地改名、加入与移除测试书，最后删除测试架，并检查书籍计数。失败会停止。发送后：'+next_step+'是否准备？')!=QMessageBox.StandardButton.Yes:return
            task=prepare_probe(self.snapshot,available[labels.index(label)]['uuid'],self.library_id)
            def work():
                store=connected_store(self.gui.device_manager,task['device'])
                if getattr(store,'experimental_mtp',False):return store.send_probe(task)
                from .protocol import digest
                if digest(store.snapshot())!=task['snapshot_digest']:raise Invalid('快照已变')
                if store.path('state/pending.json').exists() or list(store.root.rglob('*.partial')):raise Invalid('先处理未完成任务')
                for path in store.path('inbox').glob('*.json'):
                    if not store.path('results/'+path.name).exists():raise Invalid('先完成已发送任务')
                if store.path('state/probe.json').exists():
                    marker=read(store.path('state/probe.json'));old_id=marker['job_id']
                    if not isinstance(old_id,str) or any(c not in '0123456789abcdef-' for c in old_id):raise Invalid('旧测试标记无效')
                    from .ledger import validate_receipt
                    old_request=read(store.path('inbox/'+old_id+'.json'));old_result=read(store.path('results/'+old_id+'.json'))
                    if not all(r['status']=='confirmed' for r in validate_receipt(old_request,old_result).values()):raise Invalid('上次测试尚未完整确认，不能覆盖')
                    archive=store.path('state/probe-history');archive.mkdir(exist_ok=True)
                    target=archive/(old_id+'.json')
                    if target.exists():raise Invalid('旧测试归档发生冲突')
                    store.path('state/probe.json').rename(target)
                atomic_write(store.path('state/probe.json'),dict(job_id=task['job_id']))
                atomic_write(store.path('inbox/'+task['job_id']+'.json'),task)
            self.device_job(work,lambda _:self.error('测试任务已准备；'+next_step),'KC++ 准备明确授权的真机测试')
        except Exception as e:self.error(e)
    def cancel_device_probe(self):
        from .probe import cancellable_probe,cancel_probe
        def reviewed(task):
            if QMessageBox.question(self,'撤销未执行的能力测试',
                    '测试编号：'+task['job_id']+'\n设备没有该测试的执行记录。撤销会归档请求和测试标记，随后可重新选择测试书。是否撤销？')!=QMessageBox.StandardButton.Yes:return
            self.device_job(lambda:cancel_probe(connected_store(self.gui.device_manager,task['device']),task),
                lambda _:self.show_notice('未执行的能力测试已归档，可重新准备测试。','success'),'KC++ 撤销未执行的能力测试')
        self.device_job(lambda:cancellable_probe(connected_store(self.gui.device_manager)),reviewed,'KC++ 核对能力测试记录')
    def selected_task(self,p,job_id):
        job=self.service.state.job(job_id)
        if not job or job['request']['library_uuid']!=p['library_uuid'] or job['request']['device']!=p['device']:
            raise Invalid('所选任务不属于当前书库或设备，请重新读取任务记录')
        return job
    def check_task(self,job_id):
        try:
            p=self.require_profile();self.selected_task(p,job_id)
            if self.intents:raise Invalid('请先处理当前草稿，再核对设备结果')
            self._check_job_id=job_id;self.load()
        except Exception as e:self.error(e)
    def retry_task(self,job_id=None):
        try:
            p=self.require_profile();jobs=self.service.state.jobs(self.service.key(p['library_uuid'],self.snapshot),status='staged')
            if job_id:jobs=[self.selected_task(p,job_id)]
            if len(jobs)!=1:raise Invalid('没有唯一的待发送任务')
            from .usability import task_actions
            if not task_actions(jobs[0])['retry']:raise Invalid('所选任务已收到执行结果，不能重新传输')
            from .mtp import execution_hint
            next_step=execution_hint(self.gui.device_manager)
            self.device_job(lambda:self.service.retry(self.library_db.new_api,p['library_uuid'],connected_store(self.gui.device_manager,p['device']),jobs[0]),lambda _:self.error('原任务文件已核对，传输完成；'+next_step+'没有生成新任务 ID。'),'KC++ 重试原任务传输')
        except Exception as e:self.error(e)
    def recover_task_draft(self,job_id=None):
        try:
            p=self.require_profile()
            if self.intents:raise Invalid('请先处理当前草稿，避免混合两批操作')
            jobs=[j for j in self.service.state.jobs(self.service.key(p['library_uuid'],self.snapshot)) if j['status'] in ('staged','closed')]
            if not jobs:raise Invalid('没有可恢复的任务')
            if job_id:job=self.selected_task(p,job_id)
            else:
                labels=[j['request']['job_id'] for j in jobs]
                label,ok=QInputDialog.getItem(self,'恢复任务为草稿','先在 Kindle 刷新成功。只恢复未确认部分，必须重新预览，不直接执行。',labels,len(labels)-1,False)
                if not ok:return
                job=jobs[labels.index(label)]
            from .usability import task_actions
            if not task_actions(job)['recover']:raise Invalid('此任务不能直接恢复；请先核对结果，结果不确定时在 Kindle 核验原任务')
            if job.get('migration_id'):
                from .migration import MigrationService
                def recovered(value):
                    from .planner import Catalog
                    snapshot,session=value;self.snapshot=snapshot;self.catalog=Catalog(snapshot);self.active=None;self.browse()
                    self.show_notice('已恢复迁移进度，请在迁移窗口重新预览剩余项。','success')
                    self.migration_center(session['id'])
                self.device_job(lambda:MigrationService(self.service).recover(p['library_uuid'],connected_store(self.gui.device_manager,p['device']),job),recovered,'KC++ 恢复剩余迁移')
                return
            def done(value):
                from .planner import Catalog
                snapshot,values=value
                selected=self.select_recovery(values,snapshot,job)
                if selected is None:return
                # Save the accepted draft before closing the original local record.
                self.snapshot=snapshot;self.catalog=Catalog(snapshot);self.active=None
                self.queue(selected)
                if job['status']=='staged':self.service.state.close_staged(job['request']['job_id'])
                self.persist_draft();self.error('已恢复所选操作为草稿，请重新预览；设备尚未修改。')
            self.device_job(lambda:self.service.recover_draft(p['library_uuid'],connected_store(self.gui.device_manager,p['device']),job,close=False),done,'KC++ 核查并恢复原任务')
        except Exception as e:self.error(e)
    def cancel_unpublished(self,job_id=None):
        try:
            p=self.require_profile();jobs=self.service.state.jobs(self.service.key(p['library_uuid'],self.snapshot),status='staged')
            if job_id:jobs=[self.selected_task(p,job_id)]
            if len(jobs)!=1:raise Invalid('没有唯一的未确认发送任务')
            from .usability import task_actions
            if not task_actions(jobs[0])['cancel']:raise Invalid('此任务已发送或已有执行结果，不能按未发送任务取消')
            self.device_job(lambda:self.service.cancel_unpublished(connected_store(self.gui.device_manager,p['device']),jobs[0]),lambda _:self.error('设备确认未发布，已取消本地任务；可重新预览。'),'KC++ 核对并取消未发送任务')
        except Exception as e:self.error(e)
    def first_run_guide(self):
        from .task_ui import first_use_dialog
        first_use_dialog(self).exec()

    def column_creation_help(self):
        from .column_create import CreateShelfColumn
        CreateShelfColumn(self).exec()
