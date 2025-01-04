import io
import os
import random
import subprocess
import tempfile
import urllib.parse
from pathlib import Path
from typing import Tuple, Self

import attrs
import cairosvg
import httpx
import orjson
import rich
import structlog
import typer
import PIL.Image
from atproto import Client, client_utils
from atproto_client.models.app.bsky.embed.defs import AspectRatio
from mastodon import Mastodon

from cfgbot import github
from cfgbot.index import Index, GithubIndex, GhidraIndex, GithubFunction

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

COLOR_SCHEMES = ["dark", "light"]

log = structlog.get_logger()

app = typer.Typer()


@attrs.frozen(kw_only=True)
class Image:
    image_bytes: bytes
    width: int
    height: int
    alt: str

    @classmethod
    def from_svg(cls, *, svg:bytes, alt:str)->Self:
        png = cairosvg.svg2png(svg, output_width=SVG_OUTPUT_WIDTH)
        img = PIL.Image.open(io.BytesIO(png))
        img.thumbnail((BSKY_MAX_WIDTH, BSKY_MAX_HEIGHT))
        result_data = io.BytesIO()
        img.save(result_data, "PNG")
        return cls(image_bytes=result_data.getvalue(), height=img.height, width=img.width, alt=alt)

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
        svg = "\n".join(f"  {svg.text} {svg.url}" for svg in self.svgs)
        return f"Project: {self.project.text} {self.project.url}\nFile: {self.code.text} {self.code.url}\n\n{self.funcdef}\n\nSVG:\n{svg}"



def choose_function_from(indices:list[Index]):
    index = random.choices(indices)
    match index.content:
        case GithubIndex(functions=functions):
            interesting_functions = [function for function in functions if function.node_count >= MINIMAL_NODE_COUNT]
            return index.content, random.choice(interesting_functions)
        case GhidraIndex(functions=functions):
            raise NotImplementedError()

def render_svg(sourcefile:Path, colors:str, function:GithubFunction):
    return subprocess.check_output(
        list(
            filter(
                None,
                [
                    "bun",
                    "run",
                    RENDER_SCRIPT,
                    str(sourcefile.absolute()),
                    function.start_position.model_dump_json(),
                    "--colors" if colors else None,
                    colors if colors else None,
                ],
            )
        )
    )


def generate_post(index_paths:list[Path], colors_schemes:list[str])->tuple[Post, list[Image]]:
    index_path:Path = random.choice(index_paths)
    index = Index(**orjson.loads(index_path.read_text())).content

    if isinstance(index, GithubIndex):
        interesting_functions = [function for function in index.functions if function.node_count >= MINIMAL_NODE_COUNT]
        function = random.choice(interesting_functions)

        response = httpx.get(github.get_raw_url(project=index.project, ref=index.ref, filename=function.filename))
        code = response.text

        with tempfile.TemporaryDirectory() as tempdir:
            codefile = Path(tempdir, code)
            codefile.write_text(code)

            images = []
            for colors in colors_schemes:
                svg = render_svg(codefile, colors, function)
                image = Image.from_svg(svg=svg, alt=f"A control-flow-graph of the function described in the post text using a {colors} color scheme.")
                images.append(image)

        line = function.start_position.row + 1
        code_url = github.get_code_url(index.project, index.ref, filename=function.filename, line=line)
        post = Post(
        project=Link(
            text=index.project, url=github.get_project_url(index.project)
        ),
        code=Link(
            text=f"{function.filename}:{line}",
            url=code_url,
        ),
        funcdef=function.funcdef,
        svgs=[
            Link(text=colors, url=render_url(code_url, colors))
            for colors in colors_schemes
        ],
        )
        return post, images
    else:
        raise NotImplementedError()



def render_url(github_link: str, colors: str) -> str:
    return f"https://tmr232.github.io/function-graph-overview/render/?github={urllib.parse.quote_plus(github_link)}&colors={colors}"


def find_github_indices()->list[Path]:
    return list(Path(__file__, "..", "indices").glob("*.json"))

@app.command()
def main():
    index_paths= find_github_indices()
    post, images = generate_post(index_paths, colors_schemes=COLOR_SCHEMES)

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
