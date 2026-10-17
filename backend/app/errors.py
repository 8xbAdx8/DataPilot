from fastapi import HTTPException


class AppError(HTTPException):
    def __init__(self, status: int, code: str, message: str, **details):
        super().__init__(status_code=status, detail={"code": code, "message": message, **details})
