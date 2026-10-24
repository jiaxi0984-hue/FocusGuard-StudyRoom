# FocusGuard StudyRoom · 自习室

**还玩!再不学就真的完蛋啦!😠**

适用于 Windows 的自愿游戏自律工具。开启监测后，程序自动识别当前用户运行的游戏，在安装身份与文件证据可靠一致时，请求 Windows **立即强制关机**，无需逐个维护游戏名单。

> **风险提示：真实模式没有倒计时，会强制关闭其他应用，未保存内容可能丢失。请由电脑使用者知情、自愿开启，并先用模拟模式验证。** 本工具可以暂停、退出和卸载，不提供防篡改或绝对拦截保证。

当前版本：**1.3.0**。运行时离线工作，不上传进程清单，不读取账号密码、游戏命令行或浏览记录。

## 功能

- **Steam 自动收录**：读取本机全部已安装应用，仅将客户端明确标记为游戏的条目纳入识别范围；普通软件、工具、DLC、浏览器和通用启动器不因此获得游戏身份。
- **证据核验**：同时检查平台身份、安装目录、启动路径、实际文件和当前用户进程；证据不足或冲突时不关机。
- **后台监测**：可见托盘运行，支持暂停、恢复、退出；每次开启最多触发一次，触发后下次登录保持暂停。
- **安全模拟**：独立数据目录，不发送关机命令；模拟模式禁止真实安装、卸载和自启变更。
- **限期登录自启**：确认安装后自动启用当前用户登录自启，持续到 **2026 年 12 月 21 日 00:00（北京时间）**；期限内在应用中固定显示，到期清理。不绕过 Windows 禁用选择，可退出、暂停和卸载；安装不会自动开启关机监测。

不能保证任意来源、任意版本的游戏都能触发。详细边界见 [识别范围与已知限制](docs/SUPPORTED_GAMES.md)。

## 下载 EXE

