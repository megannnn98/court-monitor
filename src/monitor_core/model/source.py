from pydantic import BaseModel


class SourceReference(BaseModel):
    external_id: str
    url: str
