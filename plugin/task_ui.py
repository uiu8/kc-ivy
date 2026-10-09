"""Task review and compact, optional first-use reference."""
import json
from qt.core import (QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QFrame,QScrollArea,QWidget,
    QSplitter,QTabWidget,QTextEdit,QHeaderView,Qt,QAbstractItemView)
from .theme import InkDialog
from .maintenance_ui import grid
from .usability import task_status,task_actions


def base(parent,title,subtitle):
    d=InkDialog(parent);d.setWindowTitle(title);d.resize(1050,690)
    layout=QVBoxLayout(d);layout.setContentsMargins(22,18,22,18);layout.setSpacing(12)
    heading=QLabel(title);heading.setObjectName('heading');layout.addWidget(heading)
    hint=QLabel(subtitle);hint.setWordWrap(True);layout.addWidget(hint)
    return d,layout


def operation_rows(job,labels):
    names={c['uuid']:c['name'] for c in job['snapshot']['collections']}
    for op in job['request']['operations']:
        if op['kind']=='create_collection':names[op['collection_uuid']]=op['args']['name']
    states={r['op_id']:r for r in job.get('result',{}).get('operations',[])}
    translations=dict(confirmed='已确认成功',conflict='状态冲突',failed='失败',skipped='已跳过',pending='结果待核验',not_run='尚未执行')
    rows=[]
    for op in job['request']['operations']:
        result=states.get(op['op_id'],{});args=op['args'];cid=op['collection_uuid']
        name=names.get(cid,args.get('name','未识别收藏夹'))
        impact=str(len(args['members']))+' 本' if 'members' in args else args.get('name','—')
        if op['kind']=='rename_collection':impact=name+' → '+args['name'];names[cid]=args['name']
        message=result.get('message','')
        if message=='Exact state, counts and existing file sizes verified':message='已核对收藏关系、数量和现有文件大小'
        from .readiness import book_status
        aliases={d['alias'] for d in job['request'].get('new_books',[])}
        for bid in args.get('members',[]):
            if bid in aliases:
                state,hint=book_status(dict(uuid=bid),[job])
                message+='\n'+state+'：'+hint
                break
        rows.append([labels[op['kind']],name,impact,translations.get(result.get('status'),'未确认'),message])
    return rows


class TaskHistoryDialog(InkDialog):
    def __init__(self,host,jobs,selected=None):
        super().__init__(host)
        from .ui import LABELS
        self.setWindowTitle('任务与执行结果');self.resize(1120,720)
        layout=QVBoxLayout(self);layout.setContentsMargins(22,18,22,18);layout.setSpacing(12)
        heading=QLabel('任务与执行结果');heading.setObjectName('heading');layout.addWidget(heading)
        layout.addWidget(QLabel('选择任务查看操作结果；未收到回执不等于执行失败。'))
        split=QSplitter(Qt.Orientation.Horizontal);layout.addWidget(split,1)
        statuses=[task_status(j,host.service.state) for j in jobs]
        rows=[]
        for i,j in enumerate(jobs):
            ops=j['request']['operations'];done=sum(r['status']=='confirmed' for r in j.get('result',{}).get('operations',[]))
            status=statuses[i][0]
            compact={'已完成（无需列回填）':'已完成','执行及列值处理完成':'已完成','执行完成，回填待处理':'待回填','已关闭原记录':'已关闭','已发送，等待执行回执':'等待回执','发送状态待核对':'待核对发送','设备执行完成':'设备已完成'}.get(status,status)
            rows.append([j['request']['job_id'][:8],compact,f'{done} / {len(ops)}'])
        self.tasks=grid(['任务','状态','已确认 / 操作'],rows);self.tasks.setObjectName('task_list');self.tasks.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tasks.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch)
        self.tasks.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeMode.ResizeToContents)
        self.tasks.setColumnWidth(0,92)
        for i,j in enumerate(jobs):
            self.tasks.item(i,0).setToolTip(j['request']['job_id']);self.tasks.item(i,1).setToolTip(statuses[i][0])
        split.addWidget(self.tasks)
        panel=QWidget();right=QVBoxLayout(panel);right.setContentsMargins(8,0,0,0);right.setSpacing(12);split.addWidget(panel)
        self.next_step=QLabel('尚无任务记录。整理书架并发送后，可在这里查看执行结果。');self.next_step.setWordWrap(True);self.next_step.setObjectName('status');right.addWidget(self.next_step)
        self.identity=QLabel('');self.identity.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse);self.identity.setWordWrap(True);right.addWidget(self.identity)
        tabs=QTabWidget();right.addWidget(tabs,1)
        self.operations=grid(['操作','收藏夹','影响','结果','说明'],[]);tabs.addTab(self.operations,'操作明细')
        self.raw=QTextEdit();self.raw.setReadOnly(True);tabs.addTab(self.raw,'原始回执与传输记录')
        actionbar=QHBoxLayout();layout.addLayout(actionbar);self.buttons={}
        def invoke(fn):
            row=self.tasks.currentRow()
            if row<0:return
            jid=jobs[row]['request']['job_id'];self.accept();fn(jid)
        for key,text,fn,tip in [
            ('check','核对结果',host.check_task,'读取设备状态和回执，不重新发送。存在草稿时请先处理草稿。'),
            ('recover','恢复剩余操作',host.recover_task_draft,'只恢复当前任务未确认成功的操作；结果不确定时先在 Kindle 核验。'),
            ('retry','重试传输',host.retry_task,'核对或重传同一任务文件，不生成新任务；收到回执后不可重传。'),
            ('cancel','取消未发送任务',host.cancel_unpublished,'仅设备确认尚未发布时可取消；已发送或结果不确定的任务不可取消。')]:
            button=QPushButton(text);button.setObjectName('task_'+key);button.setToolTip(tip);button.setEnabled(False)
            button.clicked.connect(lambda checked=False,slot=fn:invoke(slot));actionbar.addWidget(button);self.buttons[key]=button
        actionbar.addStretch();close=QPushButton('关闭');close.clicked.connect(self.reject);actionbar.addWidget(close)
        def show(row,*unused):
            if row<0:return
            job=jobs[row];status,next_step=statuses[row]
            self.next_step.setText(status+'\n'+next_step+ ('\n这是迁移任务；恢复操作将返回迁移记录。' if job.get('migration_id') else ''))
            confirmed=sum(r['status']=='confirmed' for r in job.get('result',{}).get('operations',[]))
            self.next_step.setText(self.next_step.text()+f'\n已确认 {confirmed} 项不会恢复重做；“恢复剩余操作”将先显示可选清单。可关闭此窗口稍后处理，任务记录仍保留。')
            self.identity.setText('任务编号：'+job['request']['job_id'])
            values=operation_rows(job,LABELS);self.operations.setRowCount(len(values))
            from qt.core import QTableWidgetItem
            for i,values_row in enumerate(values):
                for k,value in enumerate(values_row):
                    item=QTableWidgetItem(value);item.setToolTip(value);self.operations.setItem(i,k,item)
            self.raw.setPlainText(json.dumps(dict(task_id=job['request']['job_id'],receipt=job.get('result'),transfer_events=job.get('transfer_events',[])),ensure_ascii=False,indent=2))
            available=task_actions(job)
            for key,button in self.buttons.items():button.setEnabled(available[key] and not host.busy and not (host.intents and key in ('recover','check')))
        self.tasks.currentCellChanged.connect(show);split.setSizes([400,680])
        if jobs:self.tasks.setCurrentCell(next((i for i,j in enumerate(jobs) if j['request']['job_id']==selected),0),0)


