"""Shared virtual shelf picker. Selection belongs to an edge, not a whole book."""
from collections import defaultdict
from qt.core import (Qt,QWidget,QVBoxLayout,QHBoxLayout,QPushButton,QComboBox,QLineEdit,
    QLabel,QTimer,QStackedWidget,QListView,QAbstractItemView,QSize,QStyledItemDelegate,
    QColor,QPen,QRect,QEvent,QStyle,QStyleOptionButton,pyqtSignal)
from .ui import Model,table,BookSearchProxy


class ShelfSelection:
    def __init__(self,groups=(),books=(),checked=False):
        self.groups={g['id']:dict(g,members=set(g['members'])) for g in groups}
        self.books={b['id']:b for b in books};self.links=defaultdict(set)
        for gid,g in self.groups.items():
            for bid in g['members']:self.links[bid].add(gid)
        self.chosen={g:set(v['members']) if checked else set() for g,v in self.groups.items()}
        self.enabled=set(self.groups) if checked else set()
    def group_state(self,gid):
        if gid not in self.enabled:return 0
        return 2 if self.chosen[gid]==self.groups[gid]['members'] else 1
    def book_state(self,bid,group=None):
        groups={group} if group is not None else self.links[bid]
        count=sum(g in self.enabled and bid in self.chosen[g] for g in groups)
        return 0 if not count else 2 if count==len(groups) else 1
    def set_groups(self,ids,checked):
        for gid in ids:
            self.chosen[gid]=set(self.groups[gid]['members']) if checked else set()
            if checked:self.enabled.add(gid)
            else:self.enabled.discard(gid)
    def set_books(self,ids,checked,group=None):
        for bid in ids:
            for gid in ({group} if group is not None else self.links[bid]):
                if checked:self.chosen[gid].add(bid);self.enabled.add(gid)
                else:
                    self.chosen[gid].discard(bid)
                    if not self.chosen[gid]:self.enabled.discard(gid)
    def selected_books(self):return set().union(*(self.chosen[g] for g in self.enabled))
    def relations(self):return {g:set(self.chosen[g]) for g in self.enabled}


def snapshot_shelves(snapshot):
    from .planner import Catalog
    cat=Catalog(snapshot)
    books=[dict(id=b['uuid'],name=b['title'] or '未命名书籍',detail=b.get('location') or '')
           for b in snapshot['books'] if not (b.get('location') or '').lower().endswith('.sh')]
    ids={b['id'] for b in books}
    groups=[dict(id=c['uuid'],name=c['name'],members=cat.members[c['uuid']]&ids,
                 note='含未知/非书籍关系' if not c['complete'] or cat.members[c['uuid']]-ids else '') for c in snapshot['collections']]
    return groups,books


def calibre_shelves(api,field):
    from .column_io import assert_field
    assert_field(api,field)
    with api.safe_read_lock:
        ids=api.all_book_ids();titles=api.all_field_for('title',ids);authors=api.all_field_for('authors',ids);values=api.all_field_for(field,ids)
    grouped=defaultdict(set);books=[]
    for bid in sorted(ids):
        books.append(dict(id=bid,name=titles[bid] or '未命名书籍',detail='、'.join(authors[bid] or ())))
        names=values[bid] or ()
        for name in names:grouped['column:'+name].add(bid)
        if not names:grouped['unclassified'].add(bid)
    return [dict(id=g,name=g[7:] if g.startswith('column:') else '未分类书籍',members=b,
                 virtual=g=='unclassified',note='仅分享文件，不生成收藏夹' if g=='unclassified' else '') for g,b in grouped.items()],books


class PickerModel(Model):
    def __init__(self,owner):super().__init__(['名称','选择情况','说明'],owner);self.owner=owner
    def flags(self,index):
        flags=super().flags(index)
        return flags|Qt.ItemFlag.ItemIsUserCheckable if index.column()==0 else flags
    def data(self,index,role=Qt.ItemDataRole.DisplayRole):
        if index.isValid() and index.column()==0 and role==Qt.ItemDataRole.CheckStateRole:
            return Qt.CheckState(self.owner.row_state(self.rows[index.row()]))
        return super().data(index,role)
    def setData(self,index,value,role):
        if role!=Qt.ItemDataRole.CheckStateRole or not index.isValid():return False
        self.owner.check_rows([self.rows[index.row()]],value in (2,Qt.CheckState.Checked));return True


