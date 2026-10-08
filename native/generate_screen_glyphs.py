"""Run with calibre-debug -e native/generate_screen_glyphs.py FONT.ttf.
Generates only ASCII and the Chinese characters used by the renderer; OFL applies.
"""
import sys,hashlib,json
from pathlib import Path
from calibre.gui2 import Application
from qt.core import QFontDatabase,QFont,QImage,QPainter,QColor,Qt,QRect
root=Path(__file__).resolve().parent
font_path=Path(sys.argv[1]);app=Application([])
fid=QFontDatabase.addApplicationFont(str(font_path))
if fid<0:raise RuntimeError('Cannot load subset source font')
font=QFont(QFontDatabase.applicationFontFamilies(fid)[0]);font.setPixelSize(29)
font.setWeight(QFont.Weight.Medium)
chars=sorted(set(range(32,127))|{ord(c) for c in (root/'kc_screen.c').read_text(encoding='utf8') if ord(c)>127})
rows=[]
for cp in chars:
 image=QImage(32,32,QImage.Format.Format_RGB32);image.fill(QColor('white'))
 painter=QPainter(image);painter.setFont(font);painter.setPen(QColor('black'))
 painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
 painter.drawText(QRect(0,0,16 if cp<128 else 32,32),Qt.AlignmentFlag.AlignCenter,chr(cp));painter.end()
 pixels=[round(image.pixelColor(x,y).red()/17) for y in range(32) for x in range(32)]
 rows.append('{'+','.join(str((pixels[i]<<4)|pixels[i+1]) for i in range(0,1024,2))+'}')
header='/* Generated from Noto Sans SC, SIL OFL 1.1; see licenses/NotoSansSC-OFL.txt. */\n'
header+='static const unsigned screen_codes[]={'+','.join(map(str,chars))+'};\n'
header+='static const unsigned char screen_bits[][512]={\n'+',\n'.join(rows)+'\n};\n'
(root/'screen_glyphs.h').write_text(header,encoding='utf8')
(root/'screen-font.json').write_text(json.dumps(dict(source='https://github.com/google/fonts/tree/main/ofl/notosanssc',font_sha256=hashlib.sha256(font_path.read_bytes()).hexdigest(),glyph_count=len(chars),cell=32,bits_per_pixel=4),indent=2),encoding='utf8')
print('Generated',len(chars),'glyphs;',len(chars)*512,'bytes')
