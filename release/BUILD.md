# 构建与检查

需要Calibre9.15+；Python插件运行于Calibre自带Python/Qt中。源码包保留与最终插件相同的KC运行文件，常规插件打包无需重新编译ARM程序。

在源码根目录执行：

```powershell
calibre-debug -e build.py
```

输出dist/kc-ivy_<版本>.zip和build-<版本>.json（版本取自plugin/__init__.py）。ZIP条目时间固定，相同输入得到相同摘要；build清单不表示真机认证。

## 屏幕卡片辅助程序

`kc-screen` 采用Noto Sans SC 的提示字形子集，按屏幕尺寸绘制白底灰度 PNG；查询屏幕尺寸后居中，不直接写帧缓冲。ARM 静态构建沿用已验证检查程序的目标架构，不因此扩大机型兼容承诺。

```powershell
./native/build_screen.ps1 -ZigPath 'D:/software/zig/zig.exe'
```

生成 device/kc-screen、native/kc-screen-test.exe 和 screen-build.json。screen_card_tests.py 使用主机版本验证 PNG 解码、横竖屏布局以及实际 Shell 调用；新卡片位置仍需真机验收。

## 重建原生检查程序（可选）

使用Zig0.13.0、SQLite amalgamation3.26.0；从各项目官方来源取得工具和源码。目标是arm-linux-musleabihf/cortex_a8，静态链接；不能据此声称其他架构支持。原组件摘要记录于native/check-build.json。

```powershell
./native/build_check.ps1 -ZigPath 'D:/software/zig/zig.exe' -SqliteSourceRoot 'D:/software/sqlite-amalgamation-3260000'
```

也可设置KC_ZIG、KC_SQLITE_SOURCE；路径由用户指定，不要求开发者原目录存在。此步骤同时生成用于PC模拟测试的native/kc-backup-check-test.exe。

## 1.0.5 屏幕提示回归

准备下文的主机执行测试依赖后，在源码根目录执行：

```powershell
calibre-debug -c "import unittest,sys; r=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames(['screen_card_tests','screen_progress_tests','executor_tests','mtp_tests.ShellHandoff'])); sys.exit(not r.wasSuccessful())"
calibre-debug -e shell_tests.py
```

屏幕用模拟 eips，验证超时、显示失败不影响编辑、关闭提示以及结果语义；不能证明真实屏幕可见。

## 当前版本集中发布检查

release_check.py、verify_release.py、verify_source.py 自动读取 plugin/__init__.py 的当前版本。verify_release100.py 与 verify_source100.py 仅保留作历史记录，不用于当前发布验证。

Windows下准备Git Bash、Node.js24（node:sqlite）和SQLite3.26 CLI；设置环境变量KC_BASH、KC_NODE、KC_SQLITE_CLI为对应可执行文件绝对路径。先生成上述主机测试程序，然后运行：

```powershell
calibre-debug -e release_check.py
```

检查在.config-release<版本>中隔离安装插件，不替换日常Calibre配置；在临时库/模拟设备中验证。依次构建、隔离安装、核心/原生执行测试、实际Calibre列回填、刷新Shell、Qt页面及ZIP确定性检查。报告位于dist/release-<版本>/checks.json，含最终ZIP摘要；失败时返回非零并保留日志。不会连接真实Kindle。

```powershell
calibre-debug -e source_release.py
calibre-debug -e verify_source.py
```

按照显式清单生成源码ZIP，排除工作区配置、书库、诊断、截图、运行日志和旧发布包；依赖原生测试程序需在本机重建。许可证、文档、设备文件和测试夹具一起保留。源码包根README为发行说明，非开发工作区历史记录。

## 重建中文字形

源码包包含生成好的 native/screen_glyphs.h，普通构建无需原字体。需要重生成时，从官方 google/fonts 的 ofl/notosanssc 取得字体文件，并执行：

```powershell
calibre-debug -e native/generate_screen_glyphs.py D:/software/KC-development/fonts/NotoSansSC.ttf
./native/build_screen.ps1 -ZigPath D:/software/zig/zig.exe
```

字体来源摘要及子集规模见 native/screen-font.json；许可见 licenses/NotoSansSC-OFL.txt。
