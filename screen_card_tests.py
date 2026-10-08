import struct,zlib,subprocess,tempfile,unittest,shlex
from pathlib import Path
from executor_tests import ROOT,Fixture
from plugin.planner import intent

class Card(unittest.TestCase):
 def test_real_png_geometry_and_pixels(self):
  with tempfile.TemporaryDirectory() as t:
   out=Path(t)/'card.png'
   for sw,sh in [(600,800),(1072,1448),(1448,1072),(1860,2480),(320,480),(4096,320)]:
    for msg in ['KC: Op 3/10','KC: Backing up / checking','KC: OK 12345 Review 12345','KC: Snapshot failed. See PC','KC: Checking edit ability','KC: Test step 2/6','KC: Test passed (6/6)','KC: Test NOT confirmed','KC: Snapshot ready','KC: Stopped. Check PC/log']:
     r=subprocess.run([str(ROOT/'native/kc-screen-test.exe'),str(out),msg,str(sw),str(sh)],capture_output=True,check=True)
     x,y=map(int,r.stdout.split());data=out.read_bytes();self.assertEqual(data[:8],b'\x89PNG\r\n\x1a\n')
     off=8;compressed=b''
     while off<len(data):
      n=struct.unpack('>I',data[off:off+4])[0];kind=data[off+4:off+8];body=data[off+8:off+8+n]
      self.assertEqual(zlib.crc32(kind+body)&0xffffffff,struct.unpack('>I',data[off+8+n:off+12+n])[0])
      if kind==b'IHDR':w,h=struct.unpack('>II',body[:8]);self.assertEqual(body[8:10],b'\x08\x00')
      if kind==b'IDAT':compressed+=body
      off+=n+12
     raw=zlib.decompress(compressed);self.assertEqual(len(raw),(w+1)*h)
     self.assertEqual((x,y),((sw-w)//2,(sh-h)//2));self.assertGreaterEqual(y,0)
     self.assertTrue(all(raw[i*(w+1)]==0 for i in range(h)))
     self.assertIn(255,raw);self.assertIn(80,raw)
     self.assertGreater(sum(row.count(0) for row in [raw[i*(w+1)+1:(i+1)*(w+1)] for i in range(h)]),50)
 def test_card_execution_and_receipt(self):
  f=Fixture()
  try:
   host=(ROOT/'native/kc-screen-test.exe').as_posix()
   (f.device/'kc-screen').write_text('#!/bin/sh\nexec '+shlex.quote(host)+' "$1" "$2" 1072 1448\n',encoding='utf8',newline='\n')
   (f.bin/'eips').write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> '+shlex.quote((f.root/'screen.txt').as_posix())+'\n',encoding='utf8',newline='\n')
   req=f.send([intent('rename_collection','c1',dict(name='card-test'))])
   r=f.run();self.assertEqual(r.returncode,0,r.stderr)
   self.assertEqual(f.result(req)['operations'][0]['status'],'confirmed')
   calls=(f.root/'screen.txt').read_text().splitlines();self.assertTrue(calls)
   self.assertTrue(all(c.startswith('-g ') and c.endswith('-x 54 -y 616') for c in calls),calls)
   self.assertIn('SCREEN_TOTAL_MS=',f.logs());self.assertLess(f.logs().index('MESSAGE=KC: Done. OK 1'),f.logs().index('ELAPSED_SECONDS='));self.assertIn('MESSAGE=KC: Done. OK 1',f.logs());self.assertNotIn('SCREEN_CARD_FALLBACK',f.logs())
   for c in calls:self.assertFalse(Path(c.split()[1]).exists())
   posts=len(f.posts())
   r=f.run(['--refresh']);self.assertEqual(r.returncode,0,r.stderr)
   self.assertEqual(len(f.posts()),posts)
   self.assertIn('MESSAGE=KC: Starting refresh',f.logs())
   self.assertIn('MESSAGE=KC: Snapshot ready',f.logs())
  finally:f.close()
