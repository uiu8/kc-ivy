# 来源与第三方组件

KC++/KC项目代码由项目贡献者提供，按根目录MIT LICENSE授权。以下组件保留各自条款，不因本项目选择MIT而改变。

- Calibre为外部运行宿主，插件调用其API，不随包分发Calibre。官方源码与许可：https://github.com/kovidgoyal/calibre ，https://github.com/kovidgoyal/calibre/blob/master/LICENSE 。
- kc-backup-check静态包含SQLite 3.26.0。SQLite发布代码为公有领域，官方说明：https://www.sqlite.org/copyright.html 。构建使用sqlite-amalgamation-3260000，校验摘要见native/check-build.json。
- 静态ARM检查程序使用musl及Zig工具链组件。随包保留licenses/musl.txt和licenses/zig.txt的原许可文本。Zig 0.13.0，目标arm-linux-musleabihf / cortex_a8，构建来源和参数见BUILD.md。
- Kindle的BusyBox、sqlite3、curl及本地系统服务由设备提供，本包不分发Kindle固件或认证令牌。/change是内部接口，不承诺其他固件兼容。
- 原Kindle Collections属于外部参考/兼容导入对象，不是运行依赖，不随本包分发其插件ZIP。此说明不是对其他项目重新授权。

检查日期：2026-10-07。后续加入其他第三方源码或二进制，应同步补充出处与许可。

## 中文提示字形

Noto Sans SC 提示字形子集，SIL Open Font License 1.1。见 licenses/NotoSansSC-OFL.txt；来源 https://github.com/google/fonts/tree/main/ofl/notosanssc 。原字体不随包分发，字形表由 native/generate_screen_glyphs.py 生成，来源摘要在 native/screen-font.json。本项目 MIT 不替代字体许可。
