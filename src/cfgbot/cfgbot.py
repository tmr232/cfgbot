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
MINIMAL_NODE_COUNT = 7

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
    project_name: str  #: Project name on GitHub
    name: str  #: Name of the project being indexed
    index_name: str  #: Index filename
    github_url: str  #: URL to the code in GitHub
    root: str  #: Source root path, under SOURCE_ROOT
    path_extra: str  #: Extra bit of path to add before filenames when posting


INDICES = [
    Index(
        project_name="python/cpython",
        name="Python/CPython/Lib",
        index_name="cpython_lib.json",
        github_url="https://github.com/python/cpython/blob/2bd5a7ab0f4a1f65ab8043001bd6e8416c5079bd/Lib/",
        root="CPython/Lib",
        path_extra="Lib",
    ),
    Index(
        project_name="python/cpython",
        name="Python/CPython/Python",
        index_name="cpython_python.json",
        github_url="https://github.com/python/cpython/blob/2bd5a7ab0f4a1f65ab8043001bd6e8416c5079bd/Python/",
        root="CPython/Python",
        path_extra="Python",
    ),
]

app = typer.Typer()


def load_index(index_name: str):
    text = (Path(__file__).parent / "indices" / index_name).read_text()
    return orjson.loads(text)


def choose_function() -> Tuple[Func, Index]:
    index = random.choice(INDICES)
    data = load_index(index.index_name)
    data = [entry for entry in data if entry["nodeCount"] > MINIMAL_NODE_COUNT]
    # We're adding a bit to the weights to even things out a bit, and make the node count less significant.
    entry = random.choices(
        data, [entry["nodeCount"] + WEIGHT_OFFSET for entry in data]
    )[0]
    return Func(**entry), index


def render(func: Func, sourcefile: Path, colors: Path | None = None):
    rich.print(sourcefile.absolute())
    if not sourcefile.exists():
        raise RuntimeError(f"Missing source file! {sourcefile.absolute()}")
    svg = subprocess.check_output(
        list(
            filter(
                None,
                [
                    "bun",
                    "run",
                    RENDER_SCRIPT,
                    str(sourcefile.absolute()),
                    orjson.dumps(attrs.asdict(func.start_position)),
                    "--colors" if colors else None,
                    str(os.path.normpath(colors.absolute())) if colors else None,
                ],
            )
        )
    )
    png = cairosvg.svg2png(svg, output_width=SVG_OUTPUT_WIDTH)
    img = Image.open(io.BytesIO(png))
    img.thumbnail((BSKY_MAX_WIDTH, BSKY_MAX_HEIGHT))
    result_data = io.BytesIO()
    img.save(result_data, "PNG")
    return result_data.getvalue(), (img.width, img.height)


def get_color_scheme(name: str) -> Path:
    return Path(__file__, "..", "color-schemes", f"{name}.json")


def render_url(github_link: str, colors: str) -> str:
    return f"https://tmr232.github.io/function-graph-overview/render/?github={urllib.parse.quote_plus(github_link)}&colors={colors}"


@app.command()
def main():
    function, index = choose_function()
    rich.print(function)
    sourcepath = Path(
        SOURCE_ROOT, index.root.replace("\\", "/"), function.file.replace("\\", "/")
    )
    images = []
    image_alts = []
    image_aspect_ratios = []
    for scheme_name in ("dark", "light"):
        image_bytes, (width, height) = render(
            function, sourcepath, get_color_scheme(scheme_name)
        )
        images.append(image_bytes)
        image_alts.append(
            f"A control-flow-graph of the function described in the post text using a {scheme_name} color scheme."
        )
        image_aspect_ratios.append(AspectRatio(height=height, width=width))

    client = Client()
    client.login(IDENTIFIER, PASSWORD)

    github_code_link = f"{urllib.parse.urljoin(index.github_url, function.file)}#L{function.start_position.row + 1}"
    text = (
        client_utils.TextBuilder()
        .text("Project: ")
        .link(index.project_name, f"https://github.com/{index.project_name}")
        .text(
            "\nFile: ",
        )
        .link(
            f"{index.path_extra}/{function.file.replace("\\", "/")}:{function.start_position.row+1}",
            github_code_link,
        )
        .text(f"\n\n{function.func_def}\n\n")
        .text("SVG: ")
        .link(
            "dark",
            render_url(github_code_link, "dark"),
        )
        .text(", ")
        .link(
            "light",
            render_url(github_code_link, "light"),
        )
    )

    client.send_images(
        text, images, image_alts, image_aspect_ratios=image_aspect_ratios
    )
