"""Run with calibre-debug -e release_check.py. All Calibre settings isolated."""
import os,sys,json,subprocess,time,hashlib
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
from plugin import KCPlus
VERSION='.'.join(map(str,KCPlus.version))
OUT=ROOT/('dist/release-'+VERSION);OUT.mkdir(parents=True,exist_ok=True)
env=dict(os.environ,CALIBRE_CONFIG_DIRECTORY=str(ROOT/('.config-release'+VERSION)),QT_QPA_PLATFORM='offscreen')
debug=Path(sys.executable);customize=debug.with_name('calibre-customize.exe' if os.name=='nt' else 'calibre-customize')
records=[]
def run(name,args,exe=debug):
    start=time.monotonic()
    log=OUT/(name+'.log')
    with log.open('wb') as stream:
        p=subprocess.run([str(exe),*args],cwd=ROOT,env=env,stdout=stream,stderr=subprocess.STDOUT)
    row=dict(name=name,exit_code=p.returncode,seconds=round(time.monotonic()-start,2))
    records.append(row);print(json.dumps(row),flush=True)
    if p.returncode:print(log.read_bytes().decode('utf8','replace')[-7000:],flush=True)
    return p.returncode==0
def suite(names):
    return ['-c',"import sys,unittest;sys.path.insert(0,'.');r=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames("+repr(names)+"));sys.exit(not r.wasSuccessful())"]
def main():
    if not run('build',['-e','build.py']):return False
    if not run('install',['-a','dist/kc-ivy_'+VERSION+'.zip'],customize):return False
    core=['reliability122_tests','device_books121_tests','repair118_tests','tests','workflow_tests','baseline_tests','settings_tests','scope026_tests','chain013_tests',
          'task_recovery017_tests','retention021_tests','usability020_tests','install_detection_tests',
          'migration_share_tests','mtp_tests.MTP','release100_tests','runtime_versions_tests','maintenance108_tests',
          'native_columns109_tests.NativeTags','deferred_tests.Discovery','deferred_tests.Roundtrip','deferred_tests.CalibreNewBook','release114_tests']
    with ThreadPoolExecutor(max_workers=2) as pool:
        checks=[pool.submit(run,'core',suite(core)),pool.submit(run,'native',suite(['executor_tests','mtp_tests.ShellHandoff','deferred_tests.DeferredExecutor','screen_progress_tests','screen_card_tests']))]
        passed=all([c.result() for c in checks])
    for script in ['verify_notice123.py','verify_reliability122.py','verify_books121.py','verify_notice119.py','verify_column117.py','calibre_workflow_tests.py','roundtrip013_test.py','shell_tests.py','ui028_test.py','ui072_test.py','ui074_test.py','task_ui110_tests.py','verify_ui114.py','verify_ui115.py','verify_release.py']:
        passed=run(Path(script).stem,['-e',script]) and passed
    return passed
passed=False
try:passed=main()
finally:
    artifact=ROOT/('dist/kc-ivy_'+VERSION+'.zip')
    report=dict(checks=records,hardware_tested=False,artifact_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest() if artifact.exists() else None)
    report['passed']=bool(passed) and bool(records) and all(r['exit_code']==0 for r in records)
    (OUT/'checks.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
if not passed:raise SystemExit(1)
