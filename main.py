# -*- coding: utf-8 -*-
"""
AI 智能答题助手（一体化嵌入浏览器版）
使用 PySide6 + QWebEngineView 将浏览器直接嵌入程序窗口，
不再依赖外部 Chrome + CDP。复用原 core_ai / page_bridge.js 逻辑。
"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal, QObject, Slot
from PySide6.QtGui import QFont, QAction
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QSplitter, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QLineEdit, QTextEdit, QTreeWidget, QTreeWidgetItem,
    QCheckBox, QComboBox, QSpinBox, QGroupBox, QTabWidget, QMessageBox,
    QFileDialog, QStatusBar, QProgressBar, QFrame, QDialog
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineScript

from core_ai import (
    DEFAULT_PROMPTS, PROVIDERS, chat_completions, compact_payload,
    build_web_ai_pack, format_qa_document, looks_like_questions,
    parse_answers, parse_question_payload, slice_payload,
)

APP_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
CONF_DIR = Path(os.environ.get("APPDATA") or Path.home() / ".ai-study-helper")
CONF_FILE = CONF_DIR / "settings.json"
BRIDGE_JS = (APP_DIR / "inject" / "page_bridge.js").read_text(encoding="utf-8")

USAGE = (
    "本工具仅供学习复习、自测练习使用。请遵守所在平台与学校的考试纪律，"
    "不要用于正式考试或任何违规场景。"
)


def load_settings() -> dict:
    defaults = {
        "provider": "deepseek",
        "base_url": PROVIDERS["deepseek"]["base_url"],
        "api_key": "",
        "model": PROVIDERS["deepseek"]["model"],
        "profile": "explain",
        "auto_fill": True,
        "ai_timeout": 600,
        "slice_on": True,
        "slice_size": 5,
        "last_url": "https://www.baidu.com",
    }
    try:
        if CONF_FILE.exists():
            defaults.update(json.loads(CONF_FILE.read_text(encoding="utf-8")))
    except Exception:
        pass
    return defaults


def save_settings(data: dict) -> None:
    CONF_DIR.mkdir(parents=True, exist_ok=True)
    CONF_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class BridgeSignals(QObject):
    """从网页 JS 回传数据用的信号桥。"""
    packet_received = Signal(str, str)  # url, text
    scan_result = Signal(str, str)      # questions_json, page_text
    fill_result = Signal(str)           # report json
    log_msg = Signal(str)


class ExamPage(QWebEnginePage):
    """处理考试系统常见的 window.open / target=_blank 跳转。"""

    def __init__(self, profile, parent=None):
        super().__init__(profile, parent)
        self._host_view = None

    def createWindow(self, _type):
        # 考试按钮经常用 window.open 打开新页，默认会被拦截
        # 这里把新窗口请求转到同一个视图里打开
        view = QWebEngineView(self._host_view)
        page = ExamPage(self.profile(), view)
        page._host_view = self._host_view
        view.setPage(page)
        # 把新页加载到主浏览器里
        def _load(url):
            if self._host_view and url.isValid():
                self._host_view.setUrl(url)
            view.deleteLater()
        page.urlChanged.connect(_load)
        return page


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI 智能答题助手  ·  一体化嵌入浏览器版")
        self.resize(1400, 860)
        self.setMinimumSize(1100, 700)

        self.settings = load_settings()
        self.packets: list[dict] = []
        self.last_answers: list[dict] = []
        self.last_questions: list[dict] = []
        self.last_raw = ""
        self._busy = False

        self.signals = BridgeSignals()
        self.signals.log_msg.connect(self.log)

        self._build_ui()
        self._apply_style()
        self._inject_bridge_script()
        self.statusBar().showMessage("就绪 · 在左侧浏览器打开题库页后点「扫描页面题目」")

    def _apply_style(self):
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #0f172a; color: #e2e8f0; font-family: "Microsoft YaHei UI", "Segoe UI"; }
            QGroupBox { border: 1px solid #1e293b; border-radius: 8px; margin-top: 12px; padding-top: 8px; font-weight: bold; }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; color: #94a3b8; }
            QPushButton { background: #1e293b; border: 1px solid #334155; border-radius: 6px; padding: 6px 12px; color: #e2e8f0; }
            QPushButton:hover { background: #334155; }
            QPushButton#accent { background: #4f46e5; border-color: #6366f1; font-weight: bold; }
            QPushButton#accent:hover { background: #4338ca; }
            QLineEdit, QTextEdit, QSpinBox, QComboBox { background: #1e293b; border: 1px solid #334155; border-radius: 6px; padding: 4px 8px; color: #f1f5f9; }
            QTreeWidget { background: #1e293b; border: 1px solid #334155; border-radius: 6px; }
            QTreeWidget::item:selected { background: #3730a3; }
            QTabWidget::pane { border: 1px solid #1e293b; }
            QTabBar::tab { background: #1e293b; padding: 8px 18px; border-radius: 6px 6px 0 0; margin-right: 2px; }
            QTabBar::tab:selected { background: #312e81; color: #fff; }
            QStatusBar { background: #0b1220; color: #94a3b8; }
            QSplitter::handle { background: #1e293b; }
        """)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)

        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter)

        # ---- 左侧：嵌入浏览器 ----
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        nav = QHBoxLayout()
        self.url_edit = QLineEdit(self.settings.get("last_url", "https://www.baidu.com"))
        self.url_edit.returnPressed.connect(self.navigate)
        nav.addWidget(QLabel("地址"))
        nav.addWidget(self.url_edit, 1)
        btn_go = QPushButton("打开")
        btn_go.clicked.connect(self.navigate)
        nav.addWidget(btn_go)
        btn_back = QPushButton("后退")
        btn_back.clicked.connect(lambda: self.web.back())
        nav.addWidget(btn_back)
        btn_reload = QPushButton("刷新")
        btn_reload.clicked.connect(lambda: self.web.reload())
        nav.addWidget(btn_reload)
        btn_new = QPushButton("新建窗口")
        btn_new.clicked.connect(self.open_new_window)
        nav.addWidget(btn_new)
        left_layout.addLayout(nav)

        self.web = QWebEngineView()
        profile = QWebEngineProfile.defaultProfile()
        # 伪装成正常 Chrome，避免部分考试站拦截嵌入浏览器
        profile.setHttpUserAgent(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/130.0.0.0 Safari/537.36"
        )
        page = ExamPage(profile, self.web)
        page._host_view = self.web
        self.web.setPage(page)
        # 打开常用能力
        s = self.web.settings()
        from PySide6.QtWebEngineCore import QWebEngineSettings
        for attr in (
            QWebEngineSettings.WebAttribute.JavascriptEnabled,
            QWebEngineSettings.WebAttribute.LocalStorageEnabled,
            QWebEngineSettings.WebAttribute.PluginsEnabled,
            QWebEngineSettings.WebAttribute.FullScreenSupportEnabled,
            QWebEngineSettings.WebAttribute.AllowWindowActivationFromJavaScript,
        ):
            try:
                s.setAttribute(attr, True)
            except Exception:
                pass
        self.web.setUrl(QUrl(self.url_edit.text()))
        self.web.loadFinished.connect(self._on_load_finished)
        left_layout.addWidget(self.web, 1)

        # ---- 右侧：控制台 ----
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(6, 0, 0, 0)

        tabs = QTabWidget()
        right_layout.addWidget(tabs)

        # 工作台
        work = QWidget()
        wlay = QVBoxLayout(work)

        btn_row = QHBoxLayout()
        btn_scan = QPushButton("① 扫描页面题目")
        btn_scan.setObjectName("accent")
        btn_scan.clicked.connect(self.scan_page)
        btn_row.addWidget(btn_scan)
        btn_clear = QPushButton("清空抓包")
        btn_clear.clicked.connect(self.clear_packets)
        btn_row.addWidget(btn_clear)
        btn_row.addStretch()
        wlay.addLayout(btn_row)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["来源", "摘要", "大小"])
        self.tree.setColumnWidth(0, 180)
        self.tree.setColumnWidth(1, 220)
        self.tree.itemSelectionChanged.connect(self._on_sel_packet)
        wlay.addWidget(self.tree)

        q_label = QLabel("送去 AI 的题目（可手改 / 粘贴）")
        wlay.addWidget(q_label)
        self.txt_q = QTextEdit()
        self.txt_q.setPlaceholderText("扫描结果或手动粘贴题目 JSON / 文本…")
        wlay.addWidget(self.txt_q)

        ai_row = QHBoxLayout()
        btn_ai = QPushButton("② AI 答题")
        btn_ai.setObjectName("accent")
        btn_ai.clicked.connect(self.run_ai)
        ai_row.addWidget(btn_ai)
        btn_fill = QPushButton("③ 自动勾选到页面")
        btn_fill.clicked.connect(self.auto_fill)
        ai_row.addWidget(btn_fill)
        btn_copy_pack = QPushButton("复制提示词+题目")
        btn_copy_pack.clicked.connect(self.copy_prompt_pack)
        ai_row.addWidget(btn_copy_pack)
        btn_view_packs = QPushButton("查看分段提示词")
        btn_view_packs.clicked.connect(self.view_prompt_packs)
        ai_row.addWidget(btn_view_packs)
        btn_copy_ans = QPushButton("复制答案")
        btn_copy_ans.clicked.connect(self.copy_ans)
        ai_row.addWidget(btn_copy_ans)
        ai_row.addStretch()
        wlay.addLayout(ai_row)

        self.auto_fill_cb = QCheckBox("AI 完成后自动勾选")
        self.auto_fill_cb.setChecked(bool(self.settings.get("auto_fill", True)))
        wlay.addWidget(self.auto_fill_cb)

        self.tree_ans = QTreeWidget()
        self.tree_ans.setHeaderLabels(["#", "题型", "答案"])
        self.tree_ans.setMaximumHeight(160)
        wlay.addWidget(self.tree_ans)

        wlay.addWidget(QLabel("模型原文 / 运行日志"))
        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        wlay.addWidget(self.txt_log, 1)

        tabs.addTab(work, "答题工作台")

        # 设置
        sett = QWidget()
        slay = QVBoxLayout(sett)
        form = QGroupBox("接口与策略")
        fl = QVBoxLayout(form)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("模型通道"))
        self.cmb_provider = QComboBox()
        for pid, meta in PROVIDERS.items():
            self.cmb_provider.addItem(meta["label"], pid)
        idx = self.cmb_provider.findData(self.settings.get("provider", "deepseek"))
        if idx >= 0:
            self.cmb_provider.setCurrentIndex(idx)
        self.cmb_provider.currentIndexChanged.connect(self._on_provider)
        row1.addWidget(self.cmb_provider)
        row1.addStretch()
        fl.addLayout(row1)

        fl.addWidget(QLabel("API Base URL"))
        self.ed_url = QLineEdit(self.settings.get("base_url", ""))
        fl.addWidget(self.ed_url)

        fl.addWidget(QLabel("API Key"))
        self.ed_key = QLineEdit(self.settings.get("api_key", ""))
        self.ed_key.setEchoMode(QLineEdit.Password)
        fl.addWidget(self.ed_key)

        fl.addWidget(QLabel("模型名"))
        self.ed_model = QLineEdit(self.settings.get("model", "deepseek-chat"))
        fl.addWidget(self.ed_model)

        rowp = QHBoxLayout()
        rowp.addWidget(QLabel("答题风格"))
        self.cmb_profile = QComboBox()
        self.cmb_profile.addItem("只要答案（闪电）", "flash")
        self.cmb_profile.addItem("答案 + 解析", "explain")
        pidx = self.cmb_profile.findData(self.settings.get("profile", "explain"))
        if pidx >= 0:
            self.cmb_profile.setCurrentIndex(pidx)
        rowp.addWidget(self.cmb_profile)
        rowp.addStretch()
        fl.addLayout(rowp)

        rowt = QHBoxLayout()
        rowt.addWidget(QLabel("AI 超时(秒)"))
        self.sp_timeout = QSpinBox()
        self.sp_timeout.setRange(30, 3600)
        self.sp_timeout.setValue(int(self.settings.get("ai_timeout", 600)))
        rowt.addWidget(self.sp_timeout)
        rowt.addStretch()
        fl.addLayout(rowt)

        rows = QHBoxLayout()
        self.cb_slice = QCheckBox("启用答题分段（本地模型建议打开）")
        self.cb_slice.setChecked(bool(self.settings.get("slice_on", True)))
        rows.addWidget(self.cb_slice)
        rows.addWidget(QLabel("每段题数"))
        self.sp_slice = QSpinBox()
        self.sp_slice.setRange(1, 50)
        self.sp_slice.setValue(int(self.settings.get("slice_size", 5)))
        rows.addWidget(self.sp_slice)
        rows.addStretch()
        fl.addLayout(rows)

        btn_row = QHBoxLayout()
        btn_save = QPushButton("保存设置")
        btn_save.setObjectName("accent")
        btn_save.clicked.connect(self.save_ui_settings)
        btn_row.addWidget(btn_save)
        btn_reset = QPushButton("重置为默认（清空 Key）")
        btn_reset.clicked.connect(self.reset_settings)
        btn_row.addWidget(btn_reset)
        btn_row.addStretch()
        fl.addLayout(btn_row)

        tip = QLabel(
            "DeepSeek Key 在 https://platform.deepseek.com 申请。\n"
            "本地 llama-server：通道选对应项，URL 填 http://127.0.0.1:9080/v1，Key 可空。\n"
            "超时建议 600 秒；分段建议每段 3～5 题。\n"
            "分段提示词：答题工作台点「查看分段提示词」，可逐段复制或导出 txt。\n"
            "发布给别人前，先点「重置为默认」清空自己的 Key。"
        )
        tip.setStyleSheet("color:#94a3b8;")
        fl.addWidget(tip)
        slay.addWidget(form)
        slay.addStretch()
        tabs.addTab(sett, "接口设置")

        # 说明
        doc = QTextEdit()
        doc.setReadOnly(True)
        doc.setPlainText(DOC)
        tabs.addTab(doc, "功能与原理")

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([780, 620])

        self.status = self.statusBar()
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(160)
        self.progress.setVisible(False)
        self.status.addPermanentWidget(self.progress)

    def _inject_bridge_script(self):
        """把 page_bridge.js 作为用户脚本注入，保证每次导航后可用。"""
        script = QWebEngineScript()
        script.setName("ai_study_bridge")
        script.setSourceCode(BRIDGE_JS)
        script.setInjectionPoint(QWebEngineScript.DocumentCreation)
        script.setWorldId(QWebEngineScript.MainWorld)
        script.setRunsOnSubFrames(False)
        self.web.page().scripts().insert(script)

    def navigate(self):
        url = self.url_edit.text().strip()
        if not url:
            return
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        self.web.setUrl(QUrl(url))
        self.settings["last_url"] = url
        save_settings(self.settings)

    def open_new_window(self):
        """打开一个独立的新窗口（独立浏览器、独立 AI 会话）。"""
        win = MainWindow()
        win.setWindowTitle("AI 智能答题助手  ·  窗口 " + str(len(_open_windows) + 1))
        win.show()
        _open_windows.append(win)
        # 关闭时从列表移除，避免残留引用
        win.destroyed.connect(lambda: _open_windows.remove(win) if win in _open_windows else None)

    def _on_load_finished(self, ok: bool):
        if ok:
            self.url_edit.setText(self.web.url().toString())
            self.log(f"[页面] 加载完成 {self.web.url().toString()[:80]}")
            # 再确保桥已注入（有时 DocumentCreation 时序问题）
            self.web.page().runJavaScript(BRIDGE_JS + "; true;")

    def log(self, s: str):
        self.txt_log.append(s.rstrip())
        self.txt_log.verticalScrollBar().setValue(self.txt_log.verticalScrollBar().maximum())

    def set_status(self, s: str):
        self.status.showMessage(s)

    def collect_settings(self) -> dict:
        return {
            "provider": self.cmb_provider.currentData() or "deepseek",
            "base_url": self.ed_url.text().strip(),
            "api_key": self.ed_key.text().strip(),
            "model": self.ed_model.text().strip(),
            "profile": self.cmb_profile.currentData() or "explain",
            "ai_timeout": int(self.sp_timeout.value()),
            "slice_on": bool(self.cb_slice.isChecked()),
            "slice_size": int(self.sp_slice.value()),
            "auto_fill": bool(self.auto_fill_cb.isChecked()),
            "last_url": self.url_edit.text().strip(),
        }

    def save_ui_settings(self):
        self.settings = self.collect_settings()
        save_settings(self.settings)
        QMessageBox.information(self, "已保存", f"配置已写入\n{CONF_FILE}")

    def reset_settings(self):
        """清空 Key 并恢复默认接口配置，避免打包分发时带上自己的 Key。"""
        ok = QMessageBox.question(
            self,
            "确认重置",
            "将清空 API Key，并把通道、URL、模型、超时、分段恢复为默认值。\n"
            "此操作会立刻写入配置文件。\n\n确定继续？",
        )
        if ok != QMessageBox.StandardButton.Yes:
            return
        defaults = {
            "provider": "deepseek",
            "base_url": PROVIDERS["deepseek"]["base_url"],
            "api_key": "",
            "model": PROVIDERS["deepseek"]["model"],
            "profile": "explain",
            "auto_fill": True,
            "ai_timeout": 600,
            "slice_on": True,
            "slice_size": 5,
            "last_url": self.url_edit.text().strip() or "https://www.baidu.com",
        }
        # 更新界面
        idx = self.cmb_provider.findData(defaults["provider"])
        if idx >= 0:
            self.cmb_provider.setCurrentIndex(idx)
        self.ed_url.setText(defaults["base_url"])
        self.ed_key.setText("")
        self.ed_model.setText(defaults["model"])
        pidx = self.cmb_profile.findData(defaults["profile"])
        if pidx >= 0:
            self.cmb_profile.setCurrentIndex(pidx)
        self.sp_timeout.setValue(defaults["ai_timeout"])
        self.cb_slice.setChecked(defaults["slice_on"])
        self.sp_slice.setValue(defaults["slice_size"])
        self.auto_fill_cb.setChecked(defaults["auto_fill"])
        # 写入文件
        self.settings = defaults
        save_settings(defaults)
        self.log("[设置] 已重置为默认，API Key 已清空")
        QMessageBox.information(
            self,
            "已重置",
            f"配置已恢复默认，API Key 已清空。\n文件：\n{CONF_FILE}",
        )

    def _on_provider(self):
        pid = self.cmb_provider.currentData()
        meta = PROVIDERS.get(pid) or {}
        if meta.get("base_url"):
            self.ed_url.setText(meta["base_url"])
        if meta.get("model"):
            self.ed_model.setText(meta["model"])

    # ---------- 页面交互 ----------
    def scan_page(self):
        js = """
        (function(){
          try {
            if (!window.__AI_STUDY_BRIDGE__) { return JSON.stringify({error:"bridge missing"}); }
            const qs = window.__AI_STUDY_BRIDGE__.extractQuestions();
            const text = window.__AI_STUDY_BRIDGE__.pageText();
            return JSON.stringify({questions: qs, pageText: text});
          } catch(e) {
            return JSON.stringify({error: String(e)});
          }
        })();
        """
        self.web.page().runJavaScript(js, self._scan_callback)

    def _scan_callback(self, result):
        try:
            data = json.loads(result or "{}")
        except Exception:
            data = {"error": "解析失败", "raw": str(result)[:200]}
        if data.get("error"):
            self.log(f"[扫描] 失败: {data.get('error')}")
            QMessageBox.warning(self, "扫描失败", data.get("error"))
            return
        qs = data.get("questions") or []
        text = data.get("pageText") or ""
        payload = {"source": "html-scan", "questions": qs, "pageText": text}
        self._add_packet("页面扫描", payload)
        pretty = json.dumps(qs, ensure_ascii=False, indent=2) if qs else (text or "")
        self.txt_q.setPlainText(pretty[:60000])
        self.log(f"[扫描] 识别题目块 {len(qs)} 个，正文 {len(text)} 字")
        self.set_status(f"扫描完成 · {len(qs)} 题")

    def _add_packet(self, url: str, data):
        text = compact_payload(data)
        item = {"url": url, "data": data, "text": text}
        self.packets.insert(0, item)
        self.packets = self.packets[:80]
        self._refresh_tree()
        self.log(f"[抓包] {url[:90]}  ({len(text)} 字)")

    def _refresh_tree(self):
        self.tree.clear()
        for i, p in enumerate(self.packets):
            hint = p["text"][:48].replace("\n", " ")
            it = QTreeWidgetItem([p["url"][:48], hint, f"{len(p['text'])}B"])
            it.setData(0, Qt.UserRole, i)
            self.tree.addTopLevelItem(it)

    def _on_sel_packet(self):
        items = self.tree.selectedItems()
        if not items:
            return
        chunks = []
        for it in items:
            i = it.data(0, Qt.UserRole)
            if isinstance(i, int) and 0 <= i < len(self.packets):
                chunks.append(self.packets[i]["text"])
        self.txt_q.setPlainText("\n\n".join(chunks))

    def clear_packets(self):
        self.packets.clear()
        self._refresh_tree()

    # ---------- AI ----------
    def run_ai(self):
        if self._busy:
            return
        content = self.txt_q.toPlainText().strip()
        if not content:
            QMessageBox.warning(self, "没有题目", "请先扫描页面，或直接把题目粘贴到文本框。")
            return
        cfg = self.collect_settings()
        if cfg["provider"] not in ("lmstudio", "llamacpp") and not cfg["api_key"]:
            QMessageBox.warning(self, "缺少 Key", "请到「接口设置」填写 API Key。")
            return
        self._busy = True
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        slice_on = bool(cfg.get("slice_on", True))
        slice_size = max(1, int(cfg.get("slice_size") or 5))
        chunks = slice_payload(content, slice_size) if slice_on else [(1, 0, content)]
        self.log(f"[AI] 开始请求，共 {len(chunks)} 段，超时 {cfg.get('ai_timeout') or 600}s，每段最多 {slice_size} 题")

        class AIResult(QObject):
            done = Signal(str, list, object)

        self._ai_result = AIResult()
        self._ai_result.done.connect(self._ai_done)

        def job():
            raw_parts = []
            all_items = []
            err = None
            try:
                local = cfg["provider"] in ("lmstudio", "llamacpp")
                for n, (start, end, payload) in enumerate(chunks, 1):
                    tag = f"第{n}/{len(chunks)}段"
                    if end:
                        tag += f"（题 {start}-{end}）"
                    self.signals.log_msg.emit(f"[AI] {tag} 发送中…")
                    prefix = ""
                    if len(chunks) > 1:
                        prefix = f"【{tag}】请只答本段题目，题号保持原编号。\n"
                    raw = chat_completions(
                        base_url=cfg["base_url"],
                        api_key=cfg["api_key"] or "local",
                        model=cfg["model"],
                        system=DEFAULT_PROMPTS.get(cfg["profile"], DEFAULT_PROMPTS["explain"]),
                        user=prefix + "请根据以下 JSON 数据或文本逐题作答：\n" + payload,
                        max_tokens=1024 if local else 4096,
                        timeout=int(cfg.get("ai_timeout") or 600),
                    )
                    raw_parts.append(f"--- {tag} ---\n{raw}")
                    items = parse_answers(raw)
                    all_items.extend(items)
                    self.signals.log_msg.emit(f"[AI] {tag} 完成，解析 {len(items)} 题")
                merged = "\n\n".join(raw_parts)
            except Exception as exc:
                err = exc
                merged = "\n\n".join(raw_parts) if raw_parts else ""
                if all_items:
                    merged += f"\n\n[后续段失败] {exc}"
            self._ai_result.done.emit(merged, all_items, err)

        threading.Thread(target=job, daemon=True).start()

    def _ai_done(self, raw: str, items: list, err):
        self._busy = False
        self.progress.setVisible(False)
        if err and not items:
            self.log(f"[AI] 失败：{err}")
            QMessageBox.critical(self, "AI 失败", str(err))
            return
        self.last_raw = raw
        self.last_answers = items
        self.last_questions = parse_question_payload(self.txt_q.toPlainText())
        self.tree_ans.clear()
        for it in items:
            self.tree_ans.addTopLevelItem(
                QTreeWidgetItem([str(it.get("index", "")), it.get("type", ""), (it.get("answer") or "")[:80]])
            )
        self.log("[AI] 原文：\n" + raw + "\n")
        self.log(f"[AI] 解析出 {len(items)} 题")
        self.set_status(f"AI 完成 · {len(items)} 题")
        if self.auto_fill_cb.isChecked() and items:
            self.auto_fill()

    def auto_fill(self):
        if not self.last_answers:
            QMessageBox.warning(self, "没有答案", "请先让 AI 跑出答题结果。")
            return
        payload = json.dumps(self.last_answers, ensure_ascii=False)
        js = f"""
        (function(){{
          try {{
            if (!window.__AI_STUDY_BRIDGE__) return JSON.stringify({{error:"bridge missing"}});
            return JSON.stringify(window.__AI_STUDY_BRIDGE__.applyAnswers({payload}));
          }} catch(e) {{
            return JSON.stringify({{error: String(e)}});
          }}
        }})();
        """
        self.web.page().runJavaScript(js, self._fill_callback)

    def _fill_callback(self, result):
        try:
            report = json.loads(result or "{}")
        except Exception:
            report = {"error": "解析失败", "raw": str(result)[:300]}
        if report.get("error"):
            self.log(f"[勾选] 失败: {report.get('error')}")
            QMessageBox.warning(self, "勾选失败", report.get("error"))
            return
        self.log(f"[勾选] {report}")
        applied = report.get("applied", 0)
        missed = report.get("missed", [])
        QMessageBox.information(
            self, "自动勾选完成",
            f"成功写入 {applied} 题。\n未匹配：{missed or '无'}\n"
            "若页面是高度定制组件，可对照右侧答案手动点一下漏掉的题。"
        )

    def copy_prompt_pack(self):
        content = self.txt_q.toPlainText().strip()
        if not content:
            QMessageBox.warning(self, "没有题目", "请先扫描或粘贴题目。")
            return
        cfg = self.collect_settings()
        profile = cfg.get("profile") or "explain"
        slice_on = bool(cfg.get("slice_on", True))
        slice_size = max(1, int(cfg.get("slice_size") or 5))
        chunks = slice_payload(content, slice_size) if slice_on else [(1, 0, content)]
        packs = []
        for n, (start, end, payload) in enumerate(chunks, 1):
            tag = f"第{n}/{len(chunks)}部分"
            if end:
                tag += f"（题{start}-{end}）"
            note = f"这是{tag}。请只答本部分题目，题号保持原编号。答完后等待用户粘贴下一部分。"
            packs.append((n, start, end, tag, build_web_ai_pack(payload, profile, note)))

        first = packs[0][4]
        QApplication.clipboard().setText(first)
        self.log(f"[复制] 已复制 {packs[0][3]} 到剪贴板（共 {len(packs)} 部分）")

        if len(packs) == 1:
            QMessageBox.information(self, "已复制", "提示词+题目已在剪贴板，可直接粘贴到网页 AI。")
            return

        folder = QFileDialog.getExistingDirectory(self, "选择保存分段 txt 的文件夹")
        if not folder:
            QMessageBox.information(
                self, "已复制第1部分",
                f"共 {len(packs)} 部分。第1部分已在剪贴板。\n若要下载全部分段，请再点一次并选择文件夹。"
            )
            return
        written = []
        for n, start, end, tag, text in packs:
            if end:
                name = f"AI题目_第{n}部分_题{start}-{end}.txt"
            else:
                name = f"AI题目_第{n}部分.txt"
            path = Path(folder) / name
            path.write_text(text, encoding="utf-8")
            written.append(str(path))
            self.log(f"[导出] {path}")
        QMessageBox.information(
            self, "分段已导出",
            f"共 {len(written)} 个文件。\n第1部分已在剪贴板。\n目录：\n{folder}"
        )

    def _build_packs(self):
        content = self.txt_q.toPlainText().strip()
        if not content:
            return []
        cfg = self.collect_settings()
        profile = cfg.get("profile") or "explain"
        slice_on = bool(cfg.get("slice_on", True))
        slice_size = max(1, int(cfg.get("slice_size") or 5))
        chunks = slice_payload(content, slice_size) if slice_on else [(1, 0, content)]
        packs = []
        for n, (start, end, payload) in enumerate(chunks, 1):
            tag = f"第{n}/{len(chunks)}部分"
            if end:
                tag += f"（题{start}-{end}）"
            note = f"这是{tag}。请只答本部分题目，题号保持原编号。答完后等待用户粘贴下一部分。"
            packs.append((n, start, end, tag, build_web_ai_pack(payload, profile, note)))
        return packs

    def view_prompt_packs(self):
        """弹出窗口列出全部分段提示词，可单独复制每一段。"""
        packs = self._build_packs()
        if not packs:
            QMessageBox.warning(self, "没有题目", "请先扫描或粘贴题目。")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(f"分段提示词（共 {len(packs)} 部分）")
        dlg.resize(780, 560)
        lay = QVBoxLayout(dlg)
        tip = QLabel(
            "启用分段后，每一部分都带完整系统提示词。\n"
            "点「复制这一段」即可粘贴到网页 AI；也可「全部导出为 txt」。"
        )
        tip.setStyleSheet("color:#94a3b8;")
        lay.addWidget(tip)
        # 分段选择
        row = QHBoxLayout()
        row.addWidget(QLabel("选择部分："))
        cmb = QComboBox()
        for n, start, end, tag, text in packs:
            cmb.addItem(tag)
        row.addWidget(cmb, 1)
        btn_copy = QPushButton("复制这一段")
        row.addWidget(btn_copy)
        lay.addLayout(row)
        viewer = QTextEdit()
        viewer.setReadOnly(True)
        lay.addWidget(viewer, 1)

        def show_part(idx):
            if 0 <= idx < len(packs):
                viewer.setPlainText(packs[idx][4])
        show_part(0)
        cmb.currentIndexChanged.connect(show_part)

        def copy_current():
            idx = cmb.currentIndex()
            if 0 <= idx < len(packs):
                QApplication.clipboard().setText(packs[idx][4])
                self.log(f"[复制] 已复制 {packs[idx][3]}")
                QMessageBox.information(dlg, "已复制", f"{packs[idx][3]} 已在剪贴板。")
        btn_copy.clicked.connect(copy_current)

        btn_export = QPushButton("全部导出为 txt")
        def do_export():
            folder = QFileDialog.getExistingDirectory(dlg, "选择保存文件夹")
            if not folder:
                return
            for n, start, end, tag, text in packs:
                if end:
                    name = f"AI题目_第{n}部分_题{start}-{end}.txt"
                else:
                    name = f"AI题目_第{n}部分.txt"
                (Path(folder) / name).write_text(text, encoding="utf-8")
            QMessageBox.information(dlg, "已导出", f"共 {len(packs)} 个文件已保存到：\n{folder}")
        btn_export.clicked.connect(do_export)
        lay.addWidget(btn_export)
        dlg.exec()

    def copy_ans(self):
        questions = self.last_questions or parse_question_payload(self.txt_q.toPlainText())
        text = format_qa_document(questions, self.last_answers, self.last_raw)
        if not text.strip():
            text = (self.last_raw or self.txt_log.toPlainText() or "").strip()
        QApplication.clipboard().setText(text)
        nq = len(questions)
        na = len(self.last_answers or [])
        self.log(f"[复制] 已写入剪贴板（{nq} 道题 + {na} 条答案）")

    def closeEvent(self, event):
        try:
            self.settings = self.collect_settings()
            save_settings(self.settings)
        except Exception:
            pass
        event.accept()


