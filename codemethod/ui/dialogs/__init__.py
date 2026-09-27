"""对话框集合。"""

from __future__ import annotations

from .about_dialog import AboutDialog
from .container_dialog import ContainerInfoDialog, VerifyResultDialog
from .entry_editor import EntryEditorDialog, ImplementationEditor
from .implementation_dialog import ImplementationDialog
from .tag_manager import TagManagerDialog

__all__ = [
    "AboutDialog",
    "ContainerInfoDialog",
    "VerifyResultDialog",
    "EntryEditorDialog",
    "ImplementationEditor",
    "ImplementationDialog",
    "TagManagerDialog",
]
