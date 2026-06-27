from typing import Optional, TypeVar, Set
from http import HTTPStatus


from fastapi import Request, status, Response

from Schemas import DetailField, DefaultResponse, ErrorResponse

T = TypeVar("T")

def build_responses(*, include: Set[int]) -> dict:
    result = {}

    success_codes = {200, 201, 202}

    for status_code in include:
        if status_code in success_codes:
            continue

        result[status_code] = {
            "description": HTTPStatus(status_code).phrase,
            "model": ErrorResponse
        }

    return result


def success_response(*, request: Request, response: Response, data: T, msg: str = "Success",
                     status_code: status = status.HTTP_200_OK) -> DefaultResponse[T]:
    response.status_code = status_code
    return DefaultResponse(
        status_code=status_code,
        details=DetailField(
            msg=msg,
            correlationId=request.state.correlation_id
        ),
        data=data
    )


def warning_response(*, request: Request, response: Response,
                     exc: Optional[Exception] = None,
                     msg: Optional[str] = None,
                     status_code: status = status.HTTP_400_BAD_REQUEST) -> DefaultResponse[T]:
    if not exc and not msg:
        raise ValueError("'exc' or 'msg' must be provided")
    response.status_code = status_code
    if exc:
        return DefaultResponse(
            status_code=status_code,
            details=DetailField(
                msg=f"{exc.__class__.__name__}: {str(exc)}",
                correlationId=request.state.correlation_id
            ),
            data=[]
        )
    else:
        return DefaultResponse(
            status_code=status_code,
            details=DetailField(
                msg=msg,
                correlationId=request.state.correlation_id
            ),
            data=[]
        )


def error_response(*, request: Request, response: Response,
                   exc: Optional[Exception] = None,
                   msg: Optional[str] = None,
                   status_code: status = status.HTTP_500_INTERNAL_SERVER_ERROR) -> DefaultResponse[T]:
    if not exc and not msg:
        raise ValueError("'exc' or 'msg' must be provided")
    response.status_code = status_code
    if exc:
        return DefaultResponse(
            status_code=status_code,
            details=DetailField(
                msg=f"{exc.__class__.__name__}: {str(exc)}",
                correlationId=request.state.correlation_id
            ),
            data=[]
        )
    else:
        return DefaultResponse(
            status_code=status_code,
            details=DetailField(
                msg=msg,
                correlationId=request.state.correlation_id
            ),
            data=[]
        )
