from pydantic import BaseModel, EmailStr, Field

class RequestOtp(BaseModel):
    email: EmailStr=Field(..., description="The email of the user")


class VerifyOtpRequest(BaseModel):
    email: EmailStr=Field(..., description="The email of the user")
    otp: str=Field(..., description="The OTP of the user")
    # Browser clients leave this false: the refresh token then travels ONLY in
    # the httpOnly cookie and never appears in a response body JS can read.
    deliver_refresh_token: bool = Field(
        default=False,
        description=(
            "Native clients that cannot store cookies: include the refresh "
            "token in the response body instead of relying on the cookie"
        ),
    )

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str | None = Field(
        default=None,
        description=(
            "Only present for native clients (deliver_refresh_token=true on "
            "verify-otp, or a body-based refresh); browsers receive it as an "
            "httpOnly cookie"
        ),
    )
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str | None = Field(
        default=None,
        description=(
            "Native clients send the token here; browser clients omit it and "
            "the cookie is used instead"
        ),
    )
