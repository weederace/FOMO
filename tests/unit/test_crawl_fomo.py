from scripts.crawl_fomo import PageParser, clean_url


def test_crawler_removes_query_and_disallowed_paths():
    assert clean_url("/prices?token=secret", "https://fomo.family/") == "https://fomo.family/prices"
    assert clean_url("/export-key", "https://fomo.family/") is None


def test_page_parser_extracts_public_metadata_and_links():
    parser = PageParser("https://fomo.family/")
    parser.feed('<title>Public FOMO</title><meta name="description" content="Trading"><a href="/prices">Prices</a>')
    assert parser.title == "Public FOMO"
    assert "/prices" in parser.links
