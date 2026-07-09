import urllib.parse

from bot.imagine import build_image_url


def test_build_image_url_encodes_prompt_and_appends_style():
    url = build_image_url("two cups of coffee")
    base, _, query = url.partition("?")
    # Prompt lives in the path segment, percent-encoded.
    path = base.rsplit("/", 1)[1]
    decoded = urllib.parse.unquote(path)
    assert decoded.startswith("two cups of coffee, ")
    assert "romantic style" in decoded  # house style is always appended


def test_build_image_url_includes_sizing_and_model():
    url = build_image_url("a sunset")
    params = urllib.parse.parse_qs(url.partition("?")[2])
    assert params["model"] == ["flux"]
    assert params["width"] == ["1024"]
    assert params["height"] == ["1024"]
    assert params["nologo"] == ["true"]


def test_build_image_url_seed_varies_output():
    with_seed = build_image_url("a rose", seed=99)
    without_seed = build_image_url("a rose")
    assert "seed=99" in with_seed
    assert "seed=" not in without_seed


def test_build_image_url_encodes_special_characters():
    # Slashes / ampersands in the description must not break the URL path.
    url = build_image_url("cats & dogs / together")
    path = url.partition("?")[0].rsplit("/", 1)[1]
    # No raw slash or ampersand leaks into the path segment.
    assert "/" not in path
    assert "&" not in path
    assert urllib.parse.unquote(path).startswith("cats & dogs / together")
