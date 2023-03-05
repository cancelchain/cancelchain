import os
from http.client import responses

import click
import requests
from flask import current_app
from flask.cli import AppGroup, with_appcontext
from humanfriendly import format_timespan
from millify import millify
from progress.bar import IncrementalBar
from progress.counter import Counter

from cancelchain.api_client import ApiClient
from cancelchain.block import Block
from cancelchain.chain import CURMUDGEON_PER_GRUMBLE
from cancelchain.database import db
from cancelchain.miller import Miller
from cancelchain.node import Node
from cancelchain.payload import encode_subject
from cancelchain.transaction import Transaction
from cancelchain.util import host_address, now_iso
from cancelchain.wallet import Wallet

CHAIN_MISMATCH_MSG = 'Chain/file mismatch'

def grumble_to_curmudgeons(grumble):
    return int(CURMUDGEON_PER_GRUMBLE * float(grumble))


def human_curmudgeons(curmudgeons):
    balance = int(curmudgeons) / CURMUDGEON_PER_GRUMBLE
    return f'{balance:.2f}'.rstrip('0').rstrip('.')


def human_bignum(num):
    return millify(num, precision=2, drop_nulls=False)


def human_timespan(secs):
    return format_timespan(secs)


def http_error_message(e):
    try:
        msg = e.response.json().get('error')
        if msg:
            if isinstance(msg, dict):
                return ','.join([f"{k} => {v}" for k, v in msg.items()])
            elif isinstance(msg, list):
                return ','.join(msg)
            else:
                return msg
        else:
            return e.response.text
    except (AttributeError, requests.exceptions.JSONDecodeError):
        return responses.get(e.response.status_code)


def host_api_client(host=None, wallet_file=None):
    if not host:
        host = current_app.config.get('DEFAULT_COMMAND_HOST')
    if wallet_file:
        wallet = Wallet.from_file(wallet_file)
    else:
        host, address = host_address(host)
        wallet = current_app.wallets.get(address)
    return ApiClient(
        host, wallet, timeout=current_app.config.get('API_CLIENT_TIMEOUT')
    )


def address_wallet(address, wallet_file=None):
    if wallet_file:
        wallet = Wallet.from_file(wallet_file)
    else:
        wallet = current_app.wallets.get(address)
    if wallet is None or address != wallet.address:
        msg = f"No wallet for {address}"
        raise Exception(msg)
    return wallet


def read_last_line(file):
    with open(file, 'rb') as f:
        try:
            f.seek(-2, os.SEEK_END)
            while f.read(1) != b'\n':
                f.seek(-2, os.SEEK_CUR)
        except OSError:  # catch OSError in case of one line file
            f.seek(0)
        return f.readline().decode()


class BlockCounterBar():
    def __init__(self):
        self.counter = Counter(
            message='Finding Missing Blocks: ',
            suffix='blocks'
        )

    def switch(self):
        self.finish()
        self.counter = IncrementalBar(
            message=' Adding Missing Blocks:',
            max=self.counter.index,
            suffix='%(percent).1f%% [%(index)s/%(max)s %(eta)ds]'
        )

    def next(self, n=1):
        self.counter.next(n=n)

    def finish(self):
        if self.counter.index == 0:
            self.counter.writeln('Up-To-Date')
        self.counter.finish()


class HashCounter(Counter):
    suffix = ' (%(hps)s hps)'

    @property
    def hps(self):
        if self.elapsed:
            return human_bignum(self.index / self.elapsed)
        else:
            return human_bignum(0)

    def update(self):
        suffix = self.suffix % self
        message = self.message % self
        line = ''.join([message, str(human_bignum(self.index))])
        if self.elapsed:
            line += suffix
        self.writeln(line)


@click.command('init', help='Initialize the database.')
@with_appcontext
def init_db_command():
    try:
        db.create_all()
        click.secho('Initialized the database.', fg='green')
    except Exception as e:
        click.secho(f'Initialization failed: {e}', fg='red')


@click.command(
    'sync',
    help="Synchronize the node's block chain to that of its peers."
)
@with_appcontext
def sync_blocks_command():
    try:
        node = Node(
            host=current_app.config['NODE_HOST'],
            peers=current_app.config['PEERS'],
            clients=current_app.clients,
            logger=current_app.logger
        )
        success = False
        for latest_block, _ in node.request_latest_blocks():
            try:
                progress = BlockCounterBar()
                filled = node.fill_chain(latest_block, progress=progress)
                if not success:
                    success = filled
            except requests.HTTPError as e:
                click.secho(
                    f'Synchronization failed: {http_error_message(e)}',
                    fg='red'
                )
            except Exception as e:
                current_app.logger.error(e)
            finally:
                progress.finish()
        if success:
            click.secho('Synchronized the block chain.', fg='green')
        else:
            click.secho('Unable to synchronize the block chain.', fg='red')
    except Exception as e:
        click.secho(f'Synchronization failed: {e}', fg='red')


