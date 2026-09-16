from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

class CreateUser(BaseModel):
    email:EmailStr = Field(
        max_length=50,
    )
    first_name:str|None = Field(
        default= None,
        min_length=1,
        max_length=20,
    )
    last_name:str|None = Field(
        default= None,
        min_length=1,
        max_length=20,
    )


class UserPublic(BaseModel):
    id: UUID
    email: EmailStr
    first_name: str | None = None
    last_name: str | None = None

    model_config = {"from_attributes": True}