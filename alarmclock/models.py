from __future__ import annotations

import secrets
from dataclasses import dataclass, field


def generate_alarm_id() -> str:
    return secrets.token_hex(2)  # 4 hex chars, plenty for a handful of alarms


@dataclass
class Alarm:
    time: str  # canonical "HH:MM:SS" 24-hour, local wall-clock
    label: str = ""
    repeat: list[str] = field(default_factory=list)  # weekday codes; [] = one-off
    enabled: bool = True
    id: str = field(default_factory=generate_alarm_id)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "time": self.time,
            "label": self.label,
            "repeat": self.repeat,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Alarm":
        return cls(
            id=data["id"],
            time=data["time"],
            label=data.get("label", ""),
            repeat=list(data.get("repeat", [])),
            enabled=data.get("enabled", True),
        )