class CardDelegate(QStyledItemDelegate):
    def sizeHint(self,option,index):return QSize(196,134)
    @staticmethod
    def check_rect(option):return QRect(option.rect.right()-32,option.rect.top()+13,20,20)
    def paint(self,painter,option,index):
        row=index.data(Qt.ItemDataRole.UserRole);box=option.rect.adjusted(4,4,-4,-4)
        painter.save();selected=bool(option.state&QStyle.StateFlag.State_Selected)
        painter.setPen(QPen(QColor('#a34838' if selected else '#d5cfc3')));painter.setBrush(QColor('#f0e6d9' if selected else '#faf8f1'));painter.drawRoundedRect(box,4,4)
        painter.setPen(QColor('#706657'));painter.drawText(box.adjusted(12,10,-36,-90),Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignTop,'收藏夹' if row['kind']=='group' else '书籍')
        painter.setPen(QColor('#302d29'))
        name=option.fontMetrics.elidedText(row['name'],Qt.TextElideMode.ElideRight,(box.width()-24)*2-16)
        painter.drawText(box.adjusted(12,36,-12,-32),Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignTop|Qt.TextFlag.TextWordWrap,name)
        painter.setPen(QColor('#706657'));painter.drawText(box.adjusted(12,box.height()-25,-12,-5),Qt.AlignmentFlag.AlignVCenter,option.fontMetrics.elidedText(row['cells'][1],Qt.TextElideMode.ElideRight,box.width()-24))
        painter.restore();check=QStyleOptionButton();check.rect=self.check_rect(option);check.state=QStyle.StateFlag.State_Enabled
        value=index.data(Qt.ItemDataRole.CheckStateRole)
        check.state|=QStyle.StateFlag.State_On if value==Qt.CheckState.Checked else QStyle.StateFlag.State_NoChange if value==Qt.CheckState.PartiallyChecked else QStyle.StateFlag.State_Off
        option.widget.style().drawControl(QStyle.ControlElement.CE_CheckBox,check,painter,option.widget)
    def editorEvent(self,event,model,option,index):
        if event.type()==QEvent.Type.MouseButtonRelease and event.button()==Qt.MouseButton.LeftButton and self.check_rect(option).contains(event.position().toPoint()):
            return model.setData(index,Qt.CheckState.Unchecked if index.data(Qt.ItemDataRole.CheckStateRole)==Qt.CheckState.Checked else Qt.CheckState.Checked,Qt.ItemDataRole.CheckStateRole)
        return False


