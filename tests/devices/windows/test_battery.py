"""Battery capability — tests for pc.power.battery_level.

Verifies:
- The capability is registered in the Windows Device Agent
- The tool definition exists in the PC catalog with SAFE permission
- psutil.sensors_battery() result is correctly mapped
- No-battery machines return an honest failure (not a crash)
- pc.shell.execute remains SENSITIVE (regression guard)
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from raya.contracts import CommandStatus, PermissionLevel
from raya.safety.risk import classify_risk


# ---------------------------------------------------------------------------
# 1. Capability registered in the Windows Device Agent
# ---------------------------------------------------------------------------

def test_battery_capability_registered_in_agent():
    from raya.devices.windows.agent import _CAPABILITIES
    names = [c.name for c in _CAPABILITIES]
    assert "power.battery_level" in names, "power.battery_level must be a registered Capability"


# ---------------------------------------------------------------------------
# 2. Tool definition exists in the PC catalog with SAFE permission
# ---------------------------------------------------------------------------

def test_battery_tool_registered_in_pc_catalog():
    """pc.power.battery_level must be in the tool definitions list."""
    from raya.tools.catalog.pc import register_pc_tools
    from raya.tools import ToolRegistry

    registry = ToolRegistry()
    agent_mock = MagicMock()
    agent_mock.execute.return_value = MagicMock(
        status=CommandStatus.SUCCESS, output={"percent": 80.0, "charging": True},
        evidence={"percent": 80.0}, error=None,
    )
    register_pc_tools(registry, agent_mock, lambda: False)

    tool = registry.get("pc.power.battery_level")
    assert tool is not None, "pc.power.battery_level not found in registry"


def test_battery_tool_is_safe():
    from raya.tools.catalog.pc import register_pc_tools
    from raya.tools import ToolRegistry

    registry = ToolRegistry()
    agent_mock = MagicMock()
    register_pc_tools(registry, agent_mock, lambda: False)

    tool = registry.get("pc.power.battery_level")
    assert tool is not None
    assert tool.permission_level == PermissionLevel.SAFE


# ---------------------------------------------------------------------------
# 3. SAFE classification (risk classifier)
# ---------------------------------------------------------------------------

def test_battery_level_safe_classification():
    assert classify_risk(["pc.read"], {}) == PermissionLevel.SAFE


# ---------------------------------------------------------------------------
# 4. Handler returns percent and charging fields
# ---------------------------------------------------------------------------

def test_battery_handler_returns_percent_and_charging():
    from raya.devices.windows.agent import WindowsDeviceAgent, _DISPATCH
    from raya.contracts import Command, new_id

    battery_mock = MagicMock()
    battery_mock.percent = 74.0
    battery_mock.power_plugged = True
    battery_mock.secsleft = -1  # POWER_TIME_UNLIMITED

    import psutil
    with patch.object(psutil, "sensors_battery", return_value=battery_mock), \
         patch.object(psutil, "POWER_TIME_UNLIMITED", -1), \
         patch.object(psutil, "POWER_TIME_UNKNOWN", -2):
        agent = WindowsDeviceAgent(screenshot_dir=Path("."))
        cmd = Command(device_id="windows_agent", capability_name="power.battery_level",
                      arguments={}, correlation_id=new_id("cmd"))
        result = _DISPATCH["power.battery_level"](agent, cmd, lambda: False)

    assert result.status == CommandStatus.SUCCESS
    assert result.output["percent"] == 74.0
    assert result.output["charging"] is True


def test_battery_handler_charging_false_when_on_battery():
    from raya.devices.windows.agent import WindowsDeviceAgent, _DISPATCH
    from raya.contracts import Command, new_id
    import psutil

    battery_mock = MagicMock()
    battery_mock.percent = 55.0
    battery_mock.power_plugged = False
    battery_mock.secsleft = 3600

    with patch.object(psutil, "sensors_battery", return_value=battery_mock), \
         patch.object(psutil, "POWER_TIME_UNLIMITED", -1), \
         patch.object(psutil, "POWER_TIME_UNKNOWN", -2):
        agent = WindowsDeviceAgent(screenshot_dir=Path("."))
        cmd = Command(device_id="windows_agent", capability_name="power.battery_level",
                      arguments={}, correlation_id=new_id("cmd"))
        result = _DISPATCH["power.battery_level"](agent, cmd, lambda: False)

    assert result.status == CommandStatus.SUCCESS
    assert result.output["charging"] is False
    assert result.output.get("remaining_seconds") == 3600


# ---------------------------------------------------------------------------
# 5. No-battery machine returns honest failure, not a crash
# ---------------------------------------------------------------------------

def test_battery_handler_no_battery_returns_failure():
    from raya.devices.windows.agent import WindowsDeviceAgent, _DISPATCH
    from raya.contracts import Command, new_id
    import psutil

    with patch.object(psutil, "sensors_battery", return_value=None):
        agent = WindowsDeviceAgent(screenshot_dir=Path("."))
        cmd = Command(device_id="windows_agent", capability_name="power.battery_level",
                      arguments={}, correlation_id=new_id("cmd"))
        result = _DISPATCH["power.battery_level"](agent, cmd, lambda: False)

    assert result.status == CommandStatus.FAILURE
    assert result.error is not None
    assert result.error.code == "NO_BATTERY"
    assert result.error.retryable is False


# ---------------------------------------------------------------------------
# 6. pc.shell.execute remains SENSITIVE — regression guard
# ---------------------------------------------------------------------------

def test_shell_execute_remains_sensitive():
    assert classify_risk(["pc.shell"], {}) == PermissionLevel.SENSITIVE
