from pydantic import BaseModel, EmailStr, Field

class RequestOtp(BaseModel):
    email: EmailStr=Field(..., description="The email of the user")


class VerifyOtpRequest(BaseModel):
    email: EmailStr=Field(..., description="The email of the user")
    otp: str=Field(..., description="The OTP of the user")

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., description="The refresh token to rotate or revoke")

