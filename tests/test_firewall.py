import base64

from otoscope_win import firewall


def test_rule_is_narrow():
    script = firewall.rule_script(r"C:\Apps\Otoscope.exe")
    assert "-Direction Inbound" in script
    assert "-Protocol UDP" in script
    assert "-RemoteAddress LocalSubnet" in script
    assert r"-Program 'C:\Apps\Otoscope.exe'" in script
    # Replaces an older copy (e.g. after moving the .exe) instead of piling up.
    assert script.startswith("Remove-NetFirewallRule -DisplayName 'Otoscope (Kamera-Video)'")


def test_single_quotes_in_path_are_escaped():
    script = firewall.rule_script(r"C:\Users\O'Brien\Otoscope.exe")
    assert r"'C:\Users\O''Brien\Otoscope.exe'" in script


def test_encoded_command_round_trips():
    script = firewall.rule_script(r"C:\Program Files\Otoscope\Otoscope.exe")
    decoded = base64.b64decode(firewall.encoded_command(script)).decode("utf-16-le")
    assert decoded == script


def test_rule_exists_is_false_for_unknown_program():
    assert firewall.rule_exists(r"C:\does\not\exist\nothing.exe") is False
