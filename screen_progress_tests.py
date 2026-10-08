"""Actual shell with mocked eips; device screen still needs hardware acceptance."""
import unittest,shlex,subprocess,time
from executor_tests import Fixture,ROOT,BASH
from plugin.planner import intent

class ScreenProgress(unittest.TestCase):
    def setUp(self):self.f=Fixture()
    def tearDown(self):self.f.close()
    def display(self,body):
        (self.f.bin/'eips').write_text('#!/bin/sh\n'+body+'\n',encoding='utf8',newline='\n')
    def test_confirmed_receipt_and_final_screen(self):
        self.display('printf "%s\\n" "$*" >> '+shlex.quote((self.f.root/'screen.txt').as_posix()))
        req=self.f.send([intent('rename_collection','c1',dict(name='renamed'))])
        r=self.f.run();self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(self.f.result(req)['operations'][0]['status'],'confirmed')
        self.assertIn('KC: Done. OK 1',(self.f.root/'screen.txt').read_text())
    def test_display_error_does_not_abort_native_edit(self):
        self.display('exit 1')
        req=self.f.send([intent('rename_collection','c1',dict(name='renamed'))])
        r=self.f.run();self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(self.f.result(req)['operations'][0]['status'],'confirmed')
        self.assertIn('SCREEN_PROGRESS_DISABLED',self.f.logs())
    def test_opt_out_never_calls_display(self):
        self.display('echo called > '+shlex.quote((self.f.root/'screen.txt').as_posix()))
        (self.f.mount/'kc-sync/state/screen-progress.off').touch()
        r=self.f.run();self.assertEqual(r.returncode,0,r.stderr)
        self.assertFalse((self.f.root/'screen.txt').exists())
        self.assertIn('SCREEN_PROGRESS=0',self.f.logs())
    def test_hung_display_is_bounded_and_summary_is_truthful(self):
        self.display('exec /usr/bin/sleep 30')
        code=(ROOT/'device/run.sh').read_text(encoding='utf8')
        functions=code[code.index('screen_clock() {'):code.index('case "$MODE" in')]
        script='SCREEN=1; SCREEN_LAST=0; REPORT=/dev/null; '+functions+'\nscreen_note test force\n[ "$SCREEN" = 0 ] || exit 9\n'
        # Use real timeout rather than the fixture sleep shim.
        bindir=self.f.bin.as_posix();bindir='/'+bindir[0].lower()+bindir[2:]
        start=time.monotonic()
        r=subprocess.run([BASH,'--noprofile','--norc','-c','PATH='+shlex.quote(bindir)+':/usr/bin:/bin; export PATH; '+script],capture_output=True,timeout=8)
        self.assertEqual(r.returncode,0,r.stderr);self.assertLess(time.monotonic()-start,7)
        script=functions+"\nscreen_note() { printf '%s\\n' \"$1\"; }\nMODE=sync; PROBE=0; RUN_EXIT=0; SCREEN_JOBS=1; SCREEN_OK=2; SCREEN_REVIEW=1; screen_result\nRUN_EXIT=5; screen_result\nRUN_EXIT=4; screen_result\n"
        r=subprocess.run([BASH,'--noprofile','--norc','-c',script],capture_output=True,timeout=8)
        self.assertIn(b'OK 2 Review 1',r.stdout);self.assertIn(b'Snapshot failed',r.stdout);self.assertIn(b'Stopped',r.stdout)
