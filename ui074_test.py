import pathlib,tempfile
from types import SimpleNamespace
from calibre.gui2 import Application
from calibre.db.legacy import LibraryDatabase
from calibre.utils.config import JSONConfig
from qt.core import QWidget,QApplication,QTimer,QCheckBox,QLabel,QFontDatabase,QFont
from calibre_plugins.kc_plus.ui import Manager
from calibre_plugins.kc_plus.mtp import enabled
from calibre_plugins.kc_plus import KCPlus

app=Application([])
for font in ('C:/Windows/Fonts/msyh.ttc','C:/Windows/Fonts/simsun.ttc'):
    QFontDatabase.addApplicationFont(font)
app.setFont(QFont('Microsoft YaHei',10))
with tempfile.TemporaryDirectory() as tmp:
    db=LibraryDatabase(str(pathlib.Path(tmp)/'library'))
    gui=QWidget();gui.current_db=db;gui.device_manager=SimpleNamespace(is_device_present=False)
    host=Manager(gui);host.show();app.processEvents()
    if enabled():raise AssertionError('MTP must default off')
    observations=[]
    errors=[]
    def inspect():
        dialog=QApplication.activeModalWidget()
        try:
            text=' '.join(label.text() for label in dialog.findChildren(QLabel))
            observations.append('不支持 MTP' in text and 'ENVIRONMENT_PASS=1' in text and not dialog.findChildren(QCheckBox))
        except Exception as exc:
            errors.append(str(exc))
        finally:
            if dialog:dialog.accept()
    for device in (SimpleNamespace(is_device_present=False),
                   SimpleNamespace(is_device_present=True,connected_device=SimpleNamespace(_main_prefix='E:/',name='Kindle')),
                   SimpleNamespace(is_device_present=True,connected_device=SimpleNamespace(get_mtp_file=lambda:None,is_kindle=True))):
        gui.device_manager=device
        QTimer.singleShot(50,inspect);host.mtp_settings()
    if enabled() or observations!=[True]*3 or errors:raise AssertionError((observations,errors))
    host.close();db.close()
print('PASS installed',KCPlus.version,'UI: USB-only startup instructions, no MTP opt-in')
