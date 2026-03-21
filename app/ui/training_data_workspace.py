from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QCheckBox,
    QComboBox,
)

from app.config import BASE_DIR, voice_config
from app.training import (
    DEFAULT_FAIRY_TEMPLATE,
    enrich_dataset_with_llm,
    export_metadata,
    generate_training_audio,
    parse_dataset_with_rules,
    renumber_entries,
)
from app.training.dataset_models import CATEGORY_CHOICES, LENGTH_CHOICES, TrainingEntry
from app.app_preferences import apply_app_preferences, load_app_preferences, save_app_preferences
from app.rag import get_rag_manager, save_rag_settings
from app.settings import SecretStore, save_game_mode_settings
from app.ui.app_mode_dialog import choose_app_settings


logger = logging.getLogger(__name__)


class LLMEnrichWorker(QObject):
    progress = Signal(int, int, object)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, entries: list[TrainingEntry]) -> None:
        super().__init__()
        self.entries = [TrainingEntry.from_dict(entry.to_dict()) for entry in entries]
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        try:
            result = enrich_dataset_with_llm(
                self.entries,
                progress_callback=self._emit_progress,
                stop_requested=self._stop_event.is_set,
            )
            self.finished.emit(result)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))

    def _emit_progress(self, index: int, total: int, entry: TrainingEntry) -> None:
        self.progress.emit(index, total, entry)


class BatchGenerateWorker(QObject):
    progress = Signal(object, int, int, int, int)
    finished = Signal(object, object)
    failed = Signal(str)

    def __init__(
        self,
        entries: list[TrainingEntry],
        output_dir: Path,
        raw_text: str,
        *,
        only_missing: bool,
        selected_ids: set[int] | None,
    ) -> None:
        super().__init__()
        self.entries = [TrainingEntry.from_dict(entry.to_dict()) for entry in entries]
        self.output_dir = output_dir
        self.raw_text = raw_text
        self.only_missing = only_missing
        self.selected_ids = selected_ids
        self._stop_event = threading.Event()
        self._success = 0
        self._failed = 0

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        try:
            updated_entries, summary = generate_training_audio(
                self.entries,
                self.output_dir,
                raw_text=self.raw_text,
                only_missing=self.only_missing,
                selected_ids=self.selected_ids,
                stop_requested=self._stop_event.is_set,
                item_callback=self._emit_progress,
            )
            self.finished.emit(updated_entries, summary)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))

    def _emit_progress(self, entry: TrainingEntry, processed: int, total: int) -> None:
        if entry.status == "generated":
            self._success += 1
        elif entry.status == "failed":
            self._failed += 1
        self.progress.emit(entry, processed, total, self._success, self._failed)


