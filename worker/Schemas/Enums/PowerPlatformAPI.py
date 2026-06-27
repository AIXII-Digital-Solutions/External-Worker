import inspect
import sys
from enum import Enum as __enum, IntEnum


class InvitationStatusEnum(str, __enum):
    PENDING_ACCEPTANCE = "PendingAcceptance"
    IN_PROGRESS = "InProgress"
    COMPLETED = "Completed"
    ERROR = "Error"

    @property
    def code(self) -> int:
        mapping = {
            InvitationStatusEnum.PENDING_ACCEPTANCE: 0,
            InvitationStatusEnum.IN_PROGRESS: 1,
            InvitationStatusEnum.COMPLETED: 2,
            InvitationStatusEnum.ERROR: 3,
        }
        return mapping[self]

    @classmethod
    def from_api(cls, value: str) -> int:
        """Converts a string from an API into a numeric code"""
        try:
            return cls(value).code
        except ValueError:
            raise ValueError(f"Unknown invitation status: {value}")


class UserTypesEnum(str, __enum):
    GUEST = "Guest"
    MEMBER = "Member"


class ApplicationAccessEnum(IntEnum, __enum):
    VIEW_DASHBOARD = 1
    VIEW_CLAIMS = 2
    VIEW_UPLOAD_INSURER_FILE = 3
    VIEW_UPLOAD_PAYMENT_FILE = 4
    VIEW_AIRLINES = 5
    VIEW_AIRCRAFTS = 6
    VIEW_AIRCRAFT_TYPES = 7
    VIEW_USERS = 8

    ADD_CLAIMS = 9
    EDIT_CLAIMS = 10
    DELETE_CLAIMS = 11

    UPLOAD_INSURER_FILE = 12
    UPLOAD_PAYMENT_FILE = 13
    CHANGE_CLAIM_STATUS = 14

    EDIT_AIRCRAFTS = 15
    ADD_AIRCRAFTS = 16
    DELETE_AIRCRAFTS = 17

    ADD_AIRLINES = 18
    EDIT_AIRLINES = 19
    DELETE_AIRLINES = 20

    ADD_AIRCRAFT_TYPES = 21
    EDIT_AIRCRAFT_TYPES = 22
    DELETE_AIRCRAFT_TYPES = 23

    EDIT_USER_PERMISSIONS = 24

    VIEW_AIRCRAFTS_ADMIN = 25
    VIEW_AIRLINES_ADMIN = 26
    VIEW_AIRCRAFT_TYPES_ADMIN = 27
    VIEW_USERS_ADMIN = 28


class OSTypeEnum(str, __enum):
    WINDOWS = "Windows"
    ANDROID = "Android"
    IOS = "iOS"
    LINUX = "Linux"
    MACOS = "Mac OS"


class AppearanceEnum(str, __enum):
    LIGHT = "Light"
    DARK = "Dark"


_current_module = sys.modules[__name__]

__all__ = [
    name
    for name, obj in globals().items()
    if inspect.isclass(obj) and obj.__module__ == __name__
]

