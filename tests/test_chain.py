import datetime
from unittest.mock import patch

import pytest
from cancelchain.block import TXN_TIMEOUT, Block
from cancelchain.chain import (
    CURMUDGEON_PER_GRUMBLE,
    GENESIS_HASH,
    REWARD,
    Chain,
)
from cancelchain.exceptions import (
    FutureBlockError,
    ImbalancedTransactionError,
    InflowOutflowAddressMismatchError,
    InvalidBlockError,
    InvalidBlockIndexError,
    InvalidChainError,
    InvalidCoinbaseErrorRewardError,
    InvalidTargetError,
    MissingInflowOutflowError,
    OutOfOrderBlockError,
    SpentTransactionError,
)
from cancelchain.payload import Inflow, Outflow
from cancelchain.transaction import Transaction
from cancelchain.util import now, now_iso
from cancelchain.wallet import Wallet

TEST_TARGET = 'F' * 64


def test_from(valid_chain):
    d = valid_chain.to_dict()
    new_chain = Chain.from_dict(d)
    assert new_chain == valid_chain
    j = valid_chain.to_json()
    new_chain = Chain.from_json(j)
    assert new_chain == valid_chain


def test_empty():
    chain = Chain()
    with pytest.raises(InvalidChainError):
        chain.validate()


def test_valid(add_chain_block, app, wallet):
    with app.app_context():
        chain, _ = add_chain_block()
        chain.validate()


def test_invalid_prev_hash(app, wallet):
    with app.app_context():
        chain = Chain()
        block = Block()
        block.idx = 0
        chain.link_block(block)
        block.prev_hash = 'foo'
        block.seal(wallet, chain.block_reward(block))
        block.mill()
        with pytest.raises(InvalidBlockError):
            chain.add_block(block)


def test_invalid_txn_timestamp(app, time_machine, wallet):
    with app.app_context():
        chain = Chain()
        block = Block()
        chain.link_block(block)
        block.seal(wallet, chain.block_reward(block))
        block.mill()
        chain.add_block(block)
        cb = block.coinbase
        now_dt = now()
        when_dt = now_dt+datetime.timedelta(hours=1)
        time_machine.move_to(when_dt)
        t = Transaction()
        t.add_inflow(Inflow(outflow_txid=cb.txid, outflow_idx=0))
        t.add_outflow(Outflow(amount=1, address=wallet.address))
        t.set_wallet(wallet)
        t.seal()
        t.sign()
        time_machine.move_to(now_dt)
        block = Block()
        block.add_txn(t)
        chain.link_block(block)
        block.seal(wallet, chain.block_reward(block))
        block.mill()
        with pytest.raises(InvalidBlockError, match="FutureTransactionError"):
            chain.add_block(block)
        then_dt = now_dt - TXN_TIMEOUT - datetime.timedelta(hours=1)
        time_machine.move_to(then_dt)
        t = Transaction()
        t.add_inflow(Inflow(outflow_txid=cb.txid, outflow_idx=0))
        t.add_outflow(Outflow(amount=1, address=wallet.address))
        t.set_wallet(wallet)
        t.seal()
        t.sign()
        time_machine.move_to(now_dt)
        block = Block()
        block.add_txn(t)
        chain.link_block(block)
        block.seal(wallet, chain.block_reward(block))
        block.mill()
        with pytest.raises(InvalidBlockError, match="ExpiredTransactionError"):
            chain.add_block(block)


@patch('cancelchain.chain.TARGET_INTERVAL', 5)
def test_decrease_target(app, wallet):
    with app.app_context():
        chain = Chain()
        original_target = chain.target
        for _i in range(0, 5):
            block = Block()
            chain.link_block(block)
            block.seal(wallet, chain.block_reward(block))
            assert block.target == TEST_TARGET
            assert block.target == chain.target
            block.mill()
            chain.add_block(block)
        new_target = int(int(original_target, 16) * 0.25)
        assert int(chain.target, 16) == new_target
        block = Block()
        chain.link_block(block)
        block.seal(wallet, chain.block_reward(block))
        assert block.target == chain.target
        block.mill()
        chain.add_block(block)


@patch('cancelchain.chain.TARGET_INTERVAL', 5)
def test_increase_target(app, time_machine, wallet):
    with app.app_context():
        now_dt = now()
        then_dt = now_dt-datetime.timedelta(days=100)
        time_machine.move_to(then_dt)
        chain = Chain()
        block = Block()
        chain.link_block(block)
        block.seal(wallet, chain.block_reward(block))
        block.mill()
        chain.add_block(block)
        time_machine.move_to(now_dt)
        for _i in range(0, 4):
            block = Block()
            chain.link_block(block)
            block.seal(wallet, chain.block_reward(block))
            assert block.target == chain.target == TEST_TARGET
            block.mill()
            chain.add_block(block)
        assert chain.target == TEST_TARGET


