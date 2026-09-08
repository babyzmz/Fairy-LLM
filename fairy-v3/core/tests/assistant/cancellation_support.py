from threading import Event

from fairy_core.assistant.tools import ToolResult


class BlockingToolStop:
    def __init__(self):
        self.started = Event()
        self.stop_started = Event()
        self.release_stop = Event()
        self.release_tool = Event()

    def execute(self, definition, scope, arguments):
        assert definition.name == "web.search"
        self.started.set()
        assert self.release_tool.wait(8)
        return ToolResult.create(
            public_summary="Late result", model_content="Late result", artifact_ids=(),
        )

    def cancel_command(self, command_run):
        assert command_run.command_name == "web.search"
        self.stop_started.set()
        assert self.release_stop.wait(8)
