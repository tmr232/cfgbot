from cfgbot.message import (
    GhidraPost,
    Link,
    masto_render,
    ghidra_message_template,
    bsky_render,
    GithubPost,
    github_message_template,
    bsky_get_message_length, masto_get_message_length,
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


def test_github_masto_abbreviate():
    post = GithubPost(
        project=Link(text="project", url="https://example.com"),
        code=Link(text="code", url="https://example.com"),
        funcdef="funcdef"*200,
        svgs=[Link(text="dark", url="url"), Link(text="light", url="url")],
    )

    print(post.into_mastodon())


def test_github_bsky_abbreviate():
    post = GithubPost(
        project=Link(text="project", url="https://example.com"),
        code=Link(text="code", url="https://example.com"),
        funcdef="funcdef"*200,
        svgs=[Link(text="dark", url="url"), Link(text="light", url="url")],
    )

    print(post.into_bsky().build_text())


def test_failed_post():
    post = GithubPost(project=Link(text='python/cpython', url='https://github.com/python/cpython'),
               code=Link(text='Lib/idlelib/search.py:77',
                         url='https://github.com/python/cpython/blob/2bd5a7ab0f4a1f65ab8043001bd6e8416c5079bd/Lib/idlelib/search.py#L77'),
               funcdef='def find_again(self, text):', svgs=[Link(text='dark',
                                                                 url='https://tmr232.github.io/function-graph-overview/render/?github=https%3A%2F%2Fgithub.com%2Fpython%2Fcpython%2Fblob%2F2bd5a7ab0f4a1f65ab8043001bd6e8416c5079bd%2FLib%2Fidlelib%2Fsearch.py%23L77&colors=dark'),
                                                            Link(text='light',
                                                                 url='https://tmr232.github.io/function-graph-overview/render/?github=https%3A%2F%2Fgithub.com%2Fpython%2Fcpython%2Fblob%2F2bd5a7ab0f4a1f65ab8043001bd6e8416c5079bd%2FLib%2Fidlelib%2Fsearch.py%23L77&colors=light')])
    print(masto_render(github_message_template, post))
    print(masto_get_message_length(github_message_template, post))
    print(post.into_mastodon())
    print(post.into_bsky().build_text())