@patch('cancelchain.chain.TARGET_INTERVAL', 5)
def test_invalid_target(app, time_machine, time_stepper, wallet):
    with app.app_context():
        now_dt = now()
        time_step = time_stepper(start=now_dt-datetime.timedelta(hours=1))
        _ = next(time_step)
        chain = Chain()
        original_target = chain.target
        for _i in range(0, 5):
            _ = next(time_step)
            block = Block()
            chain.link_block(block)
            block.seal(wallet, chain.block_reward(block))
            assert block.target == chain.target == TEST_TARGET
            block.mill()
            chain.add_block(block)
        time_machine.move_to(now_dt)
        new_target = int(int(original_target, 16) * 0.25)
        assert int(chain.target, 16) == new_target
        block = Block()
        chain.link_block(block)
        block.target = TEST_TARGET
        block.seal(wallet, chain.block_reward(block))
        block.mill()
        with pytest.raises(InvalidTargetError):
            chain.add_block(block)


def test_block_reward(add_chain_block, app, wallet):
    with app.app_context():
        chain = Chain()
        assert chain.block_reward() == REWARD


def test_generators(add_chain_block, app, time_stepper, wallet):
    with app.app_context():
        time_step = time_stepper(start=now()-datetime.timedelta(hours=1))
        _ = next(time_step)
        wallet2 = Wallet()
        chain, block = add_chain_block()
        for _i in range(0, 5):
            _ = next(time_step)
            prev_block = chain.last_block
            prev_coinbase = prev_block.coinbase
            block = Block()
            txn = Transaction()
            amount = 0
            for o in prev_coinbase.outflows:
                if o.address == wallet.address:
                    txn.add_inflow(
                        Inflow(outflow_txid=prev_coinbase.txid, outflow_idx=0)
                    )
                    amount += o.amount
            txn.add_outflow(
                Outflow(amount=amount, address=wallet2.address)
            )
            txn.set_wallet(wallet)
            txn.seal()
            txn.sign()
            _ = next(time_step)
            block.add_txn(txn)
            add_chain_block(chain=chain, block=block)


def test_mill(wallet):
    chain = Chain()
    block = Block()
    chain.link_block(block)
    chain.seal_block(block, wallet)
    block.mill()


@pytest.mark.multi
def test_mill_mp(wallet):
    chain = Chain()
    block = Block()
    chain.link_block(block)
    chain.seal_block(block, wallet)
    block.mill(mp=True)


def test_db(add_chain_block, app, time_machine, wallet):
    with app.app_context():
        wallet2 = Wallet()
        now_dt = now()
        then_dt = now_dt-datetime.timedelta(hours=1)
        time_machine.move_to(then_dt)
        chain, block = add_chain_block()
        block_copy = Block.from_db(block.block_hash)
        assert block == block_copy
        cb = block.coinbase
        cb_amount = list(block.coinbase.outflows)[0].amount
        remit = 2 * CURMUDGEON_PER_GRUMBLE
        then_dt += datetime.timedelta(minutes=1)
        time_machine.move_to(then_dt)
        t = Transaction()
        t.add_inflow(Inflow(outflow_txid=cb.txid, outflow_idx=0))
        t.add_outflow(Outflow(amount=remit, address=wallet2.address))
        t.add_outflow(
            Outflow(amount=cb_amount-remit, address=wallet.address)
        )
        t.set_wallet(wallet)
        t.seal()
        t.sign()
        t.to_db()
        time_machine.move_to(now_dt)
        block2 = Block()
        block2.add_txn(t)
        add_chain_block(chain=chain, block=block2)
        block2_copy = Block.from_db(block2.block_hash)
        assert block2 == block2_copy
        chain.to_db()
        chain_copy = Chain.from_db(chain.cid)
        assert chain == chain_copy


