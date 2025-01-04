from typing import Literal, Union, Annotated

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
    name: str | None
    #: Number of nodes in the graph
    node_count: PositiveInt


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
    version: str | None
    #: The source repo for the binary
    source_repo: str | None
    #: Vendor website address
    vendor_site: str | None
    #: Functions in this binary
    functions: list[GhidraFunction]


class GithubFunction(BaseModel):
    #: The function definition
    funcdef: str
    #: Number of nodes in the graph
    node_count: PositiveInt
    #: Path in the repo to the file containing the function
    filename: str
    #: Line number where the function is defined
    line: PositiveInt


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


rich.print(Index.model_json_schema())