"""串口不能把配置中的字节解释成实时控制；校验必须先于开端口。"""
import sys
from types import SimpleNamespace

import pytest
from scripts import grbl_wifi_setup as setup


@pytest.mark.parametrize('field,value', [
    ('ssid', '家里网络'), ('password', 'password家庭'),
    ('ssid', 'Home!Wifi'), ('password', 'pass?word'), ('password', 'pass~word'),
])
def test_serial_rejects_ambiguous_credentials_before_open(monkeypatch, capsys, field, value):
    opens = []
    monkeypatch.setitem(sys.modules, 'serial', SimpleNamespace(Serial=lambda: opens.append(True)))
    monkeypatch.setattr(sys, 'argv', ['setup', '--port', 'COM1', '--ssid', value if field == 'ssid' else 'Home', '--yes'])
    monkeypatch.setenv('GRBL_WIFI_PASSWORD', value if field == 'password' else 'password123')
    assert setup.main() == 2
    assert not opens
    assert 'HTTP' in capsys.readouterr().err


def test_serial_safe_ascii_is_preserved():
    setup._validate_serial_value('Home Wifi ', 'SSID')
    setup._validate_serial_value('pass_word-123 ', '密码')
