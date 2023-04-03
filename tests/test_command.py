import os
from tempfile import NamedTemporaryFile, TemporaryDirectory

from cancelchain.chain import CURMUDGEON_PER_GRUMBLE, REWARD
from cancelchain.wallet import Wallet

REWARD_CCG = int(REWARD / CURMUDGEON_PER_GRUMBLE)
SUBJECT_CCG = 2


def get_wallet_file(address, walletdir=None, app=None):
    if walletdir is None and app is not None:
        walletdir = app.config.get('WALLET_DIR')
    fn = f'{address}.pem'
    if walletdir:
        fn = os.path.join(walletdir, fn)
    return fn


def test_init(app, runner):
    with app.app_context():
        result = runner.invoke(args=['init'])
        assert 'Initialized the database.' in result.output


# def test_sync(
#     app, remote_app, remote_requests_proxy, runner, remote_chain
# ):
#     with app.app_context():
#         from cancelchain.miller import Miller
#         m = Miller(milling_wallet=Wallet())
#         assert m.longest_chain is None
#     with remote_app.app_context():
#         result = runner.invoke(args=['sync'])
#         assert 'Synchronized the block chain.' in result.output
#     with app.app_context():
#         from cancelchain.miller import Miller
#         m = Miller(milling_wallet=Wallet())
#         assert m.longest_chain.block_hash == remote_chain.block_hash


def test_validate(app, mill_block, runner, wallet):
    with app.app_context():
        mill_block(wallet)
        result = runner.invoke(args=['validate'])
        assert 'The block chain is valid.' in result.output


def test_export_import(app, mill_block, runner, wallet):
    with app.app_context():
        mill_block(wallet)
        with NamedTemporaryFile(suffix='.jsonl') as f:
            result = runner.invoke(args=['export', f.name])
            assert '100%' in result.output
            result = runner.invoke(args=['import', f.name])
            assert '100%' in result.output
            result = runner.invoke(args=['export', f.name])
            assert '100%' in result.output


def run_txn_transfer(
    runner, from_wallet, to_wallet, from_wallet_file, confirm=True
):
    return runner.invoke(
        args=[
            'txn', 'transfer',
            from_wallet.address, '2', to_wallet.address,
            '--txn-wallet', from_wallet_file
        ],
        input='Y' if confirm else 'n'
    )


def test_transfer(app, mill_block, runner, requests_proxy, wallet):
    with app.app_context():
        from_wallet = Wallet()
        fwf = from_wallet.to_file(walletdir=app.config.get('WALLET_DIR'))
        to_wallet = Wallet()
        m, _ = mill_block(from_wallet)
        result = run_txn_transfer(
            runner, from_wallet, to_wallet, fwf, confirm=False
        )
        assert 'Transfer aborted.' in result.output
        assert len(m.pending_txns) == 0
        result = run_txn_transfer(runner, from_wallet, to_wallet, fwf)
        assert 'Transfer created.' in result.output
        assert len(m.pending_txns) == 1


def test_invalid_transfer(app, mill_block, runner, requests_proxy, wallet):
    with app.app_context():
        from_wallet = Wallet()
        fwf = from_wallet.to_file(walletdir=app.config.get('WALLET_DIR'))
        to_wallet = Wallet()
        m, _ = mill_block(wallet)
        result = run_txn_transfer(runner, from_wallet, to_wallet, fwf)
        assert 'Transfer failed: InsufficientFundsError' in result.output
        assert len(m.pending_txns) == 0


def run_txn_subject(
    runner, subject, txn_wallet, txn_wallet_file, confirm=True
):
    return runner.invoke(
        args=[
            'txn', 'subject', txn_wallet.address, str(SUBJECT_CCG), subject,
            '--txn-wallet', txn_wallet_file
        ],
        input='Y' if confirm else 'n'
    )