@click.command('validate', help="Validate the node's block chain.")
@with_appcontext
def validate_chain_command():
    try:
        node = Node(logger=current_app.logger)
        lc = node.longest_chain
        click.echo(
            f"Last Block #{lc.length-1}: ({lc.block_hash})"
        )
        try:
            progress = IncrementalBar(
                message='Validating:',
                max=lc.length,
                suffix='%(percent).1f%% [%(index)s/%(max)s %(eta)ds]'
            )
            lc.validate(progress=progress)
        finally:
            progress.finish()
        click.secho('The block chain is valid.', fg='green')
    except Exception as e:
        click.secho(f'The block chain is invalid: {e}', fg='red')


@click.command('export')
@click.argument('file', type=click.Path())
@with_appcontext
def export_blocks_command(file):
    """Export the block chain to file.

    \b
    FILE is the file path to export the blocks to.
    If the file already exists, it will be appended to.
    """
    try:
        node = Node(logger=current_app.logger)
        lc = node.longest_chain
        lc_dao = lc.to_dao()
        try:
            progress = None
            last_block = None
            append_blocks = False
            if os.path.isfile(file) and (last_line := read_last_line(file)):
                last_block = Block.from_json(last_line)
                if lc_dao.get_block(last_block.block_hash) is None:
                    raise Exception(CHAIN_MISMATCH_MSG)
                append_blocks = True
            last_idx = last_block.idx if last_block is not None else -1
            num_blocks = lc_dao.block.idx - last_idx
            if num_blocks:
                progress = IncrementalBar(
                    message='Exporting:',
                    max=num_blocks,
                    suffix='%(percent).1f%% [%(index)s/%(max)s %(eta)ds]'
                )
                with open(
                    file, 'a' if append_blocks else 'w', encoding='utf-8'
                ) as f:
                    block_dao = lc_dao.get_block(idx=last_idx+1)
                    while block_dao is not None:
                        block = Block.from_dao(block_dao)
                        f.write(block.to_json())
                        f.write('\n')
                        progress.next()
                        block_dao = lc_dao.next_block(block_dao)
            else:
                click.secho('Up-To-Date')
        finally:
            if progress is not None:
                progress.finish()
        click.secho('Export complete.', fg='green')
    except Exception as e:
        click.secho(f'Export failed: {e}', fg='red')


@click.command('import')
@click.argument('file', type=click.Path(exists=True))
@with_appcontext
def import_blocks_command(file):
    """Import the block chain from file.

    \b
    FILE is the file path from which to import the blocks.
    """
    try:
        node = Node(logger=current_app.logger)
        try:
            with open(file, encoding='utf-8') as f:
                progress = IncrementalBar(
                    message='Importing:',
                    max=sum(1 for line in f),
                    suffix='%(percent).1f%% [%(index)s/%(max)s %(eta)ds]'
                )
            with open(file, encoding='utf-8') as f:
                for line in f:
                    block = Block.from_json(line)
                    if Block.from_db(block.block_hash) is None:
                        node.add_block(block)
                    progress.next()
        finally:
            progress.finish()
        click.secho('Import complete.', fg='green')
    except Exception as e:
        current_app.logger.exception(e)
        click.secho(f'Import failed: {e}', fg='red')


