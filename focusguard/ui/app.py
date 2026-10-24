import argparse
from datetime import datetime
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import shutil
import psutil

from PySide6.QtCore import Qt, QTimer, QThread, Signal, QSize
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap, QFont
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFrame, QCheckBox, QListWidget, QListWidgetItem, QStackedWidget,
    QFileDialog, QInputDialog, QMessageBox, QDialog, QDialogButtonBox, QTextEdit, QSystemTrayIcon,
    QMenu, QAbstractItemView, QScrollArea)

from focusguard.monitoring.core import Store, Settings, Monitor
from focusguard.windows.system import current_owner, processes, ShutdownAction, same_process
from focusguard.recognition.discovery import build_index
from focusguard.recognition.engine import Recognizer
from focusguard.recognition.rules import RULES
from focusguard import VERSION
from focusguard.windows import installation
from focusguard.windows.autostart_policy import before_deadline, DEADLINE_TEXT

STYLE = """
QWidget { color:#253C36; font-family:'Microsoft YaHei UI','Segoe UI'; font-size:14px; }
QMainWindow, QDialog { background:#F5F4EE; }
QFrame#sidebar { background:#EAEDE5; border-right:1px solid #DEE3D9; }
QLabel#brand { font-size:24px; font-weight:700; letter-spacing:2px; }
QLabel#eyebrow { color:#77877A; font-size:12px; letter-spacing:2px; }
QLabel#title { font-size:30px; font-weight:700; }
QLabel#muted { color:#748078; font-size:13px; }
QLabel#heroTitle { font-size:39px; font-weight:700; color:#F2F3E8; }
QLabel#heroText { color:#CFDACB; font-size:14px; }
QLabel#big { font-size:28px; font-weight:700; }
QFrame#hero { background:#254B40; border-radius:18px; }
QFrame#paper { background:#FCFCF8; border:1px solid #E1E4D9; border-radius:12px; }
QFrame#notice { background:#F0E8D7; border-radius:10px; }
QPushButton { border:1px solid #D6DDD1; border-radius:8px; padding:10px 16px; background:#FCFCF8; }
QPushButton:hover { background:#EDF0E7; border-color:#94AB92; }
QPushButton:focus { border:2px solid #608A6A; }
QPushButton:disabled { color:#A4ADA2; background:#EFEFE9; }
QPushButton#primary { background:#D9E7A4; border:0; color:#254B40; font-weight:700; padding:13px 22px; }
QPushButton#primary:hover { background:#E5F1BB; }
QPushButton#nav { text-align:left; background:transparent; border:0; padding:13px 16px; }
QPushButton#nav:checked { background:#D5DECD; color:#254B40; font-weight:700; }
QPushButton#danger { color:#9C4A37; }
QCheckBox { spacing:9px; }
QCheckBox::indicator { width:18px; height:18px; border:1px solid #ABB8A7; border-radius:4px; background:#FCFCF8; }
QCheckBox::indicator:checked { background:#3A7155; border:2px solid #254B40; }
QListWidget, QTextEdit { background:#FCFCF8; border:1px solid #DFE4D8; border-radius:10px; padding:8px; selection-background-color:#DFE8D6; selection-color:#253C36; }
QListWidget::item { padding:12px; border-bottom:1px solid #EBEEE6; }
QListWidget::item:selected { background:#E5EDDA; }
QScrollArea { border:0; background:transparent; }
QToolTip { background:#254B40; color:white; border:0; padding:6px; }
"""


def label(text, name=None, wrap=False):
    w = QLabel(text)
    if name:
        w.setObjectName(name)
    w.setTextFormat(Qt.PlainText)
    w.setWordWrap(wrap)
    return w


def button(text, callback, name=None):
    w = QPushButton(text)
    if name:
        w.setObjectName(name)
    w.clicked.connect(callback)
    w.setCursor(Qt.PointingHandCursor)
    return w


def app_icon():
    pix = QPixmap(128, 128)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor("#254B40"))
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(0, 0, 128, 128, 28, 28)
    painter.setPen(QColor("#D9E7A4"))
    painter.setFont(QFont("Microsoft YaHei UI", 49, QFont.Bold))
    painter.drawText(pix.rect(), Qt.AlignCenter, "习")
    painter.end()
    return QIcon(pix)


