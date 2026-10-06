"""Command groups. bot.py instantiates each one and hands them to the CommandRouter."""
from .admin import AdminCommands
from .base import CommandRouter
from .bots import BotCommands
from .confirm import ConfirmCommands
from .course import CourseCommands
from .groups import GroupCommands
from .help import HelpCommands
from .invite import InviteCommands
from .room import RoomCommands
from .roster import RosterCommands
from .signup import SignupCommands
from .template import TemplateCommands

COMMAND_CLASSES = [
    HelpCommands,
    ConfirmCommands,
    AdminCommands,
    CourseCommands,
    RoomCommands,
    InviteCommands,
    RosterCommands,
    GroupCommands,
    TemplateCommands,
    SignupCommands,
    BotCommands,
]

__all__ = ["COMMAND_CLASSES", "CommandRouter"]
