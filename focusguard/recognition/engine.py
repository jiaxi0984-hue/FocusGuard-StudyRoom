"""独立识别器：安装快照与文件证据共同确认，不接触关机 API。"""
from dataclasses import dataclass
from pathlib import Path
import threading
from focusguard.monitoring.core import canonical
from .discovery import Index, normalized
from .file_evidence import FileEvidence, fingerprint, watch_fingerprint
from .rules import RULES, EXCLUDED, auxiliary_image
from focusguard.windows import system


@dataclass(frozen=True)
class Decision:
    confirmed: bool
    rule_id: str
    name: str
    path: str
    reason: str
    stamp: tuple = ()
    generation: int = 0
    sources: tuple = ()
    registry_sources: tuple = ()
    window_pid: int = 0


class Recognizer:
    def __init__(self, rules=RULES, files=None, visible_window=None):
        self.base_rules = {rule.id: rule for rule in rules}
        self.rules = dict(self.base_rules)
        self.by_name = {}
        self._names()
        self.files = files if files is not None else FileEvidence()
        self.visible_window = visible_window if visible_window is not None else system.has_visible_window
        self.lock = threading.RLock()
        self.index, self.bindings = Index(), {}
        self.generation, self.ready, self.loading, self.error = 0, False, True, ""
        self.observations = ()

    def begin_refresh(self):
        with self.lock:
            self.ready, self.loading, self.error = False, True, ""
            self.generation += 1

    def publish(self, index):
        with self.lock:
            self.rules = dict(self.base_rules)
            self.rules.update((rule.id, rule) for rule in index.steam_rules if rule.steam_id)
            self._names()
            self.index, self.bindings = index, {}
            for binding in index.bindings:
                if binding.rule_id in self.rules and self.rules[binding.rule_id].supported:
                    self.bindings.setdefault(canonical(binding.path), []).append(binding)
            self.ready, self.loading, self.error = True, False, ""
            self.generation += 1

    def _names(self):
        self.by_name = {}
        for rule in self.rules.values():
            for name in rule.executables:
                self.by_name.setdefault(name, []).append(rule)
        self.rule_snapshot = tuple(self.rules.values())

    def refresh_token(self):
        with self.lock:
            if self.loading:
                return None
            changed = []
            for path, stamp in self.index.watch_sources:
                current = watch_fingerprint(path)
                if current != stamp:
                    changed.append((path, current))
            return tuple(changed) if changed else None

    def fail(self, message):
        with self.lock:
            self.index, self.bindings = Index(), {}
            self.ready, self.loading, self.error = False, False, str(message)
            self.generation += 1

    def status(self):
        with self.lock:
            return {"ready": self.ready, "loading": self.loading, "error": self.error,
                    "warnings": self.index.warnings, "bindings": self.index.bindings,
                    "observations": self.observations, "steam_games": len(self.index.steam_rules),
                    "rules": self.rule_snapshot}

    def classify(self, process):
        path = canonical(process.path)
        name = Path(path).name.casefold()
        if name in EXCLUDED:
            return Decision(False, "", "", path, "不是已收录的游戏本体")
        with self.lock:
            bound = self.bindings.get(path, ())
            identities = {b.rule_id for b in bound}
            if len(identities) > 1:
                return Decision(False, "", "游戏身份冲突", path, "同一路径对应多个游戏身份，无法唯一确认，不触发")
            candidates = [self.rules[next(iter(identities))]] if identities else self.by_name.get(name, ())
            if not candidates:
                return Decision(False, "", "", path, "不是已收录的游戏本体")
            rule = candidates[0]
            def unknown(reason):
                title = rule.name if len(candidates) == 1 else "同名程序候选（未确认游戏身份）"
                return Decision(False, rule.id, title, path, reason)
            if self.loading:
                return unknown("安装信息准备中，暂不确认")
            if not self.ready:
                return unknown("识别异常，暂不确认")
            if not rule.supported:
                return unknown("只有名称线索，尚无完整可信规则，不触发")
            bindings = [b for b in self.bindings.get(path, ()) if b.rule_id == rule.id]
            if not bindings:
                return unknown("未取得一致的安装身份与实际启动路径，不触发")
            try:
                evidence = self.files.read(path)
                if not evidence.executable:
                    return unknown("不是有效的 Windows 游戏可执行文件")
                for binding in bindings:
                    if not self._sources_valid(binding.sources) or not all(proof.valid() for proof in binding.registry_sources):
                        continue
                    if binding.steam_direct and auxiliary_image(evidence.versions):
                        return unknown("启动文件是引导或辅助程序，尚未确认游戏本体，不触发")
                    if binding.metadata_required:
                        if not any(normalized(v.get("ProductName", "")) in {normalized(p) for p in (rule.file_products or rule.products)}
                                   and normalized(v.get("CompanyName", "")) in {normalized(c) for c in rule.companies}
                                   and (not v.get("OriginalFilename") or Path(v["OriginalFilename"]).name.casefold() in rule.executables)
                                   for v in evidence.versions):
                            continue
                    if rule.requires_window and not self.visible_window(process.pid):
                        return unknown("客户端文件身份已确认，等待大厅窗口显示；后台驻留不触发")
                    return Decision(True, rule.id, rule.name, path, binding.reason,
                                    evidence.stamp, self.generation, binding.sources, binding.registry_sources,
                                    process.pid if rule.requires_window else 0)
                return unknown("安装信息已变化，或文件产品信息缺失／不符，等待重新核验")
            except (OSError, ValueError) as exc:
                return unknown(f"文件信息暂不可用：{exc}；不触发")

    @staticmethod
    def _sources_valid(sources):
        try:
            return all(fingerprint(path) == stamp for path, stamp in sources)
        except OSError:
            return False

    def still_valid(self, decision):
        with self.lock:
            if not decision.confirmed or not self.ready or decision.generation != self.generation:
                return False
            try:
                return (fingerprint(decision.path) == decision.stamp and self._sources_valid(decision.sources)
                        and all(proof.valid() for proof in decision.registry_sources)
                        and (not decision.window_pid or self.visible_window(decision.window_pid)))
            except OSError:
                return False

    def observe(self, records, owner):
        # 未知日常程序不进入界面、日志；只展示已收录名称的判断结果。
        observations = []
        for proc in records:
            if not proc.owner or proc.owner.casefold() != owner.casefold() or not proc.path or proc.created <= 0:
                continue
            decision = self.classify(proc)
            if decision.name:
                observations.append((proc.pid, proc.created, decision))
        with self.lock:
            self.observations = tuple(observations[:24])
        return tuple(observations[:24])
