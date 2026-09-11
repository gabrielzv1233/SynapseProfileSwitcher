from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(slots=True)
class AppRecord:
    id: str
    executable: str
    title: str
    launcher: str = "Manual"
    install_root: str = ""
    icon_path: str | None = None
    profile_uuid: str | None = None
    manually_added: bool = False
    removed: bool = False
    removed_by_user: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> "AppRecord":
        fields = cls.__dataclass_fields__
        kwargs = {key: value[key] for key in fields if key in value}
        if value.get("removed") and "removed_by_user" not in value:
            kwargs["removed_by_user"] = True
        return cls(**kwargs)
