# 杀戮尖塔 2 Mod 清单

这套组合用于 Steam `public-beta` 的游戏版本 **0.114.0**。

| Mod | 锁定版本 |
| --- | --- |
| RitsuLib | 0.6.16 variant pack |
| BaseLib | 3.4.8，含 Compatibility Helper |
| 随机数预测 | 0.17.0 |
| Undo And Restart | 0.114.0.1 |

## 本机安装

退出游戏，用 Python 3.11 或以上版本从仓库运行。

```bash
python3 mods.py install --dry-run
python3 mods.py install
```

默认使用 Steam 的 Linux 安装位置。其他游戏库可传入 `--game-dir`，已下载的 Release ZIP 可通过 `--packages` 指定所在目录。脚本读取游戏的 `release_info.json`，按 `mods.lock.json` 下载并核对四个 ZIP，在完成校验后安装到游戏根目录的 `mods/`。已有的同 ID 本地副本会被替换，其他本地 Mod 会一起保留。工坊订阅由 Steam 管理。

## 检查更新

```bash
python3 mods.py check
```

GitHub Actions 每天检查一次，也可以手动运行 **Track mod releases**。发现新正式版时，更新 Issue 会列出候选版本、发行说明链接和可匹配的安装包。确认游戏分支兼容后，再更新 `mods.lock.json` 里的标签和 SHA-256，然后运行安装命令。在审核期间可用 `--dry-run` 检查包文件和游戏版本。

Undo And Restart 的发行版本包含游戏版本号，更新时选择与当前游戏匹配的包。RitsuLib 使用多版本变体包。BaseLib 的压缩包包含前置兼容组件，安装时保留这两个清单。
