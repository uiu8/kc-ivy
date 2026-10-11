"""Soft-float packaging and real shell execution in isolated host fixtures."""
import hashlib,json,os,shlex,sqlite3,struct,subprocess,unittest
from pathlib import Path
from executor_tests import Fixture,ROOT,NODE
from plugin.planner import intent
from plugin.protocol import canonical,loads


class SoftFloat(unittest.TestCase):
    def setUp(self):
        self.f=Fixture()
        self.addCleanup(self.f.close)

    def fallback(self):
        (self.f.bin/'sqlite3').write_text('#!/bin/sh\nexit 127\n',encoding='utf8',newline='\n')
        host=(ROOT/'native/kc-sqlite3-test.exe').as_posix()
        adapter=(ROOT/'sqlite_posix_adapter.mjs').as_posix()
        (self.f.device/'kc-sqlite3').write_text('#!/bin/sh\nexport KC_SQLITE_CLI='+shlex.quote(host)+'\nexec '+shlex.quote(NODE)+' '+shlex.quote(adapter)+' "$@"\n',encoding='utf8',newline='\n')

    def test_bundled_sqlite_full_edit_and_no_replay(self):
        self.fallback()
        req=self.f.send([intent('rename_collection','c1',dict(name='旧固件测试'))])
        r=self.f.run()
        self.assertEqual(r.returncode,0,self.f.logs())
        self.assertEqual(self.f.result(req)['operations'][0]['status'],'confirmed')
        self.assertIn('kc-sqlite3',self.f.logs())
        n=len(self.f.posts());r=self.f.run()
        self.assertEqual(r.returncode,0,self.f.logs());self.assertEqual(len(self.f.posts()),n)

    def test_old_capability_cannot_authorize_new_runtime(self):
        caps=self.f.mount/'kc-sync/state/capabilities.json'
        v=loads(caps.read_bytes());v.pop('runtime');caps.write_bytes(canonical(v))
        self.f.send([intent('rename_collection','c1',dict(name='must not write'))])
        r=self.f.run();self.assertNotEqual(r.returncode,0)
        self.assertEqual(self.f.posts(),[])
        r=self.f.run(['--refresh']);self.assertEqual(r.returncode,0,self.f.logs())
        v=loads(caps.read_bytes())
        self.assertEqual(v['runtime'],'kc-0.7.0-softfloat')
        self.assertEqual(v['verified_operations'],['verify_state'])

    def test_unsupported_schema_preserves_database_and_snapshot(self):
        db=sqlite3.connect(self.f.root/'cc.db');db.execute('ALTER TABLE Collections RENAME TO UnsupportedCollections');db.commit();db.close()
        original=(self.f.root/'cc.db').read_bytes()
        snapshot=self.f.mount/'kc-sync/snapshots/latest.json';before=snapshot.read_bytes()
        r=self.f.run(['--refresh']);self.assertNotEqual(r.returncode,0)
        self.assertEqual(snapshot.read_bytes(),before)
        self.assertEqual((self.f.root/'cc.db').read_bytes(),original)
        self.assertEqual(self.f.posts(),[])

    def test_diagnostic_does_not_export_private_rows_or_tokens(self):
        original=(self.f.root/'cc.db').read_bytes()
        marker='PRIVATE-TOKEN-MUST-NOT-LEAK'
        (self.f.root/'session_token').write_text(marker)
        r=self.f.run(['--diagnose']);self.assertEqual(r.returncode,0,r.stderr)
        reports=list((self.f.mount/'kc-sync/logs').glob('KC环境检查-*.txt'))
        self.assertEqual(len(reports),1)
        report=reports[0].read_text('utf8')
        self.assertIn('ENVIRONMENT_PASS=1',report)
        for secret in [marker,*[b['title'] for b in self.f.s['books']]]:
            self.assertNotIn(secret,report)
        self.assertEqual((self.f.root/'cc.db').read_bytes(),original)
        self.assertEqual(self.f.posts(),[])

    def test_bundled_sqlite_failure_preserves_tasks(self):
        self.fallback()
        (self.f.device/'kc-sqlite3').write_text('#!/bin/sh\nexit 126\n',encoding='utf8',newline='\n')
        req=self.f.send([intent('rename_collection','c1',dict(name='must not write'))])
        r=self.f.run();self.assertNotEqual(r.returncode,0)
        self.assertEqual(self.f.posts(),[])
        self.assertTrue((self.f.mount/'kc-sync/inbox'/(req['job_id']+'.json')).exists())


class NativeABI(unittest.TestCase):
    def test_static_armel_artifacts_match_build_manifests(self):
        for name,manifest in [('kc-backup-check','check-build.json'),('kc-screen','screen-build.json'),('kc-sqlite3','sqlite-build.json')]:
            b=(ROOT/'device'/name).read_bytes()
            self.assertEqual(b[:7],b'\x7fELF\x01\x01\x01')
            self.assertEqual(struct.unpack_from('<H',b,18)[0],40)
            flags=struct.unpack_from('<I',b,36)[0]
            self.assertEqual(flags & 0x400,0,'hard-float flag')
            self.assertEqual(flags & 0xff000000,0x05000000)
            off=struct.unpack_from('<I',b,28)[0]
            size,count=struct.unpack_from('<HH',b,42)
            self.assertFalse(any(struct.unpack_from('<I',b,off+i*size)[0] in (2,3) for i in range(count)), 'dynamic segment/interpreter')
            m=json.loads((ROOT/'native'/manifest).read_text('utf-8-sig'))
            self.assertEqual(m['arm_sha256'],hashlib.sha256(b).hexdigest())
            self.assertEqual(m['target'],'arm-linux-musleabi')

    def test_sqlite_json_readfile_and_icu_refusal(self):
        import tempfile
        host=ROOT/'native/kc-sqlite3-test.exe'
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'fixture.db'
            db=sqlite3.connect(p);db.create_collation('icu',lambda a,b:(a>b)-(a<b))
            db.execute('CREATE TABLE Entries(p_uuid TEXT PRIMARY KEY, title TEXT COLLATE icu)')
            db.execute("INSERT INTO Entries VALUES('id','value')");db.commit();db.close()
            before=p.read_bytes()
            r=subprocess.run([str(host),'-batch','-bail','-readonly',str(p),"SELECT p_uuid FROM Entries; SELECT json_extract('{\"x\":1}','$.x'); SELECT typeof(readfile('/__kc_nonexistent__'));"],capture_output=True)
            self.assertEqual(r.returncode,0,r.stderr)
            self.assertEqual(r.stdout.decode().splitlines(),['id','1','null'])
            r=subprocess.run([str(host),'-batch','-bail','-readonly',str(p),"SELECT 'a' < 'b' COLLATE icu;"],capture_output=True)
            self.assertNotEqual(r.returncode,0)
            self.assertEqual(p.read_bytes(),before)


if __name__ == '__main__':
    suite=unittest.TestSuite()
    for case in (SoftFloat,NativeABI):
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(case))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
