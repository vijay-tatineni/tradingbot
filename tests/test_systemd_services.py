"""Tests for Fix 4 — systemd service files exist."""

import os


def test_systemd_bot_service_file_exists():
    """Bot service file exists at /etc/systemd/system/cogniflowai-bot.service."""
    assert os.path.exists('/etc/systemd/system/cogniflowai-bot.service')


def test_systemd_api_service_file_exists():
    """API service file exists at /etc/systemd/system/cogniflowai-api.service."""
    assert os.path.exists('/etc/systemd/system/cogniflowai-api.service')


def test_bot_service_has_restart():
    """Bot service has Restart=always."""
    with open('/etc/systemd/system/cogniflowai-bot.service') as f:
        content = f.read()
    assert 'Restart=always' in content
    assert 'RestartSec=30' in content
    assert '/root/trading/main.py' in content


def test_api_service_has_restart():
    """API service has Restart=always."""
    with open('/etc/systemd/system/cogniflowai-api.service') as f:
        content = f.read()
    assert 'Restart=always' in content
    assert 'RestartSec=10' in content
    assert '/root/trading/api_server.py' in content