def test_subject(app, mill_block, runner, requests_proxy, subject_raw, wallet):
    with app.app_context():
        txn_wallet = Wallet()
        txnwf = txn_wallet.to_file(walletdir=app.config.get('WALLET_DIR'))
        m, _ = mill_block(txn_wallet)
        result = run_txn_subject(
            runner, subject_raw, txn_wallet, txnwf, confirm=False
        )
        assert 'Subject aborted' in result.output
        assert len(m.pending_txns) == 0
        result = run_txn_subject(runner, subject_raw, txn_wallet, txnwf)
        assert 'Subject created' in result.output
        assert len(m.pending_txns) == 1


def test_invalid_subject(
    app, mill_block, runner, requests_proxy, subject_raw, wallet
):
    with app.app_context():
        txn_wallet = Wallet()
        txnwf = txn_wallet.to_file(walletdir=app.config.get('WALLET_DIR'))
        m, _ = mill_block(wallet)
        result = run_txn_subject(runner, subject_raw, txn_wallet, txnwf)
        assert 'Subject failed: InsufficientFundsError' in result.output
        assert len(m.pending_txns) == 0


def test_empty_chain(app, runner, requests_proxy, subject_raw, wallet):
    with app.app_context():
        txn_wallet = Wallet()
        txnwf = txn_wallet.to_file(walletdir=app.config.get('WALLET_DIR'))
        result = run_txn_subject(runner, subject_raw, txn_wallet, txnwf)
        assert 'Subject failed: EmptyChainError' in result.output


def run_txn_forgive(
    runner, subject, txn_wallet, txn_wallet_file, confirm=True
):
    return runner.invoke(
        args=[
            'txn', 'forgive', txn_wallet.address, str(SUBJECT_CCG), subject,
            '--txn-wallet', txn_wallet_file
        ],
        input='Y' if confirm else 'n'
    )


def test_forgive(
    app, mill_block, runner, requests_proxy, subject_raw, time_stepper, wallet
):
    with app.app_context():
        time_step = time_stepper()
        txn_wallet = Wallet()
        txnwf = txn_wallet.to_file(walletdir=app.config.get('WALLET_DIR'))
        m, _ = mill_block(txn_wallet)
        _ = next(time_step)
        result = run_txn_subject(runner, subject_raw, txn_wallet, txnwf)
        assert len(m.pending_txns) == 1
        m, _ = mill_block(txn_wallet)
        result = run_txn_forgive(
            runner, subject_raw, txn_wallet, txnwf, confirm=False
        )
        assert 'Forgive aborted' in result.output
        assert len(m.pending_txns) == 1
        result = run_txn_forgive(runner, subject_raw, txn_wallet, txnwf)
        assert 'Forgive created' in result.output
        assert len(m.pending_txns) == 2


def test_invalid_forgive(
    app, mill_block, runner, requests_proxy, subject_raw, wallet
):
    with app.app_context():
        txn_wallet = Wallet()
        txnwf = txn_wallet.to_file(walletdir=app.config.get('WALLET_DIR'))
        m, _ = mill_block(txn_wallet)
        result = run_txn_forgive(runner, subject_raw, txn_wallet, txnwf)
        assert 'Forgive failed: InsufficientFundsError' in result.output
        assert len(m.pending_txns) == 0


def run_txn_support(
    runner, subject, txn_wallet, txn_wallet_file, confirm=True
):
    return runner.invoke(
        args=[
            'txn', 'support', txn_wallet.address, str(SUBJECT_CCG), subject,
            '--txn-wallet', txn_wallet_file
        ],
        input='Y' if confirm else 'n'
    )


def test_support(app, mill_block, runner, requests_proxy, subject_raw, wallet):
    with app.app_context():
        txn_wallet = Wallet()
        txnwf = txn_wallet.to_file(walletdir=app.config.get('WALLET_DIR'))
        m, _ = mill_block(txn_wallet)
        result = run_txn_support(
            runner, subject_raw, txn_wallet, txnwf, confirm=False
        )
        assert 'Support aborted' in result.output
        assert len(m.pending_txns) == 0
        result = run_txn_support(runner, subject_raw, txn_wallet, txnwf)
        assert 'Support created' in result.output
        assert len(m.pending_txns) == 1


