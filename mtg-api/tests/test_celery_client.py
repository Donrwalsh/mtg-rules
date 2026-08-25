from mtg_api.celery_client import get_celery_client


def test_get_celery_client_returns_configured_instance():
    client = get_celery_client()
    assert client.conf.broker_url == "redis://redis:6379/0"
    assert client.conf.result_backend == "redis://redis:6379/0"
