# Kindle 运行环境与理论支持范围

本报告对应 kc-ivy 1.0.25 / Kindle 端 KC 0.6.15。它把“已经在本项目设备上跑过”和“根据公开型号、固件与运行条件推测可以适配”分开记录。理论支持不等于实测支持，也不建议为了进入某个区间而升级或降级固件。

## 先看结论

当前正式版的确定基线是：**已越狱、能运行 KC 入口、通过 USB 磁盘连接的 PW5，固件 5.17.1.0.3**。桌面端是 Windows + Calibre 9.15 及以上。这个组合有本项目保留的设备诊断、收藏夹读写链路和 kc-ivy 回执处理证据。

下面这些设备是优先理论候选：

| 设备 | 公开固件参考范围 | kc-ivy 判断 | 传输状态 |
| --- | --- | --- | --- |
| Paperwhite 4（PW4，第10代） | 5.16.3—5.18.1.1.1 | 现代 ARMv7/收藏夹数据库路线接近，优先验证 | USB 磁盘候选 |
| Kindle 2019（KT4，第10代） | 5.16.3—5.18.1.1.1 | 与 PW4 同组，需重新做设备诊断和能力测试 | USB 磁盘候选 |
| Oasis 3（KOA3，第10代） | 5.16.3—5.18.2.1.1 | 数据库和内部接口需验证 | USB 磁盘候选 |
| Paperwhite 5（PW5，第11代） | 5.16.3—5.19.2 | 最接近当前证据；5.17.1.0.3 已有本项目记录 | USB 磁盘 |
| Kindle 2022（KT5，第11代） | 5.16.3—5.19.2 | 平台接近 PW5，仍不能直接继承 PW5 结论 | USB 磁盘候选 |

这些设备属于“适配研究对象”，不是当前正式支持：

| 设备 | 当前限制 |
| --- | --- |
| PW3、Oasis 1/2、Kindle 2016/2017 等旧触屏设备 | 可能复用收藏夹服务，但 SQLite、认证、启动入口和运行库需要单独核对 |
| Kindle 2024、PW6、Colorsoft、Scribe | 现代机型的 USB 文件访问需要 MTP/专用传输；kc-ivy 的 MTP 入口属于实验功能，没有对应真机验收 |
| Kindle 1/2/DX/Keyboard、K4/K5、Kindle Touch 早期固件 | 不使用当前数据库和执行链作为默认目标，需独立 legacy 适配 |
| 未越狱 Kindle、Fire 平板、Kindle 手机应用、KOReader 收藏 | 当前不支持。没有可启动 KC 或没有目标 Kindle 收藏夹服务时，Calibre 端不能替代设备端执行 |