def first_use_dialog(host):
    d,layout=base(host,'开始使用 kc-ivy','首次准备完成后，日常只需整理、预览和发送；本页可随时关闭。')
    d.resize(850,730)
    scroll=QScrollArea();scroll.setWidgetResizable(True);layout.addWidget(scroll,1)
    content=QWidget();cards=QVBoxLayout(content);cards.setContentsMargins(0,0,8,0);cards.setSpacing(10);scroll.setWidget(content)
    steps=[
        ('01','安装配套 KC','连接受支持的越狱 Kindle，等待 Calibre 识别后安装。已有用户无需重复安装。','安装配套 KC',host.install_device),
        ('02','读取现有收藏夹','安全弹出 → 在 Kindle 运行“KC刷新收藏夹” → 重连电脑，再点击读取。此步骤不修改收藏夹。','读取设备状态',host.load),
        ('03','验证编辑能力','准备测试后安全弹出，在 Kindle 运行“KC验证编辑能力”，重连读取结果。首次使用或固件变更后需要验证。','准备能力测试',host.probe_device),
        ('04','选择整理方式','专用书架列（推荐）：选择已有列或直接新建，名称不限。也可使用原生标签，或仅在 kc-ivy 整理而不使用列。','同步设置',host.configure_rules),
        ('可选','把现有收藏关系导入列','使用列同步时，可先预览再导入 Kindle 现有归属。已有列值请先核对，避免覆盖自己的整理结果。','预览归属导入',host.import_column)]
    for number,title,text,button_text,fn in steps:
        card=QFrame();card.setStyleSheet('QFrame {background:#fbf9f3; border:1px solid #d7d0c3; border-radius:7px;} QLabel {border:0; background:transparent;}');row=QHBoxLayout(card);row.setContentsMargins(16,12,16,12)
        badge=QLabel(number);badge.setFixedWidth(42);badge.setStyleSheet('color:#a34838; font-size:19px;');row.addWidget(badge)
        body=QVBoxLayout();title_label=QLabel(title);title_label.setStyleSheet('font-weight:600; font-size:15px;');body.addWidget(title_label)
        description=QLabel(text);description.setWordWrap(True);body.addWidget(description);row.addLayout(body,1)
        button=QPushButton(button_text);button.setFixedWidth(142);button.setToolTip(text);button.clicked.connect(lambda checked=False,slot=fn:(d.accept(),slot()));row.addWidget(button);cards.addWidget(card)
    cards.addStretch()
    flow=QLabel('日常流程  整理 → 预览 → 发送 → 安全弹出并在 Kindle 执行 → 重连读取结果');flow.setWordWrap(True);flow.setObjectName('status');layout.addWidget(flow)
    note=QLabel('发送不等于执行成功。无需安装旧 Kindle Collections，也无需手动复制 JSON。');note.setWordWrap(True);layout.addWidget(note)
    footer=QHBoxLayout();footer.addStretch();close=QPushButton('知道了');close.clicked.connect(d.reject);footer.addWidget(close);layout.addLayout(footer)
    return d
