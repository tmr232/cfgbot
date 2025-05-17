from typing import Protocol, Self, Callable

import attrs
from atproto import client_utils

BSKY_MAX_TEXT_LENGTH = 300
MASTO_MAX_TEXT_LENGTH = 500
MASTODON_URL_LENGTH = 23


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

    def abbreviated(self, excess_length: int) -> Self:
        if self.funcdef is None or excess_length > len(self.funcdef):
            raise ValueError("Post too long regardless of funcdef length")

        funcdef = f"{self.funcdef[:-excess_length - 3]}..."
        return attrs.evolve(self, funcdef=funcdef)

    def into_bsky(self) -> client_utils.TextBuilder:
        length = masto_get_message_length(ghidra_message_template, self)
        excess_length = length - BSKY_MAX_TEXT_LENGTH
        if excess_length <= 0:
            return bsky_render(ghidra_message_template, self)

        return bsky_render(ghidra_message_template, self.abbreviated(excess_length))

    def into_mastodon(self) -> str:
        length = masto_get_message_length(ghidra_message_template, self)
        excess_length = length - MASTO_MAX_TEXT_LENGTH
        if excess_length <= 0:
            return masto_render(ghidra_message_template, self)

        return masto_render(ghidra_message_template, self.abbreviated(excess_length))


@attrs.frozen(kw_only=True)
class GithubPost:
    project: Link
    code: Link
    funcdef: str
    svgs: list[Link]

    def abbreviated(self, excess_length: int) -> Self:
        if self.funcdef is None or excess_length > len(self.funcdef):
            raise ValueError("Post too long regardless of funcdef length")

        funcdef = f"{self.funcdef[:-excess_length - 3]}..."
        return attrs.evolve(self, funcdef=funcdef)

    def into_bsky(self) -> client_utils.TextBuilder:
        length = masto_get_message_length(github_message_template, self)
        excess_length = length - BSKY_MAX_TEXT_LENGTH
        if excess_length <= 0:
            return bsky_render(github_message_template, self)

        return bsky_render(github_message_template, self.abbreviated(excess_length))

    def into_mastodon(self) -> str:
        length = masto_get_message_length(github_message_template, self)
        excess_length = length - MASTO_MAX_TEXT_LENGTH
        if excess_length <= 0:
            return masto_render(github_message_template, self)

        return masto_render(github_message_template, self.abbreviated(excess_length))


type MessagePart = str | Link | list[Link]
type MessageTemplate[T] = Callable[[T], list[MessagePart]]


def ghidra_message_template(post: GhidraPost) -> list[MessagePart]:
    parts: list[MessagePart] = []
    parts.extend(
        [
            f"Project: {post.project} {post.version}",
            "\n",
            f"File: {post.filename}",
            "\n",
            f"Address: {post.address}",
            "\n",
            "\n",
        ]
    )
    if post.funcdef:
        parts.append(f"{post.funcdef}\n\n")
    parts.append("SVG: ")
    parts.append(post.svgs)
    return parts


def github_message_template(post: GithubPost) -> list[MessagePart]:
    parts: list[MessagePart] = []
    parts.extend(
        [
            f"Project: ",
            post.project,
            "\n",
            f"File: ",
            post.code,
            "\n\n",
            f"{post.funcdef}\n\n",
            "SVG: ",
        ]
    )

    parts.append(post.svgs)
    return parts


def masto_render_list(links: list[Link]) -> str:
    return "\n" + "\n".join(f"  {link.text} {link.url}" for link in links)


def masto_link_list_length(links: list[Link]) -> int:
    return len(
        masto_render_list(
            [Link(text=link.text, url="x" * MASTODON_URL_LENGTH) for link in links]
        )
    )


def bsky_render_list(
    builder: client_utils.TextBuilder, links: list[Link]
) -> client_utils.TextBuilder:
    for i, svg_link in enumerate(links):
        if i > 0:
            builder.text(", ")
        builder.link(svg_link.text, svg_link.url)
    return builder


def masto_get_message_length[P](template: MessageTemplate[P], post: P) -> int:
    message_parts = template(post)
    total_length = 0
    for part in message_parts:
        match part:
            case str(text):
                total_length += len(text)
            case Link(text=text):
                total_length += len(text) + MASTODON_URL_LENGTH + 1
            case list(links):
                total_length += masto_link_list_length(links)
            case _:
                raise TypeError(f"Unsupported part type {type(part)}")
    return total_length


def bsky_get_message_length[P](template: MessageTemplate[P], post: P) -> int:
    message_parts = template(post)
    total_length = 0
    for part in message_parts:
        match part:
            case str(text):
                total_length += len(text)
            case Link(text=text):
                total_length += len(text)
            case list(links):
                builder = bsky_render_list(client_utils.TextBuilder(), links)
                total_length += len(builder.build_text())
            case _:
                raise TypeError(f"Unsupported part type {type(part)}")
    return total_length


def masto_render[P](template: MessageTemplate[P], post: P) -> str:
    message_parts = template(post)
    text_parts = []
    for part in message_parts:
        match part:
            case str(text):
                text_parts.append(text)
            case Link(text=text, url=url):
                text_parts.append(f"{text} {url}")
            case list(links):
                text_parts.append(masto_render_list(links))
            case _:
                raise TypeError(f"Unsupported part type {type(part)}")
    return "".join(text_parts)


def bsky_render[P](template: MessageTemplate[P], post: P) -> client_utils.TextBuilder:
    message_parts = template(post)
    builder = client_utils.TextBuilder()
    for part in message_parts:
        match part:
            case str(text):
                builder.text(text)
            case Link(text=text, url=url):
                builder.link(text, url)
            case list(links):
                bsky_render_list(builder, links)
            case _:
                raise TypeError(f"Unsupported part type {type(part)}")
    return builder