class Poller(QThread):
    message = Signal(str)
    hit = Signal(str)
    health = Signal(str)
    refresh_requested = Signal()

    def __init__(self, monitor):
        super().__init__()
        self.monitor = monitor
        self.stopped = threading.Event()
        self.failed = False
        self.last_observations = {}
        self.last_watch_token = None
        self.pending_refresh = False
        self.last_refresh = float("-inf")

    def run(self):
        while not self.stopped.is_set():
            try:
                records = processes()
                observations = self.monitor.recognizer.observe(records, self.monitor.owner)
                loading = self.monitor.recognizer.status()["loading"]
                current = {}
                for pid, created, decision in observations:
                    key = (pid, created, decision.path)
                    value = (decision.confirmed, decision.reason)
                    current[key] = value
                    if not loading and self.last_observations.get(key) != value:
                        kind = "确认游戏" if decision.confirmed else "候选，不触发"
                        self.message.emit(f"{kind}：{decision.name}；{decision.reason}")
                        rule = self.monitor.recognizer.rules.get(decision.rule_id)
                        if not decision.confirmed and rule and rule.supported:
                            # 首次候选／证据变化时立即重读；相同状态不重复请求。
                            self.pending_refresh = True
                if not loading:
                    self.last_observations = current
                    token = self.monitor.recognizer.refresh_token()
                    if token is not None and token != self.last_watch_token:
                        self.pending_refresh = True
                        self.last_watch_token = token
                    elif token is None:
                        self.last_watch_token = None
                    now = time.monotonic()
                    if self.pending_refresh and now - self.last_refresh >= 2:
                        self.refresh_requested.emit()
                        self.pending_refresh = False
                        self.last_refresh = now
                game = self.monitor.inspect(records)
                if game:
                    self.hit.emit(game.name)
                if self.failed:
                    self.message.emit("进程检测已恢复。")
                    self.health.emit("")
                    self.failed = False
            except Exception as exc:
                if not self.failed:
                    self.message.emit(f"进程检测暂不可用：{exc}")
                    self.failed = True
                    self.health.emit(str(exc))
            self.stopped.wait(0.25)


class Indexer(QThread):
    updated = Signal()

    def __init__(self, recognizer, loader):
        super().__init__()
        self.recognizer, self.loader = recognizer, loader
        self.stopped, self.wake = threading.Event(), threading.Event()

    def run(self):
        while not self.stopped.is_set():
            self.recognizer.begin_refresh()
            self.updated.emit()
            try:
                index = self.loader(self.stopped.is_set)
                if self.stopped.is_set():
                    break
                self.recognizer.publish(index)
            except Exception as exc:
                self.recognizer.fail(str(exc))
            self.updated.emit()
            self.wake.wait(60)
            self.wake.clear()