@click.command('mill')
@click.argument('address')
@click.option(
    '-m', '--multi',
    is_flag=True,
    default=False,
    help='Use python multiprocessing when calculating hashes.'
)
@click.option(
    '-r', '--rounds',
    default=1,
    help='Number of rounds of milling between new block checks. (default 1)'
)
@click.option(
    '-s', '--size', 'worksize',
    default=100000,
    help=(
        'Number of hashes to calculate per round '
        '(per CPU if multiprocessing is enabled) '
        '(default 100000)'
    )
)
@click.option(
    '-w', '--wallet',
    type=click.Path(exists=True),
    default=None,
    help='Wallet file to use for milling coinbase rewards.'
)
@click.option(
    '-p', '--peer',
    default=None,
    help=(
        "Peer node to poll before checking for new blocks and transactions."
    )
)
@click.option(
    '-b', '--blocks',
    default=0,
    help='Stop after this many blocks. (default 0 (run forever))'
)
@with_appcontext
def mill_command(address, multi, rounds, worksize, wallet, peer, blocks):
    """Start a milling process.

    \b
    ADDRESS is the address to use for milling coinbase rewards.
    """
    milling_wallet = address_wallet(address, wallet_file=wallet)
    click.secho(f"Miller Address: {milling_wallet.address}", bold=True)
    if peer is not None and current_app.clients.get(peer) is None:
        msg = f"Peer {peer} client not configured."
        raise Exception(msg)
    miller = Miller(
        host=current_app.config['NODE_HOST'],
        peers=current_app.config['PEERS'],
        clients=current_app.clients,
        logger=current_app.logger,
        milling_wallet=milling_wallet,
        milling_peer=peer
    )
    try:
        click.echo('Synchronizing the block chain...')
        progress = BlockCounterBar()
        miller.poll_latest_blocks(progress=progress)
        click.echo('Synchronized.')
    except Exception as e:
        current_app.logger.error(e)
        db.session.rollback()
    block_count = 0
    while (not blocks) or (block_count < blocks):
        try:
            click.echo()
            chain = miller.longest_chain
            if chain:
                click.echo(f'Chain #{chain.cid}: {chain.block_hash}')
            else:
                click.echo('GENESIS')
            block = miller.create_block()
            click.echo(f'Block #{block.idx} (Target: {block.target})')
            click.echo(f'  Started: {now_iso()}')
            try:
                counter = HashCounter('  Hashes:  ')
                counter.next(0)
                milled_block = miller.mill_block(
                    block,
                    mp=multi,
                    rounds=rounds,
                    worksize=worksize,
                    progress=counter
                )
            finally:
                counter.finish()
                block_count += 1
            if milled_block:
                pofw = milled_block.proof_of_work
                click.secho(
                    f'  POW:     {pofw} ({human_bignum(pofw)})',
                    fg='green'
                )
            elif block.proof_of_work is not None:
                click.secho('  POW:     SCOOPED (but so close)', fg='yellow')
            else:
                click.secho('  POW:     SCOOPED', fg='red')
            click.echo(f'  Stopped: {now_iso()}')
            click.secho(
                (
                    f'  Elapsed: {human_timespan(counter.elapsed)} '
                    f'({counter.hps} hps)'
                ),
                bold=True
            )
        except Exception as e:
            current_app.logger.error(e)
            db.session.rollback()


txn_cli = AppGroup('txn', help='Command group to create transactions.')


@txn_cli.command('transfer')
@click.argument('from_address')
@click.argument('amount', type=click.FLOAT)
@click.argument('to_address')
@click.option(
    '-t', '--txn-wallet',
    type=click.Path(exists=True),
    default=None,
    help='Wallet file to use for transaction source.'
)
@click.option(
    '-h', '--host',
    default=None,
    help='The API host to use (default from app config).'
)
@click.option(
    '-w', '--wallet',
    type=click.Path(exists=True),
    default=None,
    help='Wallet file to use for API auth.'
)
@click.option(
    '-y', '--yes',
    is_flag=True,
    default=False,
    help='Assume "yes" as answer to all prompts and run non-interactively.'
)
@with_appcontext
def create_transfer(
    from_address, amount, to_address, txn_wallet, host, wallet, yes
):
    """Create and post a transfer transaction.

    \b
    FROM_ADDRESS is the transaction source address.
    AMOUNT is the amount (as a float) of CCG to transfer.
    TO_ADDRESS is the transaction destination address.
    """
    try:
        txn_wallet = address_wallet(from_address, wallet_file=txn_wallet)
        client = host_api_client(host=host, wallet_file=wallet)
        r = client.get_transfer_transaction(
            txn_wallet.public_key_b64,
            grumble_to_curmudgeons(amount),
            to_address
        )
        txn = Transaction.from_json(r.text)
        if not (confirm := yes):
            click.echo(f'Transfer transaction created: {txn.txid}')
            confirm = click.confirm(
                'Do you want to sign and post the transaction?'
            )
        if confirm:
            txn.set_wallet(txn_wallet)
            txn.sign()
            client.post_transaction(txn)
            click.secho('Transfer created.', fg='green')
        else:
            click.secho('Transfer aborted.', fg='red')
    except requests.HTTPError as e:
        click.secho(f'Transfer failed: {http_error_message(e)}', fg='red')
    except Exception as e:
        click.secho(f'Transfer failed: {e}', fg='red')


