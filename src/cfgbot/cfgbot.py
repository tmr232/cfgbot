import os
import random
import subprocess
import tempfile
import urllib.parse
from pathlib import Path
from typing import Self, Protocol
from xml.etree import ElementTree

import attrs
import cairosvg
import httpx
import orjson
import rich
import stamina
import structlog
import typer
from atproto import Client, client_utils
from atproto_client.exceptions import InvokeTimeoutError
from atproto_client.models.app.bsky.embed.defs import AspectRatio
from mastodon import Mastodon

from cfgbot import github
from cfgbot.index import Index, GithubIndex, GhidraIndex, GithubFunction, GhidraFunction

BLUESKY_IDENTIFIER = os.getenv("BLUESKY_IDENTIFIER")
BLUESKY_PASSWORD = os.getenv("BLUESKY_PASSWORD")
MASTODON_ACCESS_TOKEN = os.getenv("MASTODON_ACCESS_TOKEN")
MASTODON_API_BASE_URL = os.getenv("MASTODON_API_BASE_URL")

FUNCTION_RENDER_SCRIPT = os.getenv("FUNCTION_RENDER_SCRIPT")
GRAPH_RENDER_SCRIPT = os.getenv("GRAPH_RENDER_SCRIPT")

GHIDRA_EXPORT_ROOT = os.getenv("GHIDRA_EXPORT_ROOT")
GHIDRA_RAW_URL_BASE = os.getenv("GHIDRA_RAW_URL_BASE")

WEIGHT_OFFSET = 30
MINIMAL_NODE_COUNT = 7

SVG_OUTPUT_WIDTH = 2000

BSKY_MAX_HEIGHT = 2000
BSKY_MAX_WIDTH = 2000
BSKY_MAX_TEXT_LENGTH = 300

COLOR_SCHEMES = ["dark", "light"]

log = structlog.get_logger()

app = typer.Typer()


@attrs.frozen(kw_only=True)
class Size:
    width: int
    height: int


def _parse_svg_length(value: str) -> int:
    return int(value.rstrip("pt"))


def get_svg_size(svg: bytes):
    root = ElementTree.XML(svg)
    return Size(
        height=_parse_svg_length(root.attrib["height"]),
        width=_parse_svg_length(root.attrib["width"]),
    )


@attrs.frozen(kw_only=True)
class Image:
    image_bytes: bytes
    width: int
    height: int
    alt: str

    @classmethod
    def from_svg(cls, *, svg: bytes, alt: str) -> Self:
        svg_size = get_svg_size(svg)
        if svg_size.height > svg_size.width:
            png = cairosvg.svg2png(svg, output_height=BSKY_MAX_HEIGHT)
        else:
            png = cairosvg.svg2png(svg, output_width=BSKY_MAX_WIDTH)
        return cls(
            image_bytes=png,
            height=svg_size.height,
            width=svg_size.width,
            alt=alt,
        )


@attrs.frozen(kw_only=True)
class Link:
    text: str
    url: str


class Post(Protocol):
    def into_bsky(self) -> client_utils.TextBuilder: ...
    def into_mastodon(self) -> str: ...


@attrs.frozen(kw_only=True)
class GhidraPost:
    project: str
    version: str
    filename: str
    address: str
    funcdef: str | None
    svgs: list[Link]

    @staticmethod
    def _format_for_bluesky(post) -> client_utils.TextBuilder:
        text = (
            client_utils.TextBuilder()
            .text(f"Project: {post.project} {post.version}")
            .text("\n")
            .text(f"File: {post.filename}")
            .text("\n")
            .text(f"Address: {post.address}")
            .text("\n")
            .text("\n")
        )
        if post.funcdef:
            text = text.text(f"{post.funcdef}").text("\n").text("\n")
        text = text.text("SVG: ")
        for i, svg_link in enumerate(post.svgs):
            if i > 0:
                text = text.text(", ")
            text = text.link(svg_link.text, svg_link.url)
        return text

    def into_bsky(self) -> client_utils.TextBuilder:
        builder = self._format_for_bluesky(self)
        post_text = builder.build_text()
        if len(post_text) <= BSKY_MAX_TEXT_LENGTH:
            return builder

        # If the post is too long, we shorten the funcdef part by as much as necessary to fit.
        to_remove = len(post_text) - BSKY_MAX_TEXT_LENGTH
        if self.funcdef is None or to_remove > len(self.funcdef):
            raise ValueError("Post too long regardless of funcdef length")

        funcdef = f"{self.funcdef[:-to_remove-3]}..."
        abbreviated = attrs.evolve(self, funcdef=funcdef)
        return self._format_for_bluesky(abbreviated)

    def into_mastodon(self) -> str:
        svg = "\n".join(f"  {svg.text} {svg.url}" for svg in self.svgs)
        return f"Project: {self.project} {self.version}\nFile: {self.filename}\nAddress: {self.address}\n\n{self.funcdef}\n\nSVG:\n{svg}"


