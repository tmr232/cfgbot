import io
import os
import random
import subprocess
import urllib.parse
from pathlib import Path
from typing import Tuple

import attrs
import cairosvg
import orjson
import rich
import structlog
import typer
from PIL import Image
from atproto import Client, client_utils
from atproto_client.models.app.bsky.embed.defs import AspectRatio
from mastodon import Mastodon

BLUESKY_IDENTIFIER = os.getenv("BLUESKY_IDENTIFIER")
BLUESKY_PASSWORD = os.getenv("BLUESKY_PASSWORD")
MASTODON_ACCESS_TOKEN = os.getenv("MASTODON_ACCESS_TOKEN")
MASTODON_API_BASE_URL = os.getenv("MASTODON_API_BASE_URL")

RENDER_SCRIPT = os.getenv("CFG_RENDER_SCRIPT")
SOURCE_ROOT = os.getenv("CLONE_SOURCE_ROOT")

WEIGHT_OFFSET = 30
MINIMAL_NODE_COUNT = 7

SVG_OUTPUT_WIDTH = 2000

BSKY_MAX_HEIGHT = 2000
BSKY_MAX_WIDTH = 2000
BSKY_MAX_TEXT_LENGTH = 300

COLOR_SCHEMES = ("dark", "light")

log = structlog.get_logger()


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


@attrs.frozen(kw_only=True)
class Image:
    image_bytes: bytes
    width: int
    height: int
    alt: str


@attrs.frozen(kw_only=True)
class Link:
    text: str
    url: str


@attrs.frozen(kw_only=True)
class Post:
    project: Link
    code: Link
    funcdef: str
    svgs: list[Link]

    @staticmethod
    def _format_for_bluesky(post) -> client_utils.TextBuilder:
        text = (
            client_utils.TextBuilder()
            .text("Project: ")
            .link(post.project.text, post.project.url)
            .text(
                "\nFile: ",
            )
            .link(post.code.text, post.code.url)
            .text(f"\n\n{post.funcdef}\n\n")
            .text("SVG: ")
        )
        for i, svg_link in enumerate(post.svgs):
            if i > 0:
                text = text.text(", ")
            text = text.link(svg_link.text, svg_link.url)
        return text

    def into_bsky(self) -> client_utils.TextBuilder:
        builder = Post._format_for_bluesky(self)
        post_text = builder.build_text()
        if len(post_text) <= BSKY_MAX_TEXT_LENGTH:
            return builder

        # If the post is too long, we shorten the funcdef part by as much as necessary to fit.
        to_remove = len(post_text) - BSKY_MAX_TEXT_LENGTH
        if to_remove > len(self.funcdef):
            raise ValueError("Post too long regardless of funcdef length")

        funcdef = f"{self.funcdef[:-to_remove-3]}..."
        abbreviated = attrs.evolve(self, funcdef=funcdef)
        return Post._format_for_bluesky(abbreviated)

    def into_mastodon(self) -> str:
        svg = [f"\n  {svg.text} {svg.url}" for svg in self.svgs]
        return f"Project: {self.project.text}\nFile: {self.code.text} {self.code.url}\n\n{self.funcdef}\n\nSVG:{svg}"


@app.command()
def main():
    function, index = choose_function()

    github_code_link = f"{urllib.parse.urljoin(index.github_url, function.file)}#L{function.start_position.row + 1}"

    post = Post(
        project=Link(
            text=index.project_name, url=f"https://github.com/{index.project_name}"
        ),
        code=Link(
            text=f"{index.path_extra}/{function.file.replace("\\", "/")}:{function.start_position.row+1}",
            url=github_code_link,
        ),
        funcdef=function.func_def,
        svgs=[
            Link(text=colors, url=render_url(github_code_link, colors))
            for colors in COLOR_SCHEMES
        ],
    )

    rich.print(function)
    sourcepath = Path(
        SOURCE_ROOT, index.root.replace("\\", "/"), function.file.replace("\\", "/")
    )

    images = render_images(function, sourcepath)

    failed = False
    try:
        post_to_bluesky(post, images)
    except Exception:
        failed = True
        log.exception("Failed posting to bluesky", post=post)

    try:
        post_to_mastodon(post, images)
    except Exception:
        failed = True
        log.exception("Failed posting to Mastodon", post=post)

    if failed:
        raise RuntimeError("Failed posting to at least one platform")


def render_images(function: Func, sourcepath: Path) -> list[Image]:
    def _impl():
        for colors in COLOR_SCHEMES:
            image_bytes, (width, height) = render(
                function, sourcepath, get_color_scheme(colors)
            )
            alt = f"A control-flow-graph of the function described in the post text using a {colors} color scheme."
            yield Image(image_bytes=image_bytes, width=width, height=height, alt=alt)

    return list(_impl())


def post_to_bluesky(post: Post, images: list[Image]):
    client = Client()
    client.login(BLUESKY_IDENTIFIER, BLUESKY_PASSWORD)

    client.send_images(
        post.into_bsky(),
        images=[image.image_bytes for image in images],
        image_alts=[image.alt for image in images],
        image_aspect_ratios=[
            AspectRatio(height=image.height, width=image.width) for image in images
        ],
    )


def post_to_mastodon(post: Post, images: list[Image]):
    mastodon = Mastodon(
        access_token=MASTODON_ACCESS_TOKEN, api_base_url=MASTODON_API_BASE_URL
    )
    media = [
        mastodon.media_post(
            image.image_bytes, mime_type="image/png", description=image.alt
        )
        for image in images
    ]
    mastodon.status_post(post.into_mastodon(), media_ids=media)
