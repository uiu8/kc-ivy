import pathlib,tempfile,subprocess,sys,os,hashlib,json
from zipfile import ZipFile
root=pathlib.Path(__file__).resolve().parent
from plugin import KCPlus
version='.'.join(map(str,KCPlus.version))
package=root/('dist/kc-ivy-kpw3_'+version+'.zip');source=root/('dist/kc-ivy-kpw3_'+version+'_source.zip')
with tempfile.TemporaryDirectory(prefix='kc-clean-build-') as tmp:
    folder=pathlib.Path(tmp)
    with ZipFile(source) as z:
        for name in z.namelist():
            if not (folder/name).resolve().is_relative_to(folder):raise AssertionError('Source path escape')
        z.extractall(folder)
    for binary,manifest in [('kc-backup-check','check-build.json'),('kc-screen','screen-build.json'),('kc-sqlite3','sqlite-build.json')]:
        info=json.loads((folder/'native'/manifest).read_text('utf-8-sig'))
        host=folder/'native'/(binary+'-test.exe')
        if hashlib.sha256(host.read_bytes()).hexdigest()!=info['host_sha256']:
            raise AssertionError('Source host helper hash differs: '+binary)
    p=subprocess.run([sys.executable,'-e',str(folder/'build.py')],cwd=folder,
        env=dict(os.environ,CALIBRE_CONFIG_DIRECTORY=str(folder/'config')),
        stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    if p.returncode:raise RuntimeError(p.stdout.decode('utf8','replace'))
    if (folder/('dist/kc-ivy-kpw3_'+version+'.zip')).read_bytes()!=package.read_bytes():raise AssertionError('Clean source rebuild differs')
report=dict(passed=True,plugin_sha256=hashlib.sha256(package.read_bytes()).hexdigest(),
            source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            boundary='Clean source ZIP rebuild using bundled device binary; not a native toolchain or hardware test')
out=root/('dist/release-'+version);out.mkdir(parents=True,exist_ok=True)
(out/'source-check.json').write_text(json.dumps(report,indent=2),encoding='utf8')
print('PASS clean source ZIP reproduces identical plugin ZIP')