前往 [GitHub Releases](https://github.com/jiaxi0984-hue/FocusGuard-StudyRoom/releases) 下载 `FocusGuard-1.3.0-Windows-x64.exe` 和对应 SHA-256 文件。无需安装 Python。双击先以便携方式打开；在“设置与卸载”中点击“安装到本机”，阅读并确认限期自启说明后才安装。单纯下载或打开 EXE 不会偷偷安装。

建议先使用 `FocusGuard-1.3.0-Windows-x64.exe --simulate` 验证识别。发布文件未签名，可能显示未知发布者或 SmartScreen 提醒；不要关闭杀毒软件或添加排除项。实际扫描范围以各版本 Release 的验证说明为准，不能保证任何软件绝对无风险。

## 源码快速开始

环境：**Windows 10/11 x64、Python 3.14**。

在仓库根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py --simulate
```

模拟模式与日常数据隔离，不会关机。确认识别结果并保存其他应用中的文件后，如确实希望启用真实模式，重新运行 `main.py`（不带 `--simulate`），再在界面中明确开启监测。首次启动或识别范围升级后默认暂停。

如果当前环境已有 Python 和依赖，也可直接运行：

```powershell
python main.py --simulate
python run_tests.py
```

## 仓库结构

```text
FocusGuard-StudyRoom/
├── main.py                    # 桌面程序入口
├── run_tests.py               # 自动化测试入口
├── requirements.txt           # 运行依赖
├── requirements-dev.txt       # 构建依赖
├── focusguard/
│   ├── monitoring/            # 配置存储、监测状态机、触发前复核
│   ├── recognition/           # 安装发现、文件证据与识别规则
│   │   └── steam/             # Steam 游戏库发现与客户端缓存解析
│   ├── ui/                    # Qt 界面、托盘与后台工作线程
│   └── windows/               # Windows 进程、关机、安装和自启适配
├── tools/
│   ├── release/               # EXE 构建及发布包制作
│   └── verification/          # 隔离模拟进程与窗口验证工具
├── tests/                     # 自动化回归测试与合成样本
├── assets/                    # 应用图标等必要资源
├── docs/                      # 设计说明、识别范围与限制
└── examples/                  # 不含个人信息的配置示例
```

日志、真实配置、依赖缓存、构建输出、截图、验收资料和历史压缩包均由 `.gitignore` 排除，不上传到源码仓库。本机已有归档保留，不作为源码或当前测试结果。

## 使用与升级

1. 从旧版托盘选择“退出并停止监测”。关闭窗口可能只是收到托盘；旧实例未退出时，重复启动会打开旧实例。
2. 启动程序，确认左下角版本为 **v1.3.0**；等待安装资料准备完成。
3. “识别范围”会显示 Steam 自动收录、内置补充规则、已定位程序和运行候选，无需添加或勾选游戏。
4. 先以 `--simulate` 启动源码，或使用自行构建发布包中的 `模拟模式.cmd`，验证识别结果。
5. 保存其他应用中的文件，确认风险后开启“自动游戏监测”。已经运行且被可靠确认的游戏同样会触发。
6. 在设置中“安装到本机”，确认后自动启用备考限期登录自启，不再需要手动勾选。**安装后的监测保持暂停**，真实关机仍须另行明确确认。升级前退出旧实例；同一限期策略的升级不恢复已被用户禁用或移除的启动项。
7. 已开启监测时关闭窗口，监测继续在可见托盘运行；托盘可暂停、打开和退出。
8. “一键卸载”或 Windows 已安装应用中的卸载入口，会停止监测并清理本程序及自身自启项。

## 识别与关机条件

识别需要相符的游戏平台身份、实际安装目录、启动路径和程序文件；必要的特殊适配还会检查产品名、公司、原始文件名或可见窗口。

触发前重新核验当前用户、进程创建时间、实际文件和安装来源。写盘期间进程退出、窗口关闭或证据失效时，取消本次动作。暂停与模拟模式不执行真实关机。

- 每 250 毫秒轮询进程，但实际时延受初始化、文件读取和系统调度影响，不能承诺捕获极短进程。
- 读取权限不足或接口不能确认进程身份时跳过；不读取游戏内存，不绕过反作弊，不自动提权。
- 不以全屏、显卡占用高或目录名像游戏作为单独判断依据。
- Windows 策略拒绝关机时记录失败并暂停。“请求已发出”与“系统实际关机成功”是不同结果。

### 1.2.0 识别更新

- 自动收录本机 Steam 客户端确认的全部已安装游戏，不再要求出现在固定 AppID 目录中。
- 同名程序按完整安装路径区分，同一路径存在冲突身份时不触发。
- 排除明显的编辑器、服务器、配置工具、安装器、反作弊和引导文件；引导文件的版本信息也会复核。
- 安装清单、缓存和游戏库变化会请求即时重读；重复请求合并，最多约每 2 秒处理一次，常规刷新仍为 60 秒。
- 保留国服英雄联盟专用安装键、客户端大厅可见窗口识别与 Apex DX12 本体适配。

### 1.3.0 限期自启更新

- 教育部公布的 **2027 年研考初试为 2026 年 12 月 19—20 日（北京时间）**。20 日业务课（二）14:00 开始，具体时长由报考点、招生单位公布，不能一概认定 17:00 结束。
- 程序截止点为 **2026-12-21 00:00:00+08:00**，覆盖 20 日全天；这是程序策略，不是所有考生统一的官方交卷时刻。不自动续期到其他年份。
- 已安装程序运行期间定时检查；若截止时程序未运行，则在下次启动/登录时清理。到期后的登录启动仅清理并退出，不继续监测或驻留。Windows 禁用自启后需手动打开或卸载才能清理残留登记。
- 只使用 HKCU `Run` 中本程序自己的项；不添加服务、计划任务、守护进程或防卸载机制，不要求管理员权限，不重写 Windows 的禁用状态。
- 详见 [限期自启与安全边界](docs/AUTOSTART.md)，含官方原文、升级与失败处理说明。

## 配置、安装与卸载

[配置示例](examples/config.example.json) 仅供阅读，不自动导入；日常通过界面操作开关。

旧版第 1 版配置在迁移前完整备份为 `config.v1-backup-<摘要>.json`，旧名单不自动转换成可信规则。第 2 版配置继续兼容；识别范围升级后需要重新确认开启。

| 内容 | 当前用户路径或标识 |
|---|---|
| 安装程序 | `%LOCALAPPDATA%\FocusGuard\App\FocusGuard.exe` |
| 日常配置和记录 | `%LOCALAPPDATA%\FocusGuard\Data` |
| 自启及卸载项 | `FocusGuardStudyRoom` |

卸载只删除固定文件与空目录，不递归清空目录，不删除游戏。默认保留数据；选择清理时删除当前配置和记录，迁移备份仍保留。便携压缩包和解压目录由使用者自行删除。

登录自启在当前用户登录后生效，Windows 可能延迟启动，不是登录前拦截。发布文件未签名，Windows 可能提示未知发布者。

## 测试与验证

```powershell
python run_tests.py
python -m tools.verification.verify_lobby_window
python -m tools.verification.verify_live_simulation
```

自动化测试使用临时目录、合成安装证据与 Mock 系统动作，不启动真实游戏，不执行真实关机，也不修改真实安装、自启或日常配置。

`verify_lobby_window` 仅创建并检查自己的测试窗口；`verify_live_simulation` 将 Windows 自带的无害 `ping.exe` 复制到隔离样本路径，在模拟模式下验证识别与触发后暂停。结果写入被忽略的 `build/`；若已有模拟结果，需先归档该样本目录再重跑。

自动化测试、静态文件检查、独立启动与真实游戏验证必须区分；本仓库不以模拟通过宣称实际关机或全部游戏实测通过。

## 构建与打包

```powershell
python -m pip install -r requirements-dev.txt
python -m tools.release.build_release
python -m tools.release.package_release --help
```

默认构建输出为 `dist/FocusGuard.exe`。如需保留旧 EXE，可使用 `python -m tools.release.build_release --dist-dir dist/1.3.0 --work-dir build/1.3.0` 构建到独立目录。本次 GitHub Release 发布版本化单文件 EXE 及校验/验证说明，不强制制作完整 ZIP。

如另行制作完整 ZIP，先将待打包的当前版本 EXE 准备为 `dist/FocusGuard.exe`，并从该 EXE 的独立模拟启动生成 `screenshots-1.3.0/` 下四页 PNG：`01-home.png`、`02-recognition.png`、`03-settings.png`、`04-logs.png`。缺图、截断或 PNG 校验失败都会终止。

准备完成后运行：

```powershell
python -m tools.release.package_release
# 或明确指定发布日期：
python -m tools.release.package_release --release-date YYYYMMDD
```

发布包包含可执行文件、递归收集的源码、必要资源、文档和界面预览；若存在当前版本验收资料目录，仅收集预设白名单，不收集日常私人事件日志。`SHA256SUMS.json` 记录包内文件摘要，ZIP 旁的 `.sha256` 校验整个压缩包。

默认使用本机当天日期，已有同版发布目录或同名压缩包时拒绝合并或覆盖，须先归档旧输出。源码仓库不会自动上传这些生成产物，也不会自动创建 GitHub Release。

## 文档与参考

- [设计说明](docs/DESIGN.md)
- [识别范围与限制](docs/SUPPORTED_GAMES.md)
- [限期自启与安全边界](docs/AUTOSTART.md)
- 教育部：[2027 年全国硕士研究生招生工作管理规定（第三十六条）](http://www.moe.gov.cn/srcsite/A15/moe_778/s3261/202609/t20260923_1451734.html)
- 界面设计参考：[distinctive-ai-design](https://github.com/houxin0010/distinctive-ai-design/blob/main/SKILL.md)
- Windows 系统行为：[shutdown](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/shutdown)、[Run／RunOnce](https://learn.microsoft.com/en-us/windows/win32/setupapi/run-and-runonce-registry-keys)
