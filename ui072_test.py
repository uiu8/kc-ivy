import pathlib
if not __debug__:
    exec(compile(pathlib.Path(__file__).read_text(encoding='utf8'),__file__,'exec',optimize=0),globals());raise SystemExit(0)
import tempfile,time,io
from types import SimpleNamespace
from unittest.mock import patch
from calibre.gui2 import Application
from calibre.ebooks.metadata.book.base import Metadata
from qt.core import QWidget,Qt,QPushButton,QFontDatabase,QApplication,QKeyEvent,QEvent,QMouseEvent,QPoint,QPointF
from calibre_plugins.kc_plus.ui import Manager
from calibre_plugins.kc_plus.service import Service
from calibre_plugins.kc_plus.planner import Catalog
from calibre_plugins.kc_plus.sharing import new_library,inspect_share
from calibre_plugins.kc_plus.protocol import loads
from calibre_plugins.kc_plus.migration_ui import MigrationCenter
from calibre_plugins.kc_plus import migration_ui
from tests import snapshot
app=Application([])
for font in ['C:/Windows/Fonts/msyh.ttc','C:/Windows/Fonts/simsun.ttc']:QFontDatabase.addApplicationFont(font)
def wait(center):
    until=time.monotonic()+10
    while center.busy and time.monotonic()<until:app.processEvents();time.sleep(.01)
    assert not center.busy,center.notice.text()
def key(view,key,mods=Qt.KeyboardModifier.NoModifier):
    QApplication.sendEvent(view,QKeyEvent(QEvent.Type.KeyPress,key,mods));app.processEvents()
def index(picker,ident):
    return next(picker.proxy.index(i,0) for i in range(picker.proxy.rowCount()) if picker.proxy.index(i,0).data(Qt.ItemDataRole.UserRole)['id']==ident)
def button(picker,text):return next(b for b in picker.findChildren(QPushButton) if b.text()==text)
def click_check(picker,ident):
    rect=picker.cards.visualRect(index(picker,ident));pos=QPoint(rect.right()-22,rect.top()+23);view=picker.cards.viewport()
    for event,buttons in ((QEvent.Type.MouseButtonPress,Qt.MouseButton.LeftButton),(QEvent.Type.MouseButtonRelease,Qt.MouseButton.NoButton)):
        QApplication.sendEvent(view,QMouseEvent(event,QPointF(pos),QPointF(view.mapToGlobal(pos)),Qt.MouseButton.LeftButton,buttons,Qt.KeyboardModifier.NoModifier))
    app.processEvents()
