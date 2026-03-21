from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QPlainTextEdit,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.app_preferences import AppPreferences
from app.providers.cloud_clients import DoubaoClient, OpenAICompatibleClient
from app.providers.provider_registry import ProviderRegistry
from app.providers.provider_schema import ProviderConfig, ProviderRequestError
from app.rag import RAGSettings, load_rag_settings
from app.settings import GameModeSettings, SecretStore, load_game_mode_settings
from app.ui.i18n import LANG_EN, LANG_ZH, normalize_ui_language, tr


MODE_LABELS = {
    "assistant": "Fairy 助手模式",
    "training": "语料训练模式",
}

PROVIDER_LABELS = {
    "doubao_seed2": "豆包 Seed 2.0",
    "qwen_dashscope": "Qwen（DashScope）",
    "openai_compatible_custom": "OpenAI-compatible Custom",
}


@dataclass(slots=True)
class AppSettingsResult:
    mode: str
    preferences: AppPreferences
    rag_settings: RAGSettings | None
    game_mode_settings: GameModeSettings
    secret_updates: dict[str, str] = field(default_factory=dict)
    cleared_secret_refs: list[str] = field(default_factory=list)


class AppModeDialog(QDialog):
    def __init__(
        self,
        current_mode: str,
        preferences: AppPreferences,
        rag_settings: RAGSettings,
        game_settings: GameModeSettings,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.registry = ProviderRegistry()
        self.secret_store = SecretStore()
        self.preferences = preferences
        self.game_settings = game_settings
        self.rag_settings = rag_settings
        self._language = normalize_ui_language(preferences.ui_language)
        self._providers_state = dict(game_settings.providers)
        self._loaded_provider_id = ""
        self._current_secret_ref = ""
        self._embedding_secret_ref = rag_settings.embedding_api_key_ref
        self._secret_updates: dict[str, str] = {}
        self._cleared_secret_refs: list[str] = []

        self.setWindowTitle("Fairy 设置")
        self.setModal(True)
        self._apply_dialog_geometry(parent)
        self._apply_styles()

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 14)
        root.setSpacing(12)

        root.addWidget(self._build_header())

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            """
            QScrollArea {
                background: transparent;
                border: none;
            }
            QScrollBar:vertical {
                background: rgba(8, 16, 30, 90);
                width: 8px;
                margin: 4px 0 4px 0;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: rgba(98, 127, 173, 150);
                border-radius: 4px;
                min-height: 28px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            """
        )
        content = QWidget(self)
        self.content_layout = QVBoxLayout(content)
        self.content_layout.setContentsMargins(0, 0, 4, 0)
        self.content_layout.setSpacing(12)
        self._build_content(current_mode, preferences, rag_settings, game_settings)
        self.content_layout.addStretch(1)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self.reply_voice_checkbox.toggled.connect(self._sync_voice_options)
        self.persona_enable_checkbox.toggled.connect(self._sync_persona_options)
        self.provider_combo.currentIndexChanged.connect(self._load_selected_provider_into_form)
        self.reveal_key_checkbox.toggled.connect(self._toggle_secret_visibility)
        self.update_key_button.clicked.connect(self._mark_secret_updated)
        self.clear_key_button.clicked.connect(self._clear_secret)
        self.test_connection_button.clicked.connect(self._test_connection)
        self.embedding_reveal_key_checkbox.toggled.connect(self._toggle_embedding_secret_visibility)
        self.embedding_update_key_button.clicked.connect(self._mark_embedding_secret_updated)
        self.embedding_clear_key_button.clicked.connect(self._clear_embedding_secret)

        self._sync_voice_options(self.reply_voice_checkbox.isChecked())
        self._sync_persona_options(self.persona_enable_checkbox.isChecked())
        self._load_selected_provider_into_form()

    def _apply_dialog_geometry(self, parent) -> None:
        screen = None
        if parent is not None and parent.windowHandle() is not None:
            screen = parent.windowHandle().screen()
        if screen is None:
            screen = QApplication.primaryScreen()
        geometry = screen.availableGeometry() if screen is not None else None
        if geometry is None:
            self.resize(760, 760)
            self.setMinimumSize(680, 560)
            return
        width = min(820, max(680, geometry.width() - 120))
        height = min(820, max(560, geometry.height() - 120))
        self.resize(width, height)
        self.setMinimumSize(min(680, width), min(560, height))

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QDialog {
                background: #08111F;
                color: #E4EDF8;
            }
            QLabel {
                color: #DCE7F5;
                font-size: 12px;
            }
            QGroupBox {
                background: rgba(10, 18, 33, 220);
                border: 1px solid rgba(67, 95, 138, 120);
                border-radius: 14px;
                margin-top: 12px;
                padding: 14px 14px 12px 14px;
                font-weight: 600;
                color: #ECF3FA;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
                color: #EDF4FB;
            }
            QLineEdit, QPlainTextEdit, QComboBox, QSpinBox {
                background: rgba(13, 22, 39, 235);
                color: #EAF1FA;
                border-radius: 10px;
                border: 1px solid rgba(73, 102, 146, 125);
                padding: 8px 10px;
                selection-background-color: rgba(123, 159, 214, 100);
            }
            QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus {
                border: 1px solid rgba(128, 166, 219, 165);
            }
            QComboBox::drop-down {
                border: none;
                width: 26px;
            }
            QCheckBox {
                spacing: 8px;
                color: #D7E5F5;
            }
            QPushButton {
                background: rgba(16, 28, 49, 230);
                color: #DFE8F5;
                border-radius: 10px;
                border: 1px solid rgba(75, 103, 145, 110);
                padding: 8px 14px;
                font-size: 12px;
            }
            QPushButton:hover {
                background: rgba(24, 39, 66, 236);
                border: 1px solid rgba(116, 147, 192, 150);
            }
            QPushButton:pressed {
                background: rgba(12, 22, 40, 240);
            }
            QDialogButtonBox QPushButton {
                min-width: 96px;
            }
            """
        )

    def _build_header(self) -> QWidget:
        card = QFrame(self)
        card.setStyleSheet(
            """
            QFrame {
                background: rgba(10, 18, 33, 226);
                border: 1px solid rgba(74, 105, 150, 96);
                border-radius: 16px;
            }
            """
        )
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(4)

        title = QLabel("Fairy Settings", card)
        title.setStyleSheet("font-size:18px; font-weight:700; color:#F1F6FC;")
        subtitle = QLabel("模式、语音、人格与游戏模式路由配置", card)
        subtitle.setStyleSheet("font-size:11px; color:#96AFCC;")
        subtitle.setWordWrap(True)

        layout.addWidget(title)
        layout.addWidget(subtitle)
        return card

    def _build_content(self, current_mode: str, preferences: AppPreferences, rag_settings: RAGSettings, game_settings: GameModeSettings) -> None:
        self.content_layout.addWidget(self._build_general_section(current_mode, preferences))
        self.content_layout.addWidget(self._build_persona_section(preferences))
        self.content_layout.addWidget(self._build_rag_section(rag_settings))
        self.content_layout.addWidget(self._build_game_mode_section(game_settings))

    def _build_general_section(self, current_mode: str, preferences: AppPreferences) -> QWidget:
        group = QGroupBox("常规", self)
        form = QFormLayout(group)
        group.setTitle("General")
        form.setLabelAlignment(Qt.AlignLeft)
        form.setFormAlignment(Qt.AlignTop)
        form.setSpacing(10)

        self.mode_combo = QComboBox(self)
        self.mode_combo.addItem(MODE_LABELS["assistant"], "assistant")
        self.mode_combo.addItem(MODE_LABELS["training"], "training")
        self.mode_combo.setCurrentIndex(max(0, self.mode_combo.findData(current_mode)))
        self.mode_combo.setItemText(0, "Fairy Assistant")
        self.mode_combo.setItemText(1, "Training")
        self.language_combo = QComboBox(self)
        self.language_combo.addItem(tr("language_zh", self._language), LANG_ZH)
        self.language_combo.addItem(tr("language_en", self._language), LANG_EN)
        self.language_combo.setCurrentIndex(max(0, self.language_combo.findData(self._language)))
        form.addRow(tr("language_label", self._language), self.language_combo)
        form.addRow("工作模式", self.mode_combo)

        self.reply_voice_checkbox = QCheckBox("整句语音播报", self)
        self.reply_voice_checkbox.setChecked(preferences.speak_responses)
        mode_label = form.labelForField(self.mode_combo)
        if mode_label is not None:
            mode_label.setText("Mode")
        self.reply_voice_checkbox.setText("Speak full replies")
        form.addRow("", self.reply_voice_checkbox)

        self.stream_voice_checkbox = QCheckBox("实时语音播报", self)
        self.stream_voice_checkbox.setChecked(preferences.stream_responses)
        self.stream_voice_checkbox.setText("Stream voice playback")
        form.addRow("", self.stream_voice_checkbox)

        note = QLabel("关闭整句语音后，实时语音也会一并关闭。", group)
        note.setStyleSheet("color:#8EA7C6; font-size:11px;")
        note.setWordWrap(True)
        note.setText("Disabling full reply speech also disables streaming voice playback.")
        form.addRow("", note)
        return group

    def _build_persona_section(self, preferences: AppPreferences) -> QWidget:
        group = QGroupBox("Persona", self)
        form = QFormLayout(group)
        form.setSpacing(10)

        self.persona_enable_checkbox = QCheckBox("Enable Persona System", self)
        self.persona_enable_checkbox.setChecked(preferences.persona_enabled)
        form.addRow("", self.persona_enable_checkbox)

        self.persona_mode_combo = QComboBox(self)
        self.persona_mode_combo.addItem("off", "off")
        self.persona_mode_combo.addItem("lightweight", "lightweight")
        self.persona_mode_combo.addItem("full", "full")
        self.persona_mode_combo.setCurrentIndex(max(0, self.persona_mode_combo.findData(preferences.persona_mode)))
        form.addRow("Persona Mode", self.persona_mode_combo)

        self.persona_game_force_disable_checkbox = QCheckBox("游戏模式下强制关闭 Persona", self)
        self.persona_game_force_disable_checkbox.setChecked(preferences.game_mode_force_disable_persona)
        form.addRow("", self.persona_game_force_disable_checkbox)

        note = QLabel("建议 4B 本地模型使用 off 或 lightweight。", group)
        note.setStyleSheet("color:#8EA7C6; font-size:11px;")
        form.addRow("", note)
        return group

    def _build_rag_section(self, rag_settings: RAGSettings) -> QWidget:
        group = QGroupBox("RAG / Knowledge", self)
        layout = QVBoxLayout(group)
        group.setTitle("Game Mode")
        layout.setSpacing(12)

        retrieval_card = QGroupBox("Retrieval", self)
        retrieval_form = QFormLayout(retrieval_card)
        retrieval_form.setSpacing(10)

        self.rag_enable_checkbox = QCheckBox("Enable RAG", self)
        self.rag_enable_checkbox.setChecked(rag_settings.rag_enabled)
        retrieval_form.addRow("", self.rag_enable_checkbox)

        self.rag_top_k_spin = QSpinBox(self)
        self.rag_top_k_spin.setRange(1, 12)
        self.rag_top_k_spin.setValue(rag_settings.rag_top_k)
        retrieval_form.addRow("Top K", self.rag_top_k_spin)

        self.rag_max_chars_spin = QSpinBox(self)
        self.rag_max_chars_spin.setRange(300, 5000)
        self.rag_max_chars_spin.setSingleStep(100)
        self.rag_max_chars_spin.setValue(rag_settings.rag_max_context_chars)
        retrieval_form.addRow("Max context chars", self.rag_max_chars_spin)

        self.rag_history_checkbox = QCheckBox("Use for history queries", self)
        self.rag_history_checkbox.setChecked(rag_settings.rag_use_for_history_queries)
        retrieval_form.addRow("", self.rag_history_checkbox)

        self.rag_document_checkbox = QCheckBox("Use for document QA", self)
        self.rag_document_checkbox.setChecked(rag_settings.rag_use_for_document_qa)
        retrieval_form.addRow("", self.rag_document_checkbox)

        self.retrieval_debug_checkbox = QCheckBox("Enable retrieval debug panel", self)
        self.retrieval_debug_checkbox.setChecked(rag_settings.enable_retrieval_debug_panel)
        retrieval_form.addRow("", self.retrieval_debug_checkbox)
        layout.addWidget(retrieval_card)

        embedding_card = QGroupBox("Embedding", self)
        embedding_form = QFormLayout(embedding_card)
        embedding_form.setSpacing(10)

        self.embedding_enable_checkbox = QCheckBox("Enable embeddings", self)
        self.embedding_enable_checkbox.setChecked(rag_settings.embedding_enabled)
        embedding_form.addRow("", self.embedding_enable_checkbox)

        self.embedding_provider_combo = QComboBox(self)
        self.embedding_provider_combo.addItem("qwen_cloud", "qwen_cloud")
        self.embedding_provider_combo.addItem("bge_local", "bge_local")
        self.embedding_provider_combo.addItem("hash", "hash")
        self.embedding_provider_combo.setCurrentIndex(max(0, self.embedding_provider_combo.findData(rag_settings.embedding_provider)))
        embedding_form.addRow("Provider", self.embedding_provider_combo)

        self.embedding_model_input = QLineEdit(rag_settings.embedding_model, self)
        embedding_form.addRow("Model", self.embedding_model_input)

        self.embedding_base_url_input = QLineEdit(rag_settings.embedding_base_url, self)
        embedding_form.addRow("Base URL", self.embedding_base_url_input)

        self.embedding_timeout_spin = QSpinBox(self)
        self.embedding_timeout_spin.setRange(5, 120)
        self.embedding_timeout_spin.setValue(rag_settings.embedding_timeout_seconds)
        embedding_form.addRow("Timeout (s)", self.embedding_timeout_spin)

        self.vector_backend_combo = QComboBox(self)
        self.vector_backend_combo.addItem("auto", "auto")
        self.vector_backend_combo.addItem("chroma", "chroma")
        self.vector_backend_combo.addItem("sqlite", "sqlite")
        self.vector_backend_combo.setCurrentIndex(max(0, self.vector_backend_combo.findData(rag_settings.vector_backend)))
        embedding_form.addRow("Vector backend", self.vector_backend_combo)

        self.chroma_persist_path_input = QLineEdit(rag_settings.chroma_persist_path, self)
        embedding_form.addRow("Chroma path", self.chroma_persist_path_input)

        self.chroma_collection_name_input = QLineEdit(rag_settings.chroma_collection_name, self)
        embedding_form.addRow("Collection base", self.chroma_collection_name_input)

        self.active_fingerprint_input = QLineEdit(rag_settings.active_embedding_fingerprint, self)
        embedding_form.addRow("Active fingerprint", self.active_fingerprint_input)

        self.embedding_model_path_input = QLineEdit(rag_settings.embedding_model_path, self)
        embedding_form.addRow("Local model path", self.embedding_model_path_input)

        self.embedding_backend_input = QLineEdit(rag_settings.embedding_backend, self)
        embedding_form.addRow("Local backend", self.embedding_backend_input)

        self.embedding_device_input = QLineEdit(rag_settings.embedding_device, self)
        embedding_form.addRow("Device", self.embedding_device_input)

        self.embedding_batch_size_spin = QSpinBox(self)
        self.embedding_batch_size_spin.setRange(1, 128)
        self.embedding_batch_size_spin.setValue(rag_settings.embedding_batch_size)
        embedding_form.addRow("Batch size", self.embedding_batch_size_spin)

        self.embedding_threads_spin = QSpinBox(self)
        self.embedding_threads_spin.setRange(1, 64)
        self.embedding_threads_spin.setValue(rag_settings.embedding_threads)
        embedding_form.addRow("Threads", self.embedding_threads_spin)

        self.reindex_enable_checkbox = QCheckBox("Enable reindex jobs", self)
        self.reindex_enable_checkbox.setChecked(rag_settings.reindex_enabled)
        embedding_form.addRow("", self.reindex_enable_checkbox)

        self.auto_switch_reindex_checkbox = QCheckBox("Auto switch collection after reindex (reserved)", self)
        self.auto_switch_reindex_checkbox.setChecked(rag_settings.auto_switch_collection_after_reindex)
        self.auto_switch_reindex_checkbox.setEnabled(False)
        self.auto_switch_reindex_checkbox.setToolTip("Lifecycle v2 requires explicit promotion. Reindex build completion will not auto-switch the active collection.")
        embedding_form.addRow("", self.auto_switch_reindex_checkbox)

        self.reindex_batch_size_spin = QSpinBox(self)
        self.reindex_batch_size_spin.setRange(1, 512)
        self.reindex_batch_size_spin.setValue(rag_settings.reindex_batch_size)
        embedding_form.addRow("Reindex batch size", self.reindex_batch_size_spin)

        embedding_secret_row = QWidget(self)
        embedding_secret_layout = QGridLayout(embedding_secret_row)
        embedding_secret_layout.setContentsMargins(0, 0, 0, 0)
        embedding_secret_layout.setHorizontalSpacing(8)
        embedding_secret_layout.setVerticalSpacing(8)
        self.embedding_api_key_input = QLineEdit(self)
        self.embedding_api_key_input.setEchoMode(QLineEdit.Password)
        self.embedding_api_key_input.setText(self.secret_store.get(rag_settings.embedding_api_key_ref))
        self.embedding_reveal_key_checkbox = QCheckBox("Reveal", self)
        self.embedding_update_key_button = QPushButton("Update", self)
        self.embedding_clear_key_button = QPushButton("Clear", self)
        self.embedding_key_status_label = QLabel("", self)
        self.embedding_key_status_label.setStyleSheet("color:#90A8C7; font-size:11px;")
        self.embedding_key_status_label.setText("Configured" if self.embedding_api_key_input.text().strip() else "Not configured")
        embedding_secret_layout.addWidget(self.embedding_api_key_input, 0, 0, 1, 3)
        embedding_secret_layout.addWidget(self.embedding_reveal_key_checkbox, 1, 0)
        embedding_secret_layout.addWidget(self.embedding_update_key_button, 1, 1)
        embedding_secret_layout.addWidget(self.embedding_clear_key_button, 1, 2)
        embedding_secret_layout.addWidget(self.embedding_key_status_label, 2, 0, 1, 3)
        embedding_form.addRow("API Key", embedding_secret_row)
        layout.addWidget(embedding_card)

        quality_card = QGroupBox("Knowledge Quality", self)
        quality_form = QFormLayout(quality_card)
        quality_form.setSpacing(10)

        self.importance_gating_checkbox = QCheckBox("Enable importance gating", self)
        self.importance_gating_checkbox.setChecked(rag_settings.importance_gating_enabled)
        quality_form.addRow("", self.importance_gating_checkbox)

        self.importance_memory_spin = QSpinBox(self)
        self.importance_memory_spin.setRange(1, 10)
        self.importance_memory_spin.setValue(rag_settings.importance_threshold_memory)
        quality_form.addRow("Memory threshold", self.importance_memory_spin)

        self.importance_summary_spin = QSpinBox(self)
        self.importance_summary_spin.setRange(1, 10)
        self.importance_summary_spin.setValue(rag_settings.importance_threshold_summary)
        quality_form.addRow("Summary threshold", self.importance_summary_spin)

        self.dedup_checkbox = QCheckBox("Enable dedup", self)
        self.dedup_checkbox.setChecked(rag_settings.dedup_enabled)
        quality_form.addRow("", self.dedup_checkbox)

        self.dedup_threshold_input = QLineEdit(f"{rag_settings.dedup_similarity_threshold:.2f}", self)
        quality_form.addRow("Dedup similarity", self.dedup_threshold_input)

        self.session_rollup_checkbox = QCheckBox("Enable session rollup", self)
        self.session_rollup_checkbox.setChecked(rag_settings.session_rollup_enabled)
        quality_form.addRow("", self.session_rollup_checkbox)

        self.session_rollup_turn_spin = QSpinBox(self)
        self.session_rollup_turn_spin.setRange(2, 20)
        self.session_rollup_turn_spin.setValue(rag_settings.session_rollup_turn_threshold)
        quality_form.addRow("Rollup turn threshold", self.session_rollup_turn_spin)

        self.max_session_summaries_spin = QSpinBox(self)
        self.max_session_summaries_spin.setRange(1, 6)
        self.max_session_summaries_spin.setValue(rag_settings.max_session_summaries_per_session)
        quality_form.addRow("Max summaries / session", self.max_session_summaries_spin)

        self.legacy_memory_mode_combo = QComboBox(self)
        self.legacy_memory_mode_combo.addItem("read_only", "read_only")
        self.legacy_memory_mode_combo.addItem("disabled", "disabled")
        self.legacy_memory_mode_combo.setCurrentIndex(max(0, self.legacy_memory_mode_combo.findData(rag_settings.legacy_memory_mode)))
        quality_form.addRow("Legacy memory", self.legacy_memory_mode_combo)

        note = QLabel(
            "Only summaries, decision cards, and imported document chunks enter the first RAG index. "
            "Legacy fairy_memory.db stays read-only by default.",
            group,
        )
        note.setStyleSheet("color:#8EA7C6; font-size:11px;")
        note.setWordWrap(True)
        quality_form.addRow("", note)
        layout.addWidget(quality_card)
        return group

    def _build_game_mode_section(self, game_settings: GameModeSettings) -> QWidget:
        group = QGroupBox("游戏模式", self)
        layout = QVBoxLayout(group)
        group.setTitle("Game Mode")
        layout.setSpacing(12)

        self.game_enable_checkbox = QCheckBox("Enable Game Mode", self)
        self.game_enable_checkbox.setChecked(game_settings.enabled)
        self.game_enable_checkbox.setText("Enable Game Mode")
        layout.addWidget(self.game_enable_checkbox)

        trigger_row = QHBoxLayout()
        trigger_row.addWidget(QLabel("触发方式", self))
        self.trigger_combo = QComboBox(self)
        self.trigger_combo.addItem("Manual only", "manual")
        self.trigger_combo.addItem("Auto switch when game detected", "auto")
        self.trigger_combo.setCurrentIndex(1 if game_settings.auto_switch_when_game_detected else 0)
        trigger_row.addWidget(self.trigger_combo, 1)
        layout.addLayout(trigger_row)

        self.global_cloud_checkbox = QCheckBox(tr("cloud_routing_enable", self._language), self)
        self.global_cloud_checkbox.setChecked(game_settings.global_enabled)
        layout.addWidget(self.global_cloud_checkbox)

        self.global_cloud_note = QLabel(tr("cloud_routing_note", self._language), self)
        self.global_cloud_note.setStyleSheet("color:#8EA7C6; font-size:11px;")
        self.global_cloud_note.setWordWrap(True)
        layout.addWidget(self.global_cloud_note)

        provider_card = QGroupBox("Cloud Provider", self)
        provider_form = QFormLayout(provider_card)
        provider_form.setSpacing(10)

        self.provider_combo = QComboBox(self)
        for provider_id, label in PROVIDER_LABELS.items():
            self.provider_combo.addItem(label, provider_id)
        self.provider_combo.setCurrentIndex(max(0, self.provider_combo.findData(game_settings.selected_provider_id)))
        for index, label in enumerate(("Doubao Seed 2.0", "Qwen / DashScope", "OpenAI-compatible Custom")):
            if index < self.provider_combo.count():
                self.provider_combo.setItemText(index, label)
        provider_form.addRow("Provider", self.provider_combo)

        self.base_url_input = QLineEdit(self)
        provider_form.addRow("Base URL", self.base_url_input)

        self.model_input = QLineEdit(self)
        provider_form.addRow("Model", self.model_input)

        self.endpoint_style_combo = QComboBox(self)
        self.endpoint_style_combo.addItem("responses", "responses")
        self.endpoint_style_combo.addItem("chat.completions", "chat_completions")
        provider_form.addRow("Endpoint Style", self.endpoint_style_combo)

        self.stream_checkbox = QCheckBox("流式输出", self)
        provider_form.addRow("", self.stream_checkbox)

        self.timeout_spin = QSpinBox(self)
        self.timeout_spin.setRange(5, 600)
        provider_form.addRow("Timeout (s)", self.timeout_spin)

        self.retry_spin = QSpinBox(self)
        self.retry_spin.setRange(0, 6)
        provider_form.addRow("Max retries", self.retry_spin)

        secret_row = QWidget(self)
        secret_layout = QGridLayout(secret_row)
        secret_layout.setContentsMargins(0, 0, 0, 0)
        secret_layout.setHorizontalSpacing(8)
        secret_layout.setVerticalSpacing(8)
        self.api_key_input = QLineEdit(self)
        self.api_key_input.setEchoMode(QLineEdit.Password)
        self.reveal_key_checkbox = QCheckBox("Reveal", self)
        self.update_key_button = QPushButton("Update", self)
        self.clear_key_button = QPushButton("Clear", self)
        self.key_status_label = QLabel("", self)
        self.key_status_label.setStyleSheet("color:#90A8C7; font-size:11px;")
        secret_layout.addWidget(self.api_key_input, 0, 0, 1, 3)
        secret_layout.addWidget(self.reveal_key_checkbox, 1, 0)
        secret_layout.addWidget(self.update_key_button, 1, 1)
        secret_layout.addWidget(self.clear_key_button, 1, 2)
        secret_layout.addWidget(self.key_status_label, 2, 0, 1, 3)
        provider_form.addRow("API Key", secret_row)

        self.test_connection_button = QPushButton("Test Connection", self)
        self.test_result_label = QLabel("", self)
        self.test_result_label.setWordWrap(True)
        self.test_result_label.setStyleSheet("color:#9CB7DB; font-size:11px;")
        provider_form.addRow(self.test_connection_button, self.test_result_label)
        layout.addWidget(provider_card)

        advanced_row = QHBoxLayout()
        advanced_row.setSpacing(12)
        advanced_row.addWidget(self._build_fallback_section(game_settings), 1)
        advanced_row.addWidget(self._build_policy_section(game_settings), 1)
        layout.addLayout(advanced_row)
        return group

    def _build_fallback_section(self, game_settings: GameModeSettings) -> QWidget:
        group = QGroupBox("Fallback", self)
        form = QFormLayout(group)
        form.setSpacing(9)
        self.enable_fallback_checkbox = QCheckBox("Enable local fallback", self)
        self.enable_fallback_checkbox.setChecked(game_settings.fallback.enabled)
        form.addRow("", self.enable_fallback_checkbox)
        self.on_timeout_checkbox = QCheckBox("On timeout", self)
        self.on_timeout_checkbox.setChecked(game_settings.fallback.on_timeout)
        form.addRow("", self.on_timeout_checkbox)
        self.on_auth_checkbox = QCheckBox("On auth error", self)
        self.on_auth_checkbox.setChecked(game_settings.fallback.on_auth_error)
        form.addRow("", self.on_auth_checkbox)
        self.on_network_checkbox = QCheckBox("On network error", self)
        self.on_network_checkbox.setChecked(game_settings.fallback.on_network_error)
        form.addRow("", self.on_network_checkbox)
        self.on_provider_checkbox = QCheckBox("On provider error", self)
        self.on_provider_checkbox.setChecked(game_settings.fallback.on_provider_error)
        form.addRow("", self.on_provider_checkbox)
        self.fallback_model_input = QLineEdit(game_settings.fallback.local_model, self)
        form.addRow("Fallback local model", self.fallback_model_input)
        return group

    def _build_policy_section(self, game_settings: GameModeSettings) -> QWidget:
        group = QGroupBox("Game Mode Policy", self)
        form = QFormLayout(group)
        form.setSpacing(9)
        self.persona_intensity_combo = QComboBox(self)
        for value in ("low", "normal", "high"):
            self.persona_intensity_combo.addItem(value, value)
        self.persona_intensity_combo.setCurrentIndex(max(0, self.persona_intensity_combo.findData(game_settings.persona_intensity)))
        form.addRow("Persona intensity", self.persona_intensity_combo)

        self.brevity_combo = QComboBox(self)
        for value in ("ultra_short", "short", "balanced"):
            self.brevity_combo.addItem(value, value)
        self.brevity_combo.setCurrentIndex(max(0, self.brevity_combo.findData(game_settings.brevity_level)))
        form.addRow("Brevity level", self.brevity_combo)

        self.verdict_combo = QComboBox(self)
        for value in ("soft", "strong", "hard"):
            self.verdict_combo.addItem(value, value)
        self.verdict_combo.setCurrentIndex(max(0, self.verdict_combo.findData(game_settings.verdict_strength)))
        form.addRow("Verdict strength", self.verdict_combo)

        self.interruption_combo = QComboBox(self)
        for value in ("low_interrupt", "balanced", "assistive"):
            self.interruption_combo.addItem(value, value)
        self.interruption_combo.setCurrentIndex(max(0, self.interruption_combo.findData(game_settings.interruption_policy)))
        form.addRow("Interruption policy", self.interruption_combo)

        self.overlay_input = QPlainTextEdit(self)
        self.overlay_input.setPlaceholderText("可选：为游戏模式追加简短的 system prompt overlay")
        self.overlay_input.setPlainText(game_settings.system_prompt_overlay)
        self.overlay_input.setFixedHeight(92)
        form.addRow("Prompt overlay", self.overlay_input)
        return group

    def _sync_voice_options(self, enabled: bool) -> None:
        self.stream_voice_checkbox.setEnabled(enabled)
        if not enabled:
            self.stream_voice_checkbox.setChecked(False)

    def _sync_persona_options(self, enabled: bool) -> None:
        self.persona_mode_combo.setEnabled(enabled)
        self.persona_game_force_disable_checkbox.setEnabled(enabled)
        if not enabled:
            self.persona_mode_combo.setCurrentIndex(0)

    def _selected_provider_id(self) -> str:
        return str(self.provider_combo.currentData())

    def _provider_from_settings(self) -> ProviderConfig:
        provider_id = self._selected_provider_id()
        return self._providers_state.get(provider_id, self.registry.get_preset(provider_id))

    def _load_selected_provider_into_form(self) -> None:
        if self._loaded_provider_id:
            self._persist_current_provider_form()
        provider = self._provider_from_settings()
        self._loaded_provider_id = provider.provider_id
        self._current_secret_ref = provider.api_key_ref
        self.base_url_input.setText(provider.base_url)
        self.model_input.setText(provider.model)
        self.endpoint_style_combo.setCurrentIndex(max(0, self.endpoint_style_combo.findData(provider.api_style)))
        self.stream_checkbox.setChecked(provider.stream)
        self.timeout_spin.setValue(provider.timeout_seconds)
        self.retry_spin.setValue(provider.max_retries)
        secret_value = self.secret_store.get(provider.api_key_ref)
        self.api_key_input.setText(secret_value)
        self.api_key_input.setEchoMode(QLineEdit.Normal if self.reveal_key_checkbox.isChecked() else QLineEdit.Password)
        self.key_status_label.setText("已配置" if secret_value else "未配置")
        self.endpoint_style_combo.setEnabled(provider.provider_id == "openai_compatible_custom")

    def _persist_current_provider_form(self) -> None:
        if not self._loaded_provider_id:
            return
        provider = self.registry.ensure_runtime_config({}, self._loaded_provider_id)
        provider.base_url = self.base_url_input.text().strip()
        provider.model = self.model_input.text().strip()
        provider.api_style = str(self.endpoint_style_combo.currentData())
        provider.stream = self.stream_checkbox.isChecked()
        provider.timeout_seconds = self.timeout_spin.value()
        provider.max_retries = self.retry_spin.value()
        self._providers_state[self._loaded_provider_id] = provider

    def _toggle_secret_visibility(self, checked: bool) -> None:
        self.api_key_input.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)

    def _mark_secret_updated(self) -> None:
        if not self._current_secret_ref:
            return
        self._secret_updates[self._current_secret_ref] = self.api_key_input.text().strip()
        if self._current_secret_ref in self._cleared_secret_refs:
            self._cleared_secret_refs.remove(self._current_secret_ref)
        self.key_status_label.setText("待保存更新")

    def _clear_secret(self) -> None:
        if not self._current_secret_ref:
            return
        self.api_key_input.clear()
        if self._current_secret_ref not in self._cleared_secret_refs:
            self._cleared_secret_refs.append(self._current_secret_ref)
        self._secret_updates.pop(self._current_secret_ref, None)
        self.key_status_label.setText("待清除")

    def _toggle_embedding_secret_visibility(self, checked: bool) -> None:
        self.embedding_api_key_input.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)

    def _mark_embedding_secret_updated(self) -> None:
        if not self._embedding_secret_ref:
            return
        self._secret_updates[self._embedding_secret_ref] = self.embedding_api_key_input.text().strip()
        if self._embedding_secret_ref in self._cleared_secret_refs:
            self._cleared_secret_refs.remove(self._embedding_secret_ref)
        self.embedding_key_status_label.setText("Pending update")

    def _clear_embedding_secret(self) -> None:
        if not self._embedding_secret_ref:
            return
        self.embedding_api_key_input.clear()
        if self._embedding_secret_ref not in self._cleared_secret_refs:
            self._cleared_secret_refs.append(self._embedding_secret_ref)
        self._secret_updates.pop(self._embedding_secret_ref, None)
        self.embedding_key_status_label.setText("Pending clear")

    def _build_provider_from_form(self) -> ProviderConfig:
        self._persist_current_provider_form()
        return self._providers_state.get(self._selected_provider_id(), self.registry.get_preset(self._selected_provider_id()))

    def _build_secret_value(self, provider: ProviderConfig) -> str:
        if provider.api_key_ref in self._cleared_secret_refs:
            return ""
        if provider.api_key_ref in self._secret_updates:
            return self._secret_updates[provider.api_key_ref]
        return self.api_key_input.text().strip()

    def _float_or_default(self, value: str, default: float) -> float:
        try:
            return float(value.strip())
        except Exception:
            return default

    def _test_connection(self) -> None:
        provider = self._build_provider_from_form()
        api_key = self._build_secret_value(provider)
        if not api_key:
            self.test_result_label.setText("未连接 · API Key 未配置")
            return
        client = DoubaoClient() if provider.provider_id == "doubao_seed2" else OpenAICompatibleClient()
        try:
            ok, detail = client.test_connection(provider, api_key)
        except ProviderRequestError as exc:
            ok, detail = False, str(exc)
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, str(exc)
        if not ok and ("header value" in detail.lower() or "非法空白字符" in detail):
            detail = "API Key 含有换行或空白字符。请重新粘贴完整 key，然后点击 Update。"
        if ok:
            model_name = detail[:120] if detail else provider.model
            self.test_result_label.setText(f"Connected · {model_name}")
            self.test_result_label.setStyleSheet("color:#8FD0B2; font-size:11px;")
        else:
            self.test_result_label.setText("Failed · " + detail[:180])
            self.test_result_label.setStyleSheet("color:#E3A6A6; font-size:11px;")

    def result_payload(self) -> AppSettingsResult:
        provider = self._build_provider_from_form()
        game_settings = GameModeSettings(
            enabled=self.game_enable_checkbox.isChecked(),
            auto_switch_when_game_detected=self.trigger_combo.currentData() == "auto",
            global_enabled=self.global_cloud_checkbox.isChecked(),
            selected_provider_id=provider.provider_id,
            providers=dict(self._providers_state),
            fallback=self.game_settings.fallback.__class__(
                enabled=self.enable_fallback_checkbox.isChecked(),
                on_timeout=self.on_timeout_checkbox.isChecked(),
                on_auth_error=self.on_auth_checkbox.isChecked(),
                on_network_error=self.on_network_checkbox.isChecked(),
                on_provider_error=self.on_provider_checkbox.isChecked(),
                local_provider=self.game_settings.fallback.local_provider,
                local_model=self.fallback_model_input.text().strip() or self.game_settings.fallback.local_model,
            ),
            system_prompt_overlay=self.overlay_input.toPlainText().strip(),
            persona_intensity=str(self.persona_intensity_combo.currentData()),
            brevity_level=str(self.brevity_combo.currentData()),
            verdict_strength=str(self.verdict_combo.currentData()),
            interruption_policy=str(self.interruption_combo.currentData()),
            auto_switch_debounce_seconds=self.game_settings.auto_switch_debounce_seconds,
            known_game_processes=list(self.game_settings.known_game_processes),
        )
        if provider.api_key_ref and provider.api_key_ref not in self._cleared_secret_refs:
            current_value = self.api_key_input.text().strip()
            if current_value:
                self._secret_updates[provider.api_key_ref] = current_value
        if self._embedding_secret_ref and self._embedding_secret_ref not in self._cleared_secret_refs:
            embedding_value = self.embedding_api_key_input.text().strip()
            if embedding_value:
                self._secret_updates[self._embedding_secret_ref] = embedding_value
        return AppSettingsResult(
            mode=str(self.mode_combo.currentData()),
            preferences=AppPreferences(
                ui_language=normalize_ui_language(str(self.language_combo.currentData() or self.preferences.ui_language)),
                speak_responses=self.reply_voice_checkbox.isChecked(),
                stream_responses=self.stream_voice_checkbox.isChecked(),
                persona_enabled=self.persona_enable_checkbox.isChecked(),
                persona_mode=str(self.persona_mode_combo.currentData()),
                game_mode_force_disable_persona=self.persona_game_force_disable_checkbox.isChecked(),
                presence_quiet_mode=self.preferences.presence_quiet_mode,
                presence_position_x=self.preferences.presence_position_x,
                presence_position_y=self.preferences.presence_position_y,
                presence_avatar_size=self.preferences.presence_avatar_size,
            ),
            rag_settings=RAGSettings(
                rag_enabled=self.rag_enable_checkbox.isChecked(),
                rag_top_k=self.rag_top_k_spin.value(),
                rag_max_context_chars=self.rag_max_chars_spin.value(),
                rag_use_for_history_queries=self.rag_history_checkbox.isChecked(),
                rag_use_for_document_qa=self.rag_document_checkbox.isChecked(),
                enable_retrieval_debug_panel=self.retrieval_debug_checkbox.isChecked(),
                embedding_enabled=self.embedding_enable_checkbox.isChecked(),
                embedding_provider=str(self.embedding_provider_combo.currentData()),
                embedding_model=self.embedding_model_input.text().strip() or "text-embedding-v4",
                embedding_base_url=self.embedding_base_url_input.text().strip() or "https://dashscope.aliyuncs.com/compatible-mode/v1",
                embedding_api_key_ref=self._embedding_secret_ref or "DASHSCOPE_API_KEY",
                embedding_timeout_seconds=self.embedding_timeout_spin.value(),
                embedding_model_path=self.embedding_model_path_input.text().strip(),
                embedding_backend=self.embedding_backend_input.text().strip() or "sentence_transformers",
                embedding_device=self.embedding_device_input.text().strip() or "cpu",
                embedding_batch_size=self.embedding_batch_size_spin.value(),
                embedding_threads=self.embedding_threads_spin.value(),
                active_embedding_fingerprint=self.active_fingerprint_input.text().strip(),
                auto_switch_collection_after_reindex=self.auto_switch_reindex_checkbox.isChecked(),
                reindex_batch_size=self.reindex_batch_size_spin.value(),
                reindex_enabled=self.reindex_enable_checkbox.isChecked(),
                vector_backend=str(self.vector_backend_combo.currentData()),
                chroma_persist_path=self.chroma_persist_path_input.text().strip(),
                chroma_collection_name=self.chroma_collection_name_input.text().strip() or "fairy_knowledge",
                importance_gating_enabled=self.importance_gating_checkbox.isChecked(),
                importance_threshold_memory=self.importance_memory_spin.value(),
                importance_threshold_summary=self.importance_summary_spin.value(),
                dedup_enabled=self.dedup_checkbox.isChecked(),
                dedup_similarity_threshold=self._float_or_default(self.dedup_threshold_input.text(), 0.92),
                session_rollup_enabled=self.session_rollup_checkbox.isChecked(),
                session_rollup_turn_threshold=self.session_rollup_turn_spin.value(),
                max_session_summaries_per_session=self.max_session_summaries_spin.value(),
                legacy_memory_mode=str(self.legacy_memory_mode_combo.currentData()),
            ),
            game_mode_settings=game_settings,
            secret_updates=dict(self._secret_updates),
            cleared_secret_refs=list(self._cleared_secret_refs),
        )


def choose_app_settings(parent, current_mode: str, preferences: AppPreferences) -> AppSettingsResult | None:
    dialog = AppModeDialog(
        current_mode=current_mode,
        preferences=preferences,
        rag_settings=load_rag_settings(),
        game_settings=load_game_mode_settings(),
        parent=parent,
    )
    if dialog.exec() != QDialog.Accepted:
        return None
    return dialog.result_payload()
