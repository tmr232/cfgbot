from cfgbot.message import (
    GhidraPost,
    Link,
    masto_render,
    ghidra_message_template,
    bsky_render,
    GithubPost,
    github_message_template,
    bsky_get_message_length,
)


def test_thing():
    post = GhidraPost(
        project="project",
        version="version",
        filename="filename",
        address="address",
        funcdef="funcdef",
        svgs=[Link(text="dark", url="url"), Link(text="light", url="url")],
    )

    print(masto_render(ghidra_message_template, post))
    print(bsky_render(ghidra_message_template, post).build_text())


def test_other_thing():
    post = GithubPost(
        project=Link(text="project", url="https://example.com"),
        code=Link(text="code", url="https://example.com"),
        funcdef="funcdef",
        svgs=[Link(text="dark", url="url"), Link(text="light", url="url")],
    )

    print(masto_render(github_message_template, post))
    print(bsky_render(github_message_template, post).build_text())

    assert bsky_get_message_length(github_message_template, post) == len(
        bsky_render(github_message_template, post).build_text()
    )


def test_bsky_get_length_text():
    def template(text: str):
        return [text]

    post = "Hello, World!"
    assert bsky_get_message_length(template, post) == len(
        bsky_render(template, post).build_text()
    )


def test_bsky_get_length_link():
    def template(link: Link):
        return [link]

    post = Link(text="Hello, World!", url="https://example.com")
    assert bsky_get_message_length(template, post) == len(
        bsky_render(template, post).build_text()
    )
