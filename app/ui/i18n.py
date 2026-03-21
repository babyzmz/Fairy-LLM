from __future__ import annotations

from app.app_preferences import load_app_preferences


LANG_ZH = "zh_CN"
LANG_EN = "en_US"


_TEXTS = {
    "app_subtitle": {
        LANG_ZH: "Ⅲ型总序式集成泛用人工智能",
        LANG_EN: "New El Dorado's strongest intelligent assistant, Fairy's wish fairy",
    },
    "nav_footer": {
        LANG_ZH: "我甚至需要您不断工作，赚钱养我。",
        LANG_EN: "I even need you to work hard, earn money for me.",
    },
    "system": {
        LANG_ZH: "系统",
        LANG_EN: "System",
    },
    "context": {
        LANG_ZH: "上下文",
        LANG_EN: "Context",
    },
    "settings": {
        LANG_ZH: "设置",
        LANG_EN: "Settings",
    },
    "exit": {
        LANG_ZH: "退出",
        LANG_EN: "Exit",
    },
    "page_chat_title": {
        LANG_ZH: "对话",
        LANG_EN: "Chat",
    },
    "page_chat_subtitle": {
        LANG_ZH: "日常互动留在这里，界面应当有陪伴感而不是控制台感。",
        LANG_EN: "Daily interaction stays here. The system should feel alive, not noisy.",
    },
    "page_knowledge_title": {
        LANG_ZH: "知识",
        LANG_EN: "Knowledge",
    },
    "page_knowledge_subtitle": {
        LANG_ZH: "结构化记忆、摘要和稳定知识沉淀在这里。",
        LANG_EN: "Structured memory, summaries, and durable system knowledge.",
    },
    "page_decisions_title": {
        LANG_ZH: "决策",
        LANG_EN: "Decisions",
    },
    "page_decisions_subtitle": {
        LANG_ZH: "需要明确确认的规则和架构判断放在这里。",
        LANG_EN: "Rules and architecture calls that deserve explicit confirmation.",
    },
    "page_jobs_title": {
        LANG_ZH: "任务",
        LANG_EN: "Jobs",
    },
    "page_jobs_subtitle": {
        LANG_ZH: "重建索引与后台任务应当可见，但不需要喧闹。",
        LANG_EN: "Reindex and background work. Quietly dangerous when invisible.",
    },
    "page_debug_title": {
        LANG_ZH: "调试",
        LANG_EN: "Debug",
    },
    "page_debug_subtitle": {
        LANG_ZH: "检索、动作流和模型实际看到的内容在这里。",
        LANG_EN: "Retrieval, action flow, and the parts the model actually saw.",
    },
    "page_settings_title": {
        LANG_ZH: "设置",
        LANG_EN: "Settings",
    },
    "page_settings_subtitle": {
        LANG_ZH: "配置集中在这里，不要让设置散落到日常界面。",
        LANG_EN: "Configuration belongs here. Random config sprawl does not.",
    },
    "chat_page": {
        LANG_ZH: "对话",
        LANG_EN: "Chat",
    },
    "knowledge_page": {
        LANG_ZH: "知识",
        LANG_EN: "Knowledge",
    },
    "decisions_page": {
        LANG_ZH: "决策",
        LANG_EN: "Decisions",
    },
    "jobs_page": {
        LANG_ZH: "任务",
        LANG_EN: "Jobs",
    },
    "debug_page": {
        LANG_ZH: "调试",
        LANG_EN: "Debug",
    },
    "settings_page": {
        LANG_ZH: "设置",
        LANG_EN: "Settings",
    },
    "chat_session_title": {
        LANG_ZH: "当前会话",
        LANG_EN: "Current Session",
    },
    "chat_default_summary": {
        LANG_ZH: "Fairy 已在线，当前会话稳定。",
        LANG_EN: "Fairy is online. The current session is stable.",
    },
    "chat_online": {
        LANG_ZH: "Fairy 已在线。",
        LANG_EN: "Fairy is online.",
    },
    "chat_task_prefix": {
        LANG_ZH: "任务",
        LANG_EN: "Task",
    },
    "chat_waiting": {
        LANG_ZH: "待命",
        LANG_EN: "waiting",
    },
    "chat_attach": {
        LANG_ZH: "附件",
        LANG_EN: "Attach",
    },
    "chat_clear": {
        LANG_ZH: "清空",
        LANG_EN: "Clear",
    },
    "chat_send": {
        LANG_ZH: "发送",
        LANG_EN: "Send",
    },
    "chat_input_placeholder": {
        LANG_ZH: "和 Fairy 说点什么，或继续当前任务。",
        LANG_EN: "Talk to Fairy or continue the current task.",
    },
    "chat_attached_prefix": {
        LANG_ZH: "已附加",
        LANG_EN: "Attached",
    },
    "chat_uploaded_attachments": {
        LANG_ZH: "[上传附件] {names}",
        LANG_EN: "[Attachments] {names}",
    },
    "chat_select_files": {
        LANG_ZH: "为 Fairy 选择文件",
        LANG_EN: "Select files for Fairy",
    },
    "chat_user_speaker": {
        LANG_ZH: "你",
        LANG_EN: "You",
    },
    "settings_title": {
        LANG_ZH: "系统设置",
        LANG_EN: "System Settings",
    },
    "settings_subtitle": {
        LANG_ZH: "配置集中在这里，避免打扰日常对话界面。",
        LANG_EN: "Configuration stays grouped here, away from the daily conversation surface.",
    },
    "settings_open_full": {
        LANG_ZH: "打开完整设置",
        LANG_EN: "Open Full Settings",
    },
    "settings_current_runtime": {
        LANG_ZH: "当前运行态",
        LANG_EN: "Current Runtime",
    },
    "settings_mode_label": {
        LANG_ZH: "模式",
        LANG_EN: "mode",
    },
    "settings_provider_label": {
        LANG_ZH: "提供方",
        LANG_EN: "provider",
    },
    "settings_model_label": {
        LANG_ZH: "模型",
        LANG_EN: "model",
    },
    "settings_backend_label": {
        LANG_ZH: "向量后端",
        LANG_EN: "vector backend",
    },
    "settings_fingerprint_label": {
        LANG_ZH: "当前指纹",
        LANG_EN: "active fingerprint",
    },
    "settings_rag_label": {
        LANG_ZH: "RAG 已启用",
        LANG_EN: "rag enabled",
    },
    "settings_persona_label": {
        LANG_ZH: "Persona 模式",
        LANG_EN: "persona mode",
    },
    "settings_runtime_hint": {
        LANG_ZH: "若需修改 provider、embedding、reindex、mode 或 persona，请打开完整设置窗口。",
        LANG_EN: "Open the full settings dialog for provider, embedding, reindex, mode, and persona controls.",
    },
    "presence_ignore": {
        LANG_ZH: "忽略提醒",
        LANG_EN: "Ignore reminder",
    },
    "presence_ignore_many": {
        LANG_ZH: "忽略 {count} 条",
        LANG_EN: "Ignore {count}",
    },
    "presence_ignore_tooltip": {
        LANG_ZH: "忽略这些可以安全跳过的提醒。",
        LANG_EN: "Dismiss the reminders that can be safely ignored.",
    },
    "presence_open_console": {
        LANG_ZH: "打开主窗口",
        LANG_EN: "Open Console",
    },
    "presence_open_jobs": {
        LANG_ZH: "打开任务页",
        LANG_EN: "Open Jobs",
    },
    "presence_open_decisions": {
        LANG_ZH: "打开决策页",
        LANG_EN: "Open Decisions",
    },
    "presence_quiet_mode": {
        LANG_ZH: "静默模式",
        LANG_EN: "Quiet Mode",
    },
    "presence_close_reply": {
        LANG_ZH: "关闭回复",
        LANG_EN: "Close reply",
    },
    "presence_attach": {
        LANG_ZH: "添加附件",
        LANG_EN: "Attach files",
    },
    "presence_voice_placeholder": {
        LANG_ZH: "语音输入为后续版本预留。",
        LANG_EN: "Voice input is reserved for a later version.",
    },
    "presence_input_placeholder": {
        LANG_ZH: "给 Fairy 发一句话...",
        LANG_EN: "Ask Fairy anything...",
    },
    "presence_select_attachments": {
        LANG_ZH: "选择附件",
        LANG_EN: "Select attachments",
    },
    "presence_attached_prefix": {
        LANG_ZH: "已附加：",
        LANG_EN: "Attached: ",
    },
    "presence_reply_title": {
        LANG_ZH: "Fairy",
        LANG_EN: "Fairy",
    },
    "presence_tooltip_idle_title": {
        LANG_ZH: "Fairy 正在待机",
        LANG_EN: "Fairy is resting",
    },
    "presence_tooltip_idle_summary": {
        LANG_ZH: "保持轻量常驻，等待下一步。",
        LANG_EN: "Stay light. Stay nearby.",
    },
    "presence_tooltip_sleep_title": {
        LANG_ZH: "Fairy 正在休眠",
        LANG_EN: "Fairy is sleeping",
    },
    "presence_tooltip_sleep_summary": {
        LANG_ZH: "当前没有新的动作，系统处于低功耗待机。",
        LANG_EN: "No new activity right now. The system is idling in low power.",
    },
    "presence_tooltip_thinking_title": {
        LANG_ZH: "Fairy 正在思考",
        LANG_EN: "Fairy is thinking",
    },
    "presence_tooltip_thinking_summary": {
        LANG_ZH: "正在处理你刚才的请求。",
        LANG_EN: "Working through your latest request.",
    },
    "presence_tooltip_critical_title": {
        LANG_ZH: "Fairy 检测到关键问题",
        LANG_EN: "Fairy found a critical issue",
    },
    "presence_tooltip_critical_summary": {
        LANG_ZH: "有关键系统事项待处理。",
        LANG_EN: "There is a critical system item waiting.",
    },
    "presence_tooltip_action_title": {
        LANG_ZH: "Fairy 需要处理事项",
        LANG_EN: "Fairy needs attention",
    },
    "presence_tooltip_action_summary": {
        LANG_ZH: "有动作需要你确认。",
        LANG_EN: "Something needs your confirmation.",
    },
    "presence_tooltip_busy_title": {
        LANG_ZH: "Fairy 正在忙碌",
        LANG_EN: "Fairy is busy",
    },
    "presence_tooltip_busy_summary": {
        LANG_ZH: "{count} 个后台任务正在运行。",
        LANG_EN: "{count} background jobs are running.",
    },
    "presence_tooltip_reply_title": {
        LANG_ZH: "Fairy 有新的回复",
        LANG_EN: "Fairy has a new reply",
    },
    "presence_tooltip_reply_summary": {
        LANG_ZH: "结果已返回。双击头像可打开主窗口查看。",
        LANG_EN: "The result is ready. Double-click the avatar to open the console.",
    },
    "presence_tooltip_notify_title": {
        LANG_ZH: "Fairy 有待处理事项",
        LANG_EN: "Fairy has something pending",
    },
    "presence_tooltip_notify_summary": {
        LANG_ZH: "系统里有一些值得你看一眼的事项。",
        LANG_EN: "There are a few items worth checking.",
    },
    "presence_quiet_summary": {
        LANG_ZH: "静默模式已开启，提醒保持低干扰。",
        LANG_EN: "Quiet Mode is on, so reminders stay low-friction.",
    },
    "presence_notifications_ignored": {
        LANG_ZH: "当前提醒已忽略。",
        LANG_EN: "The current reminders were ignored.",
    },
    "presence_request_cancelled": {
        LANG_ZH: "请求已取消。",
        LANG_EN: "The request was cancelled.",
    },
    "presence_request_cancelled_summary": {
        LANG_ZH: "当前请求已取消。",
        LANG_EN: "The current request was cancelled.",
    },
    "presence_request_failed_summary": {
        LANG_ZH: "最近一次请求失败，主窗口里有完整上下文。",
        LANG_EN: "The latest request failed. The console has the full context.",
    },
    "presence_request_complete_summary": {
        LANG_ZH: "上一轮请求已完成。",
        LANG_EN: "The last request is complete.",
    },
    "language_label": {
        LANG_ZH: "界面语言",
        LANG_EN: "Interface Language",
    },
    "language_zh": {
        LANG_ZH: "中文",
        LANG_EN: "Chinese",
    },
    "language_en": {
        LANG_ZH: "英文",
        LANG_EN: "English",
    },
    "cloud_routing_title": {
        LANG_ZH: "云端模型",
        LANG_EN: "Cloud Routing",
    },
    "cloud_routing_enable": {
        LANG_ZH: "在非游戏模式下也使用所选云端模型",
        LANG_EN: "Use the selected cloud model globally outside Game Mode",
    },
    "cloud_routing_note": {
        LANG_ZH: "这里配置的是全局云端路由；游戏模式激活时仍会沿用同一套云端 provider 配置。",
        LANG_EN: "This config is used for global cloud routing. Game Mode will reuse the same provider profile when active.",
    },
    "footer_idle": {
        LANG_ZH: "Fairy 很安静，空间是稳定的。",
        LANG_EN: "Fairy is calm. The room is quiet.",
    },
    "status_pending": {
        LANG_ZH: "{count} 待处理",
        LANG_EN: "{count} pending",
    },
    "status_jobs": {
        LANG_ZH: "{count} 任务",
        LANG_EN: "{count} jobs",
    },
    "status_active": {
        LANG_ZH: "{count} 活跃",
        LANG_EN: "{count} active",
    },
    "status_action": {
        LANG_ZH: "{count} 待操作",
        LANG_EN: "{count} action",
    },
    "status_critical": {
        LANG_ZH: "{count} 严重",
        LANG_EN: "{count} critical",
    },
    "meta_provider": {
        LANG_ZH: "提供方",
        LANG_EN: "Provider",
    },
    "meta_backend": {
        LANG_ZH: "后端",
        LANG_EN: "Backend",
    },
    "meta_fingerprint": {
        LANG_ZH: "指纹",
        LANG_EN: "Fingerprint",
    },
}


