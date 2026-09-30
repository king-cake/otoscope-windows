"""Windows Wi-Fi control through the Native Wifi API (wlanapi.dll).

Language-independent, unlike parsing ``netsh`` output. Covers what the app
needs: scan for ``Soulear-`` access points, join an open one through a
temporary profile, and put the previous connection back afterwards.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass
from xml.sax.saxutils import escape

log = logging.getLogger(__name__)

SSID_PREFIX = "Soulear-"

ERROR_SUCCESS = 0
ERROR_ACCESS_DENIED = 5
ERROR_NOT_FOUND = 1168
ERROR_INVALID_STATE = 5023

_WLAN_INTF_OPCODE_CURRENT_CONNECTION = 7
_WLAN_INTERFACE_STATE_CONNECTED = 1
_WLAN_CONNECTION_MODE_PROFILE = 0
_DOT11_BSS_TYPE_INFRASTRUCTURE = 1


class WifiError(Exception):
    pass


class LocationAccessDenied(WifiError):
    """Windows 11 24H2+ hides Wi-Fi scan results from desktop apps unless
    location access for desktop apps is enabled."""


@dataclass(frozen=True)
class WifiNetwork:
    ssid: str
    signal: int  # 0..100
    secured: bool


@dataclass(frozen=True)
class Connection:
    ssid: str
    profile: str


def soulear_networks(networks: list[WifiNetwork]) -> list[WifiNetwork]:
    """Camera APs only, one entry per SSID (strongest), strongest first."""
    best: dict[str, WifiNetwork] = {}
    for n in networks:
        if n.ssid.lower().startswith(SSID_PREFIX.lower()):
            if n.ssid not in best or n.signal > best[n.ssid].signal:
                best[n.ssid] = n
    return sorted(best.values(), key=lambda n: -n.signal)


def open_profile_xml(ssid: str) -> str:
    """Profile for an open (no password) AP. Manual connection mode, so
    Windows never auto-joins the camera later."""
    hex_ssid = ssid.encode("utf-8").hex().upper()
    name = escape(ssid)
    return (
        '<?xml version="1.0"?>\n'
        '<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">\n'
        f"  <name>{name}</name>\n"
        f"  <SSIDConfig><SSID><hex>{hex_ssid}</hex><name>{name}</name></SSID></SSIDConfig>\n"
        "  <connectionType>ESS</connectionType>\n"
        "  <connectionMode>manual</connectionMode>\n"
        "  <MSM><security><authEncryption>"
        "<authentication>open</authentication><encryption>none</encryption><useOneX>false</useOneX>"
        "</authEncryption></security></MSM>\n"
        "</WLANProfile>\n"
    )


# -- ctypes structures ---------------------------------------------------------

class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


class WLAN_INTERFACE_INFO(ctypes.Structure):
    _fields_ = [("InterfaceGuid", GUID), ("strInterfaceDescription", ctypes.c_wchar * 256),
                ("isState", ctypes.c_uint)]


class WLAN_INTERFACE_INFO_LIST(ctypes.Structure):
    _fields_ = [("dwNumberOfItems", wintypes.DWORD), ("dwIndex", wintypes.DWORD),
                ("InterfaceInfo", WLAN_INTERFACE_INFO * 1)]


class DOT11_SSID(ctypes.Structure):
    _fields_ = [("uSSIDLength", wintypes.ULONG), ("ucSSID", ctypes.c_ubyte * 32)]

    def text(self) -> str:
        return bytes(self.ucSSID[: self.uSSIDLength]).decode("utf-8", "replace")


class WLAN_AVAILABLE_NETWORK(ctypes.Structure):
    _fields_ = [
        ("strProfileName", ctypes.c_wchar * 256),
        ("dot11Ssid", DOT11_SSID),
        ("dot11BssType", ctypes.c_uint),
        ("uNumberOfBssids", wintypes.ULONG),
        ("bNetworkConnectable", wintypes.BOOL),
        ("wlanNotConnectableReason", wintypes.DWORD),
        ("uNumberOfPhyTypes", wintypes.ULONG),
        ("dot11PhyTypes", ctypes.c_uint * 8),
        ("bMorePhyTypes", wintypes.BOOL),
        ("wlanSignalQuality", wintypes.ULONG),
        ("bSecurityEnabled", wintypes.BOOL),
        ("dot11DefaultAuthAlgorithm", ctypes.c_uint),
        ("dot11DefaultCipherAlgorithm", ctypes.c_uint),
        ("dwFlags", wintypes.DWORD),
        ("dwReserved", wintypes.DWORD),
    ]


class WLAN_AVAILABLE_NETWORK_LIST(ctypes.Structure):
    _fields_ = [("dwNumberOfItems", wintypes.DWORD), ("dwIndex", wintypes.DWORD),
                ("Network", WLAN_AVAILABLE_NETWORK * 1)]


class WLAN_ASSOCIATION_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("dot11Ssid", DOT11_SSID),
        ("dot11BssType", ctypes.c_uint),
        ("dot11Bssid", ctypes.c_ubyte * 6),
        ("dot11PhyType", ctypes.c_uint),
        ("uDot11PhyIndex", wintypes.ULONG),
        ("wlanSignalQuality", wintypes.ULONG),
        ("ulRxRate", wintypes.ULONG),
        ("ulTxRate", wintypes.ULONG),
    ]


class WLAN_SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("bSecurityEnabled", wintypes.BOOL), ("bOneXEnabled", wintypes.BOOL),
                ("dot11AuthAlgorithm", ctypes.c_uint), ("dot11CipherAlgorithm", ctypes.c_uint)]


class WLAN_CONNECTION_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("isState", ctypes.c_uint),
        ("wlanConnectionMode", ctypes.c_uint),
        ("strProfileName", ctypes.c_wchar * 256),
        ("wlanAssociationAttributes", WLAN_ASSOCIATION_ATTRIBUTES),
        ("wlanSecurityAttributes", WLAN_SECURITY_ATTRIBUTES),
    ]


class WLAN_CONNECTION_PARAMETERS(ctypes.Structure):
    _fields_ = [
        ("wlanConnectionMode", ctypes.c_uint),
        ("strProfile", wintypes.LPCWSTR),
        ("pDot11Ssid", ctypes.POINTER(DOT11_SSID)),
        ("pDesiredBssidList", ctypes.c_void_p),
        ("dot11BssType", ctypes.c_uint),
        ("dwFlags", wintypes.DWORD),
    ]


def _load_wlanapi():
    api = ctypes.WinDLL("wlanapi.dll")
    H = wintypes.HANDLE
    PG = ctypes.POINTER(GUID)
    sig = {
        "WlanOpenHandle": [wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(H)],
        "WlanCloseHandle": [H, ctypes.c_void_p],
        "WlanEnumInterfaces": [H, ctypes.c_void_p, ctypes.POINTER(ctypes.POINTER(WLAN_INTERFACE_INFO_LIST))],
        "WlanScan": [H, PG, ctypes.POINTER(DOT11_SSID), ctypes.c_void_p, ctypes.c_void_p],
        "WlanGetAvailableNetworkList": [H, PG, wintypes.DWORD, ctypes.c_void_p,
                                        ctypes.POINTER(ctypes.POINTER(WLAN_AVAILABLE_NETWORK_LIST))],
        "WlanQueryInterface": [H, PG, ctypes.c_uint, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD),
                               ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint)],
        "WlanSetProfile": [H, PG, wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.BOOL,
                           ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)],
        "WlanGetProfile": [H, PG, wintypes.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR),
                           ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD)],
        "WlanDeleteProfile": [H, PG, wintypes.LPCWSTR, ctypes.c_void_p],
        "WlanConnect": [H, PG, ctypes.POINTER(WLAN_CONNECTION_PARAMETERS), ctypes.c_void_p],
        "WlanFreeMemory": [ctypes.c_void_p],
    }
    for name, args in sig.items():
        fn = getattr(api, name)
        fn.argtypes = args
        fn.restype = None if name == "WlanFreeMemory" else wintypes.DWORD
    return api


class Wifi:
    """Handle to the first Wi-Fi adapter."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise WifiError("Wi-Fi control needs Windows")
        self._api = _load_wlanapi()
        self._handle = wintypes.HANDLE()
        version = wintypes.DWORD()
        self._check(self._api.WlanOpenHandle(2, None, ctypes.byref(version), ctypes.byref(self._handle)),
                    "WlanOpenHandle")
        interfaces = ctypes.POINTER(WLAN_INTERFACE_INFO_LIST)()
        self._check(self._api.WlanEnumInterfaces(self._handle, None, ctypes.byref(interfaces)),
                    "WlanEnumInterfaces")
        try:
            if interfaces.contents.dwNumberOfItems == 0:
                raise WifiError("Kein WLAN-Adapter gefunden")
            info = interfaces.contents.InterfaceInfo[0]
            self._guid = GUID()
            ctypes.pointer(self._guid)[0] = info.InterfaceGuid
            self.adapter_name = info.strInterfaceDescription
        finally:
            self._api.WlanFreeMemory(interfaces)

    def close(self) -> None:
        if self._handle:
            self._api.WlanCloseHandle(self._handle, None)
            self._handle = wintypes.HANDLE()

    def _check(self, code: int, what: str) -> None:
        if code == ERROR_ACCESS_DENIED:
            raise LocationAccessDenied(f"{what}: Zugriff verweigert")
        if code != ERROR_SUCCESS:
            raise WifiError(f"{what} fehlgeschlagen (Fehler {code})")

    def scan(self, wait_s: float = 3.0) -> list[WifiNetwork]:
        """Trigger a fresh scan, then return what the adapter sees."""
        code = self._api.WlanScan(self._handle, ctypes.byref(self._guid), None, None, None)
        if code not in (ERROR_SUCCESS,):
            log.warning("WlanScan returned %d", code)
        time.sleep(wait_s)
        return self.visible_networks()

    def visible_networks(self) -> list[WifiNetwork]:
        lst = ctypes.POINTER(WLAN_AVAILABLE_NETWORK_LIST)()
        self._check(self._api.WlanGetAvailableNetworkList(
            self._handle, ctypes.byref(self._guid), 0, None, ctypes.byref(lst)), "WlanGetAvailableNetworkList")
        try:
            n = lst.contents.dwNumberOfItems
            arr = ctypes.cast(ctypes.addressof(lst.contents.Network),
                              ctypes.POINTER(WLAN_AVAILABLE_NETWORK * n)).contents
            return [WifiNetwork(net.dot11Ssid.text(), int(net.wlanSignalQuality), bool(net.bSecurityEnabled))
                    for net in arr if net.dot11Ssid.uSSIDLength]
        finally:
            self._api.WlanFreeMemory(lst)

    def current(self) -> Connection | None:
        size = wintypes.DWORD()
        data = ctypes.c_void_p()
        kind = ctypes.c_uint()
        code = self._api.WlanQueryInterface(self._handle, ctypes.byref(self._guid),
                                            _WLAN_INTF_OPCODE_CURRENT_CONNECTION, None,
                                            ctypes.byref(size), ctypes.byref(data), ctypes.byref(kind))
        if code == ERROR_INVALID_STATE:
            return None  # not connected
        self._check(code, "WlanQueryInterface")
        try:
            attrs = ctypes.cast(data, ctypes.POINTER(WLAN_CONNECTION_ATTRIBUTES)).contents
            if attrs.isState != _WLAN_INTERFACE_STATE_CONNECTED:
                return None
            return Connection(attrs.wlanAssociationAttributes.dot11Ssid.text(), attrs.strProfileName)
        finally:
            self._api.WlanFreeMemory(data)

    def has_profile(self, name: str) -> bool:
        xml = wintypes.LPWSTR()
        flags = wintypes.DWORD()
        access = wintypes.DWORD()
        code = self._api.WlanGetProfile(self._handle, ctypes.byref(self._guid), name, None,
                                        ctypes.byref(xml), ctypes.byref(flags), ctypes.byref(access))
        if code == ERROR_SUCCESS:
            self._api.WlanFreeMemory(xml)
            return True
        return False

    def set_profile(self, xml: str) -> None:
        reason = wintypes.DWORD()
        code = self._api.WlanSetProfile(self._handle, ctypes.byref(self._guid), 0, xml, None, True, None,
                                        ctypes.byref(reason))
        self._check(code, f"WlanSetProfile (Grund {reason.value})")

    def delete_profile(self, name: str) -> None:
        code = self._api.WlanDeleteProfile(self._handle, ctypes.byref(self._guid), name, None)
        if code not in (ERROR_SUCCESS, ERROR_NOT_FOUND):
            log.warning("WlanDeleteProfile(%s) returned %d", name, code)

    def connect(self, profile: str) -> None:
        params = WLAN_CONNECTION_PARAMETERS()
        params.wlanConnectionMode = _WLAN_CONNECTION_MODE_PROFILE
        params.strProfile = profile
        params.pDot11Ssid = None
        params.pDesiredBssidList = None
        params.dot11BssType = _DOT11_BSS_TYPE_INFRASTRUCTURE
        params.dwFlags = 0
        self._check(self._api.WlanConnect(self._handle, ctypes.byref(self._guid), ctypes.byref(params), None),
                    "WlanConnect")

    def wait_connected(self, ssid: str, timeout_s: float) -> bool:
        end = time.monotonic() + timeout_s
        while time.monotonic() < end:
            cur = self.current()
            if cur and cur.ssid == ssid:
                return True
            time.sleep(0.5)
        return False