def test_dao(add_chain_block, app, time_machine, time_stepper, wallet):
    with app.app_context():
        now_dt = now()
        time_step = time_stepper(now_dt-datetime.timedelta(hours=1))
        wallet2 = Wallet()
        wallet3 = Wallet()
        _ = next(time_step)
        chain, _ = add_chain_block()
        _ = next(time_step)
        _, block2 = add_chain_block(chain=chain)
        _ = next(time_step)
        alt_chain = Chain(block_hash=block2.block_hash)
        add_chain_block(chain=alt_chain)
        _ = next(time_step)
        add_chain_block(chain=chain, milling_wallet=wallet2)
        _ = next(time_step)
        add_chain_block(chain=alt_chain, milling_wallet=wallet3)
        _ = next(time_step)
        add_chain_block(chain=chain, milling_wallet=wallet3)
        _ = next(time_step)
        add_chain_block(chain=chain, milling_wallet=wallet3)
        _ = next(time_step)
        add_chain_block(chain=chain)
        _ = next(time_step)
        add_chain_block(chain=chain)
        _ = next(time_step)
        _, last_block = add_chain_block(chain=chain)
        time_machine.move_to(now_dt)

        blocks = chain.to_dao(create=True).blocks
        assert blocks.count() == 8
        assert [b.id for b in blocks.all()] == [10, 9, 8, 7, 6, 4, 2, 1]
        alt_blocks = alt_chain.to_dao(create=True).blocks
        assert alt_blocks.count() == 4
        assert [b.id for b in alt_blocks.all()] == [5, 3, 2, 1]

        transactions = chain.to_dao(create=True).transactions
        assert transactions.count() == 8
        assert [t.id for t in transactions.all()] == [10, 9, 8, 7, 6, 4, 2, 1]
        alt_transactions = alt_chain.to_dao(create=True).transactions
        assert alt_transactions.count() == 4
        assert [b.id for b in alt_transactions.all()] == [5, 3, 2, 1]

        outflows = chain.to_dao(create=True).outflows
        assert outflows.count() == 8
        assert [t.id for t in outflows.all()] == [10, 9, 8, 7, 6, 4, 2, 1]
        alt_outflows = alt_chain.to_dao(create=True).outflows
        assert alt_outflows.count() == 4
        assert [b.id for b in alt_outflows.all()] == [5, 3, 2, 1]

        inflows = chain.to_dao(create=True).inflows
        assert inflows.count() == 0
        assert [t.id for t in inflows.all()] == []
        alt_inflows = alt_chain.to_dao(create=True).inflows
        assert alt_inflows.count() == 0
        assert [b.id for b in alt_inflows.all()] == []

        wallet_leaders = list(chain.to_dao(create=True).wallet_leaderboard())
        assert (wallet_leaders[0][0] == wallet.address)
        assert (wallet_leaders[0][1] == 5 * REWARD)
        assert (wallet_leaders[1][0] == wallet3.address)
        assert (wallet_leaders[1][1] == 2 * REWARD)
        assert (wallet_leaders[2][0] == wallet2.address)
        assert (wallet_leaders[2][1] == REWARD)
        assert (len(wallet_leaders) == 3)
        wallet_leaders = list(chain.to_dao(create=True).wallet_leaderboard(
            earliest=block2.timestamp_dt,
            latest=last_block.timestamp_dt,
            limit=2
        ))
        assert (len(wallet_leaders) == 2)
        assert (wallet_leaders[0][0] == wallet.address)
        assert (wallet_leaders[0][1] == 3 * REWARD)
        assert (wallet_leaders[1][0] == wallet3.address)
        assert (wallet_leaders[1][1] == 2 * REWARD)


def test_validate_block(add_chain_block, app, time_machine, wallet):
    with app.app_context():
        chain, block = add_chain_block()
        now_dt = now()
        then_dt = now_dt+datetime.timedelta(hours=1)
        time_machine.move_to(then_dt)
        block2 = Block()
        chain.link_block(block2)
        chain.seal_block(block2, wallet)
        block2.mill()
        time_machine.move_to(now_dt)
        with pytest.raises(FutureBlockError):
            chain.add_block(block2)

        then_dt = now_dt-datetime.timedelta(hours=1)
        time_machine.move_to(then_dt)
        block2 = Block()
        chain.link_block(block2)
        chain.seal_block(block2, wallet)
        block2.mill()
        time_machine.move_to(now_dt)
        with pytest.raises(OutOfOrderBlockError):
            chain.add_block(block2)

        block2 = Block()
        chain.link_block(block2)
        chain.seal_block(block2, wallet)
        block2.idx += 1
        block2.mill()
        with pytest.raises(InvalidBlockIndexError):
            chain.add_block(block2)


