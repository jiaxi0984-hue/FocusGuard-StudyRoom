"""与界面、系统关机相互独立的匹配和状态机。"""
from dataclasses import dataclass
import os
from pathlib import Path
import json
import tempfile
import threading
import hashlib
from typing import Protocol

CONSENT_VERSION = 4


def canonical(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(path))) if path else ""


@dataclass(frozen=True)
class Game:
    """确认后的动作目标，不承担手动名单或信任配置。"""
    name: str
    path: str


@dataclass
class Settings:
    armed: bool = False
    consent_version: int = 0
    tripped: bool = False

    def to_dict(self):
        return {"schema": 2, "recognition_mode": "automatic", "armed": self.armed,
                "consent_version": self.consent_version,
                "tripped": self.tripped}


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.migration_backup = None
        self.consent_reset = False

    def load(self):
        if not self.path.exists():
            return Settings()
        original = self.path.read_bytes()
        raw = json.loads(original.decode("utf-8"))
        if not isinstance(raw, dict) or type(raw.get("schema")) is not int or raw["schema"] not in (1, 2):
            raise ValueError("配置格式不兼容；为避免误操作，监测保持关闭。")
        if raw["schema"] == 1:
            # 完整保留旧名单，但绝不把旧路径自动视为可信游戏规则。
            backup = self.path.with_name("config.v1-backup-" + hashlib.sha256(original).hexdigest()[:12] + ".json")
            if backup.exists():
                if backup.read_bytes() != original:
                    raise ValueError("旧版备份冲突，迁移已停止。")
            else:
                with backup.open("xb") as handle:
                    handle.write(original)
                    handle.flush()
                    os.fsync(handle.fileno())
            migrated = Settings(tripped=raw.get("tripped") is True)
            self.save(migrated)
            self.migration_backup = backup
            return migrated
        if raw.get("recognition_mode") != "automatic":
            raise ValueError("配置中的识别模式不受支持，监测保持关闭。")
        consent = raw.get("consent_version", 0)
        tripped = raw.get("tripped") is True
        self.consent_reset = raw.get("armed") is True and consent != CONSENT_VERSION
        return Settings(raw.get("armed") is True and type(consent) is int and consent == CONSENT_VERSION and not tripped,
                        consent, tripped)

    def save(self, settings):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(settings.to_dict(), handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)


@dataclass(frozen=True)
class ProcessRecord:
    pid: int
    path: str
    owner: str
    created: float = 0


class Recognition(Protocol):
    def classify(self, process: ProcessRecord): ...
    def still_valid(self, decision) -> bool: ...


class Monitor:
    """暂停与触发共用锁；写盘成功才执行，每次开启最多触发一次。"""
    def __init__(self, settings, store, owner, action, report, recognizer: Recognition, validate_process):
        self.settings, self.store, self.owner = settings, store, owner
        self.action, self.report = action, report
        self.recognizer, self.validate_process = recognizer, validate_process
        self.lock = threading.RLock()
        self.fired = False

    def arm(self, consent=False):
        with self.lock:
            if not consent:
                raise ValueError("需要使用者明确同意立即关机和未保存数据丢失风险。")
            self.settings.consent_version = CONSENT_VERSION
            self.settings.armed, self.settings.tripped, self.fired = True, False, False
            try:
                self.store.save(self.settings)
            except Exception:
                self.settings.armed = False
                raise

    def pause(self):
        with self.lock:
            self.settings.armed = False
            self.store.save(self.settings)

    def inspect(self, processes):
        with self.lock:
            if not self.settings.armed or self.fired:
                return None
            for proc in processes:
                # 无法确定用户或程序路径时跳过，不做模糊名称匹配。
                if not proc.owner or proc.owner.casefold() != self.owner.casefold() or not proc.path or proc.created <= 0:
                    continue
                decision = self.recognizer.classify(proc)
                if not decision.confirmed or not self.recognizer.still_valid(decision):
                    continue
                # 解析可能耗时；重新核验进程仍存在，且 PID 没有被复用。
                if not self.validate_process(proc):
                    continue
                game = Game(decision.name, canonical(proc.path))
                self.fired = True
                self.settings.armed, self.settings.tripped = False, True
                try:
                    self.store.save(self.settings)
                except Exception:
                    self.report("无法保存触发记录，已停止监测；本次不执行关机。")
                    return None
                self.report(f"确认游戏：{game.name}（进程 {proc.pid}）；依据：{decision.reason}。已记录本次触发。")
                try:
                    # 写盘也可能耗时；动作前再次取消已经退出或证据失效的任务。
                    if not self.validate_process(proc) or not self.recognizer.still_valid(decision):
                        self.report("关机前复核发现进程或识别证据失效，本次已取消，监测保持暂停。")
                        return None
                    self.action(game)
                except Exception as exc:
                    self.report(f"关机请求失败，监测已暂停：{exc}")
                return game
            return None