_MODE_LABELS = {
    "NORMAL": {
        LANG_ZH: "普通模式",
        LANG_EN: "NORMAL",
    },
    "GAME AUTO": {
        LANG_ZH: "游戏自动",
        LANG_EN: "GAME AUTO",
    },
    "AUTO WAIT": {
        LANG_ZH: "等待切换",
        LANG_EN: "AUTO WAIT",
    },
    "GAME MODE": {
        LANG_ZH: "游戏模式",
        LANG_EN: "GAME MODE",
    },
}


_ROUTE_LABELS = {
    "local": {
        LANG_ZH: "本地",
        LANG_EN: "local",
    },
    "cloud": {
        LANG_ZH: "云端",
        LANG_EN: "cloud",
    },
    "cloud_vision": {
        LANG_ZH: "云端视觉",
        LANG_EN: "cloud vision",
    },
    "local_fallback": {
        LANG_ZH: "本地回退",
        LANG_EN: "local fallback",
    },
    "local_fallback_vision": {
        LANG_ZH: "本地视觉回退",
        LANG_EN: "local vision fallback",
    },
    "cloud_error": {
        LANG_ZH: "云端错误",
        LANG_EN: "cloud error",
    },
}


def normalize_ui_language(value: str | None) -> str:
    text = str(value or "").strip().lower()
    if text in {"en", "en_us", "en-us", "english"}:
        return LANG_EN
    return LANG_ZH


def current_ui_language() -> str:
    return normalize_ui_language(load_app_preferences().ui_language)


def tr(key: str, language: str | None = None, **kwargs) -> str:
    lang = normalize_ui_language(language or current_ui_language())
    table = _TEXTS.get(key, {})
    text = table.get(lang) or table.get(LANG_ZH) or key
    if kwargs:
        return text.format(**kwargs)
    return text


def localize_mode_label(value: str, language: str | None = None) -> str:
    lang = normalize_ui_language(language or current_ui_language())
    key = str(value or "").strip().upper()
    table = _MODE_LABELS.get(key)
    if table:
        return table.get(lang) or table.get(LANG_ZH) or value
    return value


def localize_route_label(value: str, language: str | None = None) -> str:
    lang = normalize_ui_language(language or current_ui_language())
    key = str(value or "").strip().lower()
    table = _ROUTE_LABELS.get(key)
    if table:
        return table.get(lang) or table.get(LANG_ZH) or value
    return value
