import datetime
from unittest.mock import patch

import pytest
from cancelchain.block import TXN_TIMEOUT
from cancelchain.chain import CURMUDGEON_PER_GRUMBLE as CPG
from cancelchain.chain import REWARD
from cancelchain.exceptions import InsufficientFundsError
from cancelchain.miller import Miller
from cancelchain.payload import Inflow, Outflow
from cancelchain.transaction import Transaction
from cancelchain.util import now
from cancelchain.wallet import Wallet


def test_miller_create_block(app, time_machine, time_stepper, wallet):
    with app.app_context():
        now_dt = now()
        time_step = time_stepper(start=now_dt-datetime.timedelta(hours=1))
        m = Miller(milling_wallet=wallet)
        _ = next(time_step)
        b0 = m.create_block()
        m.mill_block(b0)
        _ = next(time_step)
        assert m.longest_chain.length == 1
        assert (
            m.longest_chain.balance(wallet.address) == REWARD
        )
        b1 = m.create_block()
        m.mill_block(b1)
        _ = next(time_step)
        assert m.longest_chain.length == 2
        assert (
            m.longest_chain.balance(wallet.address) == 2 * REWARD
        )
        cb0 = b0.coinbase
        cb0_amount = list(cb0.outflows)[0].amount
        w2 = Wallet()
        remit = 2 * CPG
        t0 = Transaction()
        t0.add_inflow(Inflow(outflow_txid=cb0.txid, outflow_idx=0))
        t0.add_outflow(Outflow(amount=remit, address=w2.address))
        t0.add_outflow(
            Outflow(amount=cb0_amount-remit, address=wallet.address)
        )
        t0.set_wallet(wallet)
        t0.seal()
        t0.sign()
        m.receive_transaction(t0.txid, t0.to_json())
        b2 = m.create_block()
        m.mill_block(b2)
        _ = next(time_step)
        assert m.longest_chain.length == 3
        assert (
            m.longest_chain.balance(wallet.address) == (3 * REWARD) - (2 * CPG)
        )
        assert m.longest_chain.balance(w2.address) == 2 * CPG
        time_machine.move_to(now_dt)
        assert m.longest_chain.get_block(b2.block_hash)


def test_expired_transaction(app, time_machine, wallet):
    with app.app_context():
        m = Miller(milling_wallet=wallet)
        b0 = m.create_block()
        m.mill_block(b0)
        now_dt = now()
        when_dt = now_dt-TXN_TIMEOUT-datetime.timedelta(seconds=1)
        time_machine.move_to(when_dt)
        t0 = m.longest_chain.create_transfer(
            wallet, m.longest_chain.balance(wallet.address), wallet.address
        )
        t0.sign()
        time_machine.move_to(now_dt)
        m.receive_transaction(t0.txid, t0.to_json())
        assert len(m.pending_txns) == 1
        b1 = m.create_block()
        assert len(m.pending_txns) == 0
        m.mill_block(b1)
        assert len(b1.txns) == 1


def test_duplicate_transaction(app, time_machine, wallet):
    with app.app_context():
        now_dt = now()
        when_dt = now_dt-datetime.timedelta(hours=1)
        time_machine.move_to(when_dt)
        m = Miller(milling_wallet=wallet)
        b0 = m.create_block()
        m.mill_block(b0)
        cb0 = b0.coinbase
        cb0_amount = list(cb0.outflows)[0].amount
        when_dt = when_dt + datetime.timedelta(minutes=1)
        time_machine.move_to(when_dt)
        t0 = Transaction()
        t0.add_inflow(Inflow(outflow_txid=cb0.txid, outflow_idx=0))
        t0.add_outflow(Outflow(amount=cb0_amount, address=wallet.address))
        t0.set_wallet(wallet)
        t0.seal()
        t0.sign()
        m.receive_transaction(t0.txid, t0.to_json())
        m.receive_transaction(t0.txid, t0.to_json())
        when_dt = when_dt + datetime.timedelta(minutes=1)
        time_machine.move_to(when_dt)
        assert len(m.pending_txns) == 1
        b1 = m.create_block()
        assert len(m.pending_txns) == 1
        m.mill_block(b1)
        assert len(b1.txns) == 2
        assert len(m.pending_txns) == 1
        b2 = m.create_block()
        assert len(m.pending_txns) == 1
        m.mill_block(b2)
        assert len(b2.txns) == 1
        assert len(m.pending_txns) == 1
        when_dt = when_dt + datetime.timedelta(minutes=1)
        time_machine.move_to(when_dt)
        assert len(m.pending_txns) == 1
        b3 = m.create_block()
        assert len(m.pending_txns) == 1
        m.mill_block(b3)
        assert len(b3.txns) == 1
        assert len(m.pending_txns) == 1
        when_dt = when_dt + datetime.timedelta(minutes=1)
        time_machine.move_to(when_dt)
        t1 = Transaction()
        t1.add_inflow(Inflow(outflow_txid=cb0.txid, outflow_idx=0))
        t1.add_outflow(Outflow(amount=cb0_amount, address=wallet.address))
        t1.set_wallet(wallet)
        t1.seal()
        t1.sign()
        m.receive_transaction(t1.txid, t1.to_json())
        when_dt = when_dt + datetime.timedelta(minutes=1)
        time_machine.move_to(when_dt)
        assert len(m.pending_txns) == 2
        b4 = m.create_block()
        assert len(m.pending_txns) == 1
        m.mill_block(b4)
        assert len(b4.txns) == 1
        assert len(m.pending_txns) == 1


