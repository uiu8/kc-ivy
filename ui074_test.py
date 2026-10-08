import pathlib,tempfile
from types import SimpleNamespace
from calibre.gui2 import Application
from calibre.db.legacy import LibraryDatabase
from calibre.utils.config import JSONConfig
from qt.core import QWidget,QApplication,QTimer,QCheckBox,QDialogButtonBox,QPushButton,QFontDatabase,QFont
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
    def inspect_disabled():
        dialog=QApplication.activeModalWidget()
        observations.append(not dialog.findChild(QCheckBox).isEnabled())
        dialog.reject()
    QTimer.singleShot(50,inspect_disabled);host.mtp_settings()
    gui.device_manager=SimpleNamespace(is_device_present=True,connected_device=SimpleNamespace(_main_prefix='E:/',name='Kindle'))
    QTimer.singleShot(50,inspect_disabled);host.mtp_settings()
    gui.device_manager=SimpleNamespace(is_device_present=True,connected_device=SimpleNamespace(get_mtp_file=lambda:None,is_kindle=False))
    QTimer.singleShot(50,inspect_disabled);host.mtp_settings()
    gui.device_manager.connected_device.is_kindle=True
    def enable():
        dialog=QApplication.activeModalWidget();box=dialog.findChild(QCheckBox)
        observations.append(box.isEnabled() and not box.isChecked());box.setChecked(True)
        buttons=[b for b in dialog.findChildren(QPushButton) if b.text()=='撤下实验入口并关闭 MTP']
        observations.append(len(buttons)==1 and buttons[0].isEnabled())
        image=pathlib.Path(__file__).parent/'screenshots/mtp074-settings.png';image.parent.mkdir(exist_ok=True)
        dialog.grab().save(str(image))
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok).click()
    QTimer.singleShot(50,enable);host.mtp_settings()
    if not enabled():raise AssertionError('enable persistence')
    gui.device_manager=SimpleNamespace(is_device_present=False)
    def disable():
        dialog=QApplication.activeModalWidget();box=dialog.findChild(QCheckBox)
        observations.append(box.isEnabled() and box.isChecked());box.setChecked(False)
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok).click()
    QTimer.singleShot(50,disable);host.mtp_settings()
    if enabled() or observations!=[True]*6:raise AssertionError(observations)
    host.close();db.close()
print('PASS installed',KCPlus.version,'UI: disconnected/USB/non-Kindle blocked, MTP Kindle enables, rollback entry, offline disable')
