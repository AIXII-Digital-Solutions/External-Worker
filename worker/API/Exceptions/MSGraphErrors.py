import inspect
import sys


class InvalidUserFilterError(Exception):
    """Raised when no or multiple filters are provided for user lookup."""

    def __init__(self, message: str | None = None, docs_url: str | None = None) -> None:
        if docs_url is None:
            docs_url = "https://learn.microsoft.com/en-us/graph/api/resources/user?view=graph-rest-1.0#properties"

        if message is None:
            message = (
                "You must provide exactly one filter argument "
                "(e.g. mail='...' or id='...').\n"
                "See documentation for available properties: {}".format(docs_url)
            )

        super().__init__(message)


class InvalidSubscriptionError(Exception):
    """Raised when no or one param are provided for subscription."""

    def __init__(self, message: str | None = None) -> None:
        if message is None:
            message = (
                "You must provide exactly all subscription arguments. change_type, resource"
            )

        super().__init__(message)


_current_module = sys.modules[__name__]

__all__ = [
    name
    for name, obj in globals().items()
    if inspect.isclass(obj) and obj.__module__ == __name__
]