@patch('cancelchain.miller.MAX_TRANSACTIONS', 10)
def test_max_txns(app, time_machine, wallet):
    with app.app_context():
        max_txns = 10
        now_dt = now()
        when_dt = now_dt-datetime.timedelta(hours=4)
        time_machine.move_to(when_dt)
        m = Miller(milling_wallet=wallet)
        b0 = m.create_block()
        m.mill_block(b0)
        cb0 = b0.coinbase
        prev_t = cb0
        for _i in range(max_txns + 3):
            amount = list(prev_t.outflows)[0].amount
            when_dt += datetime.timedelta(seconds=1)
            time_machine.move_to(when_dt)
            t = Transaction()
            t.add_inflow(Inflow(outflow_txid=prev_t.txid, outflow_idx=0))
            t.add_outflow(Outflow(amount=amount, address=wallet.address))
            t.set_wallet(wallet)
            t.seal()
            t.sign()
            prev_t = t
            m.receive_transaction(t.txid, t.to_json())
        assert len(m.pending_txns) == max_txns + 3
        when_dt += datetime.timedelta(minutes=1)
        time_machine.move_to(when_dt)
        b1 = m.create_block()
        assert len(b1.txns) == max_txns


def test_subject_forgive_txns(app, subject, time_machine, wallet):
    with app.app_context():
        now_dt = now()
        when_dt = now_dt-datetime.timedelta(hours=1)
        time_machine.move_to(when_dt)
        m = Miller(milling_wallet=wallet)
        b0 = m.create_block()
        m.mill_block(b0)
        when_dt += datetime.timedelta(minutes=1)
        time_machine.move_to(when_dt)
        amount = m.longest_chain.balance(wallet.address)
        t0 = m.longest_chain.create_subject(wallet, amount, subject)
        t0.sign()
        m.receive_transaction(t0.txid, t0.to_json())
        b1 = m.create_block()
        m.mill_block(b1)
        assert (m.longest_chain.subject_balance(subject) == amount)
        when_dt += datetime.timedelta(minutes=1)
        time_machine.move_to(when_dt)
        t1 = m.longest_chain.create_forgive(wallet, amount, subject)
        t1.sign()
        m.receive_transaction(t1.txid, t1.to_json())
        b2 = m.create_block()
        m.mill_block(b2)
        assert (m.longest_chain.subject_balance(subject) == 0)


def test_invalid_subject_forgive_txns(app, subject, time_machine, wallet):
    with app.app_context():
        now_dt = now()
        when_dt = now_dt-datetime.timedelta(hours=1)
        time_machine.move_to(when_dt)
        m = Miller(milling_wallet=wallet)
        b0 = m.create_block()
        m.mill_block(b0)
        when_dt += datetime.timedelta(minutes=1)
        time_machine.move_to(when_dt)
        amount = m.longest_chain.balance(wallet.address) + 1
        with pytest.raises(InsufficientFundsError):
            t0 = m.longest_chain.create_subject(wallet, amount, subject)
        amount = m.longest_chain.balance(wallet.address) - 2
        t0 = m.longest_chain.create_subject(wallet, amount, subject)
        assert (len(t0.outflows) == 2)
        t0.sign()
        m.receive_transaction(t0.txid, t0.to_json())
        b1 = m.create_block()
        assert (len(b1.txns) == 2)
        m.mill_block(b1)
        when_dt += datetime.timedelta(minutes=1)
        time_machine.move_to(when_dt)
        with pytest.raises(InsufficientFundsError):
            t1 = m.longest_chain.create_forgive(wallet, amount+1, subject)
        t1 = m.longest_chain.create_forgive(wallet, amount-1, subject)
        t1.sign()
        m.receive_transaction(t1.txid, t1.to_json())
        b2 = m.create_block()
        assert (len(b2.txns) == 2)
        m.mill_block(b2)
        when_dt += datetime.timedelta(minutes=1)
        time_machine.move_to(when_dt)
        with pytest.raises(InsufficientFundsError):
            t2 = m.longest_chain.create_forgive(wallet, 2, subject)
        t2 = m.longest_chain.create_forgive(wallet, 1, subject)
        t2.sign()
        m.receive_transaction(t2.txid, t2.to_json())
        b3 = m.create_block()
        m.mill_block(b3)
        when_dt += datetime.timedelta(minutes=1)
        time_machine.move_to(when_dt)
        with pytest.raises(InsufficientFundsError):
            m.longest_chain.create_forgive(wallet, 1, subject)
