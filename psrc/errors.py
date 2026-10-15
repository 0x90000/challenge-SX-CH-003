"""Stable, JSON-serializable failures at every public boundary."""


class ContractError(Exception):
    def __init__(self, code, message, *, stage="validation", details=None):
        super().__init__(message)
        self.code = code
        self.stage = stage
        self.details = details or {}

    def to_dict(self):
        return {
            "code": self.code,
            "stage": self.stage,
            "message": str(self),
            "details": self.details,
            "retryable": False,
        }


def require(condition, code, message, **details):
    if not condition:
        raise ContractError(code, message, details=details)
