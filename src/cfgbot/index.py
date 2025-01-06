from pathlib import Path
from typing import Literal, Union, Annotated

import orjson
import rich
from pydantic import BaseModel, PositiveInt, Field, StringConstraints

HexString = Annotated[str, StringConstraints(pattern=r"[0-9a-f]+")]


class GhidraFunction(BaseModel):
    """Describes a single function from the binary.

    The JSON file containing the graph should match the `address` field.
    """

    #: The address of the function in the binary
    address: HexString
    #: Demangled function name, if exists, including signature info
    name: str | None = None
    #: Number of nodes in the graph
    node_count: int


class GhidraIndex(BaseModel):
    """Index of a Ghidra export

    Should sit in the same directory as the exported data.
    Describes project metadata, and lists all the functions.
    """

    #: Used for discriminated union
    index_type: Literal["ghidra"]
    #: Name of the project the binary is from
    project: str
    #: Name of the binary file
    filename: str
    #: Version of the file or project
    version: str | None = None
    #: Hash of the file
    sha256: str
    #: Functions in this binary
    functions: list[GhidraFunction]
    #: Extra info
    extra: dict[str, str] | None = None


class Position(BaseModel):
    row: int
    column: int


class GithubFunction(BaseModel):
    #: The function definition
    funcdef: str
    #: Number of nodes in the graph
    node_count: PositiveInt
    #: Path in the repo to the file containing the function
    filename: str
    #: Start position of the function node in tree-sitter
    start_position: Position


class GithubIndex(BaseModel):
    #: Used for discriminated union
    index_type: Literal["github"]
    #: user/project of the GitHub project
    project: str
    #: git ref
    ref: str
    #: All the functions to choose from
    functions: list[GithubFunction]


class Index(BaseModel):
    #: Version of the model, to allow later modification
    version: Literal[1]
    #: The content of the index
    content: Union[GhidraIndex, GithubIndex] = Field(discriminator="index_type")


if __name__ == "__main__":
    index_data = orjson.loads(
        Path(
            r"C:\Code\github.com\tmr232\cfgbot\src\cfgbot\indices\python.json"
        ).read_text()
    )
    Index(**index_data)
