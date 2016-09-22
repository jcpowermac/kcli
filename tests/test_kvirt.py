import pytest
from kvirt import Kvirt


@pytest.fixture
def getk():
    host = '127.0.0.1'
    k = Kvirt(host)
    return k


def test_list():
    k = getk()
    klist = k.list()
    assert klist is not None


def test_create():
    k = getk()
    k.create('test', numcpus=1, memory=512, pool='vms', nets=['private1'])
    status = k.status('test')
    assert status is not None


def test_delete():
    k = getk()
    k.delete('test')
    status = k.status('test')
    assert status is None
