import pathlib
if not __debug__:
    exec(compile(pathlib.Path(__file__).read_text(encoding='utf8'),__file__,'exec',optimize=0),globals());raise SystemExit(0)
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
from calibre.gui2 import Application
from calibre.db.legacy import LibraryDatabase
from qt.core import QWidget,QLabel,QPushButton,QComboBox,QDialogButtonBox,QTimer,QApplication,QFontDatabase
from calibre_plugins.kc_plus.ui import Manager
from calibre_plugins.kc_plus.service import Service
from calibre_plugins.kc_plus.protocol import Invalid
from calibre_plugins.kc_plus.metadata import read_metadata
from calibre_plugins.kc_plus.planner import Catalog,intent
from tests import snapshot

app=Application([])
for font in ['C:/Windows/Fonts/msyh.ttc','C:/Windows/Fonts/simsun.ttc']:QFontDatabase.addApplicationFont(font)
with tempfile.TemporaryDirectory() as tmp:
    root=pathlib.Path(tmp);db=LibraryDatabase(str(root/'library'));api=db.new_api
    s=snapshot();library=str(db.library_id);service=Service(root/'state')
    p=service.profile(library,s)
    assert p['sync_mode']=='column' and p['field']=='' and p['scope_mode']=='all'
    try:service.preview(api,library,s,[])
    except Invalid as e:assert '同步设置' in str(e) and '选择原生标签或多值文本自定义列' in str(e)
    else:raise AssertionError('unconfigured default silently became manual')
    assert not service.state.job_summaries(service.key(library,s))
    gui=QWidget();gui.current_db=db;gui.device_manager=SimpleNamespace(is_device_present=False)
    d=Manager(gui);d.service=service;d.loaded((s,[],Catalog(s),[],[]))
    assert '尚未选择同步列' in QLabel.text(d.status)
    assert not any(w.text() in ('书架 · 墨韵','白话操作流程') for w in d.findChildren(QWidget) if isinstance(w,(QLabel,QPushButton)))
    assert not any(type(w).__name__=='InkLandscape' for w in d.findChildren(QWidget))
    output=pathlib.Path(__file__).parent/'screenshots'/'ink-028';output.mkdir(parents=True,exist_ok=True)
    d.show();app.processEvents();d.resize(1440,900);app.processEvents();d.grab().save(str(output/'clean-main.png'))
    # Missing column cannot close settings as though it were saved. The alternative
    # manual mode stays available and is an explicit choice.
    checks=[]
    def check():
        dialog=QApplication.activeModalWidget()
        try:
            mode=dialog.findChild(QComboBox,'sync_mode');notice=dialog.findChild(QLabel,'missing_column_notice')
            assert mode.currentData()=='custom' and notice.isVisible()
            app.processEvents();dialog.grab().save(str(output/'column-required.png'))
            with patch.object(d,'column_creation_help') as help:
                dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok).click()
                help.assert_not_called();assert dialog.isVisible()
            mode.setCurrentIndex(mode.findData('manual'));assert not notice.isVisible();checks.append(True)
        except Exception as e:checks.append(e)
        finally:dialog.reject()
    QTimer.singleShot(70,check);d.open_sync_settings(p,read_metadata(api,[]),[]);assert checks==[True],checks
    # Initial setup remains reachable after the user has already recorded drafts.
    d.intents=[intent('create_collection','draft',dict(name='新架'))]
    with patch.object(d,'background') as background:d.configure_rules();background.assert_called_once()
    d.intents=[]
    manual=service.configure_manual(library,s,p['revision'],p['settings'])
    reopened=Service(root/'state');assert reopened.profile(library,s)['sync_mode']=='manual'
    assert reopened.metadata(api,manual)['rows']==[]
    # Existing pre-mode profiles without a field retain their previous manual behavior.
    legacy=dict(manual);legacy.pop('sync_mode');assert reopened.metadata(api,legacy)['rows']==[]
    db.create_custom_column('shelf','Kindle书架','text',is_multiple=True);d.close();db.close()
    db=LibraryDatabase(str(root/'library'));api=db.new_api
    m=read_metadata(api,['#shelf'])
    configured=service.configure(api,library,s,'#shelf',{},[],manual['settings'],manual['revision'],m['fingerprint'],scope_mode='all')
    assert configured['sync_mode']=='column' and configured['field']=='#shelf'
    service.preview(api,library,s,[])
    assert service.profile(library,s)['field']=='#shelf'
    db.close()
print('PASS clean UI, default column setup guard, in-dialog missing-column help, initial setup reachable with drafts, real custom column, persisted manual choice and legacy compatibility')
