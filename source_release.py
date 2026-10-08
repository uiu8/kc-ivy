"""Curated source bundle; never recurse over the development workspace."""
import ast,hashlib,json
from pathlib import Path
from zipfile import ZipFile,ZipInfo,ZIP_DEFLATED

root=Path(__file__).resolve().parent
files={}
for folder,patterns in [('plugin',['*.py','*.txt']),('plugin/images',['*.svg']),('schemas',['*.json']),('device',['*.sh','*.sql','kc-backup-check','kc-screen']),('licenses',['*.txt']),('release',['*.md'])]:
    for pattern in patterns:
        for p in sorted((root/folder).glob(pattern)):files[p.relative_to(root).as_posix()]=p
names=['verify_notice123.py','reliability122_tests.py','verify_reliability122.py','device_books121_tests.py','verify_books121.py','verify_notice119.py','repair118_tests.py','verify_column117.py','verify_ui115.py','verify_release.py','verify_source.py','release114_tests.py','verify_ui114.py','LICENSE','.gitignore','build.py','source_release.py','release_check.py','verify_release100.py','verify_ui101.py','screen_progress_tests.py','verify_source100.py','release100_tests.py',
       'native/build_screen.ps1','native/kc_screen.c','native/screen_glyphs.h','native/generate_screen_glyphs.py','native/screen-font.json','native/screen-build.json','screen_card_tests.py','runtime_versions_tests.py','maintenance108_tests.py','native_columns109_tests.py','task_ui110_tests.py','native/build_check.ps1','native/kc_backup_check.c','native/check-build.json',
       'deferred_tests.py','tests.py','workflow_tests.py','baseline_tests.py','settings_tests.py','scope026_tests.py','chain013_tests.py',
       'task_recovery017_tests.py','retention021_tests.py','usability020_tests.py','install_detection_tests.py',
       'migration_share_tests.py','mtp_tests.py','executor_tests.py','calibre_workflow_tests.py',
       'roundtrip013_test.py','shell_tests.py','ui028_test.py','ui072_test.py','ui074_test.py',
       'sqlite_posix_adapter.mjs','executor_driver.mjs']
for name in names:files[name]=root/name
files['README.md']=root/'release/README.md'
tree=ast.parse((root/'plugin/__init__.py').read_text(encoding='utf8'))
version=next(ast.literal_eval(n.value) for n in ast.walk(tree) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='version' for t in n.targets))
version='.'.join(map(str,version))
out=root/('dist/KC++_'+version+'_source.zip');manifest={}
with ZipFile(out,'w',ZIP_DEFLATED) as z:
    for name,path in sorted(files.items()):
        data=path.read_bytes();manifest[name]=hashlib.sha256(data).hexdigest()
        info=ZipInfo(name,(2026,10,7,0,0,0));info.compress_type=ZIP_DEFLATED;info.external_attr=0o100644<<16
        z.writestr(info,data)
(root/('dist/source-'+version+'.json')).write_text(json.dumps(dict(sha256=hashlib.sha256(out.read_bytes()).hexdigest(),files=manifest),indent=2),encoding='utf8')
print('Created curated source bundle:',out.name,'files:',len(files))
