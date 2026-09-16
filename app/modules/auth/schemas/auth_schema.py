from pydantic import BaseModel, EmailStr, Field

class SignupRequest(BaseModel):
    email: EmailStr=Field(..., description="The email of the user")

class LoginRequest(BaseModel):
    email: EmailStr=Field(..., description="The email of the user")

class VerifyOtpRequest(BaseModel):
    email: EmailStr=Field(..., description="The email of the user")
    otp: str=Field(..., description="The OTP of the user")

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"