DOC = """功能（一体化嵌入浏览器版）
════════════════════════════════════
本版把浏览器直接嵌进程序窗口（QWebEngineView），不再需要单独启动调试 Chrome。

1. 在左侧地址栏打开题库页面
2. 点「① 扫描页面题目」——注入脚本抽取题目
3. 点「② AI 答题」——调用大模型（支持分段、超时）
4. 点「③ 自动勾选到页面」或勾上「AI 完成后自动勾选」

仍支持：
- 复制提示词+题目 / 查看分段提示词（逐段复制或导出 txt，方便粘贴到网页 AI）
- 复制题目+答案
- 接口设置（DeepSeek / OpenAI 兼容 / 本地 LM Studio / llama-server）
- 超时与分段控制

原理
════════════════════════════════════
┌──────────────────────────┐
│  本程序（PySide6）        │
│  ┌────────┐  ┌─────────┐ │
│  │QWebEngine│  │ 控制台  │ │
│  │ 浏览器  │←→│ AI+勾选 │ │
│  └────────┘  └─────────┘ │
└──────────────────────────┘
        │
        ▼
  DeepSeek / 本地模型

自动勾选复用原 page_bridge.js（含常见题库页 button.sel 等特殊逻辑）。

打包（Windows）
  pip install PySide6 requests pyinstaller
  pyinstaller --onefile --windowed --name AI答题助手 main.py
  需把 inject/ 目录一起打进去（可用 --add-data）。
"""


# 保持已打开窗口的引用，防止被回收
_open_windows: list = []


if __name__ == "__main__":
    # 高 DPI
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("AI答题助手")
    w = MainWindow()
    w.show()
    _open_windows.append(w)
    sys.exit(app.exec())
