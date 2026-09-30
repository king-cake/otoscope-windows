import xml.etree.ElementTree as ET

from otoscope_win.wifi import WifiNetwork, open_profile_xml, soulear_networks

NS = {"w": "http://www.microsoft.com/networking/WLAN/profile/v1"}


def test_soulear_filter_dedupes_and_sorts_by_signal():
    nets = [
        WifiNetwork("HomeNet", 90, True),
        WifiNetwork("Soulear-318eb", 40, False),
        WifiNetwork("Soulear-318eb", 70, False),
        WifiNetwork("soulear-abcde", 80, False),
        WifiNetwork("Cooleer_123", 99, False),
    ]
    assert soulear_networks(nets) == [WifiNetwork("soulear-abcde", 80, False), WifiNetwork("Soulear-318eb", 70, False)]


def test_open_profile_is_manual_open_network():
    root = ET.fromstring(open_profile_xml("Soulear-318eb"))
    assert root.find("w:name", NS).text == "Soulear-318eb"
    assert root.find("w:SSIDConfig/w:SSID/w:hex", NS).text == b"Soulear-318eb".hex().upper()
    assert root.find("w:connectionMode", NS).text == "manual"
    auth = root.find("w:MSM/w:security/w:authEncryption", NS)
    assert auth.find("w:authentication", NS).text == "open"
    assert auth.find("w:encryption", NS).text == "none"


def test_profile_escapes_xml():
    root = ET.fromstring(open_profile_xml("Soulear-<&>"))
    assert root.find("w:name", NS).text == "Soulear-<&>"
