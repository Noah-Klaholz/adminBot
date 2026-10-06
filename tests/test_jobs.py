from mautrix.errors import MatrixRequestError, MForbidden

from adminbot.commands.base import describe
from adminbot.errors import NotFound
from adminbot.jobs import describe_error


def test_describe_error_handles_all_matrix_errors():
    # The base class has no .message (found against a real Synapse: it crashed the job worker).
    assert describe_error(MatrixRequestError("login failed: 403")) == "login failed: 403"
    assert describe_error(MForbidden(403, "nope")) == "nope"
    assert describe_error(NotFound("err.unknown_course", code="X")) == "There is no course 'X'."
    assert describe_error(ValueError("x")) == "ValueError: x"
    assert "login failed" in describe(MatrixRequestError("login failed"))
