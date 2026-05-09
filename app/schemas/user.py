from pydantic import BaseModel


class UserProfile(BaseModel):
    language: str


class UserProfileUpdate(BaseModel):
    language: str
