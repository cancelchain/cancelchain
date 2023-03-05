import requests
from cancelchain.block import Block


def test_index(app, mill_block, test_client, wallet):
    with app.app_context():
        response = test_client.get('/')
        assert response.status_code == requests.codes.ok
        assert 'No chain' in str(response.data)
        m, b = mill_block(wallet)
        response = test_client.get('/')
        assert response.status_code == requests.codes.ok
        assert b.block_hash in str(response.data)


def test_chains(app, mill_block, test_client, wallet):
    with app.app_context():
        response = test_client.get('/chains')
        assert response.status_code == requests.codes.ok
        assert 'No chains' in str(response.data)
        m, b = mill_block(wallet)
        response = test_client.get('/chains')
        assert response.status_code == requests.codes.ok
        assert b.block_hash in str(response.data)


def test_block(app, mill_block, test_client, wallet):
    with app.app_context():
        response = test_client.get('/block')
        assert response.status_code == requests.codes.not_found
        m, b = mill_block(wallet)
        response = test_client.get('/block')
        assert response.status_code == requests.codes.ok
        assert b.block_hash in str(response.data)
        response = test_client.get(f'/block/{b.block_hash}')
        assert response.status_code == requests.codes.ok
        assert b.block_hash in str(response.data)


def test_transaction(app, add_chain_block, subject, test_client, wallet):
    with app.app_context():
        response = test_client.get('/transaction/foo')
        assert response.status_code == requests.codes.not_found
        c, _ = add_chain_block()
        c.to_db()
        t = c.create_support(wallet, 1, subject)
        t.seal()
        t.sign()
        b = Block()
        b.add_txn(t)
        c, _ = add_chain_block(chain=c, block=b)
        response = test_client.get(f'/transaction/{t.txid}')
        assert response.status_code == requests.codes.ok
        assert t.txid in str(response.data)
