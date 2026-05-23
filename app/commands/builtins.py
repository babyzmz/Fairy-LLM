from __future__ import annotations

from app.commands.registry import CommandKind, CommandRegistry, CommandSpec


SCREENSHOT_SPEC = CommandSpec(
    name="/截图",
    aliases=("/screenshot", "/截屏"),
    description="截取当前屏幕并把分析请求发送给 Fairy。",
    usage="/截图",
    kind=CommandKind.CLIENT_ACTION,
    client_action_type="screenshot_then_chat",
)

STRATEGY_SPEC = CommandSpec(
    name="/攻略",
    aliases=("/strategy", "/guide"),
    description="查找指定游戏或关卡的攻略。",
    usage="/攻略 <游戏或关卡名>",
    kind=CommandKind.REWRITE_MESSAGE,
    rewrite_template="搜索 {arg} 的最新攻略与打法要点，给出关键步骤。",
    argument_label="游戏或关卡名",
)

PET_SPEC = CommandSpec(
    name="/喂",
    aliases=("/pet", "/撸"),
    description="撸一下右下角 Fairy，触发心心动画。",
    usage="/喂",
    kind=CommandKind.SERVER_ACTION,
    server_handler_name="pet",
)

MUTE_SPEC = CommandSpec(
    name="/静音",
    aliases=("/mute",),
    description="静音/取消静音 Fairy 浮窗气泡。",
    usage="/静音",
    kind=CommandKind.SERVER_ACTION,
    server_handler_name="mute_toggle",
)


def _pet_handler(_args: dict) -> dict:
    from app.companion import get_bubble_state

    started = get_bubble_state().trigger_pet()
    return {"ok": True, "pet_started_at": started}


def _mute_toggle_handler(_args: dict) -> dict:
    from app.companion import get_bubble_state, get_companion_observer

    new_muted = not get_companion_observer().muted
    get_bubble_state().set_muted(new_muted)
    return {"ok": True, "muted": new_muted}


def register_builtins(registry: CommandRegistry) -> None:
    registry.register(SCREENSHOT_SPEC)
    registry.register(STRATEGY_SPEC)
    registry.register(PET_SPEC, server_handler=_pet_handler)
    registry.register(MUTE_SPEC, server_handler=_mute_toggle_handler)
