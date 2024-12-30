import io
import random
import subprocess
from pathlib import Path
from typing import Tuple
from PIL import Image
import rich
import typer
from atproto import Client, client_utils
import os
import attrs
import orjson
import cairosvg
from atproto_client.models.app.bsky.embed.defs import AspectRatio
import urllib.parse

IDENTIFIER = os.getenv("BLUESKY_IDENTIFIER")
PASSWORD = os.getenv("BLUESKY_PASSWORD")

RENDER_SCRIPT = os.getenv("CFG_RENDER_SCRIPT")
SOURCE_ROOT = os.getenv("CLONE_SOURCE_ROOT")

WEIGHT_OFFSET = 30
SVG_OUTPUT_WIDTH = 2000
BSKY_MAX_HEIGHT = 2000
BSKY_MAX_WIDTH = 2000


@attrs.frozen
class Point:
    row: int
    column: int


@attrs.frozen
class Func:
    file: str
    start_index: int = attrs.field(alias="startIndex")
    node_count: int = attrs.field(alias="nodeCount")
    func_def: str = attrs.field(alias="funcDef")
    start_position: Point = attrs.field(
        alias="startPosition", converter=lambda x: Point(**x)
    )


@attrs.frozen(kw_only=True)
class Index:
    name: str  #: Name of the project being indexed
    index_name: str  #: Index filename
    github_url: str  #: URL to the code in GitHub
    root: str  #: Root path, under SOURCE_ROOT


INDICES = [
    Index(
        name="Python/CPython/Lib",
        index_name="cpython_lib.json",
        github_url="https://github.com/python/cpython/blob/2bd5a7ab0f4a1f65ab8043001bd6e8416c5079bd/Lib/",
        root="CPython/Lib",
    ),
    Index(
        name="Python/CPython/Python",
        index_name="cpython_python.json",
        github_url="https://github.com/python/cpython/blob/2bd5a7ab0f4a1f65ab8043001bd6e8416c5079bd/Python/",
        root="CPython/Python",
    ),
]

app = typer.Typer()


def load_index(index_name: str):
    text = (Path(__file__).parent / "indices" / index_name).read_text()
    return orjson.loads(text)


def choose_function() -> Tuple[Func, Index]:
    index = random.choice(INDICES)
    data = load_index(index.index_name)
    # We're adding a bit to the weights to even things out a bit, and make the node count less significant.
    entry = random.choices(
        data, [entry["nodeCount"] + WEIGHT_OFFSET for entry in data]
    )[0]
    return Func(**entry), index


def render(func: Func, sourcefile: Path):
    rich.print(sourcefile.absolute())
    if not sourcefile.exists():
        raise RuntimeError(f"Missing source file! {sourcefile.absolute()}")
    svg = subprocess.check_output(
        [
            "bun",
            "run",
            RENDER_SCRIPT,
            str(sourcefile.absolute()),
            orjson.dumps(attrs.asdict(func.start_position)),
        ]
    )
    png = cairosvg.svg2png(svg, output_width=SVG_OUTPUT_WIDTH)
    img = Image.open(io.BytesIO(png))
    img.thumbnail((BSKY_MAX_WIDTH, BSKY_MAX_HEIGHT))
    result_data = io.BytesIO()
    img.save(result_data, "PNG")
    return result_data.getvalue(), (img.width, img.height)


@app.command()
def main():
    function, index = choose_function()
    rich.print(function)
    image_bytes, (width, height) = render(
        function,
        Path(
            SOURCE_ROOT, index.root.replace("\\", "/"), function.file.replace("\\", "/")
        ),
    )

    title = f"{index.name}/{Path(function.file).as_posix()}:{function.start_position.row+1}:{function.func_def}"
    link = f"{urllib.parse.urljoin(index.github_url, function.file)}#L{function.start_position.row+1}"

    client = Client()
    client.login(IDENTIFIER, PASSWORD)

    text = (
        client_utils.TextBuilder()
        .text(title)
        .text("\n\n")
        .link(
            "jump-to-source",
            link,
        )
    )
    client.send_image(
        text,
        image_bytes,
        image_alt="A control-flow-graph of the function described in the post text.",
        image_aspect_ratio=AspectRatio(height=height, width=width),
    )