@attrs.frozen(kw_only=True)
class GithubPost:
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
        builder = self._format_for_bluesky(self)
        post_text = builder.build_text()
        if len(post_text) <= BSKY_MAX_TEXT_LENGTH:
            return builder

        # If the post is too long, we shorten the funcdef part by as much as necessary to fit.
        to_remove = len(post_text) - BSKY_MAX_TEXT_LENGTH
        if to_remove > len(self.funcdef):
            raise ValueError("Post too long regardless of funcdef length")

        funcdef = f"{self.funcdef[:-to_remove-3]}..."
        abbreviated = attrs.evolve(self, funcdef=funcdef)
        return self._format_for_bluesky(abbreviated)

    def into_mastodon(self) -> str:
        svg = "\n".join(f"  {svg.text} {svg.url}" for svg in self.svgs)
        return f"Project: {self.project.text} {self.project.url}\nFile: {self.code.text} {self.code.url}\n\n{self.funcdef}\n\nSVG:\n{svg}"


def choose_function_from(indices: list[Index]):
    index = random.choices(indices)
    match index.content:
        case GithubIndex(functions=functions):
            interesting_functions = [
                function
                for function in functions
                if function.node_count >= MINIMAL_NODE_COUNT
            ]
            return index.content, random.choice(interesting_functions)
        case GhidraIndex(functions=functions):
            raise NotImplementedError()


def render_function_svg(
    sourcefile: Path, colors: str, function: GithubFunction
) -> bytes:
    return subprocess.check_output(
        list(
            filter(
                None,
                [
                    "bun",
                    "run",
                    FUNCTION_RENDER_SCRIPT,
                    "--colors" if colors else None,
                    colors if colors else None,
                    str(sourcefile.absolute()),
                    function.start_position.model_dump_json(),
                ],
            )
        )
    )


def render_graph_svg(graph_file: Path, colors: str) -> bytes:
    return subprocess.check_output(
        list(
            filter(
                None,
                [
                    "bun",
                    "run",
                    GRAPH_RENDER_SCRIPT,
                    "--colors" if colors else None,
                    colors if colors else None,
                    str(graph_file.absolute()),
                ],
            )
        )
    )


@attrs.frozen(kw_only=True)
class IndexLocator:
    path: Path
    repo_base: Path
    raw_url_base: str


def generate_post(
    index_paths: list[Path | IndexLocator], colors_schemes: list[str]
) -> tuple[Post, list[Image]]:
    index_path: Path | IndexLocator = random.choice(index_paths)
    log.info("Index selected", index=index_path)
    match index_path:
        case Path():
            index = Index(**orjson.loads(index_path.read_text())).content
        case IndexLocator():
            index = Index(**orjson.loads(index_path.path.read_text())).content
        case _:
            raise TypeError(f"expected Path or IndexLocator, found {type(index_path)}")

    if isinstance(index, GithubIndex):
        return generate_github_post(index, colors_schemes)
    else:
        return generate_ghidra_post(index_path, index, colors_schemes)


