"""Read diagnostic output without requiring an initialized collection snapshot."""
from pathlib import Path
from .protocol import Invalid


def latest_report(mount):
    mount=Path(mount).resolve()
    folder=mount/'kc-sync/logs'
    if not folder.resolve().is_relative_to(mount):
        raise Invalid('环境报告目录越界，停止读取')
    reports=list(folder.glob('KC环境检查-*.txt'))
    if not reports:
        return '尚无报告','请安全弹出 Kindle，运行 KC检查运行环境，完成后重连并读取报告。电脑不会代替 Kindle 执行诊断。'
    path=max(reports,key=lambda p:(p.stat().st_mtime_ns,p.name))
    if not path.resolve().is_relative_to(mount) or not path.is_file() or path.stat().st_size>1024*1024:
        raise Invalid('环境报告路径或大小异常，停止读取')
    body=path.read_text('utf8',errors='replace')
    lines=body.splitlines()
    passed=(lines[:1]==['KC_ENVIRONMENT_V1 | KC 0.7.0 | kc-ivy-kpw3 soft-float | no native edits']
            and lines.count('ENVIRONMENT_PASS=1')==1 and lines[-2:]==['ENVIRONMENT_PASS=1','RUN_FINISHED=1'])
    status='诊断通过' if passed else '未通过或报告不完整'
    next_step='下一步：刷新收藏夹并完成六步编辑能力验证。此报告不是编辑能力凭据。' if passed else '请保留报告并按故障说明处理，暂勿准备编辑任务。'
    return status,path.name+'\n'+next_step+'\n\n'+body
