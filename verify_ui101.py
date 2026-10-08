import tempfile
from pathlib import Path
from types import SimpleNamespace
from calibre.gui2 import Application
from calibre.db.legacy import LibraryDatabase
from calibre.gui2.actions import InterfaceAction
from qt.core import QWidget,QAction,QGroupBox,QFontDatabase,QFont
from calibre_plugins.kc_plus.ui import Manager,KCPlusAction
app=Application([])
QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc');app.setFont(QFont('Microsoft YaHei',10))
root=Path(__file__).resolve().parent
with tempfile.TemporaryDirectory() as tmp:
 db=LibraryDatabase(str(Path(tmp)/'library'))
 gui=QWidget();gui.current_db=db;gui.device_manager=SimpleNamespace(is_device_present=False)
 action=SimpleNamespace(gui=gui,qaction=QAction(gui),plugin_path=str(root/'dist/KC++_1.0.1.zip'),open_manager=lambda:None,metadata_ready=lambda *a:None,connection_changed=lambda *a:None)
 action.load_resources=lambda names:InterfaceAction.load_resources(action,names)
 KCPlusAction.genesis(action)
 if action.qaction.icon().isNull():raise RuntimeError('missing toolbar icon')
 for size in [16,32,64,128]:
  pix=action.qaction.icon().pixmap(size,size)
  if pix.isNull():raise RuntimeError('icon render failed')
 action.qaction.icon().pixmap(128,128).save(str(root/'dist/KC++_1.0.1_icon.png'))
 host=Manager(gui)
 groups=[g for g in host.findChildren(QGroupBox) if g.title()=='列值导入与回填']
 group=groups[0];layout=group.parentWidget().layout()
 titles=[layout.itemAt(i).widget().title() for i in range(layout.count()) if isinstance(layout.itemAt(i).widget(),QGroupBox)]
 if titles[:3]!=['日常设置','列值导入与回填','备份、迁移与分享']:raise RuntimeError(titles)
 host.close();db.close()
print('PASS installed 1.0.1 action genesis, packaged SVG at four sizes, settings group order')
