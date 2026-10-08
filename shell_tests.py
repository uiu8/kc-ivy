import pathlib
if not __debug__:
    exec(compile(pathlib.Path(__file__).read_text(encoding='utf-8'),__file__,'exec',optimize=0),globals())
    raise SystemExit(0)
import hashlib
import json
import os
import shlex
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
from plugin.protocol import canonical,loads,validate

cli=os.environ.get('KC_SQLITE_CLI','D:/software/KC-development/sqlite-3.26.0/tools/sqlite-tools-win32-x86-3260000/sqlite3.exe')
bash=os.environ.get('KC_BASH','C:/Program Files/Git/bin/bash.exe')
node=os.environ.get('KC_NODE','D:/software/node-v24.18.1-win-x64/node.exe')
checks=[]
with tempfile.TemporaryDirectory(prefix='.shell-',dir=ROOT) as temp:
    base=Path(temp); mount=base/'mount'; mount.mkdir(); bindir=base/'bin'; bindir.mkdir()
    db=base/'cc.db'
    con=sqlite3.connect(db)
    con.executescript('''CREATE TABLE Entries(p_uuid TEXT PRIMARY KEY,p_type TEXT,p_titles_0_nominal TEXT,
      p_location TEXT,p_cdeKey TEXT,p_cdeType TEXT,p_collectionCount INTEGER);
      CREATE TABLE Collections(i_collection_uuid TEXT,i_member_uuid TEXT,i_member_cde_type TEXT,
      i_member_cde_key TEXT,i_member_is_present INTEGER,i_is_sideloaded INTEGER,i_order INTEGER);
      INSERT INTO Entries VALUES('book','Entry:Item','测试书','/mnt/us/documents/a.azw3','K','EBOK',1);
      INSERT INTO Entries VALUES('shelf','Collection','手动架',NULL,NULL,NULL,NULL);
      INSERT INTO Collections VALUES('shelf','book','EBOK','K',1,1,0);
      INSERT INTO Collections VALUES('shelf','cloud','EBOK','REMOTE',0,0,1);''')
    con.commit(); con.close()
    before=hashlib.sha256(db.read_bytes()).hexdigest()
    (mount/'metadata.calibre').write_bytes(canonical([dict(lpath='documents/a.azw3',uuid='calibre-book')]))
    (mount/'driveinfo.calibre').write_bytes(canonical(dict(device_store_uuid='storage',last_library_uuid=None)))
    from plugin.install import VERSION
    script=(ROOT/'dist'/('KC刷新设备状态_'+VERSION+'测试版.sh')).read_text(encoding='utf-8')
    script=script.replace("DB='/var/local/cc.db'","DB='"+db.as_posix()+"'")
    script=script.replace("MOUNT='/mnt/us'","MOUNT='"+mount.as_posix()+"'")
    script=script.replace("LOCK='/tmp/kc-sync.lock'","LOCK='"+(base/'lock').as_posix()+"'")
    path=base/'refresh.sh'; path.write_text(script,encoding='utf-8',newline='\n')
    (bindir/'sqlite3').write_text('#!/bin/sh\nexec '+shlex.quote(cli)+' "$@"\n',encoding='utf-8',newline='\n')
    (bindir/'uuidgen').write_text('#!/bin/sh\nexec '+shlex.quote(node)+' -e '+shlex.quote("process.stdout.write(require('crypto').randomUUID()+'\\n')")+'\n',encoding='utf-8',newline='\n')
    # A surprise service call is an immediate failure. No stub can mutate cc.db.
    (bindir/'curl').write_text('#!/bin/sh\nprintf called > '+shlex.quote((base/'forbidden-service-call').as_posix())+'\nexit 99\n',encoding='utf-8',newline='\n')
    binpath=bindir.as_posix()
    binpath='/'+binpath[0].lower()+binpath[2:]
    command='PATH='+shlex.quote(binpath)+':/usr/bin:/bin; export PATH; exec sh '+shlex.quote(path.as_posix())
    def run():
        return subprocess.run([bash,'--noprofile','--norc','-c',command],capture_output=True,timeout=30)
    first=run(); assert first.returncode==0,(first.stdout,first.stderr)
    snap=validate(loads((mount/'kc-sync/snapshots/latest.json').read_bytes()))
    assert len(snap['relations'])==2 and not snap['collections'][0]['complete']
    assert snap['mapping']['stable'] and snap['mapping']['calibre_library_uuid'] is None
    assert snap['books'][0]['calibre_uuid']=='calibre-book'
    assert not (base/'forbidden-service-call').exists()
    assert not (base/'lock').exists()
    checks.extend(['standalone shell starts','complete unknown relations','stable mapping with null library marker','no service call','owned lock released'])
    second=run(); assert second.returncode==0,(second.stdout,second.stderr)
    snap2=validate(loads((mount/'kc-sync/snapshots/latest.json').read_bytes()))
    assert snap2['device']==snap['device'] and snap2['snapshot_id']!=snap['snapshot_id']
    checks.append('stable device identity and new snapshot identity')
    old=(mount/'kc-sync/snapshots/latest.json').read_bytes()
    (base/'lock').mkdir(); locked=run(); assert locked.returncode==3
    assert (mount/'kc-sync/snapshots/latest.json').read_bytes()==old
    (base/'lock').rmdir(); checks.append('existing lock blocks without changing published snapshot')
    (mount/'driveinfo.calibre').write_bytes(canonical(dict(device_store_uuid='other')))
    changed=run(); assert changed.returncode==4
    assert (mount/'kc-sync/snapshots/latest.json').read_bytes()==old
    checks.append('changed storage blocks identity reuse')
    assert hashlib.sha256(db.read_bytes()).hexdigest()==before
    checks.append('cc.db byte-for-byte unchanged across all runs')
(ROOT/'shell-results.json').write_text(json.dumps(dict(passed=True,checks=checks,
    runtime='Git POSIX shell + actual SQLite 3.26.0 on Windows; not Kindle BusyBox',device_writes=0),ensure_ascii=False,indent=2),encoding='utf-8')
print('PASS standalone export shell: '+str(len(checks))+' checks')