def generate_ghidra_post(
    index_locator: IndexLocator, index: GhidraIndex, colors_schemes: list[str]
) -> tuple[GhidraPost, list[Image]]:
    interesting_functions = [
        function
        for function in index.functions
        if function.node_count >= MINIMAL_NODE_COUNT
    ]
    function: GhidraFunction = random.choice(interesting_functions)
    index_dir = index_locator.path.parent
    graph_path = index_dir / f"{function.address}.json"
    images = []
    for colors in colors_schemes:
        svg = render_graph_svg(graph_path, colors)
        image = Image.from_svg(
            svg=svg,
            alt=f"A control-flow-graph of the function described in the post text using a {colors} color scheme.",
        )
        images.append(image)

    graph_url = f"{index_locator.raw_url_base}/{graph_path.relative_to(index_locator.repo_base)!s}".replace(
        "\\", "/"
    )
    return GhidraPost(
        project=index.project,
        version=index.version,
        filename=index.filename,
        address=function.address,
        funcdef=function.name,
        svgs=[
            Link(text=colors, url=render_graph_url(graph_url, colors))
            for colors in colors_schemes
        ],
    ), images


@stamina.retry(on=httpx.ReadTimeout)
def fetch_github_function(function: GithubFunction, index: GithubIndex):
    return httpx.get(
        github.get_raw_url(
            project=index.project, ref=index.ref, filename=function.filename
        )
    ).text


def generate_github_post(
    index: GithubIndex, colors_schemes: list[str]
) -> tuple[GithubPost, list[Image]]:
    interesting_functions = [
        function
        for function in index.functions
        if function.node_count >= MINIMAL_NODE_COUNT
    ]
    function: GithubFunction = random.choice(interesting_functions)
    code = fetch_github_function(function, index)
    with tempfile.TemporaryDirectory() as tempdir:
        codefile = Path(tempdir, Path(function.filename).name)
        codefile.write_text(code)

        images = []
        for colors in colors_schemes:
            svg = render_function_svg(codefile, colors, function)
            image = Image.from_svg(
                svg=svg,
                alt=f"A control-flow-graph of the function described in the post text using a {colors} color scheme.",
            )
            images.append(image)
    line = function.start_position.row + 1
    code_url = github.get_code_url(
        index.project, index.ref, filename=function.filename, line=line
    )
    post = GithubPost(
        project=Link(text=index.project, url=github.get_project_url(index.project)),
        code=Link(
            text=f"{function.filename}:{line}",
            url=code_url,
        ),
        funcdef=function.funcdef,
        svgs=[
            Link(text=colors, url=render_github_url(code_url, colors))
            for colors in colors_schemes
        ],
    )
    return post, images


def render_github_url(github_link: str, colors: str) -> str:
    return f"https://tmr232.github.io/function-graph-overview/render/?github={urllib.parse.quote_plus(github_link)}&colors={colors}"


def render_graph_url(ghidra_link: str, colors: str) -> str:
    return f"https://tmr232.github.io/function-graph-overview/render/?graph={urllib.parse.quote_plus(ghidra_link)}&colors={colors}"


def find_github_indices() -> list[Path | IndexLocator]:
    ghidra_index_locators = []
    for index_path in Path(GHIDRA_EXPORT_ROOT).rglob("**/index.json"):
        ghidra_index_locators.append(
            IndexLocator(
                path=index_path,
                repo_base=Path(GHIDRA_EXPORT_ROOT),
                raw_url_base=GHIDRA_RAW_URL_BASE,
            )
        )
    github_code_indices = list((Path(__file__).parent / "indices").glob("*.json"))
    return ghidra_index_locators + github_code_indices


@app.command()
def main():
    log.info("Loading indices")
    index_paths = find_github_indices()
    log.info("Indices found", indices=index_paths)
    log.info("Generating post")
    post, images = generate_post(index_paths, colors_schemes=COLOR_SCHEMES)
    rich.print(post)
    failed = False
    try:
        log.info("Posting to Bluesky")
        post_to_bluesky(post, images)
    except Exception:
        failed = True
        log.exception("Failed posting to bluesky", post=post)

    try:
        log.info("Posting to Mastodon")
        post_to_mastodon(post, images)
    except Exception:
        failed = True
        log.exception("Failed posting to Mastodon", post=post)

    if failed:
        raise RuntimeError("Failed posting to at least one platform")

    log.info("Posting successful")


from atproto_client.request import Request


class MyRequest(Request):
    def __init__(self):
        super().__init__()
        self._client = httpx.Client(timeout=10.0, follow_redirects=True)


def post_to_bluesky(post: Post, images: list[Image]):
    client = Client(request=MyRequest())
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