@txn_cli.command('subject')
@click.argument('address')
@click.argument('amount', type=click.FLOAT)
@click.argument('subject')
@click.option(
    '-t', '--txn-wallet',
    type=click.Path(exists=True),
    default=None,
    help='Wallet file to use for transaction source.'
)
@click.option(
    '-h', '--host',
    default=None,
    help='The API host to use (default from app config).'
)
@click.option(
    '-w', '--wallet',
    type=click.Path(exists=True),
    default=None,
    help='Wallet file to use for API auth.'
)
@click.option(
    '-y', '--yes',
    is_flag=True,
    default=False,
    help='Assume "yes" as answer to all prompts and run non-interactively.'
)
@with_appcontext
def create_subject(address, amount, subject, txn_wallet, host, wallet, yes):
    """Create a subject ("cancel") transaction.

    \b
    ADDRESS is the transaction source address.
    AMOUNT is the amount (as a float) of CCG to apply.
    SUBJECT is the raw (unencoded) subject string.
    """
    try:
        txn_wallet = address_wallet(address, wallet_file=txn_wallet)
        client = host_api_client(host=host, wallet_file=wallet)
        r = client.get_subject_transaction(
            txn_wallet.public_key_b64, grumble_to_curmudgeons(amount), subject
        )
        txn = Transaction.from_json(r.text)
        if not (confirm := yes):
            click.echo(f'Subject transaction created: {txn.txid}')
            confirm = click.confirm(
                'Do you want to sign and post the transaction?'
            )
        if confirm:
            txn.set_wallet(txn_wallet)
            txn.sign()
            client.post_transaction(txn)
            click.secho(f'Subject created: {txn.txid}', fg='green')
        else:
            click.secho('Subject aborted.', fg='red')
    except requests.HTTPError as e:
        click.secho(f'Subject failed: {http_error_message(e)}', fg='red')
    except Exception as e:
        click.secho(f'Subject failed: {e}', fg='red')


@txn_cli.command('forgive')
@click.argument('address')
@click.argument('amount', type=click.FLOAT)
@click.argument('subject')
@click.option(
    '-t', '--txn-wallet',
    type=click.Path(exists=True),
    default=None,
    help='Wallet file to use for transaction source.'
)
@click.option(
    '-h', '--host',
    default=None,
    help='The API host to use (default from app config).'
)
@click.option(
    '-w', '--wallet',
    type=click.Path(exists=True),
    default=None,
    help='Wallet file to use for API auth.'
)
@click.option(
    '-y', '--yes',
    is_flag=True,
    default=False,
    help='Assume "yes" as answer to all prompts and run non-interactively.'
)
@with_appcontext
def create_forgive(address, amount, subject, txn_wallet, host, wallet, yes):
    """Create a forgive transaction.

    \b
    ADDRESS is the transaction source address.
    AMOUNT is the amount (as a float) of CCG to apply.
    SUBJECT is the raw (unencoded) subject string.
    """
    try:
        txn_wallet = address_wallet(address, wallet_file=txn_wallet)
        client = host_api_client(host=host, wallet_file=wallet)
        r = client.get_forgive_transaction(
            txn_wallet.public_key_b64, grumble_to_curmudgeons(amount), subject
        )
        txn = Transaction.from_json(r.text)
        if not (confirm := yes):
            click.echo(f'Subject transaction created: {txn.txid}')
            confirm = click.confirm(
                'Do you want to sign and post the transaction?'
            )
        if confirm:
            txn.set_wallet(txn_wallet)
            txn.sign()
            client.post_transaction(txn)
            click.secho(f'Forgive created: {txn.txid}', fg='green')
        else:
            click.secho('Forgive aborted.', fg='red')
    except requests.HTTPError as e:
        click.secho(f'Forgive failed: {http_error_message(e)}', fg='red')
    except Exception as e:
        click.secho(f'Forgive failed: {e} ', fg='red')


