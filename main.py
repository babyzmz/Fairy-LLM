from __future__ import annotations

# LEGACY_ENTRYPOINT
# Qt shell is deprecated. The default desktop path is the Tauri shell:
# Tauri -> FastAPI -> FairyRuntimeV2 -> structured response -> React renderer.

import logging
import sys
from pathlib import Path

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication


def _inject_vendor_site_packages() -> None:
    vendor = Path(__file__).resolve().parent / ".vendor" / "site-packages"
    vendor_path = str(vendor)
    if vendor.exists() and vendor_path not in sys.path:
        sys.path.insert(0, vendor_path)


_inject_vendor_site_packages()

from app.app_preferences import load_and_apply_app_preferences
from app.assistant_mode import AssistantModeController
from app.logging_utils import configure_logging
from app.ui.training_data_workspace import TrainingDataWorkspace


logger = logging.getLogger(__name__)


class AppModeRouter(QObject):
    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self.app = app
        self.training_window: TrainingDataWorkspace | None = None
        self.assistant_controller: AssistantModeController | None = None

    def start(self, mode: str = "assistant") -> None:
        self.switch_mode(mode)

    def switch_mode(self, mode: str) -> None:
        target = "assistant" if mode == "assistant" else "training"
        logger.info("Switching application mode -> %s", target)
        if target == "assistant":
            self._show_assistant()
        else:
            self._show_training()

    def shutdown(self) -> None:
        if self.training_window is not None:
            window = self.training_window
            self.training_window = None
            window.on_close_requested = None
            window.close_for_mode_switch()
        if self.assistant_controller is not None:
            controller = self.assistant_controller
            self.assistant_controller = None
            controller.on_quit_requested = None
            controller.close_for_mode_switch()

    def _show_training(self) -> None:
        new_window = TrainingDataWorkspace()
        new_window.on_mode_switch_requested = self.switch_mode
        new_window.on_close_requested = self.app.quit
        new_window.show()

        old_training = self.training_window
        old_assistant = self.assistant_controller
        self.training_window = new_window
        self.assistant_controller = None

        if old_training is not None:
            old_training.on_close_requested = None
            old_training.close_for_mode_switch()
        if old_assistant is not None:
            old_assistant.on_quit_requested = None
            old_assistant.close_for_mode_switch()

    def _show_assistant(self) -> None:
        controller = AssistantModeController()
        controller.on_mode_switch_requested = self.switch_mode
        controller.on_quit_requested = self.app.quit
        controller.run()

        old_training = self.training_window
        old_assistant = self.assistant_controller
        self.assistant_controller = controller
        self.training_window = None

        if old_training is not None:
            old_training.on_close_requested = None
            old_training.close_for_mode_switch()
        if old_assistant is not None:
            old_assistant.on_quit_requested = None
            old_assistant.close_for_mode_switch()


def main() -> None:
    log_path = configure_logging()
    load_and_apply_app_preferences()
    logger.info("Application starting log=%s", log_path)

    app = QApplication(sys.argv)
    app.setApplicationName("Fairy")
    app.setQuitOnLastWindowClosed(False)

    router = AppModeRouter(app)
    app.aboutToQuit.connect(router.shutdown)
    router.start("assistant")

    try:
        sys.exit(app.exec())
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received, shutting down")
        router.shutdown()
        app.quit()
        sys.exit(0)


if __name__ == "__main__":
    main()
