from app.connectors.dataset import DatasetConnector
from app.schemas import SearchRequest, normalize_domain, normalize_name


def test_normalization_handles_punctuation_and_www():
    assert normalize_name("  Café & Co.! ") == "caf co"
    assert normalize_domain("https://WWW.Example.com/path") == "example.com"


def test_synthetic_dataset_is_filtered_and_labelled():
    connector = DatasetConnector()
    items = connector.search(
        SearchRequest(query="example", country="IN", max_results=10),
        {"path": "app/fixtures/synthetic_businesses.json"},
    )
    assert len(items) == 1
    assert items[0].name == "Example Bakery"
    assert items[0].synthetic is True
