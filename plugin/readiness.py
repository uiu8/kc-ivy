"""Evidence-based status descriptions; file presence is not indexing evidence."""
from .deferred import pending

def attention_jobs(jobs,state):
    from .usability import task_status
    finished={'已关闭（历史结果）','已关闭原记录','已完成（无需列回填）','执行及列值处理完成'}
    return [j for j in jobs if task_status(j,state)[0] not in finished]

def book_status(book,jobs=()):
    uid=book['uuid']
    if not pending(uid):return '已有原生编号','按当前设备快照整理；设备上另有修改时需刷新快照。'
    for job in jobs:
        if not any(d['alias']==uid for d in job['request'].get('new_books',[])):continue
        result=job.get('result',{})
        if any(b['alias']==uid for b in result.get('book_bindings',[])):
            return '执行时已匹配编号','请刷新 Kindle 收藏状态并重连，更新电脑快照。'
        affected={o['op_id'] for o in job['request']['operations'] if uid in o['args'].get('members',[])}
        outcomes=[r for r in result.get('operations',[]) if r['op_id'] in affected]
        if any(r['status']=='pending' for r in outcomes):return '执行结果待核验','先核验原任务，不重复发送。'
        if any(r['status'] in ('conflict','failed') for r in outcomes):
            return '上次未能匹配编号','可能尚未索引、文件已变化或匹配不唯一；回执未细分原因。请核对文件并在 Kindle 刷新，不要盲目重试。'
        if not result:return '已提交，等待设备识别','安全弹出，在 Kindle 执行原任务，再重连接收回执。'
    return '已发现文件，索引未确认','可先整理；扩展名受支持不保证原生索引成功，执行前由 Kindle 核验。'

def library_context(snapshot,library):
    source=snapshot.get('mapping',{}).get('calibre_library_uuid')
    text=f'当前书库：{library}\n快照书库：{source or "未记录（可使用明确的手动绑定）"}'
    if source and source!=library:
        text+='\n两者不同：暂停列同步。确认当前 Calibre 书库，等待设备识别完成；安全弹出后运行 KC刷新收藏夹，再重连读取。若仍不同，请勿通过清空列或删除任务解决，可切回原书库或仅使用 kc-ivy 手动整理。'
    return text

def file_guidance(book,jobs=()):
    """Short list label plus a complete explanation for the selected file."""
    state,_=book_status(book,jobs)
    if state=='执行结果待核验':
        return ('先核对原任务','这本书的任务结果还不确定，可能已经改了一部分。',
                '打开“查看任务记录”，选择原任务并核对结果。按提示在 Kindle 核验后重连；现在不要重新发送。')
    if state=='已提交，等待设备识别':
        return ('到 Kindle 执行任务','任务已经发送，电脑还没有收到执行结果。',
                '安全弹出 Kindle → 在 Kindle 运行“KC执行收藏夹任务”（MTP 使用同名 MTP 入口）→ 重连电脑 → 点击“读取设备状态”。')
    if state=='执行时已匹配编号':
        return ('更新电脑里的书单','Kindle 执行任务时已经认出了这本书，但电脑读取的书单还没更新。',
                '安全弹出 → 在 Kindle 运行“KC刷新收藏夹” → 重连电脑 → 点击“读取设备状态”。这一步不需要重发收藏任务。')
    if state=='上次未能匹配编号':
        return ('先在 Kindle 检查这本书','上次任务没能确认这本书。可能是 Kindle 还没认出它，也可能是文件已变化；现有结果不能确定是哪一种。',
                '先安全弹出，在 Kindle 自带书库里确认能找到并打开它。能打开：运行“KC刷新收藏夹”，重连读取，再到任务记录恢复未完成项。找不到或打不开：先检查文件和格式，不要反复发送收藏任务。')
    if '/dictionaries/' in (book.get('location') or '').replace('\\','/').casefold():
        return ('词典目录文件，可先不管','文件在 Kindle 的词典目录里；kc-ivy 扫到了它，但上次读取的 Kindle 书单里没有对应记录。这不代表词典坏了或上传失败。',
                '如果只是用它查词，不想放进收藏夹，可以不处理。若确实想归入收藏夹，先在 Kindle 自带书库确认能找到它，再运行“KC刷新收藏夹”，重连后读取状态。')
    return ('想整理这本书时再处理','文件已经在 Kindle 存储里，但上次读取的 Kindle 书单里没有它。可能是书单没更新，也可能是 Kindle 还没有认出这个文件；电脑目前无法确定。',
            '不想整理它：可以先不管，不影响其他书。\n想整理它：可以先在 kc-ivy 加入收藏夹，预览并发送，再安全弹出到 Kindle 执行任务。\n如果执行未成功：在 Kindle 自带书库确认能找到并打开它，运行“KC刷新收藏夹”，重连后读取；找不到或打不开时，先检查文件格式。')
