import ast,hashlib,pathlib,tempfile,runpy
from zipfile import ZipFile
from types import SimpleNamespace
from plugin.install import install,rollback,FILES,VERSION

root=pathlib.Path(__file__).parent;path=root/'dist/KC++_1.0.0.zip'
before=hashlib.sha256(path.read_bytes()).hexdigest()
runpy.run_path(str(root/'build.py'),run_name='__main__')
if hashlib.sha256(path.read_bytes()).hexdigest()!=before:raise AssertionError('nondeterministic plugin build')
with ZipFile(path) as z:
    resources={'runtime/'+n:z.read('runtime/'+n) for n in FILES}
    prior=root/'dist/KC++_0.7.4_设备测试版.zip'
    if prior.exists():
        with ZipFile(prior) as old:
            for name,data in resources.items():
                if old.read(name)!=data:raise AssertionError('Device runtime differs from 0.7.4: '+name)
    for name,data in resources.items():
        p=root/'device'/name.split('/')[-1];source=p.read_bytes()
        if p.suffix in ('.sh','.sql'):source=source.replace(b'\r\n',b'\n')
        if data!=source:raise AssertionError(name)
    if not all(n in z.namelist() for n in ['LICENSE','docs/README.md','docs/SUPPORT.md','licenses/musl.txt']):raise AssertionError('missing release notices')
    for name in z.namelist():
        if name.endswith('.py'):compile(z.read(name),name,'exec')
        if name.endswith(('.sqlite','.log')) or 'session_token' in name:raise AssertionError('private data in archive')
    with tempfile.TemporaryDirectory() as tmp:
        mount=pathlib.Path(tmp)
        for f in ('documents','system','audible'):(mount/f).mkdir()
        target=mount/'documents/KC执行收藏夹任务.sh';target.write_bytes(b'old launcher')
        manager=SimpleNamespace(is_device_present=True,connected_device=SimpleNamespace(_main_prefix=tmp,name='Kindle'))
        backup=install(manager,resources);rollback(manager,backup);rollback(manager,backup)
        if target.read_bytes()!=b'old launcher':raise AssertionError('rollback')
print('PASS final 1.0 ZIP compilation, source resources, notices, deterministic rebuild, isolated USB install and repeated rollback')
