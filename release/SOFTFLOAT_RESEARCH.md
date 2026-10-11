# kc-ivy-kpw3：PW3 5.16.2.1.1 适配依据

研究日期：2026-10-11。对象：`codex/kpw3-softfloat`，基线提交 `384f4b57f9b2dc517598d9c4e44a677cb0262d14`。本次只读研究未连接设备、未改写 `cc.db`、未运行项目测试。

## 结论

- **不能说旧 soft-float 固件必须直写数据库。** LibrarianSync 的原始设备端实现从初始提交起就读取 `/var/local/cc.db`，使用 `Entries.p_uuid` 作为书籍/收藏夹 UUID，并通过本机 `/change` 服务提交 `ChangeRequest`；它没有执行 SQLite `INSERT/UPDATE/DELETE` 写入。
- **`localhost:9101/change` 一直存在，但 `/tmp/session_token` 不是一直存在。** 初始代码使用 `http://localhost:9101/change`、`Content-Type: application/json`；NiLuJe fork 的 2020-11-01 提交 `fe1f53a` 才读取 `/tmp/session_token`，并加入 `AuthToken`。因此旧版/旧固件的接口路径结论可以追溯到早期源码，令牌结论应限定为 FW 5.12+ 时代的实现。
- **UUID 字段已被原始源码直接核实。** 读取：`Entries.p_uuid`、`Entries.p_location`、`Entries.p_cdeKey`、`Entries.p_cdeType`；关系表读取 `Collections.i_collection_uuid` 与 `Collections.i_member_uuid`。提交时 collection/book 的 `uuid` 均来自这些字段；新收藏夹才用 `uuid.uuid4()` 生成 UUID。
- **当前分支的安全方向与该原理一致。** `device/run.sh` 从 `/tmp/session_token` 读令牌，以 `AuthToken` 请求 `http://127.0.0.1:9101/change`；`device/export.sql` 只读主数据库并以临时表分析 `Entries`/`Collections`。这证明当前代码选择的是“只读数据库 + 本地服务提交”的路线，但不证明 PW3 真机已验收。
- **PW3 5.16.2.1.1 的环境证据：** Amazon 官方源码索引把 `Kindle_src_5.16.2.1.1_4097470002.tar.gz` 列在 Kindle Paperwhite（7th Generation）下；对应设备证据记录为 `muscat / Wario`、系统版本 `002-juno_16020101_muscat_wario-409747`、固件 `5.16.2.1.1 (409747 002)`。独立 PW3 源码项目记录其为 ARMv7 soft-float ABI、i.MX6SL、内核 `3.0.35-lab126`、glibc 2.20。
- **兼容性边界：** 上述环境证据能支持“目标确实是 soft-float PW3，硬浮点 ELF 不应直接假设可运行”，不能推出 PW3 的 SQLite 版本、`cc.db` schema、`/change` 认证状态或 KC 任务流程与 PW5 相同。项目当前 `KINDLE_ENVIRONMENT.md` 的 SQLite 3.26/JSON1/`readfile()` 记录属于 PW5 5.17.1.0.3，不能当作 PW3 证据。

## 原始源码与行位置

研究使用了公开的 LibrarianSync 源码仓库快照；测试包不包含该第三方源码。

| 事实 | 源码位置 |
|---|---|
| 初始数据库读取：`/var/local/cc.db`；`Entries.p_uuid`、`p_location`、`p_cdeKey`、`p_cdeType`；`Collections.i_collection_uuid`、`i_member_uuid` | [generate_collections.py](https://github.com/NiLuJe/librariansync/blob/cda8880bc571962092d8c19365d92186f81f45e4/librariansync/generate_collections.py#L23-L35)，[同文件读取逻辑](https://github.com/NiLuJe/librariansync/blob/cda8880bc571962092d8c19365d92186f81f45e4/librariansync/generate_collections.py#L39-L67) |
| 原始 2014 实现的 `localhost:9101/change` 与 JSON ChangeRequest | [barsanuphe 初始提交](https://github.com/barsanuphe/librariansync/blob/8f746d7cc1fc257e4e9a39af7730bb360/librariansync/generate_collections.py#L73-L78) |
| 2020-11-01 加入 `/tmp/session_token` 与 `AuthToken` | [fe1f53a](https://github.com/NiLuJe/librariansync/commit/fe1f53adf494d61a1f1df775e65dec2c15c74871)，[当前 cc_update.py](https://github.com/NiLuJe/librariansync/blob/cda8880bc571962092d8c19365d92186f81f45e4/librariansync/cc_update.py#L28-L40)，[提交请求](https://github.com/NiLuJe/librariansync/blob/cda8880bc571962092d8c19365d92186f81f45e4/librariansync/cc_update.py#L106-L121) |
| 按 UUID 生成新收藏夹、提交 collection/member 更新 | [generate_collections.py](https://github.com/NiLuJe/librariansync/blob/cda8880bc571962092d8c19365d92186f81f45e4/librariansync/generate_collections.py#L182-L235) |

## PW3 证据 URL 与位置

- [Amazon Kindle 源码索引](https://digprjsurvey.amazon.com/csad/help/node/200203720#L553-L555)：PW3/Kindle Paperwhite（7th Generation）列出 `Kindle_src_5.16.2.1.1_4097470002.tar.gz`。
- [PW3 Plato fork README](https://github.com/adriaanm/plato/blob/16dfa9b63cc71e4ab440f135b5e20d0ddc35d0bc/README.md#L6-L17)：`muscat`、i.MX6SL Wario、5.16.2.1.1、内核 3.0.35-lab126、ARMv7 soft-float ABI、glibc 2.20。
- 公开资料和社区记录把目标恢复包标为 `4097470002`；本分支不携带恢复包、设备镜像或任何个人设备资料。
- [KindleModding FAQ](https://kindlemodding.org/jailbreaking/jailbreak-faq.html#L45-L50)：社区规则把 5.16.3 作为 hard-float 分界；这是社区兼容性说明，不能替代设备 ELF/运行库检查。

## 对 kc-ivy-kpw3 的直接指导

沿用 `/tmp/session_token`、`127.0.0.1:9101/change` 和 UUID 驱动的 ChangeRequest 设计是有原始源码依据的；不要因为 PW3 较旧就退回直写 `cc.db`。发布说明应把 PW3 5.16.2.1.1 标为“soft-float 环境已取得公开/脱敏证据，KC 数据库 schema、SQLite 能力和本地服务认证仍待真机只读探测及小样本验收”。
