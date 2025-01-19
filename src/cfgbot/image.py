from typing import Self
from xml.etree import ElementTree

import attrs
import cairosvg

BSKY_MAX_HEIGHT = 2000
BSKY_MAX_WIDTH = 2000


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
