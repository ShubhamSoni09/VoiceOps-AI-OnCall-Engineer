from app.integrations.github.user_repo import normalize_repo_slug, repo_dir_name


def test_normalize_repo_slug():
    assert normalize_repo_slug("https://github.com/acme/app.git") == "acme/app"
    assert normalize_repo_slug("acme/app") == "acme/app"


def test_repo_dir_name():
    assert repo_dir_name("acme/checkout-api") == "acme__checkout-api"