class ShelfPicker(QWidget):
    changed=pyqtSignal()
    def __init__(self,parent=None):
        super().__init__(parent);self.selection=ShelfSelection();self.active=None
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0);bar=QHBoxLayout();layout.addLayout(bar)
        self.back=QPushButton('所有收藏夹');self.back.clicked.connect(self.go_back);bar.addWidget(self.back)
        self.scope=QComboBox();self.scope.addItems(['收藏夹','全部书籍']);bar.addWidget(self.scope);self.scope.currentIndexChanged.connect(self.scope_changed)
        self.search=QLineEdit();self.search.setClearButtonEnabled(True);self.search.setPlaceholderText('搜索名称；进入收藏夹后搜索书名');bar.addWidget(self.search,1)
        self.mode=QComboBox();self.mode.addItems(['书架卡片','列表']);bar.addWidget(self.mode)
        self.path=QLabel('所有收藏夹');layout.addWidget(self.path)
        self.model=PickerModel(self);self.proxy=BookSearchProxy(self);self.proxy.setSourceModel(self.model);self.proxy.set_terms('');self.proxy.sort(0)
        self.stack=QStackedWidget();layout.addWidget(self.stack,1)
        self.cards=QListView();self.cards.setModel(self.proxy);self.cards.setViewMode(QListView.ViewMode.IconMode)
        self.cards.setResizeMode(QListView.ResizeMode.Adjust);self.cards.setMovement(QListView.Movement.Static)
        self.cards.setGridSize(QSize(196,134));self.cards.setUniformItemSizes(True);self.cards.setWrapping(True)
        self.cards.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop);self.cards.setItemDelegate(CardDelegate(self.cards));self.stack.addWidget(self.cards)
        self.table=table(self.proxy);self.table.setSortingEnabled(True);self.table.sortByColumn(0,Qt.SortOrder.AscendingOrder);self.stack.addWidget(self.table)
        for view in (self.cards,self.table):
            view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection);view.installEventFilter(self)
            view.doubleClicked.connect(self.enter)
        self.mode.currentIndexChanged.connect(self.stack.setCurrentIndex)
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(180)
        self.search.textChanged.connect(lambda:self.timer.start());self.timer.timeout.connect(lambda:self.proxy.set_terms(self.search.text()))
        actions=QHBoxLayout();layout.addLayout(actions)
        for label,fn,tip in [('进入收藏夹',self.open_current,'双击收藏夹或按 Enter 也可进入。'),
            ('全选',lambda:self.check_rows(self.model.rows,True),'勾选当前层全部项目，包含搜索隐藏项；架内只影响该架。'),
            ('全不选',lambda:self.check_rows(self.model.rows,False),'取消当前层全部项目，包含搜索隐藏项；架内只影响该架。'),
            ('勾选所选',lambda:self.check_rows(self.highlighted(),True),'Ctrl / Shift 多选后批量勾选；Space 也可切换。'),
            ('取消所选',lambda:self.check_rows(self.highlighted(),False),'只取消高亮项目。Ctrl+A 高亮当前搜索结果，再按 Space 切换勾选。')]:
            button=QPushButton(label);button.setToolTip(tip);button.clicked.connect(fn);actions.addWidget(button)
        self.summary=QLabel();layout.addWidget(self.summary);self.refresh()
    def load(self,groups,books,checked=False):
        self.selection=ShelfSelection(groups,books,checked);self.active=None;self.scope.setCurrentIndex(0);self.reset_search();self.rebuild()
    def reset_search(self):
        self.timer.stop();self.search.blockSignals(True);self.search.clear();self.search.blockSignals(False);self.proxy.set_terms('')
    def scope_changed(self,*args):self.active=None;self.reset_search();self.rebuild()
    def go_back(self):self.active=None;self.scope.setCurrentIndex(0);self.reset_search();self.rebuild()
    def enter(self,index):
        row=index.data(Qt.ItemDataRole.UserRole)
        if row and row['kind']=='group':self.active=row['id'];self.reset_search();self.rebuild()
    def open_current(self):self.enter(self.stack.currentWidget().currentIndex())
    def highlighted(self):
        return [i.data(Qt.ItemDataRole.UserRole) for i in self.stack.currentWidget().selectionModel().selectedRows()]
    def eventFilter(self,watched,event):
        if event.type()==QEvent.Type.KeyPress:
            if event.key()==Qt.Key.Key_Space:
                rows=self.highlighted()
                if rows:self.check_rows(rows,not all(self.row_state(r)==2 for r in rows))
                return True
            if event.key() in (Qt.Key.Key_Enter,Qt.Key.Key_Return):self.open_current();return True
            if event.key()==Qt.Key.Key_Backspace and self.active is not None:self.go_back();return True
        return super().eventFilter(watched,event)
    def row_state(self,row):return self.selection.group_state(row['id']) if row['kind']=='group' else self.selection.book_state(row['id'],self.active)
    def check_rows(self,rows,checked):
        if rows:
            if rows[0]['kind']=='group':self.selection.set_groups([r['id'] for r in rows],checked)
            else:self.selection.set_books([r['id'] for r in rows],checked,self.active)
        self.refresh();self.changed.emit()
    def rebuild(self):
        s=self.selection
        if self.active is None and self.scope.currentIndex()==0:
            rows=[dict(id=g,name=v['name'],kind='group',detail=v.get('note','')) for g,v in s.groups.items()];self.path.setText('所有收藏夹 · 双击进入；Ctrl / Shift 多选，Space 切换勾选')
        else:
            ids=s.groups[self.active]['members'] if self.active is not None else set(s.books)&set(s.links)
            rows=[dict(id=b,name=s.books[b]['name'],kind='book',detail=s.books[b].get('detail','')) for b in sorted(ids,key=str)]
            self.path.setText(('所有收藏夹 › '+s.groups[self.active]['name']+' · 只改变本收藏夹的选择') if self.active is not None else '全部书籍 · 勾选一本书会包含它的所有收藏归属')
        for row in rows:row['cells']=[row['name'],'',row['detail']]
        self.model.replace(rows);self.refresh()
    def refresh(self):
        s=self.selection
        for r in self.model.rows:
            if r['kind']=='group':
                g=r['id'];text=f'{len(s.chosen[g]) if g in s.enabled else 0} / {len(s.groups[g]["members"])} 本'
                if not s.groups[g]['members']:text='空架 · '+('已勾选' if g in s.enabled else '未勾选')
                if s.group_state(g)==1:text+=' · 部分勾选'
                if s.groups[g].get('note') and not s.groups[g].get('virtual'):text+=' · 待核对'
            else:text=['未勾选','部分归属已勾选','已勾选'][self.row_state(r)]
            r['cells'][1]=text
            r['tooltip']=r['name']+'\n'+text+('\n'+r['detail'] if r['detail'] else '')
        if self.model.rowCount():self.model.dataChanged.emit(self.model.index(0,0),self.model.index(self.model.rowCount()-1,2))
        groups=sum(not s.groups[g].get('virtual') for g in s.enabled)
        self.summary.setText(f'已勾选 {groups} 个收藏夹、{len(s.selected_books())} 本书。搜索和显示模式不会改变勾选。')