@txn_cli.command('support')
@click.argument('address')
@click.argument('amount', type=click.FLOAT)
@click.argument('subject')
@click.option(
    '-t', '--txn-wallet',
    type=click.Path(exists=True),
    default=None,
    help='Wallet file to use for transaction source.'
)
@click.option(
    '-h', '--host',
    default=None,
    help='The API host to use (default from app config).'
)
@click.option(
    '-w', '--wallet',
    type=click.Path(exists=True),
    default=None,
    help='Wallet file to use for API auth.'
)
@click.option(
    '-y', '--yes',
    is_flag=True,
    default=False,
    help='Assume "yes" as answer to all prompts and run non-interactively.'
)
@with_appcontext
def create_support(address, amount, subject, txn_wallet, host, wallet, yes):
    """Create a subject support transaction.

    \b
    ADDRESS is the transaction source address.
    AMOUNT is the amount (as a float) of CCG to apply.
    SUBJECT is the raw (unencoded) subject string.
    """
    try:
        txn_wallet = address_wallet(address, wallet_file=txn_wallet)
        client = host_api_client(host=host, wallet_file=wallet)
        r = client.get_support_transaction(
            txn_wallet.public_key_b64, grumble_to_curmudgeons(amount), subject
        )
        txn = Transaction.from_json(r.text)
        if not (confirm := yes):
            click.echo(f'Support transaction created: {txn.txid}')
            confirm = click.confirm(
                'Do you want to sign and post the transaction?'
            )
        if confirm:
            txn.set_wallet(txn_wallet)
            txn.sign()
            client.post_transaction(txn)
            click.secho(f'Support created: {txn.txid}', fg='green')
        else:
            click.secho('Support aborted.', fg='red')
    except requests.HTTPError as e:
        click.secho(f'Support failed: {http_error_message(e)}', fg='red')
    except Exception as e:
        click.secho(f'Support failed: {e}', fg='red')


wallet_cli = AppGroup('wallet', help='Command group to work with wallets.')


@wallet_cli.command('create')
@click.option(
    '-d', '--walletdir',
    type=click.Path(exists=True),
    default=None,
    help="Parent directory for the wallet file (default from app config)."
)
@with_appcontext
def create_wallet(walletdir):
    """Create a new wallet file."""
    walletdir = walletdir or current_app.config.get('WALLET_DIR')
    w = Wallet()
    filename = w.to_file(walletdir=walletdir)
    click.echo(f'{filename}')


@wallet_cli.command('balance')
@click.argument('address')
@click.option(
    '-h', '--host',
    default=None,
    help='The API host to use (default from app config).'
)
@click.option(
    '-w', '--wallet',
    type=click.Path(exists=True),
    default=None,
    help='Wallet file to use for API auth.'
)
@with_appcontext
def wallet_balance(address, host, wallet):
    """Get the wallet balance in CCG for an address.

    \b
    ADDRESS is the wallet address.
    """
    try:
        client = host_api_client(host=host, wallet_file=wallet)
        r = client.get_wallet_balance(address)
        balance = r.json().get('balance')
        click.secho(f'{human_curmudgeons(balance)} CCG', fg='green')
    except requests.HTTPError as e:
        click.secho(f'Balance failed: {http_error_message(e)}', fg='red')
    except Exception as e:
        click.secho(f'Balance failed: {e}', fg='red')


subject_cli = AppGroup('subject', help='Command group to work with subjects.')


@subject_cli.command('balance')
@click.argument('subject')
@click.option(
    '-h', '--host',
    default=None,
    help='The API host to use (default from app config).'
)
@click.option(
    '-w', '--wallet',
    type=click.Path(exists=True),
    default=None,
    help='Wallet file to use for API auth.'
)
@with_appcontext
def subject_balance(subject, host, wallet):
    """Get the balance (i.e. subject transactions minus forgiveness
       transactions) in CCG for a subject.

    \b
    SUBJECT is the raw (unencoded) subject string.
    """
    try:
        client = host_api_client(host=host, wallet_file=wallet)
        r = client.get_subject_balance(encode_subject(subject))
        balance = r.json().get('balance')
        click.secho(f'{human_curmudgeons(balance)} CCG', fg='green')
    except requests.HTTPError as e:
        click.secho(
            f'Subject balance failed: {http_error_message(e)}', fg='red'
        )
    except Exception as e:
        click.secho(f'Subject balance failed: {e}', fg='red')


@subject_cli.command('support')
@click.argument('subject')
@click.option(
    '-h', '--host',
    default=None,
    help='The API host to use (default from app config)'
)
@click.option(
    '-w', '--wallet',
    type=click.Path(exists=True),
    default=None,
    help='Wallet file to use for API auth'
)
@with_appcontext
def support_balance(subject, host, wallet):
    """Get the support total in CCG for a subject.

    \b
    SUBJECT is the raw (unencoded) subject string.
    """
    try:
        client = host_api_client(host=host, wallet_file=wallet)
        r = client.get_subject_support(encode_subject(subject))
        support = r.json().get('support')
        click.secho(f'{human_curmudgeons(support)} CCG', fg='green')
    except requests.HTTPError as e:
        click.secho(
            f'Support balance failed: {http_error_message(e)}', fg='red'
        )
    except Exception as e:
        click.secho(f'Support balance failed: {e}', fg='red')
