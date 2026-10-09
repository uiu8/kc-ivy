import pathlib,tempfile,subprocess,sys,os,hashlib,json
from zipfile import ZipFile
root=pathlib.Path(__file__).resolve().parent
from plugin import KCPlus
version='.'.join(map(str,KCPlus.version))
package=root/('dist/kc-ivy_'+version+'.zip');source=root/('dist/kc-ivy_'+version+'_source.zip')
with tempfile.TemporaryDirectory(prefix='kc-clean-build-') as tmp:
    folder=pathlib.Path(tmp)
    with ZipFile(source) as z:
        for name in z.namelist():
            if not (folder/name).resolve().is_relative_to(folder):raise AssertionError('Source path escape')
        z.extractall(folder)
    p=subprocess.run([sys.executable,'-e',str(folder/'build.py')],cwd=folder,
        env=dict(os.environ,CALIBRE_CONFIG_DIRECTORY=str(folder/'config')),
        stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    if p.returncode:raise RuntimeError(p.stdout.decode('utf8','replace'))
    if (folder/('dist/kc-ivy_'+version+'.zip')).read_bytes()!=package.read_bytes():raise AssertionError('Clean source rebuild differs')
report=dict(passed=True,plugin_sha256=hashlib.sha256(package.read_bytes()).hexdigest(),
            source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            boundary='Clean source ZIP rebuild using bundled device binary; not a native toolchain or hardware test')
(root/('dist/release-'+version)/'source-check.json').write_text(json.dumps(report,indent=2),encoding='utf8')
print('PASS clean source ZIP reproduces identical plugin ZIP')