def test_invalid_support(
    app, mill_block, runner, requests_proxy, subject_raw, wallet
):
    with app.app_context():
        txn_wallet = Wallet()
        txnwf = txn_wallet.to_file(walletdir=app.config.get('WALLET_DIR'))
        m, _ = mill_block(wallet)
        result = run_txn_support(runner, subject_raw, txn_wallet, txnwf)
        assert 'Support failed: InsufficientFundsError' in result.output
        assert len(m.pending_txns) == 0


def test_create_wallet(app, runner):
    with app.app_context(), TemporaryDirectory() as walletdir:
            result = runner.invoke(
                args=[
                    'wallet', 'create', '--walletdir', walletdir
                ]
            )
            wallet_filename = result.output.strip()[len('Created '):]
            assert Wallet.from_file(wallet_filename) is not None


def test_wallet_balance(
    app, mill_block, runner, requests_proxy, subject_raw, wallet
):
    with app.app_context():
        mill_block(wallet)
        result = runner.invoke(args=['wallet', 'balance', wallet.address])
        assert f'{REWARD_CCG} CCG' in result.output
        wf = get_wallet_file(wallet.address, app=app)
        run_txn_subject(runner, subject_raw, wallet, wf)
        w = Wallet()
        mill_block(w)
        result = runner.invoke(args=['wallet', 'balance', wallet.address])
        assert f'{REWARD_CCG-SUBJECT_CCG} CCG' in result.output
        to_wallet = Wallet()
        run_txn_transfer(runner, wallet, to_wallet, wf)
        mill_block(w)
        result = runner.invoke(args=['wallet', 'balance', wallet.address])
        assert f'{REWARD_CCG-2*SUBJECT_CCG} CCG' in result.output
        result = runner.invoke(args=['wallet', 'balance', to_wallet.address])
        assert f'{SUBJECT_CCG} CCG' in result.output
        result = runner.invoke(args=['wallet', 'balance', w.address])
        assert f'{int(2*REWARD_CCG+0.5*SUBJECT_CCG)} CCG' in result.output
        result = runner.invoke(args=['wallet', 'balance', 'foo'])
        assert 'Not Found' in result.output


def test_subject_balance(
    app, mill_block, runner, requests_proxy, subject_raw, wallet
):
    with app.app_context():
        mill_block(wallet)
        result = runner.invoke(args=['subject', 'balance', subject_raw])
        assert '0 CCG' in result.output
        wf = get_wallet_file(wallet.address, app=app)
        run_txn_subject(runner, subject_raw, wallet, wf)
        mill_block(wallet)
        result = runner.invoke(args=['subject', 'balance', subject_raw])
        assert f'{SUBJECT_CCG} CCG' in result.output
        run_txn_subject(runner, subject_raw, wallet, wf)
        mill_block(wallet)
        result = runner.invoke(args=['subject', 'balance', subject_raw])
        assert f'{2*SUBJECT_CCG} CCG' in result.output


def test_subject_support(
    app, mill_block, runner, requests_proxy, subject_raw, wallet
):
    with app.app_context():
        mill_block(wallet)
        result = runner.invoke(args=['subject', 'support', subject_raw])
        assert '0 CCG' in result.output
        wf = get_wallet_file(wallet.address, app=app)
        run_txn_support(runner, subject_raw, wallet, wf)
        mill_block(wallet)
        result = runner.invoke(args=['subject', 'support', subject_raw])
        assert f'{SUBJECT_CCG} CCG' in result.output
        run_txn_support(runner, subject_raw, wallet, wf)
        mill_block(wallet)
        result = runner.invoke(args=['subject', 'support', subject_raw])
        assert f'{2*SUBJECT_CCG} CCG' in result.output


def test_mill(app, runner, wallet):
    with app.app_context():
        result = runner.invoke(args=['mill', wallet.address, '--blocks', 2])
        assert 'GENESIS' in result.output
        assert 'Block Index 0' in result.output
        assert 'Block Index 1' in result.output
        result = runner.invoke(
            args=['mill', wallet.address, '--blocks', 2]
        )
        assert 'Block Index 2' in result.output
        assert 'Block Index 3' in result.output
