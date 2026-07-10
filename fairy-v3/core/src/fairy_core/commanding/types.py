from enum import StrEnum


class PermissionProfile(StrEnum):
    OBSERVE = "observe"
    STANDARD = "standard"
    AUTONOMOUS = "autonomous"
