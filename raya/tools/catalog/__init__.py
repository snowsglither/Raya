"""Catalogue de Tools (RAYA_V2_REPOSITORY_STRUCTURE.md §11). `demo.py`
(Phase 3) est le premier catalogue réel — un vrai Tool catalog exhaustif
(83 capacités V1) reste hors scope, cette phase prouve le PIPELINE, pas la
couverture fonctionnelle complète. `pc.py`/`browser.py` (Phase 4) délèguent
aux Device Agents réels (devices/windows/, devices/browser/).
"""

from .browser import register_browser_tools
from .demo import register_demo_tools
from .notify import NotifyOps, register_notify_tools
from .pc import register_pc_tools
from .phone import register_phone_tools
from .preferences import PreferenceOps, register_preference_tools
from .spatial import register_spatial_tools
from .system_time import register_system_time_tool
from .tasks import TaskControlOps, register_task_control_tools
from .ui_views import register_ui_view_tools

__all__ = [
    "register_demo_tools", "register_pc_tools", "register_browser_tools",
    "register_task_control_tools", "TaskControlOps", "register_ui_view_tools",
    "register_spatial_tools", "register_notify_tools", "NotifyOps",
    "register_system_time_tool", "register_preference_tools", "PreferenceOps",
    "register_phone_tools",
]
