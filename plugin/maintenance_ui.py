"""Compact maintenance dashboards; destructive work stays in the caller."""
from qt.core import (QVBoxLayout,QHBoxLayout,QLabel,QFrame,QPushButton,QTableWidget,
    QTableWidgetItem,QHeaderView,QAbstractItemView,QTabWidget,QProgressBar,Qt)
from .theme import InkDialog


def amount(size):
    return f'{size/1048576:.2f} MiB' if size>=1048576 else f'{size/1024:.1f} KiB'


def shell(parent,title,subtitle,metrics):
    dialog=InkDialog(parent);dialog.setWindowTitle(title);dialog.resize(920,660)
    layout=QVBoxLayout(dialog);layout.setContentsMargins(24,20,24,20);layout.setSpacing(16)
    heading=QLabel(title);heading.setObjectName('heading');layout.addWidget(heading)
    label=QLabel(subtitle);label.setWordWrap(True);layout.addWidget(label)
    cards=QHBoxLayout();cards.setSpacing(12);layout.addLayout(cards)
    for name,value in metrics:
        card=QFrame();card.setStyleSheet('QFrame { background:#fbf9f3; border:1px solid #d7d0c3; border-radius:8px; } QLabel { border:0; background:transparent; }')
        box=QVBoxLayout(card);box.setContentsMargins(16,12,16,12)
        box.addWidget(QLabel(name));number=QLabel(value);number.setStyleSheet('font-size:24px; font-weight:600; color:#774a3c;');box.addWidget(number);cards.addWidget(card)
    return dialog,layout


def grid(headers,rows):
    table=QTableWidget(len(rows),len(headers));table.setHorizontalHeaderLabels(headers)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setAlternatingRowColors(True);table.setShowGrid(False);table.verticalHeader().hide()
    table.verticalHeader().setDefaultSectionSize(40)
    for i,row in enumerate(rows):
        for j,value in enumerate(row):
            item=QTableWidgetItem(str(value));item.setToolTip(str(value));table.setItem(i,j,item)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    table.horizontalHeader().setSectionResizeMode(len(headers)-1,QHeaderView.ResizeMode.Stretch)
    return table


def notice(layout,text,level):
    label=QLabel(text);label.setWordWrap(True);label.setObjectName('status');label.setProperty('level',level);layout.addWidget(label)


def footer(dialog,layout,label,enabled):
    row=QHBoxLayout();row.addStretch();close=QPushButton('关闭');close.clicked.connect(dialog.reject);row.addWidget(close)
    button=QPushButton(label);button.setObjectName('primary');button.setEnabled(enabled);row.addWidget(button);layout.addLayout(row)
    return button


def runtime_dialog(parent,plan):
    old=[r for r in plan['versions'] if r['remove']];backups=[b for b in plan['backups'] if b['remove']]
    size=sum(r['size'] for r in old)+sum(b['size'] for b in backups)
    dialog,layout=shell(parent,'设备版本管理','保留正在使用的版本和最新两个其他版本，供需要时回退。',[
        ('正在使用',' / '.join(plan['active']) or '未识别'),('可清理旧版本',str(len(old))),('预计释放',amount(size))])
    tabs=QTabWidget();layout.addWidget(tabs,1)
    rows=sorted(plan['versions'],key=lambda r:tuple(map(int,r['version'].split('.'))),reverse=True)
    tabs.addTab(grid(['运行版本','占用空间','处理方式'],[(r['version'],amount(r['size']),r['status']) for r in rows]),'运行版本')
    tabs.addTab(grid(['备份编号','引用版本','占用','处理方式'],[(b['name'],', '.join(b['refs']) or '旧入口',amount(b['size']),'清理' if b['remove'] else '保留回退') for b in plan['backups']]),f"入口备份 · {len(plan['backups'])}")
    if plan['blocked']:
        tabs.addTab(grid(['需处理的问题'],[(v,) for v in plan['blocked']]),f"检查问题 · {len(plan['blocked'])}")
        notice(layout,'暂不能清理：'+plan['blocked'][0]+'。详见“检查问题”。','warning')
    elif old or backups:notice(layout,f'将清理 {len(old)} 个旧版本、{len(backups)} 份过期入口备份。任务、书籍及数据库备份均保留。','info')
    else:notice(layout,'无需清理，当前版本和回退备份均在保留范围内。','success')
    return dialog,footer(dialog,layout,'清理旧版本' if old or backups else '无需清理',bool(old or backups) and not plan['blocked'])


def storage_dialog(parent,usage,items,note):
    size=sum(x['size'] for x in items)
    device=[r for r in usage if r[0]!='电脑任务历史与列备份']
    local=[r for r in usage if r[0]=='电脑任务历史与列备份']
    dialog,layout=shell(parent,'空间占用与安全清理','诊断日志与中断快照各保留最近 5 份；旧运行版本请在“设备版本管理”中清理。',[
        ('Kindle 占用',amount(sum(s for n,c,s in device))),('电脑占用',amount(sum(s for n,c,s in local))),('可清理诊断文件',str(len(items))),('预计释放',amount(size))])
    tabs=QTabWidget();layout.addWidget(tabs,1)
    for title,rows in [('Kindle 占用',device),('电脑占用',local)]:
        table=grid(['数据分类','文件数','占用空间','本页占比'],[(n,c,amount(s),'') for n,c,s in rows])
        total=sum(s for n,c,s in rows) or 1
        for i,(name,count,size) in enumerate(rows):
            bar=QProgressBar();bar.setRange(0,1000);bar.setValue(round(size/total*1000));bar.setFormat(f'{size/total:.1%}');table.setCellWidget(i,3,bar)
        tabs.addTab(table,title)
    tabs.addTab(grid(['待清理文件','大小'],[(x['path'],amount(x['size'])) for x in items]),f'清理明细 · {len(items)}')
    rules=[('诊断日志 / 中断快照','各保留最近 5 份；本窗口清理超出部分'),('已完成任务','确认执行及回填完毕后保留最近 5 次，读取结果时自动整理'),('未完成 / 待核验任务','保留，不因超出 5 次而删除'),('数据库备份','保留最近 5 份，由设备端管理'),('KC 运行版本 / 入口备份','通过“设备版本管理”单独预览和清理'),('电脑任务历史与列备份','计入电脑占用，不计入设备占用')]
    tabs.addTab(grid(['分类','保留方式'],rules),'保留规则')
    notice(layout,note if note else (f'可清理 {len(items)} 个过期诊断文件。' if items else '无需清理：诊断日志和中断快照未超过保留数量。'), 'warning' if note else 'info' if items else 'success')
    return dialog,footer(dialog,layout,'清理过期文件' if items else '暂无可清理文件',bool(items) and not note)