Amazon 的更新页目前列出：PW5 和 2022 Kindle 的最高公开版本为 5.19.2，PW4/2019 Kindle 为 5.18.1.1.1，Oasis 3 为 5.18.2.1.1；2024 Kindle、PW6、Colorsoft 和 Scribe 列为 5.20.1。这里的版本是型号公开更新参考，不是 kc-ivy 已验收版本。[Amazon Kindle 更新页](https://digprjsurvey.amazon.co.uk/csad/help/node/GKMQC26VQQMM8XSW?theme=light)

## 已知 Kindle 运行环境

以下内容来自项目保存的 PW5 设备诊断与构建记录：

| 项目 | 记录 |
| --- | --- |
| 型号 / 固件 | Kindle Paperwhite 5 / 5.17.1.0.3 |
| CPU / Shell | armv7l；`/bin/busybox`，脚本按 POSIX `sh` 编写 |
| SQLite | 系统 `sqlite3` 3.26.0；JSON1 和 `readfile()` 可用 |
| 内存观察 | MemTotal 485604 KiB，MemAvailable 202132 KiB；这是一次诊断读数，不是容量承诺 |
| 临时目录 | `/tmp` 所在 tmpfs 总计 65536 KiB；SQLite 工作空间不能用 Kindle 书籍剩余空间替代估算 |
| 书籍存储 | `/mnt/us` 为设备文件系统；任务、快照、备份与日志分开保存 |
| 本地执行接口 | `127.0.0.1:9101/change`；认证令牌从设备 `/tmp/session_token` 读取，令牌不进入报告和发布包 |
| 设备端运行包 | KC 0.6.15，包含 USB/MTP 入口、导出 SQL、备份检查和任务执行文件；ARM 辅助程序为静态构建 |

KC 不直接改写 Kindle 的业务数据库。它只读导出 `cc.db` 的书籍、收藏夹和成员关系，任务执行时通过本地服务提交修改；提交前备份，提交后重新读取并逐项核验。kc-ivy 在电脑端负责列值、手动草稿、预览、任务记录和已确认结果回填。

## 为什么不能只看型号或固件

一台设备要能使用当前链路，需要同时满足：

1. 已越狱，并且仍有可运行 `documents` 入口的启动环境；
2. 设备端能执行 KC 0.6.15 所需的 Shell、SQLite 和静态辅助程序；
3. 收藏夹数据库仍提供当前导出脚本需要的字段；
4. 本地 `/change` 接口和认证方式仍兼容；
5. 电脑端能可靠交换任务、回执和快照；
6. 通过六步小样本能力测试：建架、改名、加入、移除、再次加入、带成员删架，并检查回执与文件保留。

越狱方法只说明“能否取得执行权限”，不说明收藏夹接口、数据库或 USB/MTP 传输兼容。公开越狱规则当前把 PW5、PW6、KT5、KT6、Colorsoft、Scribe 等分到不同方法和固件段；这只能作为研究入口，不能替代 KC 能力测试。[KindleModding 越狱规则](https://raw.githubusercontent.com/KindleModding/kindlemodding.github.io/main/static/jailbreaks.json)

## 运行链路

```text
KC 读取 cc.db 与设备元数据
        ↓
kc-ivy 读取快照、列值和本地草稿
        ↓
计算差异 → 预览 → 本机保存任务
        ↓
USB 发布任务（MTP 为实验传输）
        ↓
Kindle 执行 KC：备份 → /change → 回读核验 → 写回执
        ↓
kc-ivy 重连读取回执，只回填已确认的列值
```

kc-ivy 不依赖旧 Kindle Collections 插件生成日常 JSON；旧 JSON 只用于兼容导入。设备端仍依赖越狱和可用启动器。当前实现以 Kindle 原生书籍索引和设备 UUID 为准，标题不是身份主键；未被 Kindle 索引的文件不能被强行当成已可操作的收藏夹成员。

## 证据、结论和代码位置

| 证据 | 结论 | 代码 / 文档位置 |
| --- | --- | --- |
| PW5 诊断：固件、armv7l、BusyBox、SQLite 3.26、JSON1、`readfile()` | 当前确定运行基线 | 项目保留的脱敏设备诊断；原始设备报告不随公开仓库分发 |
| ARM 静态检查构建清单 | 当前辅助程序目标为 `arm-linux-musleabihf / cortex_a8` | `native/check-build.json`、`release/BUILD.md` |
| 设备端入口和执行脚本 | 本地接口、备份、回读核验和任务回执链路 | `device/run.sh`、`device/refresh.sh`、`device/export.sql` |
| MTP 适配代码和独立入口 | 可以实验性尝试传输，但没有 KPW6/2024 真机结论 | `plugin/mtp.py`、`release/MTP新书实验说明.md` |
| 自动化测试 | 验证 Qt 界面、模拟设备、临时 Calibre 库和发布包一致性 | `dist/release-1.0.25/source-check.json` 及本次发布校验日志 |

## 发布时应如何表述

- **正式支持**：Windows + Calibre 9.15+；已越狱、USB 磁盘模式的 PW5 / 5.17.1.0.3，使用 KC 0.6.15。
- **理论优先候选**：PW4、2019 Kindle、Oasis 3、PW5 其他固件、2022 Kindle；每台都必须重新做诊断和能力测试。
- **实验适配**：PW6、2024 Kindle、Colorsoft、Scribe 的 MTP 传输；迁移与分享的大包流程。
- **不支持**：未越狱设备、KOReader 收藏、Fire/手机应用和没有对应收藏夹服务的旧平台。

报告不包含设备序列号、`/tmp/session_token`、个人书名、整库快照或日志。公开源码时只发布脱敏证据和构建清单。