def test_validate_block_txn(add_chain_block, app, time_machine, wallet):
    with app.app_context():
        chain = Chain()
        assert chain.get_block_by_reverse_index(0) is None

        _, block = add_chain_block(chain=chain)
        cb = block.coinbase
        cb_amount = list(cb.outflows)[0].amount

        block2 = Block()
        chain.link_block(block2)
        now_dt = now()
        then_dt = now_dt+datetime.timedelta(hours=1)
        time_machine.move_to(then_dt)
        t = Transaction()
        t.add_inflow(Inflow(outflow_txid=cb.txid, outflow_idx=0))
        t.add_outflow(Outflow(amount=cb_amount, address=wallet.address))
        t.set_wallet(wallet)
        t.seal()
        t.sign()
        time_machine.move_to(now_dt)
        block2.add_txn(t)
        chain.seal_block(block2, wallet)
        block2.mill()
        with pytest.raises(InvalidBlockError, match="FutureTransactionError"):
            chain.add_block(block2)

        block2 = Block()
        chain.link_block(block2)
        t = Transaction()
        t.add_inflow(Inflow(outflow_txid=cb.txid, outflow_idx=0))
        t.add_outflow(Outflow(amount=cb_amount-1, address=wallet.address))
        t.set_wallet(wallet)
        t.seal()
        t.sign()
        block2.add_txn(t)
        chain.seal_block(block2, wallet)
        block2.mill()
        with pytest.raises(ImbalancedTransactionError):
            chain.add_block(block2)


def test_validate_txn_inflow(add_chain_block, app, time_machine, txid, wallet):
    with app.app_context():
        chain = Chain()
        # txn inflow's outflow exists and amount > 0
        block = Block()
        t = Transaction()
        t.add_inflow(Inflow(outflow_txid=txid, outflow_idx=0))
        t.add_outflow(Outflow(amount=100, address=wallet.address))
        t.set_wallet(wallet)
        t.seal()
        t.sign()
        block.add_txn(t)
        chain.link_block(block)
        chain.seal_block(block, wallet)
        block.mill()
        with pytest.raises(MissingInflowOutflowError):
            chain.add_block(block)
        wallet2 = Wallet()
        now_dt = now()
        then_dt = now_dt-datetime.timedelta(hours=1)
        time_machine.move_to(then_dt)
        chain, block = add_chain_block()
        cb = block.coinbase
        cb_amount = list(block.coinbase.outflows)[0].amount
        then_dt += datetime.timedelta(minutes=1)
        time_machine.move_to(then_dt)
        t = Transaction()
        t.add_inflow(Inflow(outflow_txid=cb.txid, outflow_idx=1000))
        t.add_outflow(Outflow(amount=cb_amount, address=wallet2.address))
        t.set_wallet(wallet)
        t.seal()
        t.sign()
        block2 = Block()
        block2.add_txn(t)
        chain.link_block(block2)
        chain.seal_block(block2, wallet)
        block2.mill()
        with pytest.raises(MissingInflowOutflowError):
            chain.add_block(block2)
        # txn inflow's outflow not already used in other inflow
        t = Transaction()
        t.add_inflow(Inflow(outflow_txid=cb.txid, outflow_idx=0))
        t.add_outflow(Outflow(amount=cb_amount, address=wallet2.address))
        t.set_wallet(wallet)
        t.seal()
        t.sign()
        block2 = Block()
        block2.add_txn(t)
        add_chain_block(chain=chain, block=block2)
        time_machine.move_to(now_dt)
        t = Transaction()
        t.add_inflow(Inflow(outflow_txid=cb.txid, outflow_idx=0))
        t.add_outflow(Outflow(amount=cb_amount, address=wallet2.address))
        t.set_wallet(wallet)
        t.seal()
        t.sign()
        block3 = Block()
        block3.add_txn(t)
        chain.link_block(block3)
        chain.seal_block(block3, wallet)
        block3.mill()
        with pytest.raises(SpentTransactionError):
            chain.add_block(block3)


