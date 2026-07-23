from typing import Literal

from pydantic import BaseModel, Field


class DQRunRequest(BaseModel):
    analytical_name: str = Field(min_length=1, max_length=200)
    days: Literal[7, 14, 30] = 7
    force: bool = False
