# 杀戮尖塔 2 Mod 打包

从 [GitHub Releases](https://github.com/zclkkk/sts2-mods/releases/latest) 下载 `sts2-mods.zip`。这套包用于 Steam Public Beta，包含 RitsuLib、BaseLib（含 Compatibility Helper）、随机数预测、Undo And Restart 和 Combat Solver。Combat Solver 仅在单人战斗中启用。

## 安装

退出游戏。ZIP 顶层的 `mods/` 可以放进游戏安装目录。覆盖更新时，先移出同一个 Mod 的旧文件夹，再放入包里的新文件夹。联机时可在游戏的 Mod 列表里核对版本。

Release 同时提供 ZIP 的 SHA-256 和 `mods.bundle.json`，后者记录各上游版本、原始安装包名及其 SHA-256。

## 自动发布

GitHub Actions 每天新加坡时间 10:23 计划运行。也可在 Actions 页面手动运行 **Build and release mods**。每次运行直接查询各作者的最新正式 Release，下载 `mods.json` 指定的 ZIP，核对下载和解压是否成功，再把完整的 `mods/` 打成 ZIP。上游包组合相同就沿用现有 Release。

游戏升级后，CI 继续追踪各作者的发布。游戏补丁和 Mod 发布时间可能有差异，实际兼容性以作者说明和游戏内表现为准。
