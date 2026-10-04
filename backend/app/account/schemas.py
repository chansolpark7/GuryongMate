"""account 공개 요청·응답 스키마. 명세: docs/account/README.md"""
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

LoginId = Annotated[str, Field(min_length=4, max_length=32, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{3,31}$",
                               description="영문·숫자·.·_·- 4~32자 (대문자는 소문자로 바꿔 저장)")]
Password = Annotated[str, Field(min_length=8, max_length=128)]
DisplayName = Annotated[str, Field(min_length=1, max_length=30)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SignupRequest(Strict):
    login_id: LoginId
    password: Password
    display_name: DisplayName | None = None

    @field_validator("login_id")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.lower()


class LoginRequest(Strict):
    login_id: Annotated[str, Field(min_length=1, max_length=32)]
    password: Annotated[str, Field(min_length=1, max_length=128)]

    @field_validator("login_id")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.lower()


class GoogleLoginRequest(Strict):
    id_token: Annotated[str, Field(min_length=1, max_length=8192)]


class RefreshRequest(Strict):
    refresh_token: Annotated[str, Field(min_length=1, max_length=256)]


class ProfileUpdate(Strict):
    display_name: DisplayName


class PasswordChange(Strict):
    current_password: Annotated[str, Field(min_length=1, max_length=128)]
    new_password: Password


class UserView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    provider: Literal["password", "google"]
    login_id: str | None
    email: str | None
    display_name: str
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(description="access 토큰 유효 시간(초)")
    refresh_token: str
    refresh_expires_in: int
    user: UserView
    created: bool = Field(False, description="이번 요청으로 새 계정이 만들어졌는지")