def test_validate_block_coinbase(add_chain_block, app, subject, txid, wallet):
    with app.app_context():
        chain = Chain()
        block = Block()
        chain.link_block(block)
        t = Transaction()
        t.add_inflow(Inflow(outflow_txid=txid, outflow_idx=0))
        t.add_outflow(Outflow(amount=1, subject=subject))
        t.set_wallet(wallet)
        t.seal()
        t.sign()
        block.add_txn(t, is_coinbase=False)
        block.merkle_root = block.get_merkle_root()
        block.timestamp = now_iso()
        block.mill()
        with pytest.raises(InvalidBlockError, match="inflows"):
            chain.add_block(block)

        block = Block()
        chain.link_block(block)
        block.link(0, GENESIS_HASH, TEST_TARGET)
        block.seal(wallet, REWARD+1)
        block.mill()
        with pytest.raises(InvalidCoinbaseErrorRewardError):
            chain.add_block(block)

        _, block = add_chain_block(chain=chain)
        cb = block.coinbase
        cb_amount = list(cb.outflows)[0].amount

        t = Transaction()
        t.add_inflow(Inflow(outflow_txid=cb.txid, outflow_idx=0))
        t.add_outflow(Outflow(amount=cb_amount, subject=subject))
        t.set_wallet(wallet)
        t.seal()
        t.sign()
        block2 = Block()
        chain.link_block(block2)
        block2.add_txn(t)
        cb2 = Transaction()
        cb2.add_outflow(
            Outflow(amount=chain.block_reward(), address=wallet.address)
        )
        cb2.set_wallet(wallet)
        cb2.seal()
        cb2.sign()
        block2.add_txn(cb2, is_coinbase=True)
        block2.merkle_root = block2.get_merkle_root()
        block2.timestamp = now_iso()
        block2.mill()
        with pytest.raises(InvalidBlockError, match="InvalidCoinbaseError"):
            chain.add_block(block2)

        t = Transaction()
        t.add_inflow(Inflow(outflow_txid=cb.txid, outflow_idx=0))
        t.add_outflow(Outflow(amount=cb_amount, subject=subject))
        t.set_wallet(wallet)
        t.seal()
        t.sign()
        block2 = Block()
        block2.add_txn(t)
        add_chain_block(chain=chain, block=block2)

        t2 = Transaction()
        t2.add_inflow(Inflow(outflow_txid=t.txid, outflow_idx=0))
        t2.add_outflow(Outflow(amount=cb_amount, forgive=subject))
        t2.set_wallet(wallet)
        t2.seal()
        t2.sign()
        block3 = Block()
        chain.link_block(block3)
        block3.add_txn(t2)
        cb3 = Transaction()
        cb3.add_outflow(
            Outflow(amount=chain.block_reward(), address=wallet.address)
        )
        cb3.set_wallet(wallet)
        cb3.seal()
        cb3.sign()
        block3.add_txn(cb3, is_coinbase=True)
        block3.merkle_root = block3.get_merkle_root()
        block3.timestamp = now_iso()
        block3.mill()
        with pytest.raises(InvalidBlockError, match="InvalidCoinbaseError"):
            chain.add_block(block3)


def test_validate_io_address_mismatch(app, wallet):
    with app.app_context():
        wallet2 = Wallet()
        chain = Chain()
        block = Block()
        chain.link_block(block)
        chain.seal_block(block, wallet)
        block.mill()
        chain.add_block(block)
        cb = block.coinbase
        cb_amount = list(cb.outflows)[0].amount

        block2 = Block()
        chain.link_block(block2)
        t2 = Transaction()
        t2.add_inflow(Inflow(outflow_txid=cb.txid, outflow_idx=0))
        t2.add_outflow(Outflow(amount=cb_amount, address=wallet2.address))
        t2.set_wallet(wallet2)
        t2.seal()
        t2.sign()
        block2.add_txn(t2)
        chain.seal_block(block2, wallet2)
        block2.mill()
        with pytest.raises(InflowOutflowAddressMismatchError):
            chain.add_block(block2)


def test_validate_subject_ioflows(app, subject, wallet):
    with app.app_context():
        chain = Chain()
        block = Block()
        chain.link_block(block)
        chain.seal_block(block, wallet)
        block.mill()
        chain.add_block(block)
        cb = block.coinbase
        cb_amount = list(cb.outflows)[0].amount

        block2 = Block()
        chain.link_block(block2)
        t = Transaction()
        t.add_inflow(Inflow(outflow_txid=cb.txid, outflow_idx=0))
        t.add_outflow(Outflow(amount=cb_amount, subject=subject))
        t.set_wallet(wallet)
        t.seal()
        t.sign()
        block2.add_txn(t)
        chain.seal_block(block2, wallet)
        block2.mill()
        chain.add_block(block2)

        block3 = Block()
        chain.link_block(block3)
        t2 = Transaction()
        t2.add_inflow(Inflow(outflow_txid=t.txid, outflow_idx=0))
        t2.add_outflow(Outflow(amount=cb_amount, forgive=subject))
        t2.set_wallet(wallet)
        t2.seal()
        t2.sign()
        block3.add_txn(t2)
        chain.seal_block(block3, wallet)
        block3.mill()
        chain.add_block(block3)
