from app.connectors.dataset import DatasetConnector
from app.connectors.openstreetmap import OpenStreetMapConnector

CONNECTORS = {
    "dataset": DatasetConnector(),
    "openstreetmap": OpenStreetMapConnector(),
}
