import logging
from rest_framework.views import exception_handler
from rest_framework.exceptions import APIException

logger = logging.getLogger(__name__)


class InsufficientFundsError(APIException):
    """Raised when merchant does not have enough available balance."""
    status_code = 409
    default_detail = "Insufficient funds"
    default_code = "insufficient_funds"


def custom_exception_handler(exc, context):
    """Pass-through to DRF default — our custom errors already subclass APIException."""
    response = exception_handler(exc, context)
    if response is None:
        logger.exception("Unhandled exception in view", exc_info=exc)
    return response
