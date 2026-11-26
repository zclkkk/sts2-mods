# 杀戮尖塔 2 Mod 打包

从 [GitHub Releases](https://github.com/zclkkk/sts2-mods/releases/latest) 下载与游戏版本匹配的 `sts2-mods-<游戏版本>.zip`。当前打包目标是 Steam `public-beta` 的 **0.114.0**，包含 RitsuLib、BaseLib（含 Compatibility Helper）、随机数预测和 Undo And Restart。

## 安装

退出游戏。ZIP 顶层的 `mods/` 可以放进游戏安装目录。覆盖更新时，先移出同一个 Mod 的旧文件夹，再放入包里的新文件夹。联机时可在游戏的 Mod 列表里核对版本。

Release 同时提供 ZIP 的 SHA-256 和 `mods.bundle.json`，后者记录各上游版本、原始安装包名及其 SHA-256。

## 自动发布

GitHub Actions 每天新加坡时间 10:23 计划运行。也可在 Actions 页面手动运行 **Build and release mods**。每次运行会下载作者的正式 Release 安装包、检查包里的 Mod ID、版本、游戏版本下限及依赖，然后把完整的 `mods/` 打成 ZIP。上游包组合相同就沿用现有 Release。

Undo And Restart 按游戏版本选择对应正式包。RitsuLib 使用多版本变体包，并检查包中有对应的游戏版本组件。如果上游包无法匹配或依赖检查失败，工作流会报错。

`mods.lock.json` 记录目标游戏版本、Mod 来源和文件命名规则。里面的 `tag` 和 `sha256` 是一组已知版本，供 `python3 mods.py install` 回退安装。自动打包直接查询上游，游戏升级时先更新目标版本。

用 Python 3.11 或以上版本可以在本机查看打包会选用的版本，或者生成一份同结构的 ZIP。

```bash
python3 mods.py check
python3 mods.py bundle --output /tmp/sts2-mod-bundle
```
