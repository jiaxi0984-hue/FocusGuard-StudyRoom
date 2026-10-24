"""内置身份目录。Steam 相对启动路径来自本机官方客户端缓存，不猜测。"""
from dataclasses import dataclass
from pathlib import PureWindowsPath
import re


@dataclass(frozen=True)
class Rule:
    id: str
    name: str
    executables: tuple[str, ...]
    steam_id: int = 0
    products: tuple[str, ...] = ()
    publishers: tuple[str, ...] = ()
    companies: tuple[str, ...] = ()
    relative_paths: tuple[str, ...] = ()
    registry_product: str = ""
    file_products: tuple[str, ...] = ()
    requires_window: bool = False
    indirect_launchers: tuple[str, ...] = ()
    auto_discovered: bool = False

    @property
    def supported(self):
        return bool(self.steam_id or (self.products and self.publishers and self.companies and self.relative_paths))

    @property
    def source(self):
        if self.steam_id:
            return f"https://store.steampowered.com/app/{self.steam_id}/"
        if self.id in {"lol", "lol-client"}:
            return "https://support-leagueoflegends.riotgames.com/hc/en-us/articles/46323147935507"
        return ""


RULES = (
    Rule("lol", "英雄联盟（对局）", ("league of legends.exe",),
         products=("League of Legends", "英雄联盟"),
         publishers=("Riot Games", "Riot Games, Inc.", "腾讯科技（深圳）有限公司", "Tencent"),
         companies=("Riot Games", "Riot Games, Inc."),
         relative_paths=("Game/League of Legends.exe",), registry_product="tencent_lol",
         file_products=("League of Legends", "League of Legends (TM) Client", "英雄联盟")),
    Rule("lol-client", "英雄联盟（客户端大厅）", ("leagueclientux.exe",),
         products=("League of Legends", "英雄联盟"),
         publishers=("Riot Games", "Riot Games, Inc.", "腾讯科技（深圳）有限公司", "Tencent"),
         companies=("Riot Games", "Riot Games, Inc."),
         relative_paths=("LeagueClient/LeagueClientUx.exe", "LeagueClientUx.exe"),
         registry_product="tencent_lol", file_products=("League of Legends",), requires_window=True),
    Rule("cs2", "反恐精英 2", ("cs2.exe",), 730),
    Rule("dota2", "Dota 2", ("dota2.exe",), 570),
    Rule("pubg", "绝地求生", ("tslgame.exe",), 578080),
    Rule("eldenring", "艾尔登法环", ("eldenring.exe",), 1245620),
    Rule("hollowknight", "空洞骑士", ("hollow_knight.exe",), 367520),
    Rule("stardew", "星露谷物语", ("stardew valley.exe",), 413150),
    Rule("terraria", "泰拉瑞亚", ("terraria.exe",), 105600),
    Rule("palworld", "幻兽帕鲁", ("palworld-win64-shipping.exe",), 1623730),
    Rule("witcher3", "巫师 3", ("witcher3.exe",), 292030),
    Rule("apex", "Apex 英雄", ("r5apex.exe", "r5apex_dx12.exe"), 1172470,
         companies=("Respawn Entertainment",), file_products=("Apex Legends",),
         relative_paths=("r5apex.exe", "r5apex_dx12.exe"),
         indirect_launchers=("EAAntiCheat.GameServiceLauncher.exe", "start_protected_game.exe")),
    Rule("overwatch", "守望先锋（Steam 版）", ("overwatch.exe",), 2357570),
    Rule("valorant", "无畏契约", ("valorant-win64-shipping.exe",)),
    Rule("genshin", "原神", ("genshinimpact.exe", "yuanshen.exe")),
    Rule("starrail", "崩坏：星穹铁道", ("starrail.exe",)),
    Rule("minecraft", "我的世界", ("minecraft.windows.exe",)),
    Rule("fortnite", "堡垒之夜", ("fortniteclient-win64-shipping.exe",)),
)

# 平台、工具与共享宿主永不作为游戏本体，无法用其他旁证提升为游戏。
EXCLUDED = frozenset({"steam.exe", "steamwebhelper.exe", "wegame.exe", "riotclientservices.exe",
    "leagueclient.exe", "leagueclientuxrenderer.exe", "epicgameslauncher.exe",
    "battle.net.exe", "vgc.exe", "vgm.exe", "vgtray.exe", "easyanticheat.exe", "easyanticheat_eos.exe",
    "csgocfg.exe", "dota2cfg.exe", "eaanticheat.gameservicelauncher.exe", "start_protected_game.exe",
    "palworld.exe", "java.exe", "javaw.exe", "chrome.exe", "msedge.exe", "firefox.exe"})


def auxiliary_entry(executable, description="", launch_type=""):
    """仅用于排除明显辅助入口，绝不据此单独肯定一个程序是游戏。"""
    name = PureWindowsPath(executable).name.casefold()
    if name in EXCLUDED or name in {"cmd.exe", "powershell.exe", "pwsh.exe", "python.exe", "rundll32.exe"}:
        return True
    if name.endswith(("launcher.exe", "updater.exe", "setup.exe", "installer.exe", "editor.exe", "crashreporter.exe", "crashhandler.exe")):
        return True
    if re.search(r"(^|[_. -])(server|editor|config|uninstall|benchmark)([_. -]|$)", name):
        return True
    if str(launch_type).casefold() in {"tool", "editor", "server", "dedicated", "config"}:
        return True
    description = str(description).casefold()
    return any(term in description for term in ("dedicated server", "workshop tools", "modding tools", "configuration tool", "launch editor"))


def auxiliary_image(versions):
    markers = ("bootstrappackagedgame", "crashreportclient", "crashpad_handler", "prerequisitessetup")
    return any(any(marker in " ".join(str(v.get(k, "")) for k in ("OriginalFilename", "FileDescription", "InternalName")).casefold()
                   for marker in markers) for v in versions)
