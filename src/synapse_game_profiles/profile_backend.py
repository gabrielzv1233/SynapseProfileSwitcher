from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProfileInfo:
    uuid: str
    name: str


class DummyProfileBackend:
    """Temporary profile provider. UUIDs deliberately match the eventual storage contract."""

    profiles = (
        ProfileInfo("0e4f3116-8eef-4a51-b9bb-995027dd47e4", "Default"),
        ProfileInfo("80d91e67-2e77-466b-909e-b50ed1fb5e48", "FPS"),
        ProfileInfo("c46a3352-81c1-4235-9f9e-b9408386e74b", "Desktop"),
        ProfileInfo("273a51fa-76de-4ab4-9dc4-7f52de822147", "MMO"),
    )

    def __init__(self) -> None:
        self._active_uuid = self.profiles[0].uuid

    def list_profiles(self) -> tuple[ProfileInfo, ...]:
        return self.profiles

    def get_active_profile_uuid(self) -> str | None:
        return self._active_uuid

    def switch_profile(self, profile_uuid: str) -> bool:
        if not any(profile.uuid == profile_uuid for profile in self.profiles):
            return False
        self._active_uuid = profile_uuid
        return True