class Window(QMainWindow):
    log_event = Signal(str)

    def __init__(self, data_dir, simulate=False, demo=False, *, index_loader=None, recognizer=None, start_paused=False):
        super().__init__()
        self.simulate, self.demo = simulate, demo
        if not simulate and (index_loader is not None or recognizer is not None):
            raise ValueError("自定义识别输入仅允许用于模拟测试。")
        self.closing = False
        self.process_error = ""
        self.autostart_error = ""
        self.last_index_summary = None
        self.catalog_view_key = None
        self.observation_view_key = None
        self.recognizer = recognizer if recognizer is not None else Recognizer()
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.store = Store(self.data_dir / "config.json")
        self.initial_error = ""
        try:
            self.settings = self.store.load()
        except Exception as exc:
            self.settings = Settings()
            self.initial_error = f"配置无法读取，监测关闭。原文件保留：{exc}"
            # 后续保存前先保留损坏原件，避免静默覆盖。
            backup = self.store.path.with_name("config.corrupt-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".json")
            shutil.copy2(self.store.path, backup)
        if simulate or start_paused:
            # 安装后重启和模拟会话都不继承开启状态。
            self.settings.armed = False
            if start_paused and not simulate:
                try:
                    self.store.save(self.settings)
                except Exception as exc:
                    self.initial_error = f"无法保存安装后的暂停状态；本次监测保持关闭：{exc}"
        self.log = logging.getLogger(f"FocusGuard.{id(self)}")
        self.log.setLevel(logging.INFO)
        self.log.propagate = False
        self.log_handler = RotatingFileHandler(self.data_dir / "events.log", maxBytes=500_000, backupCount=0, encoding="utf-8")
        self.log_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S"))
        self.log.addHandler(self.log_handler)
        self.monitor = Monitor(self.settings, self.store, current_owner(), ShutdownAction(simulate, self.record_event), self.record_event, self.recognizer, same_process)
        self.log_event.connect(self.display_log)
        self.setWindowTitle("自习室 · 游戏自律" + (" · 模拟模式" if simulate else ""))
        self.setWindowIcon(app_icon())
        self.resize(1080, 760)
        self.setMinimumSize(900, 680)
        self.build()
        self.setup_tray()
        self.refresh_rules()
        self.refresh()
        self.poller = Poller(self.monitor)
        self.poller.message.connect(self.add_log)
        self.poller.health.connect(self.set_process_health)
        self.poller.hit.connect(self.on_hit)
        self.indexer = Indexer(self.recognizer, index_loader if index_loader is not None else build_index)
        self.indexer.updated.connect(self.on_index_updated)
        self.poller.refresh_requested.connect(self.indexer.wake.set)
        self.indexer.start()
        self.poller.start()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1000)
        self.add_log("程序已启动。" + ("模拟模式不会执行关机。" if simulate else "真实监测需使用者明确开启。"))
        if self.store.migration_backup:
            self.add_log(f"旧版配置已备份到 {self.store.migration_backup.name}；改用自动识别，等待重新确认开启。")
        if self.store.consent_reset:
            self.add_log("本版扩展到 Steam 全部安装游戏及英雄联盟大厅，监测保持暂停；请保存文件后重新确认开启。")
        if self.initial_error:
            self.add_log(self.initial_error)
        if self.settings.tripped:
            self.add_log("上次已经触发；为避免登录后反复关机，本次保持暂停，请确认后重新开启。")

    def build(self):
        root = QWidget()
        row = QHBoxLayout(root)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.setCentralWidget(root)
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(208)
        left = QVBoxLayout(side)
        left.setContentsMargins(23, 32, 23, 24)
        left.addWidget(label("自习室", "brand"))
        left.addWidget(label("FOCUS BEFORE PLAY", "eyebrow"))
        left.addSpacing(42)
        self.nav = []
        for i, text in enumerate(["01   专注面板", "02   识别范围", "03   设置与卸载", "04   运行记录"]):
            b = button(text, lambda checked=False, index=i: self.navigate(index), "nav")
            b.setCheckable(True)
            left.addWidget(b)
            self.nav.append(b)
        left.addStretch()
        left.addWidget(label("给未来的自己\n留一点确定性。", "muted"))
        left.addSpacing(20)
        left.addWidget(label(f"本地运行 · 无需账号\nv{VERSION}", "eyebrow"))
        row.addWidget(side)
        self.pages = QStackedWidget()
        row.addWidget(self.pages, 1)
        home, layout = self.page("FOCUS / 01", "把选择，交还给计划。", "Steam 游戏自动收录，其他来源按已核验规则识别。")
        hero = QFrame()
        hero.setObjectName("hero")
        h = QVBoxLayout(hero)
        h.setContentsMargins(30, 24, 30, 28)
        self.pill = label("●  监测尚未开启", "heroText")
        h.addWidget(self.pill)
        h.addSpacing(10)
        self.hero_title = label("今天，先不排位。", "heroTitle")
        h.addWidget(self.hero_title)
        self.hero_sub = label("开启后，可靠确认正在运行的游戏，电脑将立即强制关机。", "heroText", True)
        h.addWidget(self.hero_sub)
        h.addSpacing(19)
        actions = QHBoxLayout()
        self.arm_button = button("开启自动游戏监测  →", self.toggle_monitor, "primary")
        actions.addWidget(self.arm_button)
        actions.addStretch()
        h.addLayout(actions)
        layout.addWidget(hero)
        stats = QHBoxLayout()
        self.count = self.stat(stats, "Steam 自动收录", "准备中")
        self.action_stat = self.stat(stats, "触发动作", "立即关机")
        self.start_stat = self.stat(stats, "登录自启", "未开启")
        layout.addLayout(stats)
        notice = QFrame()
        notice.setObjectName("notice")
        n = QVBoxLayout(notice)
        n.setContentsMargins(18, 13, 18, 13)
        n.addWidget(label("请先保存文件，再开启监测。"))
        n.addWidget(label("强制关机没有倒计时，会关闭其他应用并丢失未保存内容。仅供电脑使用者知情、自愿启用。", "muted", True))
        layout.addWidget(notice)
        self.recent = label("最近动态：等待你的第一个专注决定。", "muted", True)
        layout.addWidget(self.recent)
        layout.addStretch()
        self.pages.addWidget(home)
        games, gl = self.page("RECOGNITION / 02", "自动识别，证据说话。", "无需添加名单。只有安装身份、实际路径和文件信息符合规则时才会触发。")
        self.range_status = label("正在读取安装信息…", "muted", True)
        gl.addWidget(self.range_status)
        self.rule_list = QListWidget()
        self.rule_list.setWordWrap(True)
        gl.addWidget(self.rule_list, 1)
        gl.addWidget(label("当前运行的已收录候选", "muted"))
        self.process_results = QListWidget()
        self.process_results.setWordWrap(True)
        self.process_results.setFixedHeight(130)
        gl.addWidget(self.process_results)
        gl.addWidget(label("冷门游戏、共享宿主、模拟器与网页游戏可能漏检。平台内的普通软件、启动器和反作弊进程不作为游戏本体。", "muted", True))
        self.pages.addWidget(games)
        settings, sl = self.page("PREFERENCES / 03", "规则清楚，进退自由。", "不隐藏进程、不阻止退出，也不设置卸载障碍。")
        panel = QFrame()
        panel.setObjectName("paper")
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(22, 22, 22, 22)
        self.autostart = QCheckBox("登录 Windows 后自动启动")
        self.autostart.setChecked(False)
        self.autostart.toggled.connect(self.set_autostart)
        pl.addWidget(self.autostart)
        self.autostart_policy_text = label("", "muted", True)
        pl.addWidget(self.autostart_policy_text)
        pl.addSpacing(20)
        self.test_mode_label = label("模拟模式：只记录识别结果，绝不关机。" if self.simulate else "当前为真实模式：明确开启后，可靠确认游戏便立即关机。", None, True)
        pl.addWidget(self.test_mode_label)
        pl.addWidget(button("查看当前识别结果（不关机）", self.simulate_once))
        sl.addWidget(panel)
        installed = QFrame()
        installed.setObjectName("paper")
        il = QVBoxLayout(installed)
        il.setContentsMargins(22, 22, 22, 22)
        self.install_status = label("已安装到本机" if installation.is_installed() else "当前为便携运行")
        il.addWidget(self.install_status)
        il.addWidget(label("安装位置：当前用户的本地应用目录；不修改游戏文件。", "muted", True))
        bar = QHBoxLayout()
        bar.addWidget(button("安装到本机", self.install))
        bar.addWidget(button("一键卸载…", self.uninstall, "danger"))
        bar.addStretch()
        il.addLayout(bar)
        sl.addWidget(installed)
        sl.addWidget(label("关闭窗口后，已开启的监测继续在托盘运行；可从托盘暂停或退出。触发一次后，下次启动保持暂停，避免反复关机。", "muted", True))
        sl.addStretch()
        self.pages.addWidget(settings)
        logs, ll = self.page("ACTIVITY / 04", "每一次动作，都有记录。", "只保存在本机，记录状态变化与触发结果，不上传进程清单。")
        self.logs = QTextEdit()
        self.logs.setReadOnly(True)
        self.logs.document().setMaximumBlockCount(600)
        ll.addWidget(self.logs, 1)
        ll.addWidget(button("复制显示的记录", lambda: QApplication.clipboard().setText(self.logs.toPlainText())))
        self.pages.addWidget(logs)
        self.navigate(0)

    def page(self, eyebrow, title, sub):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(34, 29, 34, 25)
        layout.setSpacing(17)
        layout.addWidget(label(eyebrow, "eyebrow"))
        layout.addWidget(label(title, "title"))
        layout.addWidget(label(sub, "muted", True))
        return w, layout

    def stat(self, row, title, value):
        f = QFrame()
        f.setObjectName("paper")
        l = QVBoxLayout(f)
        l.setContentsMargins(18, 17, 18, 17)
        l.addWidget(label(title, "muted"))
        out = label(value, "big")
        l.addWidget(out)
        row.addWidget(f, 1)
        return out

    def navigate(self, index):
        self.pages.setCurrentIndex(index)
        for i, b in enumerate(self.nav):
            b.setChecked(i == index)

    def refresh(self):
        status = self.recognizer.status()
        self.count.setText(f"{status['steam_games']} 款")
        self.action_stat.setText("模拟记录" if self.simulate else "立即关机")
        self.refresh_autostart()
        armed = self.settings.armed
        if status["loading"]:
            state = "安装信息准备中" + (" · 等待识别" if armed else " · 已暂停")
        elif status["error"] or self.process_error:
            state = "识别异常 · 暂不触发"
        elif not armed:
            state = "监测已暂停"
        elif self.simulate:
            state = "模拟监测中 · 不会关机"
        else:
            state = "自动监测中 · 可靠确认后关机"
        if status["warnings"] and status["ready"]:
            state += " · 部分范围受限"
        self.hero_sub.setText("模拟环境只验证自动识别和状态流转，不会发送关机命令。" if self.simulate else "开启后，可靠确认正在运行的游戏，电脑将立即强制关机。")
        self.pill.setText("●  " + state)
        self.hero_title.setText("计划，正在被守护。" if armed else "今天，先不排位。")
        self.arm_button.setText("暂停监测" if armed else "开启模拟监测  →" if self.simulate else "开启自动游戏监测  →")
        self.arm_button.setEnabled(armed or status["ready"])
        self.setWindowTitle("自习室 · " + ("模拟模式" if self.simulate else state))
        if hasattr(self, "tray"):
            self.tray.setToolTip("自习室 · " + state)
        self.refresh_rules(status)

    def refresh_autostart(self):
        explanation = f"安装后限期自启至 {DEADLINE_TEXT}，覆盖 12 月 20 日全天；不是统一交卷时间。"
        try:
            installed = installation.is_installed()
            if self.simulate:
                state = installation.AutostartState()
                text = explanation + " 模拟模式不修改安装或启动项。"
                summary = "模拟禁改"
            else:
                if installation.expire_autostart():
                    self.add_log("备考自启期限已结束，已移除本程序的登录自启登记。")
                state = installation.autostart_state()
                summary = "已到期" if state.expired else "限期自启" if state.enabled and state.limited else "已开启" if state.enabled else "未开启"
                if state.expired:
                    text = "备考自启期限已结束，本版本不再自动启用自启；可继续手动打开程序。"
                elif state.limited:
                    text = explanation + (" 期限内在应用中固定开启。" if state.enabled else " 已在 Windows 中禁用或移除，程序不会恢复该选择。")
                else:
                    text = explanation + " 首次安装确认后自动启用，旧版安装不会在后台静默加入。"
                text += " 可在 Windows 启动应用中禁用，也可随时暂停、退出或卸载；不自动开启关机监测。"
            self.autostart.blockSignals(True)
            self.autostart.setChecked(state.enabled)
            self.autostart.blockSignals(False)
            self.autostart.setEnabled(not self.simulate and installed and not state.limited and not state.expired)
            self.autostart_policy_text.setText(text)
            self.start_stat.setText(summary)
            self.autostart_error = ""
        except Exception as exc:
            self.autostart.setEnabled(False)
            self.start_stat.setText("待检查")
            self.autostart_policy_text.setText(f"自启状态无法检查，未尝试恢复启动项：{exc}")
            if self.autostart_error != str(exc):
                self.autostart_error = str(exc)
                self.add_log(f"自启状态检查失败：{exc}")

    def refresh_rules(self, status=None):
        status = status if status is not None else self.recognizer.status()
        if status["loading"]:
            summary = "正在准备安装信息；每 60 秒自动刷新，无需选择游戏。"
        elif status["error"]:
            summary = "识别异常：" + status["error"] + "；暂不触发，稍后自动重试。"
        else:
            summary = f"自动收录 {status['steam_games']} 款 Steam 游戏，定位 {len(status['bindings'])} 个可进一步核验的程序；未定位不等于没有游戏。"
            if status["warnings"]:
                summary += " 部分来源不可用，详情见运行记录。"
        self.range_status.setText(summary)
        bindings = status["bindings"] if status["ready"] else ()
        key = (tuple((r.id, r.name, r.executables) for r in status["rules"]),
               tuple((b.rule_id, b.path) for b in bindings))
        if key != self.catalog_view_key:
            scroll = self.rule_list.verticalScrollBar().value()
            self.rule_list.clear()
            for rule in status["rules"]:
                count = sum(b.rule_id == rule.id for b in bindings)
                if rule.auto_discovered:
                    description = "Steam 自动收录 · 游戏分类、安装身份与本体路径共同核验"
                elif rule.requires_window:
                    description = "条件识别 · 专用客户端身份与可见大厅窗口；后台驻留不触发"
                elif rule.indirect_launchers:
                    description = "条件识别 · Steam 安装身份与已核验的游戏本体，排除反作弊启动器"
                elif rule.steam_id:
                    description = "条件识别 · Steam 安装身份、实际目录与游戏启动配置一致"
                elif rule.supported:
                    description = "条件识别 · 安装记录 + 准确路径 + 文件产品与公司信息"
                else:
                    description = "仅名称候选 · 缺少完整证据，不触发关机"
                located = f" · 已定位 {count} 个程序，运行时继续核验" if count else ""
                item = QListWidgetItem(f"{rule.name}{located}\n{description}")
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                item.setToolTip((f"游戏平台标识：{rule.steam_id}\n" if rule.steam_id else "") + rule.source)
                self.rule_list.addItem(item)
            self.rule_list.verticalScrollBar().setValue(scroll)
            self.catalog_view_key = key
        view = tuple((pid, created, d.confirmed, d.name, d.path, d.reason) for pid, created, d in status["observations"])
        if view != self.observation_view_key:
            self.process_results.clear()
            for pid, created, d in status["observations"]:
                kind = "确认游戏" if d.confirmed else "候选 · 不触发"
                self.process_results.addItem(f"{kind}：{d.name}（{pid}）\n{d.reason}")
            if not view:
                self.process_results.addItem("尚未检测到已收录的运行候选；这不代表所有游戏都未运行。")
            self.observation_view_key = view

    def on_index_updated(self):
        status = self.recognizer.status()
        if not status["loading"]:
            summary = (status["error"], status["warnings"], status["steam_games"], tuple((b.rule_id, b.path) for b in status["bindings"]))
            if summary != self.last_index_summary:
                if status["error"]:
                    self.add_log("识别异常，暂不触发：" + status["error"])
                else:
                    self.add_log(f"自动识别安装信息已更新：收录 {status['steam_games']} 款 Steam 游戏，定位 {len(status['bindings'])} 个程序；每 60 秒刷新。")
                    for note in status["warnings"]:
                        self.add_log(note)
                self.last_index_summary = summary
        self.refresh()

    def set_process_health(self, message):
        self.process_error = message
        self.refresh()

    def add_log(self, message):
        self.log.info(message)
        self.display_log(message)

    def record_event(self, message):
        # 关机之前同步写盘；界面更新再通过信号交回主线程。
        self.log.info(message)
        self.log_event.emit(message)

    def display_log(self, message):
        stamp = datetime.now().strftime("%H:%M:%S")
        self.logs.append(f"{stamp}  {message}".replace("<", "&lt;").replace(">", "&gt;"))
        self.recent.setText("最近动态：" + message)

    def error(self, message):
        self.add_log(str(message))
        QMessageBox.warning(self, "操作未完成", str(message))

    def toggle_monitor(self):
        try:
            if self.settings.armed:
                self.monitor.pause()
                self.add_log("使用者已暂停监测。")
            else:
                if not self.recognizer.status()["ready"]:
                    QMessageBox.information(self, "识别尚未就绪", "安装信息正在准备或暂不可用，请等待自动重试。")
                    return
                if not self.simulate:
                    box = QMessageBox(self)
                    box.setWindowTitle("由电脑使用者确认开启")
                    box.setIcon(QMessageBox.Warning)
                    box.setText("自动确认游戏正在运行后，电脑将立即强制关机。")
                    box.setInformativeText("无需添加名单，自动收录 Steam 已安装游戏，并包含英雄联盟客户端大厅。只有可靠确认才触发。没有倒计时，其他应用也会被强制关闭，未保存内容将丢失。当前已运行且可靠确认的游戏同样会触发。请先保存文件。")
                    consent = QCheckBox("我正在使用这台电脑，理解风险并自愿开启。")
                    box.setCheckBox(consent)
                    box.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
                    box.button(QMessageBox.Ok).setText("确认开启立即关机监测")
                    box.button(QMessageBox.Ok).setEnabled(False)
                    box.button(QMessageBox.Cancel).setText("暂不开启")
                    box.setDefaultButton(QMessageBox.Cancel)
                    consent.toggled.connect(box.button(QMessageBox.Ok).setEnabled)
                    if box.exec() != QMessageBox.Ok:
                        return
                self.monitor.arm(consent=True)
                self.add_log("使用者已明确开启" + ("模拟监测。" if self.simulate else "立即关机监测。"))
            self.refresh()
        except Exception as exc:
            self.error(exc)

    def simulate_once(self):
        # 查看结果前先暂停真实监测，避免用户把模拟按钮误认为保护开关。
        try:
            if self.settings.armed:
                self.monitor.pause()
                self.add_log("查看模拟识别结果前，已暂停监测。")
            status = self.recognizer.status()
            if not status["ready"]:
                message = "安装信息尚未就绪，暂不能确认游戏。"
            else:
                lines = [("确认游戏：" if d.confirmed else "候选，不触发：") + d.name + "；" + d.reason
                         for _, _, d in status["observations"]]
                message = "\n".join(lines) if lines else "最近一次检测中没有已收录的运行候选；不代表电脑没有运行任何游戏。"
            self.add_log("查看当前识别结果，未执行关机。")
            self.refresh()
            QMessageBox.information(self, "当前识别结果 · 不执行关机", message)
        except Exception as exc:
            self.error(exc)

    def on_hit(self, name):
        self.refresh()
        if self.simulate:
            self.tray.showMessage("自习室 · 模拟命中", f"已识别 {name}。未关机，监测已暂停。")

    def set_autostart(self, enabled):
        try:
            if self.simulate:
                raise ValueError("模拟模式不修改登录自启。请使用正常启动的已安装版本。")
            installation.set_autostart(enabled)
            self.add_log("登录自启已" + ("开启。" if enabled else "关闭。"))
        except Exception as exc:
            self.autostart.blockSignals(True)
            self.autostart.setChecked(installation.autostart_enabled())
            self.autostart.blockSignals(False)
            self.error(exc)
        self.refresh()

    def install(self):
        if self.simulate:
            self.error("模拟模式不执行安装。")
            return
        if installation.is_installed():
            QMessageBox.information(self, "已安装", "当前程序已安装到本机。")
            return
        notice = (f"首次安装确认后自动启用当前用户限期登录自启，持续至 {DEADLINE_TEXT}，覆盖 12 月 20 日全天。期限内在应用中固定开启，到期移除自己的启动项。"
                  if before_deadline() else "本版备考自启期限已过，安装不会启用登录自启。")
        notice += "\n\n安装到当前用户本地应用目录，并添加到 Windows 已安装应用。可在 Windows 启动应用中禁用，程序不会恢复；同版升级保留已禁用或移除的选择。可随时退出、暂停或卸载，不隐藏驻留、不提权。安装后监测保持暂停，真实关机必须另行确认。是否继续？"
        if QMessageBox.question(self, "安装与限期自启确认", notice, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            self.monitor.pause()
            target = installation.install()
            subprocess.Popen([str(target), "--after-pid", str(os.getpid()), "--after-install"], creationflags=subprocess.DETACHED_PROCESS)
            self.finish(preserve=True)
        except Exception as exc:
            self.error(exc)

    def uninstall(self):
        if self.simulate:
            self.error("模拟模式不执行卸载。")
            return
        if not installation.exe_path().is_file():
            QMessageBox.information(self, "便携版本", "本机没有已安装版本，无需卸载。退出后删除解压目录即可。")
            return
        box = QMessageBox(self)
        box.setWindowTitle("卸载自习室")
        box.setText("停止监测、移除登录自启并卸载本程序？")
        box.setInformativeText("只清理本工具，不删除游戏或其他软件。旧配置迁移备份会保留，方便恢复。")
        remove = QCheckBox("同时删除本工具的配置与运行记录")
        box.setCheckBox(remove)
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        if box.exec() != QMessageBox.Yes:
            return
        try:
            self.monitor.pause()
            self.stop_workers()
            self.log_handler.close()
            self.log.removeHandler(self.log_handler)
            installation.schedule_uninstall(remove.isChecked())
            self.closing = True
            QApplication.quit()
        except Exception as exc:
            self.error(exc)

    def setup_tray(self):
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        menu = QMenu()
        menu.addAction("打开自习室", self.reveal)
        menu.addAction("开启／暂停监测", self.tray_toggle)
        menu.addSeparator()
        menu.addAction("退出并停止监测", lambda: self.finish(preserve=False))
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.reveal() if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def tray_toggle(self):
        self.reveal()
        self.toggle_monitor()

    def reveal(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def stop_workers(self):
        self.timer.stop()
        self.poller.stopped.set()
        self.poller.wait()
        self.indexer.stopped.set()
        self.indexer.wake.set()
        self.indexer.wait()

    def finish(self, preserve=False):
        try:
            if not preserve:
                self.monitor.pause()
            self.closing = True
            self.stop_workers()
            self.log_handler.close()
            QApplication.quit()
        except Exception as exc:
            self.closing = False
            self.error(exc)

    def closeEvent(self, event):
        if not self.closing and self.settings.armed and self.tray.isVisible():
            event.ignore()
            self.hide()
            self.tray.showMessage("监测仍在运行", "自习室已收到托盘。右键托盘图标可暂停或退出。")
        elif not self.closing:
            event.ignore()
            self.finish(preserve=False)
        else:
            event.accept()


def handle_instance_request(window, message):
    window.reveal()
    if message == b"uninstall":
        window.uninstall()
    elif message == b"install":
        window.install()


def autostart_launch_allowed(simulate=False):
    if simulate:
        return True
    installation.expire_autostart()
    return before_deadline()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--simulate", action="store_true")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--autostart", action="store_true")
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--screenshot", default="")
    parser.add_argument("--simulation-data", default="", help="仅模拟模式使用的独立测试数据目录")
    parser.add_argument("--after-pid", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--after-install", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.after_pid:
        try:
            psutil.Process(args.after_pid).wait(timeout=15)
        except psutil.NoSuchProcess:
            pass
        except psutil.TimeoutExpired:
            return 1
    simulate = args.simulate or args.demo or args.smoke or bool(args.screenshot)
    app = QApplication(sys.argv[:1])
    app.setApplicationName("FocusGuard")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    app.setWindowIcon(app_icon())
    app.setQuitOnLastWindowClosed(False)
    data = (Path(args.simulation_data) if args.simulation_data else Path(tempfile.mkdtemp(prefix="FocusGuard-simulation-"))) if simulate else installation.root_dir() / "Data"
    if simulate and data.resolve() == (installation.root_dir() / "Data").resolve():
        parser.error("模拟数据目录不能指向正式配置目录。")
    name = "FocusGuard-" + hashlib.sha256((current_owner() + (str(data) if simulate else "")).encode()).hexdigest()[:22]
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.UserAccessOption)
    client = QLocalSocket()
    client.connectToServer(name)
    if client.waitForConnected(400):
        client.write(b"uninstall" if args.uninstall else b"install" if args.install else b"show")
        client.waitForBytesWritten(1000)
        return 0
    QLocalServer.removeServer(name)
    if not server.listen(name):
        QMessageBox.warning(None, "启动失败", "无法建立本机单实例锁；为避免重复监测，本次停止启动。")
        return 1
    if args.autostart:
        try:
            if not autostart_launch_allowed(simulate):
                return 0  # 到期后的首次登录仅清理，不启动监测或后台驻留。
        except Exception as exc:
            QMessageBox.warning(None, "自启检查未完成", f"未启动监测，请手动打开程序检查：{exc}")
            return 1
    window = Window(data, simulate, args.demo, start_paused=args.after_install or args.install or args.uninstall)
    def receive():
        connection = server.nextPendingConnection()
        def read():
            message = bytes(connection.readAll())
            handle_instance_request(window, message)
            connection.disconnectFromServer()
        connection.readyRead.connect(read)
        if connection.bytesAvailable():
            read()
    server.newConnection.connect(receive)
    app.aboutToQuit.connect(window.stop_workers)
    if args.install and not simulate:
        window.show()
        QTimer.singleShot(100, window.install)
    elif args.uninstall and not simulate:
        window.show()
        QTimer.singleShot(100, window.uninstall)
    elif not args.autostart or not window.settings.armed or not window.tray.isVisible():
        window.show()
    if args.screenshot:
        capture_deadline = time.monotonic() + 10
        def capture():
            if window.recognizer.status()["loading"] and time.monotonic() < capture_deadline:
                QTimer.singleShot(200, capture)
                return
            window.refresh()
            folder = Path(args.screenshot)
            folder.mkdir(parents=True, exist_ok=True)
            for index, filename in enumerate(["01-home.png", "02-recognition.png", "03-settings.png", "04-logs.png"]):
                window.navigate(index)
                app.processEvents()
                window.grab().save(str(folder / filename))
            window.finish()
        QTimer.singleShot(500, capture)
    elif args.smoke:
        QTimer.singleShot(600, window.finish)
    return app.exec()