class WifiSwitcher:
    """Joins a camera AP and later restores whatever the adapter was on before."""

    def __init__(self, wifi: Wifi) -> None:
        self._wifi = wifi
        self.previous: Connection | None = None
        self._created_profile: str | None = None
        self.camera_ssid: str | None = None

    def switch_to(self, ssid: str, timeout_s: float = 20.0) -> None:
        cur = self._wifi.current()
        self.camera_ssid = ssid
        if cur and cur.ssid == ssid:
            log.info("already on %s", ssid)
            return
        self.previous = cur
        if not self._wifi.has_profile(ssid):
            self._created_profile = ssid
        self._wifi.set_profile(open_profile_xml(ssid))
        log.info("joining %s (previous: %s)", ssid, cur)
        self._wifi.connect(ssid)
        if not self._wifi.wait_connected(ssid, timeout_s):
            raise WifiError(f"Verbindung zu {ssid} kam nicht zustande")

    def restore(self, timeout_s: float = 15.0) -> None:
        """Best effort: drop the temporary camera profile and rejoin the
        previous network. Never raises."""
        try:
            if self._created_profile:
                # Deleting the profile of the connected network also disconnects.
                self._wifi.delete_profile(self._created_profile)
                self._created_profile = None
            if self.previous and self.previous.profile:
                log.info("restoring %s", self.previous.profile)
                self._wifi.connect(self.previous.profile)
                self._wifi.wait_connected(self.previous.ssid, timeout_s)
        except WifiError as e:
            log.warning("restore failed: %s", e)
        finally:
            self.previous = None
            self.camera_ssid = None
