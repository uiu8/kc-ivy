"""Readable device notices, with technical evidence available on demand."""
from html import escape
from qt.core import (Qt,QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,
                     QTabWidget,QTextBrowser,QTableWidget,QTableWidgetItem,
                     QAbstractItemView,QHeaderView,QSplitter,QApplication)
from .theme import InkDialog
from .deferred import pending
from .readiness import attention_jobs,file_guidance,library_context
from .usability import task_status


def _section(title,text):
    return '<h3>'+escape(title)+'</h3><p>'+escape(str(text)).replace('\n','<br>')+'</p>'


def _browser(name,html=''):
    w=QTextBrowser();w.setObjectName(name);w.setOpenLinks(False)
    w.document().setDefaultStyleSheet('h3 {color:#774a3c; margin-top:16px; margin-bottom:6px;} p {line-height:140%; margin-bottom:12px;}')
    w.document().setDocumentMargin(16);w.setHtml(html);return w


def notice_dialog(host):
    snap=host.snapshot or {};state=host.service.state if host.service else None
    jobs=([state.job(j['job_id']) for j in state.job_summaries(host.service.key(host.library_id,snap))]
          if state and snap else [])
    active=attention_jobs(jobs,state)
    books=[b for b in snap.get('books',[]) if pending(b['uuid'])]
    by_id={b['uuid']:b for b in books}
    mode=host.mode_summary.toolTip();raw=host.status.text()
    context=library_context(snap,host.library_id)
    diagnostic_text=mode+'\n\n'+raw+'\n\n'+context
    latest='暂无本地任务记录。'
    if jobs:
        status,next_step=task_status(jobs[0],state);latest=status+'\n'+next_step
    counts=f'已读取书籍记录 {len(snap.get("books",[]))-len(books)} 项 · 未确认文件 {len(books)} 项 · 收藏夹 {len(snap.get("collections",[]))} 个'
    counts+='\n设备书单生成时间（UTC）：'+str(snap.get('generated_utc','尚未读取'))
    action=(f'有 {len(active)} 个任务需要核对结果或处理回填。点击下方“查看任务记录”继续。'
            if active else '当前没有需要核对结果或处理回填的本地任务。可以继续整理其他书籍。')
    why='文件已在 Kindle 里，但上次读取的 Kindle 书单没有它。可能只是书单没更新，不代表上传失败。'
    d=InkDialog(host);d.setWindowTitle('设备状态与提示');d.resize(940,700)
    layout=QVBoxLayout(d);layout.setContentsMargins(22,18,22,18);layout.setSpacing(12)
    heading=QLabel('设备状态与提示');heading.setObjectName('heading');layout.addWidget(heading)
    subtitle=QLabel('先看是否需要处理；选中书籍可查看原因和操作步骤。');subtitle.setWordWrap(True);layout.addWidget(subtitle)
    tabs=QTabWidget();layout.addWidget(tabs,1)
    overview=_section('现在需要做什么',action)+_section('已读取的设备状态',counts)
    if books:overview+=_section(f'{len(books)} 个文件尚未对应到书单',why+'\n不打算整理这些文件，可以暂时不管；具体说明见“未确认的书籍文件”。')
    source=snap.get('mapping',{}).get('calibre_library_uuid')
    library_hint=('当前 Calibre 书库与设备书单记录的书库一致。' if source==host.library_id else
                  '设备书单没有记录来源书库。需要同步到 Calibre 列时，请先确认书籍对应关系。' if not source else context)
    overview+=_section('整理方式',mode)+_section('书库对应情况',library_hint)+_section('最近任务',latest)
    tabs.addTab(_browser('notice_overview',overview),'状态概览')

    page=QWidget();pl=QVBoxLayout(page);pl.setContentsMargins(0,10,0,0)
    banner=QLabel('为什么在这里：'+why);banner.setObjectName('status');banner.setProperty('level','info');banner.setWordWrap(True);pl.addWidget(banner)
    hint=QLabel('不需要整理的文件可以先不管。点击一行，下方会告诉你具体怎么做。');hint.setWordWrap(True);pl.addWidget(hint)
    split=QSplitter(Qt.Orientation.Vertical);pl.addWidget(split,1)
    table=QTableWidget(len(books),3);table.setObjectName('pending_files')
    table.setHorizontalHeaderLabels(['书名 / 文件名','格式','建议'])
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setAlternatingRowColors(True);table.verticalHeader().hide();table.setWordWrap(False)
    for row,b in enumerate(books):
        path=b.get('location','');fmt=path.rsplit('.',1)[-1].upper() if '.' in path else '未知'
        for col,value in enumerate([b.get('title') or path.rsplit('/',1)[-1],fmt,file_guidance(b,jobs)[0]]):
            item=QTableWidgetItem(value);item.setData(Qt.ItemDataRole.UserRole,b['uuid']);item.setToolTip(value);table.setItem(row,col,item)
    header=table.horizontalHeader();header.setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch)
    header.setSectionResizeMode(1,QHeaderView.ResizeMode.ResizeToContents);header.setSectionResizeMode(2,QHeaderView.ResizeMode.Stretch)
    table.setSortingEnabled(True);table.sortItems(0,Qt.SortOrder.AscendingOrder);split.addWidget(table)
    detail=_browser('file_explanation');split.addWidget(detail);split.setSizes([150,270])
    path_button=QPushButton('显示所选文件的完整路径');path_button.setObjectName('file_path_toggle');path_button.setCheckable(True);pl.addWidget(path_button)
    path_label=QLabel();path_label.setObjectName('file_path');path_label.setTextFormat(Qt.TextFormat.PlainText)
    path_label.setWordWrap(True);path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse);path_label.hide();pl.addWidget(path_label)
    def select_file():
        row=table.currentRow()
        if row<0:return
        b=by_id[table.item(row,0).data(Qt.ItemDataRole.UserRole)]
        recommendation,reason,steps=file_guidance(b,jobs)
        detail.setHtml('<b>'+escape(b.get('title') or '未命名文件')+' · '+escape(recommendation)+'</b>'+_section('为什么会出现在这里',reason)+_section('你可以这样做',steps))
        path_label.setText(b.get('location','未记录路径'))
    table.itemSelectionChanged.connect(select_file)
    def show_path(checked):
        path_label.setVisible(checked);path_button.setText('收起完整路径' if checked else '显示所选文件的完整路径')
    path_button.toggled.connect(show_path)
    if books:table.selectRow(0)
    else:
        detail.setHtml(_section('没有需要说明的文件','当前读取结果中，没有尚未对应到 Kindle 书单的书籍文件。'))
        path_button.setEnabled(False)
    tabs.addTab(page,f'未确认的书籍文件（{len(books)}）')

    diagnostic=QWidget();dl=QVBoxLayout(diagnostic);dl.setContentsMargins(0,10,0,0)
    grouped=_section('任务处理',action)+_section('设备书单',counts)+_section('同步设置',mode)+_section('最近任务',latest)
    grouped+=_section('书库对应情况',library_hint)
    lines=raw.splitlines()
    external=[s for s in lines if s.startswith('外部任务回执')]
    if external:grouped+=_section('其他来源的任务结果','设备里有结果未对应到当前书库的本地任务记录。KC++ 会保留它们，但不会据此更新当前书库的同步起点。任务编号可展开原始记录查看。')
    retention=[s for s in lines if s.startswith('已清理') or s.startswith('诊断记录')]
    if retention:grouped+=_section('历史文件清理','\n'.join(retention)+'\n这是历史文件的保留情况，不要求你删除书籍或重新发送任务。')
    extra=[s for s in lines if s and not s.startswith(('状态生成于','含 ','外部任务回执','已清理','诊断记录','最近任务：'))]
    if extra:grouped+=_section('其他提示','\n'.join(extra))
    dl.addWidget(_browser('diagnostic_summary',grouped),1)
    toggle=QPushButton('展开原始诊断记录（排查问题时使用）');toggle.setObjectName('diagnostic_toggle');toggle.setCheckable(True);dl.addWidget(toggle)
    technical=_browser('raw_diagnostics');technical.setPlainText(diagnostic_text);technical.hide();dl.addWidget(technical,1)
    def show_raw(checked):
        technical.setVisible(checked);toggle.setText('收起原始诊断记录' if checked else '展开原始诊断记录（排查问题时使用）')
    toggle.toggled.connect(show_raw);tabs.addTab(diagnostic,'诊断详情')
    footer=QHBoxLayout();layout.addLayout(footer)
    copy=QPushButton('复制诊断信息');copy.clicked.connect(lambda:QApplication.clipboard().setText(diagnostic_text));footer.addWidget(copy)
    if callable(getattr(host,'dismiss_notice',None)):
        ignore=QPushButton('忽略本条提示');ignore.setToolTip('只隐藏当前提示，不取消任务，也不删除文件。')
        def dismiss():host.dismiss_notice();d.accept()
        ignore.clicked.connect(dismiss);footer.addWidget(ignore)
    tasks=QPushButton('查看任务记录');tasks.setEnabled(bool(jobs));footer.addWidget(tasks)
    target=(active or jobs)
    def history():
        d.accept();host.task_history(target[0]['request']['job_id'] if target else None)
    tasks.clicked.connect(history);footer.addStretch(1)
    close=QPushButton('关闭');close.clicked.connect(d.accept);footer.addWidget(close)
    return d
