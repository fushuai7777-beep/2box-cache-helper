# 2Box Cache Helper

用于《无尽的拉格朗日》Windows 客户端的 2Box 缓存检查与启动辅助工具。

这是独立的非官方辅助工具，不属于 2Box 或游戏官方。**本仓库和下载包不包含 2Box 主程序、游戏文件或任何账号数据。**

## 下载与使用

1. 从 [2Box 原作者发布页](https://github.com/Wei1030/2Box/releases) 下载 2Box 2.1.2，解压到可写目录。
2. 从本仓库 Releases 下载辅助工具，将全部文件解压到同一个目录，保证 `2Box.exe` 与 `RefreshCache.exe` 放在一起。
3. 双击 `更新缓存后启动2Box.cmd`。
4. 在 2Box 内为每个账号创建固定环境。首次自行完成登录验证，以后复用同一个环境。

游戏有更新时，先正常启动游戏完成更新，再通过辅助入口启动多开。运行检查前，正常关闭已经打开的 2Box 和多开游戏。

便携下载包自带检查器运行时，无需另外安装 Python。源码运行和自行构建需要 Python 3.12。

## 作用与限制

正常游戏和 2Box 隔离环境中的补丁清单、资源索引可能不同。已有隔离副本不会自动跟随正常目录更新。本工具先比较更新标记；标记一致时快速返回，发现差异时扫描已缓存的补丁、脚本和资源副本，并备份需要替换的文件。

- 默认只读检查；只有 `--apply` 才写入隔离资源副本。
- 启动脚本使用 `--quick`：更新标记一致时跳过全面校验。这不是完整性或损坏检测工具。
- 使用本机 `%LOCALAPPDATA%\lagrange_online_branch\Documents` 作为已更新资源来源。
- 保留账号数据库、用户设置以及 2Box 主程序；失败时尝试恢复本次已替换的文件。
- 不实现自动登录、密码保存、扫码代办或登录验证绕过。登录状态由游戏与 2Box 自行维护。
- 当前只有原使用者在一台 Windows 电脑上的实测反馈：固定环境可稳定复用登录状态。未做跨机器或其他游戏的兼容性保证。
- 扫描阶段默认超时 120 秒；文件复制和系统磁盘操作不受这个计时器的硬中断控制。

运行后生成的 `Env`、`bin`、`log`、`repair-backups` 和 `repair-last-report.json` 属于本机数据。不要上传它们。

## 源码运行

将脚本放在 `2Box.exe` 所在目录后执行：

```powershell
python repair_2box_assets.py --quick --report check.json
python repair_2box_assets.py --apply --quick --report repair-last-report.json
```

## 构建与测试

```powershell
python -m unittest -v test_2box_launcher.py
powershell -NoProfile -ExecutionPolicy Bypass -File .\build.ps1
```

`build.ps1` 在本地创建虚拟环境，安装锁定的构建依赖，生成独立 `RefreshCache.exe` 并打包。生成物位于 `dist`。构建过程需要联网下载依赖。

## 发布内容

辅助工具下载包包含检查器、启动脚本、对应源码、说明及许可证，不包含 `2Box.exe`。本工具源码采用 MIT 许可证。内置运行库许可证见 `licenses` 和 `THIRD_PARTY_NOTICES.md`。

2Box 的源码与许可由其原作者维护：[Wei1030/2Box](https://github.com/Wei1030/2Box)。如自行再分发 2Box，需自行满足其 GPL-3.0 的许可证、版权及对应源码提供要求。
