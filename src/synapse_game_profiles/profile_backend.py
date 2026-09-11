from __future__ import annotations

from dataclasses import dataclass

from .debug_log import get_logger


_LOG = get_logger("synapse")
_TARGET_SEPARATOR = "::"

try:
    from synapsectrl import SynapseError, SynapseService
except ImportError:  # Keep the UI diagnosable if dependencies were not refreshed yet.
    SynapseError = None  # type: ignore[assignment]
    SynapseService = None  # type: ignore[assignment]


@dataclass(frozen=True, slots=True)
class ProfileInfo:
    uuid: str
    name: str
    device_id: str
    profile_id: str
    device_name: str
    active: bool = False


def _target_id(device_id: str, profile_id: str) -> str:
    return f"{device_id}{_TARGET_SEPARATOR}{profile_id}"


def _split_target(target_id: str | None) -> tuple[str, str] | None:
    if not target_id or _TARGET_SEPARATOR not in target_id:
        return None
    device_id, profile_id = target_id.split(_TARGET_SEPARATOR, 1)
    if not device_id or not profile_id:
        return None
    return device_id, profile_id


class SynapseProfileBackend:
    """Long-lived SynapseCTRL-backed profile provider.

    Stored profile identifiers are opaque target IDs containing both SynapseCTRL's
    stable device ID and the profile GUID. Keeping both values prevents a profile
    from ever being routed to the wrong Razer device while preserving the UI's
    existing single-string storage contract.
    """

    def __init__(self) -> None:
        self._service = None
        self._last_device_id: str | None = None
        self._last_error: str | None = None

        if SynapseService is None:
            self._last_error = "SynapseCTRL is not installed. Reinstall project dependencies."
            _LOG.error(self._last_error)
            return

        try:
            self._service = SynapseService(
                poll_interval=0.75,
                unavailable_interval=2.0,
            )
            self._service.start()
            _LOG.info("SynapseCTRL persistent service started")
        except Exception as error:
            self._last_error = str(error)
            _LOG.exception("Could not start SynapseCTRL service")

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def close(self) -> None:
        if self._service is None:
            return
        try:
            self._service.close()
        except Exception:
            _LOG.exception("Could not close SynapseCTRL service cleanly")

    def _devices(self, *, refresh: bool) -> tuple:
        if self._service is None:
            return ()
        try:
            devices = tuple(self._service.list_devices(refresh=refresh))
            self._last_error = None
            return devices
        except Exception as error:
            self._last_error = str(error)
            if SynapseError is not None and isinstance(error, SynapseError):
                _LOG.warning("SynapseCTRL discovery unavailable [%s]: %s", error.code, error)
            else:
                _LOG.exception("SynapseCTRL discovery failed")
            return ()

    def list_profiles(self) -> tuple[ProfileInfo, ...]:
        devices = tuple(
            device
            for device in self._devices(refresh=True)
            if getattr(device, "connected", False)
            and getattr(device, "profiles_supported", False)
            and getattr(device, "controllable", False)
        )
        multiple_devices = len(devices) > 1
        profiles: list[ProfileInfo] = []

        for device in devices:
            for profile in device.profiles:
                label = profile.name
                if multiple_devices:
                    label = f"{profile.name} ({device.name})"
                profiles.append(
                    ProfileInfo(
                        uuid=_target_id(device.id, profile.id),
                        name=label,
                        device_id=device.id,
                        profile_id=profile.id,
                        device_name=device.name,
                        active=bool(profile.active),
                    )
                )

        profiles.sort(key=lambda item: (item.device_name.casefold(), item.name.casefold()))
        _LOG.info("SynapseCTRL discovered %d selectable profile(s) across %d device(s)", len(profiles), len(devices))
        return tuple(profiles)

    def get_active_profile_uuid(self) -> str | None:
        """Return one unambiguous active target, primarily for compatibility.

        The foreground watcher normally uses get_active_profile_for() so fallback
        capture is tied to the same device as the selected game profile.
        """
        devices = self._devices(refresh=False)
        if not devices:
            devices = self._devices(refresh=True)

        active = [
            (device.id, device.active_profile_id)
            for device in devices
            if getattr(device, "active_profile_id", None)
        ]
        if self._last_device_id:
            for device_id, profile_id in active:
                if device_id == self._last_device_id:
                    return _target_id(device_id, profile_id)
        if len(active) == 1:
            return _target_id(active[0][0], active[0][1])
        return None

    def get_active_profile_for(self, target_id: str | None) -> str | None:
        target = _split_target(target_id)
        if target is None:
            return None
        device_id, _ = target

        devices = self._devices(refresh=False)
        device = next((item for item in devices if item.id == device_id), None)
        if device is None:
            devices = self._devices(refresh=True)
            device = next((item for item in devices if item.id == device_id), None)
        if device is None or not device.active_profile_id:
            return None

        return _target_id(device.id, device.active_profile_id)

    def switch_profile(self, target_id: str) -> bool:
        target = _split_target(target_id)
        if target is None:
            _LOG.warning("Ignoring invalid/legacy profile target: %r", target_id)
            return False
        if self._service is None:
            return False

        device_id, profile_id = target
        try:
            result = self._service.switch_profile(
                device_id,
                profile_id,
                timeout=5.0,
                verify=True,
            )
        except Exception as error:
            self._last_error = str(error)
            if SynapseError is not None and isinstance(error, SynapseError):
                _LOG.warning(
                    "SynapseCTRL switch failed [%s] device=%s profile=%s: %s",
                    error.code,
                    device_id,
                    profile_id,
                    error,
                )
            else:
                _LOG.exception("SynapseCTRL switch crashed device=%s profile=%s", device_id, profile_id)
            return False

        self._last_device_id = device_id
        self._last_error = None
        success = result.status in {"verified", "already_active"}
        if success:
            _LOG.info(
                "SynapseCTRL profile switch %s device=%s profile=%s elapsed=%sms",
                result.status,
                device_id,
                profile_id,
                result.elapsed_ms,
            )
        else:
            _LOG.warning(
                "SynapseCTRL profile switch returned %s device=%s profile=%s error=%r",
                result.status,
                device_id,
                profile_id,
                result.error,
            )
        return success


# Compatibility alias keeps the existing UI/watcher imports stable while the
# implementation is now fully backed by SynapseCTRL.
DummyProfileBackend = SynapseProfileBackend
