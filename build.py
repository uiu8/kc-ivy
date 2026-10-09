import hashlib
import json
import pathlib
import sys
import ast
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

ROOT=pathlib.Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
from plugin.protocol import SCHEMAS
from plugin.install import VERSION as RUNTIME_VERSION

tree=ast.parse((ROOT/'plugin/__init__.py').read_text(encoding='utf-8'))
version=next(ast.literal_eval(node.value) for node in ast.walk(tree)
             if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='version' for t in node.targets))
VERSION='.'.join(map(str,version))

out=ROOT/'dist'; out.mkdir(exist_ok=True)
schemas=ROOT/'schemas'; schemas.mkdir(exist_ok=True)
for name,schema in SCHEMAS.items():
    path=schemas/(name.replace('/','-')+'.schema.json')
    path.write_text(json.dumps(dict(schema,**{'$schema':'https://json-schema.org/draft/2020-12/schema',
        '$id':'https://kc.invalid/schemas/'+path.name}),ensure_ascii=False,indent=2),encoding='utf-8')
sql=(ROOT/'device/export.sql').read_text(encoding='utf-8').replace('\r\n','\n')
script=(ROOT/'device/refresh.sh').read_text(encoding='utf-8').replace('\r\n','\n')
script=script.replace('[ -r "$DB" ] && [ -r "$SCRIPT_DIR/export.sql" ] || exit 1','[ -r "$DB" ] || exit 1')
script=script.replace('    cat "$SCRIPT_DIR/export.sql"',"    cat <<'KC_EXPORT_SQL_EOF'\n"+sql.rstrip()+"\nKC_EXPORT_SQL_EOF")
if '\r' in script or 'cat "$SCRIPT_DIR/export.sql"' in script: raise ValueError('Invalid shell build')
refresh=out/('KC刷新设备状态_'+RUNTIME_VERSION+'测试版.sh')
raw=script.encode('utf-8')
if refresh.exists() and refresh.read_bytes()!=raw:
    raise ValueError('Existing runtime release differs; increment runtime version before rebuilding')
if not refresh.exists():refresh.write_bytes(raw)
def put(z,name,data):
    info=ZipInfo(name,(2026,10,7,0,0,0));info.compress_type=ZIP_DEFLATED
    info.external_attr=0o100644<<16;z.writestr(info,data)

plugin=out/('kc-ivy_'+VERSION+'.zip')
with ZipFile(plugin,'w',ZIP_DEFLATED) as z:
    for path in sorted((ROOT/'plugin').iterdir()):
        if path.suffix not in ('.py','.txt'): continue
        source=path.read_text(encoding='utf-8').replace('\r\n','\n')
        if path.suffix=='.py': compile(source,str(path),'exec')
        put(z,path.name,source.encode('utf-8'))
    for path in sorted((ROOT/'plugin/images').glob('*.svg')):put(z,'images/'+path.name,path.read_bytes())
    for path in sorted(schemas.glob('*.json')):put(z,'schemas/'+path.name,path.read_bytes())
    from plugin.install import FILES
    for name in FILES:
        path=ROOT/'device'/name;data=path.read_bytes()
        if path.suffix in ('.sh','.sql'):data=data.replace(b'\r\n',b'\n')
        put(z,'runtime/'+name,data)
    put(z,'LICENSE', (ROOT/'LICENSE').read_bytes())
    for path in sorted((ROOT/'release').glob('*.md')):put(z,'docs/'+path.name,path.read_bytes())
    for path in sorted((ROOT/'licenses').glob('*.txt')):put(z,'licenses/'+path.name,path.read_bytes())
manifest=dict(stage='stable release; hardware evidence limited to documented history; MTP and migration/sharing experimental',
              plugin=plugin.name,sha256=hashlib.sha256(plugin.read_bytes()).hexdigest())
(out/('build-'+VERSION+'.json')).write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(manifest,ensure_ascii=False))
