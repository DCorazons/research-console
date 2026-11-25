import re

import pytest
from fastapi.testclient import TestClient

from console import db, web


@pytest.fixture
def client():
    with TestClient(web.app) as test_client:
        yield test_client


def login(client, password):
    return client.post('/login', data={'password': password}, follow_redirects=False)


def csrf_of(client):
    page = client.get('/plans').text
    return re.search(r'name="csrf" value="([^"]+)"', page).group(1)


def test_pages_need_a_login(client):
    response = client.get('/', follow_redirects=False)
    assert response.status_code == 303 and response.headers['location'] == '/login'
    assert client.get('/export/runs.csv', follow_redirects=False).status_code == 303


def test_wrong_password(client):
    response = login(client, 'nope')
    assert response.status_code == 200 and 'Password salah' in response.text


def test_view_role_reads_but_cannot_control(client):
    assert login(client, 'view-secret').status_code == 303
    for path in ('/', '/partials/status', '/plans?blocks=1', '/runs', '/runs?plan=&block=1&impl=&kind=&scenario=&status=',
                 '/plans?blocks=1&first_block=', '/results', '/export/runs.csv',
                 '/export/endpoints.csv', '/export/resources.csv', '/export/aggregate.csv'):
        assert client.get(path).status_code == 200, path
    assert 'Masukkan ke antrean' not in client.get('/plans').text
    response = client.post('/control/start', data={'csrf': 'x'}, follow_redirects=False)
    assert response.status_code == 403


def test_control_role_creates_a_plan_and_starts_the_queue(client):
    assert login(client, 'control-secret').status_code == 303
    token = csrf_of(client)

    assert client.post('/control/start', data={'csrf': 'wrong'}).status_code == 400

    response = client.post('/plans', data={'csrf': token, 'blocks': 1, 'first_block': 1, 'suite': 'web',
                                            'stress': 1, 'note': 'uji'}, follow_redirects=False)
    assert response.status_code == 303
    page = client.get(response.headers['location']).text
    assert page.count('/runs/') >= 41

    response = client.post('/control/start', data={'csrf': token}, follow_redirects=False)
    assert response.status_code == 303
    conn = db.connect()
    assert not db.is_paused(conn)
    run = db.next_queued(conn)
    assert run['label'].endswith('-ar-single-a1')

    assert client.post(f'/runs/{run["id"]}/cancel', data={'csrf': token}).status_code == 200
    assert conn.execute('SELECT status FROM runs WHERE id = ?', (run['id'],)).fetchone()[0] == 'cancelled'
    assert client.post(f'/runs/{run["id"]}/retry', data={'csrf': token}).status_code == 200
    assert db.next_queued(conn)['attempt'] == 2
    assert client.get(f'/runs/{run["id"]}').status_code == 200
    db.set_control(conn, 'paused', '1')
    conn.close()