class TrainingDataWorkspace(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Fairy Dataset Workspace")
        self.resize(1520, 960)

        self.entries: list[TrainingEntry] = []
        self._visible_entry_ids: list[int] = []
        self._table_updating = False
        self._llm_thread: QThread | None = None
        self._llm_worker: LLMEnrichWorker | None = None
        self._batch_thread: QThread | None = None
        self._batch_worker: BatchGenerateWorker | None = None
        self.on_mode_switch_requested: Callable[[str], None] | None = None
        self.on_close_requested: Callable[[], None] | None = None
        self._switching_mode = False
        self.rag_manager = get_rag_manager()

        self.default_output_dir = BASE_DIR / "project_output" / "fairy_dataset"

        self._build_ui()
        self._populate_template()
        self._refresh_filters()
        self._refresh_table()
        self._update_stats()

    def _build_ui(self) -> None:
        central = QWidget(self)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        toolbar = QHBoxLayout()
        self.load_template_btn = QPushButton("载入 Fairy 模板")
        self.load_file_btn = QPushButton("读取文本/JSON")
        self.save_template_btn = QPushButton("保存当前模板")
        self.settings_btn = QPushButton("设置")
        self.parse_btn = QPushButton("规则解析")
        self.enrich_btn = QPushButton("智能整理/标注")
        self.reindex_btn = QPushButton("重新编号")
        self.delete_btn = QPushButton("删除选中")
        toolbar.addWidget(self.load_template_btn)
        toolbar.addWidget(self.load_file_btn)
        toolbar.addWidget(self.save_template_btn)
        toolbar.addWidget(self.settings_btn)
        toolbar.addStretch(1)
        toolbar.addWidget(self.parse_btn)
        toolbar.addWidget(self.enrich_btn)
        toolbar.addWidget(self.reindex_btn)
        toolbar.addWidget(self.delete_btn)
        root.addLayout(toolbar)

        splitter = QSplitter(Qt.Vertical)
        root.addWidget(splitter, 1)

        top_widget = QWidget()
        top_layout = QVBoxLayout(top_widget)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.addWidget(QLabel("训练语料原文"))
        self.raw_input = QPlainTextEdit()
        self.raw_input.setPlaceholderText("在这里粘贴 Fairy 训练语料，然后点击“规则解析”。")
        top_layout.addWidget(self.raw_input, 1)
        splitter.addWidget(top_widget)

        bottom_widget = QWidget()
        bottom_layout = QVBoxLayout(bottom_widget)
        bottom_layout.setContentsMargins(0, 0, 0, 0)

        filter_row = QGridLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索关键词")
        self.category_filter = QComboBox()
        self.length_filter = QComboBox()
        self.enabled_only_checkbox = QCheckBox("仅看已启用")
        self.output_dir_input = QLineEdit(str(self.default_output_dir))
        self.output_dir_btn = QPushButton("输出目录")

        filter_row.addWidget(QLabel("搜索"), 0, 0)
        filter_row.addWidget(self.search_input, 0, 1)
        filter_row.addWidget(QLabel("类别"), 0, 2)
        filter_row.addWidget(self.category_filter, 0, 3)
        filter_row.addWidget(QLabel("长度"), 0, 4)
        filter_row.addWidget(self.length_filter, 0, 5)
        filter_row.addWidget(self.enabled_only_checkbox, 0, 6)
        filter_row.addWidget(QLabel("导出目录"), 1, 0)
        filter_row.addWidget(self.output_dir_input, 1, 1, 1, 5)
        filter_row.addWidget(self.output_dir_btn, 1, 6)
        bottom_layout.addLayout(filter_row)

        self.voice_status_label = QLabel(self._build_voice_status_text())
        self.stats_label = QLabel("总条目数 0")
        self.progress_label = QLabel("当前进度：空闲")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        bottom_layout.addWidget(self.voice_status_label)
        bottom_layout.addWidget(self.stats_label)
        bottom_layout.addWidget(self.progress_label)
        bottom_layout.addWidget(self.progress_bar)

        table_actions = QHBoxLayout()
        self.generate_btn = QPushButton("批量生成音频")
        self.generate_missing_btn = QPushButton("仅生成未生成项")
        self.regenerate_selected_btn = QPushButton("重生成选中项")
        self.stop_btn = QPushButton("停止")
        self.export_btn = QPushButton("导出 metadata")
        table_actions.addWidget(self.generate_btn)
        table_actions.addWidget(self.generate_missing_btn)
        table_actions.addWidget(self.regenerate_selected_btn)
        table_actions.addWidget(self.stop_btn)
        table_actions.addStretch(1)
        table_actions.addWidget(self.export_btn)
        bottom_layout.addLayout(table_actions)

        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(
            ["启用", "ID", "文本", "类别", "长度", "情绪", "状态", "音频路径", "备注"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(
            QAbstractItemView.DoubleClicked
            | QAbstractItemView.EditKeyPressed
            | QAbstractItemView.SelectedClicked
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(7, QHeaderView.Stretch)
        header.setSectionResizeMode(8, QHeaderView.Stretch)
        bottom_layout.addWidget(self.table, 1)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(400)
        bottom_layout.addWidget(QLabel("任务日志"))
        bottom_layout.addWidget(self.log_view, 0)

        pending_actions = QHBoxLayout()
        self.pending_refresh_btn = QPushButton("刷新 Pending Decisions")
        self.pending_confirm_btn = QPushButton("Confirm Selected")
        self.pending_reject_btn = QPushButton("Reject Selected")
        pending_actions.addWidget(self.pending_refresh_btn)
        pending_actions.addWidget(self.pending_confirm_btn)
        pending_actions.addWidget(self.pending_reject_btn)
        pending_actions.addStretch(1)
        bottom_layout.addLayout(pending_actions)

        self.pending_table = QTableWidget(0, 5)
        self.pending_table.setHorizontalHeaderLabels(["ID", "Title", "Importance", "Updated", "Status"])
        self.pending_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.pending_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.pending_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        pending_header = self.pending_table.horizontalHeader()
        pending_header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        pending_header.setSectionResizeMode(1, QHeaderView.Stretch)
        pending_header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        pending_header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        pending_header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        bottom_layout.addWidget(QLabel("Pending Decisions"))
        bottom_layout.addWidget(self.pending_table, 0)

        splitter.addWidget(bottom_widget)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 5)

        self.setCentralWidget(central)

        self._build_actions()
        self._connect_signals()

    def _build_actions(self) -> None:
        parse_action = QAction("规则解析", self)
        parse_action.triggered.connect(self.parse_corpus)
        self.addAction(parse_action)

    def _connect_signals(self) -> None:
        self.load_template_btn.clicked.connect(self._populate_template)
        self.load_file_btn.clicked.connect(self.load_template_from_file)
        self.save_template_btn.clicked.connect(self.save_template_to_file)
        self.settings_btn.clicked.connect(self._open_settings_dialog)
        self.parse_btn.clicked.connect(self.parse_corpus)
        self.enrich_btn.clicked.connect(self.start_llm_enrich)
        self.reindex_btn.clicked.connect(self.reindex_entries)
        self.delete_btn.clicked.connect(self.delete_selected_entries)
        self.generate_btn.clicked.connect(self.start_batch_generate)
        self.generate_missing_btn.clicked.connect(self.start_generate_missing)
        self.regenerate_selected_btn.clicked.connect(self.regenerate_selected)
        self.stop_btn.clicked.connect(self.stop_current_task)
        self.export_btn.clicked.connect(self.export_metadata_files)
        self.pending_refresh_btn.clicked.connect(self._refresh_pending_decisions)
        self.pending_confirm_btn.clicked.connect(self._confirm_selected_decision)
        self.pending_reject_btn.clicked.connect(self._reject_selected_decision)
        self.output_dir_btn.clicked.connect(self.choose_output_dir)
        self.search_input.textChanged.connect(self._refresh_table)
        self.category_filter.currentTextChanged.connect(self._refresh_table)
        self.length_filter.currentTextChanged.connect(self._refresh_table)
        self.enabled_only_checkbox.toggled.connect(self._refresh_table)
        self.table.itemChanged.connect(self._on_table_item_changed)
        self.table.cellClicked.connect(self._on_table_cell_clicked)

    def _populate_template(self) -> None:
        self.raw_input.setPlainText(DEFAULT_FAIRY_TEMPLATE)
        self.append_log("已载入内置 Fairy 训练语料模板。")

    def choose_output_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "选择训练数据输出目录", str(self.default_output_dir))
        if chosen:
            self.output_dir_input.setText(chosen)

    def load_template_from_file(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            "读取模板",
            str(BASE_DIR),
            "Template (*.txt *.json);;All Files (*)",
        )
        if not path_str:
            return
        path = Path(path_str)
        try:
            if path.suffix.lower() == ".json":
                payload = json.loads(path.read_text(encoding="utf-8"))
                self.raw_input.setPlainText(str(payload.get("raw_text", "")))
                entry_payloads = payload.get("entries")
                if isinstance(entry_payloads, list):
                    self.entries = [TrainingEntry.from_dict(item) for item in entry_payloads if isinstance(item, dict)]
                    self._refresh_filters()
                    self._refresh_table()
                    self._update_stats()
            else:
                self.raw_input.setPlainText(path.read_text(encoding="utf-8"))
            self.append_log(f"已读取模板：{path}")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "读取失败", str(exc))

    def save_template_to_file(self) -> None:
        path_str, _ = QFileDialog.getSaveFileName(
            self,
            "保存模板",
            str(BASE_DIR / "fairy_training_template.json"),
            "JSON (*.json);;Text (*.txt)",
        )
        if not path_str:
            return
        path = Path(path_str)
        try:
            if path.suffix.lower() == ".json":
                payload = {
                    "raw_text": self.raw_input.toPlainText(),
                    "entries": [entry.to_dict() for entry in self.entries],
                }
                path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            else:
                path.write_text(self.raw_input.toPlainText(), encoding="utf-8")
            self.append_log(f"已保存模板：{path}")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "保存失败", str(exc))

    def parse_corpus(self) -> None:
        raw_text = self.raw_input.toPlainText().strip()
        logger.info("dataset_import_start chars=%s", len(raw_text))
        if not raw_text:
            QMessageBox.warning(self, "没有内容", "请先粘贴训练语料。")
            return
        self.entries = parse_dataset_with_rules(raw_text)
        logger.info("dataset_parse_done count=%s", len(self.entries))
        self.append_log(f"规则解析完成，共 {len(self.entries)} 条。")
        self._refresh_filters()
        self._refresh_table()
        self._update_stats()
        self._refresh_pending_decisions()

    def start_llm_enrich(self) -> None:
        if self._llm_thread is not None:
            return
        if not self.entries:
            QMessageBox.information(self, "没有条目", "请先解析训练语料。")
            return
        self.append_log("dataset_llm_enrich_start")
        self.progress_bar.setValue(0)
        self.progress_label.setText("当前进度：正在执行 LLM 标注")

        worker = LLMEnrichWorker(self.entries)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_llm_enrich_progress)
        worker.finished.connect(self._on_llm_enrich_finished)
        worker.failed.connect(self._on_worker_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._cleanup_llm_worker)
        self._llm_worker = worker
        self._llm_thread = thread
        thread.start()

    def _on_llm_enrich_progress(self, index: int, total: int, entry: TrainingEntry) -> None:
        percent = int(index / max(total, 1) * 100)
        self.progress_bar.setValue(percent)
        self.progress_label.setText(f"当前进度：LLM 标注 {index}/{total}，当前条目 #{entry.id}")

    def _on_llm_enrich_finished(self, entries: list[TrainingEntry]) -> None:
        self.entries = entries
        self.append_log("dataset_llm_enrich_done")
        self.progress_bar.setValue(100)
        self.progress_label.setText("当前进度：LLM 标注完成")
        self._refresh_filters()
        self._refresh_table()
        self._update_stats()
        self._refresh_pending_decisions()

    def _cleanup_llm_worker(self) -> None:
        self._llm_worker = None
        self._llm_thread = None

    def start_batch_generate(self) -> None:
        self._start_batch_generation(only_missing=False, selected_ids=None)

    def start_generate_missing(self) -> None:
        self._start_batch_generation(only_missing=True, selected_ids=None)

    def regenerate_selected(self) -> None:
        selected_ids = self._selected_entry_ids()
        if not selected_ids:
            QMessageBox.information(self, "没有选择", "请先在表格中选中至少一条记录。")
            return
        self._start_batch_generation(only_missing=False, selected_ids=selected_ids)

    def _start_batch_generation(self, *, only_missing: bool, selected_ids: set[int] | None) -> None:
        if self._batch_thread is not None:
            return
        if not self.entries:
            QMessageBox.information(self, "没有条目", "请先解析训练语料。")
            return

        problem = self._validate_cosyvoice_ready()
        if problem:
            QMessageBox.critical(self, "CosyVoice 不可用", problem)
            return

        output_dir = Path(self.output_dir_input.text().strip() or self.default_output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        for entry in self.entries:
            should_queue = entry.enabled and (not selected_ids or entry.id in selected_ids)
            if only_missing and entry.audio_path:
                should_queue = False
            if should_queue and entry.status != "generated":
                entry.status = "queued"

        self._refresh_table()
        self._update_stats()
        self.append_log(f"batch_generate_start -> {output_dir}")
        self.progress_bar.setValue(0)
        self.progress_label.setText("当前进度：准备批量生成音频")

        worker = BatchGenerateWorker(
            self.entries,
            output_dir,
            self.raw_input.toPlainText(),
            only_missing=only_missing,
            selected_ids=selected_ids,
        )
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_batch_progress)
        worker.finished.connect(self._on_batch_finished)
        worker.failed.connect(self._on_worker_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._cleanup_batch_worker)
        self._batch_worker = worker
        self._batch_thread = thread
        thread.start()

    def _on_batch_progress(
        self,
        entry: TrainingEntry,
        processed: int,
        total: int,
        success_count: int,
        failed_count: int,
    ) -> None:
        self._merge_entry(entry)
        percent = int(processed / max(total, 1) * 100)
        self.progress_bar.setValue(percent)
        self.progress_label.setText(
            f"当前进度：{processed}/{total}，成功 {success_count}，失败 {failed_count}，当前 #{entry.id}"
        )
        self._refresh_table()
        self._update_stats()

    def _on_batch_finished(self, entries: list[TrainingEntry], summary: object) -> None:
        self.entries = entries
        self.progress_bar.setValue(100)
        self.progress_label.setText("当前进度：批量生成完成")
        self.append_log(f"批量生成完成。成功 {summary.succeeded}，失败 {summary.failed}。")
        self._refresh_filters()
        self._refresh_table()
        self._update_stats()

    def _cleanup_batch_worker(self) -> None:
        self._batch_worker = None
        self._batch_thread = None

    def stop_current_task(self) -> None:
        stopped = False
        if self._llm_worker is not None:
            self._llm_worker.stop()
            stopped = True
        if self._batch_worker is not None:
            self._batch_worker.stop()
            stopped = True
        if stopped:
            self.append_log("已发送停止请求。")

    def export_metadata_files(self) -> None:
        if not self.entries:
            QMessageBox.information(self, "没有条目", "请先解析训练语料。")
            return
        output_dir = Path(self.output_dir_input.text().strip() or self.default_output_dir)
        export_metadata(self.entries, output_dir, raw_text=self.raw_input.toPlainText())
        self.append_log(f"export_metadata_done -> {output_dir}")

    def reindex_entries(self) -> None:
        if not self.entries:
            return
        self.entries = renumber_entries(self.entries, reset_generated=True)
        self.append_log("条目已重新编号。")
        self._refresh_table()
        self._update_stats()

    def delete_selected_entries(self) -> None:
        selected_ids = self._selected_entry_ids()
        if not selected_ids:
            return
        self.entries = [entry for entry in self.entries if entry.id not in selected_ids]
        self.entries = renumber_entries(self.entries, reset_generated=True)
        self.append_log(f"已删除 {len(selected_ids)} 条记录。")
        self._refresh_filters()
        self._refresh_table()
        self._update_stats()

    def _selected_entry_ids(self) -> set[int]:
        selected_rows = {item.row() for item in self.table.selectedItems()}
        ids: set[int] = set()
        for row in selected_rows:
            if row < len(self._visible_entry_ids):
                ids.add(self._visible_entry_ids[row])
        return ids

    def _visible_entries(self) -> list[TrainingEntry]:
        keyword = self.search_input.text().strip().lower()
        category = self.category_filter.currentText().strip()
        length_type = self.length_filter.currentText().strip()
        enabled_only = self.enabled_only_checkbox.isChecked()

        visible: list[TrainingEntry] = []
        for entry in self.entries:
            if keyword and keyword not in entry.text.lower() and keyword not in (entry.note or "").lower():
                continue
            if category and category != "全部" and (entry.category or "") != category:
                continue
            if length_type and length_type != "全部" and (entry.length_type or "") != length_type:
                continue
            if enabled_only and not entry.enabled:
                continue
            visible.append(entry)
        return visible

    def _refresh_filters(self) -> None:
        current_category = self.category_filter.currentText()
        current_length = self.length_filter.currentText()

        categories = ["全部"] + sorted({entry.category for entry in self.entries if entry.category}) + CATEGORY_CHOICES[1:]
        dedup_categories: list[str] = []
        for item in categories:
            if item not in dedup_categories:
                dedup_categories.append(item)

        self.category_filter.blockSignals(True)
        self.length_filter.blockSignals(True)
        self.category_filter.clear()
        self.category_filter.addItems(dedup_categories)
        self.length_filter.clear()
        self.length_filter.addItems(["全部"] + LENGTH_CHOICES[1:])
        if current_category and self.category_filter.findText(current_category) >= 0:
            self.category_filter.setCurrentText(current_category)
        if current_length and self.length_filter.findText(current_length) >= 0:
            self.length_filter.setCurrentText(current_length)
        self.category_filter.blockSignals(False)
        self.length_filter.blockSignals(False)

    def _refresh_table(self) -> None:
        visible = self._visible_entries()
        self._visible_entry_ids = [entry.id for entry in visible]
        self._table_updating = True
        self.table.setRowCount(len(visible))

        for row, entry in enumerate(visible):
            enabled_item = QTableWidgetItem()
            enabled_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
            enabled_item.setCheckState(Qt.Checked if entry.enabled else Qt.Unchecked)
            self.table.setItem(row, 0, enabled_item)

            self._set_readonly_item(row, 1, str(entry.id))
            self._set_editable_item(row, 2, entry.text)
            self._set_editable_item(row, 3, entry.category or "")
            self._set_editable_item(row, 4, entry.length_type or "")
            self._set_editable_item(row, 5, entry.emotion or "")
            self._set_readonly_item(row, 6, entry.status)
            self._set_readonly_item(row, 7, entry.audio_path or "")
            self._set_editable_item(row, 8, entry.note)

        self._table_updating = False

    def _set_editable_item(self, row: int, column: int, text: str) -> None:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() | Qt.ItemIsEditable)
        self.table.setItem(row, column, item)

    def _set_readonly_item(self, row: int, column: int, text: str) -> None:
        item = QTableWidgetItem(text)
        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        self.table.setItem(row, column, item)

    def _on_table_item_changed(self, item: QTableWidgetItem) -> None:
        if self._table_updating:
            return
        row = item.row()
        if row >= len(self._visible_entry_ids):
            return
        entry = self._find_entry(self._visible_entry_ids[row])
        if entry is None:
            return

        column = item.column()
        if column == 0:
            entry.enabled = item.checkState() == Qt.Checked
        elif column == 2:
            new_text = item.text().strip()
            if not new_text:
                return
            if new_text != entry.text:
                entry.text = new_text
                self._invalidate_audio_after_text_change(entry)
        elif column == 3:
            entry.category = item.text().strip() or None
        elif column == 4:
            entry.length_type = item.text().strip() or None
        elif column == 5:
            entry.emotion = item.text().strip() or None
        elif column == 8:
            entry.note = item.text().strip()

        self._refresh_filters()
        self._update_stats()

    def _on_table_cell_clicked(self, row: int, column: int) -> None:
        if column != 2:
            return
        if row >= len(self._visible_entry_ids):
            return
        entry = self._find_entry(self._visible_entry_ids[row])
        if entry is None or not entry.text.strip():
            return
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(entry.text)
        self.progress_label.setText(f"当前进度：已复制条目 #{entry.id} 文本")

    def _invalidate_audio_after_text_change(self, entry: TrainingEntry) -> None:
        if entry.audio_path:
            entry.audio_path = None
            entry.generated_at = None
            entry.voice_provider = None
            entry.status = "tagged" if entry.category or entry.length_type or entry.emotion else "parsed"
            entry.note = self._merge_note(entry.note, "文本已修改，请重新生成音频。")

    def _merge_note(self, left: str, right: str) -> str:
        if not left:
            return right
        if right in left:
            return left
        return f"{left} | {right}"

    def _update_stats(self) -> None:
        total = len(self.entries)
        enabled = sum(1 for entry in self.entries if entry.enabled)
        short_count = sum(1 for entry in self.entries if entry.length_type == "short")
        medium_count = sum(1 for entry in self.entries if entry.length_type == "medium")
        long_count = sum(1 for entry in self.entries if entry.length_type == "long")
        generated = sum(1 for entry in self.entries if entry.status == "generated" and entry.audio_path)
        failed = sum(1 for entry in self.entries if entry.status == "failed")
        pending = total - generated
        self.stats_label.setText(
            f"总条目数 {total} | 已启用 {enabled} | short {short_count} | medium {medium_count} | "
            f"long {long_count} | 已生成 {generated} | 未生成 {pending} | 失败 {failed}"
        )

    def _find_entry(self, entry_id: int) -> TrainingEntry | None:
        for entry in self.entries:
            if entry.id == entry_id:
                return entry
        return None

    def _merge_entry(self, updated: TrainingEntry) -> None:
        current = self._find_entry(updated.id)
        if current is None:
            return
        current.category = updated.category
        current.length_type = updated.length_type
        current.emotion = updated.emotion
        current.enabled = updated.enabled
        current.audio_path = updated.audio_path
        current.status = updated.status
        current.note = updated.note
        current.error_message = updated.error_message
        current.generated_at = updated.generated_at
        current.voice_provider = updated.voice_provider

    def _on_worker_failed(self, message: str) -> None:
        self.append_log(f"任务失败：{message}")
        self.progress_label.setText(f"当前进度：任务失败 - {message}")
        QMessageBox.critical(self, "任务失败", message)

    def append_log(self, message: str) -> None:
        logger.info("%s", message)
        self.log_view.appendPlainText(message)

    def _refresh_pending_decisions(self) -> None:
        items = self.rag_manager.list_pending_decisions(limit=50)
        self.pending_table.setRowCount(len(items))
        for row, item in enumerate(items):
            values = [
                str(item.get("id", "")),
                str(item.get("title", "")),
                str(item.get("importance", "")),
                str(item.get("updated_at", ""))[:19],
                str(item.get("decision_status", "")),
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                self.pending_table.setItem(row, column, cell)

    def _selected_pending_id(self) -> str:
        row = self.pending_table.currentRow()
        if row < 0:
            return ""
        item = self.pending_table.item(row, 0)
        return item.text().strip() if item is not None else ""

    def _confirm_selected_decision(self) -> None:
        item_id = self._selected_pending_id()
        if not item_id:
            QMessageBox.information(self, "未选择", "请先选中一条待确认决策。")
            return
        result = self.rag_manager.confirm_decision_candidate(item_id)
        if result.get("ok"):
            self.append_log(f"decision_confirmed -> {result.get('item_id', item_id)}")
            self._refresh_pending_decisions()
        else:
            QMessageBox.warning(self, "确认失败", str(result.get("reason", "unknown")))

    def _reject_selected_decision(self) -> None:
        item_id = self._selected_pending_id()
        if not item_id:
            QMessageBox.information(self, "未选择", "请先选中一条待确认决策。")
            return
        result = self.rag_manager.reject_decision_candidate(item_id)
        if result.get("ok"):
            self.append_log(f"decision_rejected -> {result.get('item_id', item_id)}")
            self._refresh_pending_decisions()
        else:
            QMessageBox.warning(self, "拒绝失败", str(result.get("reason", "unknown")))

    def _open_settings_dialog(self) -> None:
        settings = choose_app_settings(self, "training", load_app_preferences())
        if settings is None:
            return

        save_app_preferences(settings.preferences)
        apply_app_preferences(settings.preferences)
        save_game_mode_settings(settings.game_mode_settings)
        if settings.rag_settings is not None:
            save_rag_settings(settings.rag_settings)
        secret_store = SecretStore()
        for secret_ref in settings.cleared_secret_refs:
            secret_store.clear(secret_ref)
        for secret_ref, secret_value in settings.secret_updates.items():
            if secret_value.strip():
                secret_store.set(secret_ref, secret_value.strip())

        if settings.mode != "training":
            self._switching_mode = True
            callback = self.on_mode_switch_requested
            if callback is not None:
                callback(settings.mode)

    def _build_voice_status_text(self) -> str:
        mode = "zero-shot clone" if voice_config.voice_clone_enabled else "sft"
        return (
            "当前训练语音引擎："
            f"{voice_config.backend} / {mode} | "
            f"prompt wav: {voice_config.prompt_wav_path.name} | "
            f"prompt text: {voice_config.prompt_text_path.name}"
        )

    def _validate_cosyvoice_ready(self) -> str | None:
        if voice_config.backend != "cosyvoice2_service":
            return f"当前 backend={voice_config.backend}，训练导出已强制要求使用 cosyvoice2_service。"
        if not voice_config.cosyvoice_python_path.exists():
            return f"未找到 CosyVoice Python 运行时：{voice_config.cosyvoice_python_path}"
        if voice_config.voice_clone_enabled and not voice_config.prompt_wav_path.exists():
            return f"未找到 Fairy prompt wav：{voice_config.prompt_wav_path}"
        if voice_config.voice_clone_enabled and not voice_config.prompt_text_path.exists():
            return f"未找到 Fairy prompt text：{voice_config.prompt_text_path}"
        return None

    def closeEvent(self, event) -> None:  # noqa: N802
        self.stop_current_task()
        if self._llm_thread is not None:
            self._llm_thread.quit()
            self._llm_thread.wait(1000)
        if self._batch_thread is not None:
            self._batch_thread.quit()
            self._batch_thread.wait(1000)
        if not self._switching_mode:
            callback = self.on_close_requested
            self.on_close_requested = None
            if callback is not None:
                callback()
        super().closeEvent(event)

    def close_for_mode_switch(self) -> None:
        self._switching_mode = True
        self.on_close_requested = None
        self.close()
