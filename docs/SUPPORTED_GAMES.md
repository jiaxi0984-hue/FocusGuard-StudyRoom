# 1.2.0 识别范围

本文描述 1.2.0 的识别策略与边界，不代表所有游戏都经过实际运行验证。策略：自动扩大收录范围，可靠确认后才触发。

## Steam 全量收录

读取本机 Steam 游戏库、全部 `appmanifest_*.acf` 和客户端 `appinfo.vdf`。只纳入客户端类型为 `game`、AppID 与安装身份一致的游戏，不再受固定游戏名称或 AppID 目录限制。

运行时需要核对：游戏分类 → 本机安装清单 → 安装目录 → 客户端声明的 Windows 本体启动路径 → 实际程序文件 → 当前用户仍存活的进程。

- 普通软件、工具、DLC 等不会因为在 Steam 中安装就被当作游戏。
- 同名可执行文件使用完整路径区分；同一路径归属冲突时不触发。
- 明显的编辑器、服务器、安装器、更新器、通用平台、反作弊和引导入口排除。
- 无法直接确认本体的游戏保留收录项和原因，不凭目录内任意程序猜测游戏身份。
- 未知缓存格式、读取受限、数据损坏、路径越界或文件更新期间不触发。
- 安装清单、缓存和库目录变化会请求即时刷新；请求合并、限频，避免重复重读。
- 文件大小、节点数量及每库安装项设安全上限，超限会显示受限状态，不声称无限处理。

## 已核验的特殊适配

| 对象 | 判断条件 |
|---|---|
| 国服英雄联盟对局 | 腾讯 `SOFTWARE\Tencent\LOL` 安装键或精确安装记录、实际对局相对路径与 Riot 产品信息；兼容 `(TM) Client` 产品名 |
| 英雄联盟客户端大厅 | 同一可信安装身份，`LeagueClientUx.exe` 的准确目录、Riot 文件信息及可见大厅窗口；隐藏驻留和 WeGame 不触发 |
| Apex Steam 版 | 安装身份和已登记反作弊引导配置，加上 `r5apex.exe`／`r5apex_dx12.exe` 精确本体、Apex 产品名与 Respawn 公司信息；反作弊本身不触发 |

这些适配不会取消同用户、文件身份、来源变化及动作前复核。

## 已知限制

- **tModLoader**：脚本／共享宿主入口，尚无可靠本体进程证明时仅保留候选。
- **幻兽帕鲁**：若客户端入口为引导程序、无法取得足够本体证据，则仅保留候选。

自动化测试和静态文件检查不等于启动真实游戏，更不等于实际关机测试。不同安装来源、版本和客户端资料可能导致识别结果不同；请先使用模拟模式检查自己的环境。

## 其他来源

本版不保证任意来源的游戏均被识别。网页游戏、云游戏、模拟器、共享 Java／其他宿主、便携或特殊发行版本需要额外可靠适配。

原有无畏契约、原神、崩坏：星穹铁道、我的世界、堡垒之夜的名称线索仍仅作候选，不凭名字关机。独立安装版不会因为与 Steam 游戏同名就自动获得 Steam 身份。

## 数据与技术依据

运行时离线读取本机官方客户端安装及启动资料，不读取凭据、登录配置或浏览历史。游戏名称和路径来自实际资料，不虚构客户端或默认安装路径。

缓存格式参考维护者原始文档：[SteamAppInfo](https://github.com/xPaw/SteamAppInfo#appinfovdf)、[ValvePython appcache](https://github.com/ValvePython/steam/blob/master/steam/utils/appcache.py)。Windows 文件信息使用 [GetFileVersionInfoW](https://learn.microsoft.com/en-us/windows/win32/api/winver/nf-winver-getfileversioninfow) 和 [VerQueryValueW](https://learn.microsoft.com/en-us/windows/win32/api/winver/nf-winver-verqueryvaluew)。

这是自愿使用的自律工具；用户主动修改程序、文件或客户端本地资料不属于防篡改保证。