with tempfile.TemporaryDirectory() as tmp:
    root=pathlib.Path(tmp);db=new_library(root/'lib');api=db.new_api;library=str(db.library_id)
    try:
        ids=[api.create_book_entry(Metadata('示例书籍'+str(i),['作者'])) for i in range(3)]
        for i in ids:api.add_format(i,'TXT',io.BytesIO(b'book'),run_hooks=False)
        api.set_field('#kindlecollections',{ids[0]:['文学','待读'],ids[1]:['待读']})
        gui=QWidget();gui.current_db=db;gui.device_manager=SimpleNamespace(is_device_present=False)
        gui.library_view=SimpleNamespace(get_selected_ids=lambda:[ids[2]])
        host=Manager(gui);host.service=Service(root/'state')
        s=snapshot();s['mapping']['calibre_library_uuid']=library;s['relations'].append(dict(s['relations'][0],collection_uuid='c2'))
        s['collections'].append(dict(uuid='empty',name='空收藏夹',complete=True))
        for b,i in zip(s['books'],ids):b['calibre_uuid']=api.field_for('uuid',i)
        host.loaded((s,[],Catalog(s),[],[]));center=MigrationCenter(host);center.show();app.processEvents();center.resize(1180,820);app.processEvents()
        picker=center.backup_picker
        assert picker.mode.currentIndex()==0 and picker.selection.enabled=={'c1','c2','empty'}
        click_check(picker,'empty');assert 'empty' not in picker.selection.enabled
        click_check(picker,'empty');assert 'empty' in picker.selection.enabled
        picker.cards.doubleClicked.emit(index(picker,'c1'));app.processEvents();assert picker.active=='c1'
        picker.proxy.setData(index(picker,'b0'),Qt.CheckState.Unchecked,Qt.ItemDataRole.CheckStateRole)
        assert picker.selection.chosen['c1']=={'b1'} and picker.selection.chosen['c2']=={'b0'}
        assert picker.selection.group_state('c1')==1
        picker.mode.setCurrentIndex(1);assert picker.selection.chosen['c1']=={'b1'}
        picker.table.setCurrentIndex(index(picker,'b0'));picker.table.setFocus();key(picker.table,Qt.Key.Key_Down,Qt.KeyboardModifier.ShiftModifier)
        assert len(picker.highlighted())==2
        picker.table.setFocus();key(picker.table,Qt.Key.Key_A,Qt.KeyboardModifier.ControlModifier)
        assert len(picker.highlighted())==2
        key(picker.table,Qt.Key.Key_Space);assert picker.selection.chosen['c1']=={'b0','b1'}
        key(picker.table,Qt.Key.Key_Space);assert 'c1' not in picker.selection.enabled and picker.selection.chosen['c2']=={'b0'}
        # Full selection includes items hidden by search, and stays local to the open shelf.
        picker.search.setText('书籍0');picker.timer.stop();picker.proxy.set_terms('书籍0');assert picker.proxy.rowCount()==1
        button(picker,'全选').click();assert picker.selection.chosen['c1']=={'b0','b1'}
        button(picker,'全不选').click();assert picker.selection.chosen['c2']=={'b0'}
        picker.proxy.setData(index(picker,'b0'),Qt.CheckState.Checked,Qt.ItemDataRole.CheckStateRole)
        backup=root/'backup.json'
        with patch.object(migration_ui.QFileDialog,'getSaveFileName',return_value=(str(backup),'')):center.save_backup()
        wait(center);value=loads(backup.read_bytes());assert len(value['books'])==1 and len(value['relations'])==2 and len(value['collections'])==3
        picker.go_back();button(picker,'全不选').click();assert not picker.selection.enabled
        button(picker,'全选').click();assert 'empty' in picker.selection.enabled
        # The same picker is used in sharing, with actual Calibre-column groups.
        center.tabs.setCurrentIndex(2);center.load_share_shelves();wait(center);share=center.share_picker
        assert not share.selection.enabled and len(share.selection.groups)==3
        share.cards.doubleClicked.emit(index(share,'column:文学'));app.processEvents()
        button(share,'全选').click();assert share.selection.selected_books()=={ids[0]}
        assert share.selection.group_state('column:待读')==0
        sharefile=root/'share.zip';center.formats.setText('TXT')
        with patch.object(migration_ui.QFileDialog,'getSaveFileName',return_value=(str(sharefile),'')):center.export_books()
        wait(center);m=inspect_share(sharefile);assert len(m['backup']['books'])==1 and [c['name'] for c in m['backup']['collections']]==['文学']
        center.use_calibre_selection();wait(center);assert share.selection.selected_books()=={ids[2]}
        assert share.selection.groups['unclassified']['virtual']
        # Import still runs through the separate import page after the picker redesign.
        center.share_loaded(str(sharefile),m);assert center.share_tabs.currentIndex()==1 and center.import_button.isEnabled()
        target=root/'new';target.mkdir()
        with patch.object(migration_ui.QFileDialog,'getExistingDirectory',return_value=str(target)),patch.object(migration_ui.QMessageBox,'question',return_value=migration_ui.QMessageBox.StandardButton.Yes):center.import_books()
        wait(center);assert '导入完成' in center.notice.text(),center.notice.text()
        from calibre.db.legacy import LibraryDatabase
        imported=LibraryDatabase(str(target));assert len(imported.new_api.all_book_ids())==1;imported.close()
        out=pathlib.Path(__file__).parent/'screenshots'/'migration-072';out.mkdir(parents=True,exist_ok=True)
        center.notice.setText('示例数据：进入收藏夹勾选书籍，再保存或导出。')
        center.tabs.setCurrentIndex(0);picker.mode.setCurrentIndex(0);picker.go_back();app.processEvents();center.grab().save(str(out/'backup-cards.png'))
        picker.cards.doubleClicked.emit(index(picker,'c1'));picker.selection.set_books(['b0'],False,'c1');picker.refresh();app.processEvents();center.grab().save(str(out/'backup-books.png'))
        picker.mode.setCurrentIndex(1);app.processEvents();center.grab().save(str(out/'backup-list.png'))
        center.tabs.setCurrentIndex(2);center.share_tabs.setCurrentIndex(0);share.go_back();app.processEvents();center.grab().save(str(out/'sharing.png'))
        center.close();host.close()
    finally:db.close()
print('PASS shared shelf navigation, partial edge selection, Ctrl+A/Space, full selection with filters, virtual cards/list persistence, real backup/share outputs and import')
