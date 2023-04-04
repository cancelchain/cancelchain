import os
from datetime import timedelta
from http.client import responses

import click
import requests
from flask import current_app
from flask.cli import AppGroup, with_appcontext
from humanfriendly import format_timespan
from millify import millify
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.prompt import Confirm
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from cancelchain.api_client import ApiClient
from cancelchain.block import Block
from cancelchain.chain import CURMUDGEON_PER_GRUMBLE
from cancelchain.console import console
from cancelchain.database import db
from cancelchain.miller import Miller
from cancelchain.node import Node
from cancelchain.payload import encode_subject
from cancelchain.transaction import Transaction
from cancelchain.util import host_address, now_iso
from cancelchain.wallet import Wallet

REFRESH_PER_SECOND = 8
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


class ProgressBar():
    def __init__(self, title, console=None, total=None, completed=0):
        self.progress = Progress(
            # TextColumn(title),
            BarColumn(),
            TextColumn('{task.completed}/{task.total}'),
            TaskProgressColumn(),
            TimeRemainingColumn(),
            TextColumn('['),
            TimeElapsedColumn(),
            TextColumn(']'),
            console=console
        )
        self.task_id = self.progress.add_task(
            title, total=total, completed=completed
        )
        self.panel = Panel.fit(
            self.progress,
            title=title
        )
        self.live = Live(
            self.panel,
            console=console,
            refresh_per_second=REFRESH_PER_SECOND
        )

    @property
    def console(self):
        return self.live.console

    def next(self, n=1):
        self.progress.advance(self.task_id, advance=n)

    def __enter__(self):
        self.live.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.live.__exit__(exc_type, exc_val, exc_tb)


class BlockSyncProgress():
    def __init__(self, peer=None, console=None):
        self.find_progress = Progress(
            SpinnerColumn(spinner_name='aesthetic', style='none'),
            TextColumn('{task.completed} Blocks'),
            TextColumn('['),
            TimeElapsedColumn(),
            TextColumn(']')
        )
        self.find_task_id = self.find_progress.add_task('Finding', total=None)
        self.load_title_text = TextColumn('Waiting...')
        self.load_count_text = TextColumn('')
        self.load_progress = Progress(
            self.load_title_text,
            BarColumn(),
            self.load_count_text,
            TaskProgressColumn(),
            TimeRemainingColumn(),
            TextColumn('['),
            TimeElapsedColumn(),
            TextColumn(']')
        )
        self.load_task_id = self.load_progress.add_task(
            'Loading', total=None, start=False
        )
        self.finding_panel = Panel.fit(
            self.find_progress,
            title='Finding Blocks'
        )
        self.loading_panel = Panel.fit(
            self.load_progress,
            title='Loading Blocks',
            border_style='dim'
        )
        progress_table = Table.grid()
        progress_table.add_row(self.finding_panel, self.loading_panel)
        self.live = Live(
            progress_table,
            console=console,
            refresh_per_second=REFRESH_PER_SECOND
        )
        self.progress = self.find_progress
        self.task_id = self.find_task_id
        self.console.print(
            Rule(title=f'Synchronizing with peer [bold]{peer}', align='left')
        )

    @property
    def console(self):
        return self.live.console

    def next(self, n=1):
        self.progress.advance(self.task_id, advance=n)

    def complete_find(self):
        block_count = self.find_progress.tasks[0].completed
        self.find_progress.update(
            self.find_task_id, total=block_count
        )
        self.load_title_text.text_format = ''
        return block_count

    def switch(self):
        block_count = self.complete_find()
        self.loading_panel.border_style = 'none'
        self.load_count_text.text_format = '{task.completed}/{task.total}'
        self.load_progress.update(self.load_task_id, total=block_count)
        self.load_progress.start_task(self.load_task_id)
        self.progress = self.load_progress
        self.task_id = self.load_task_id

    def finish(self):
        self.complete_find()

    def __enter__(self):
        self.live.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.live.__exit__(exc_type, exc_val, exc_tb)


class MillingProgress():
    def __init__(self, console=None):
        self.block = None
        self.chain = None
        self.progress = Progress(
            SpinnerColumn(spinner_name='aesthetic', style='milling'),
            TextColumn('{task.fields[hash_count]}h @'),
            TextColumn('{task.fields[hps]} hps'),
            TextColumn('['),
            TimeElapsedColumn(),
            TextColumn(']')
        )
        self.task_id = self.progress.add_task(
            'Milling', total=None, hash_count=0, hps=0
        )
        self.task = self.progress.tasks[0]
        self.panel = Panel.fit(
            self.progress,
            title="Milling",
            border_style='milling'
        )
        self.live = Live(
            self.panel,
            console=console,
            refresh_per_second=REFRESH_PER_SECOND
        )

    @property
    def hash_count(self):
        return str(human_bignum(self.task.completed))

    @property
    def hps(self):
        if self.task.elapsed:
            return human_bignum(self.task.completed / self.task.elapsed)
        else:
            return human_bignum(0)

    @property
    def console(self):
        return self.live.console

    @property
    def elapsed(self):
        if self.task.elapsed is None:
            return Text("-:--:--", style="progress.elapsed")
        delta = timedelta(seconds=int(self.task.elapsed))
        return Text(str(delta), style="progress.elapsed")

    def next(self, n=1):
        self.progress.update(
            self.task_id, advance=n, hash_count=self.hash_count, hps=self.hps
        )

    def next_block(self, block, chain):
        self.block = block
        self.chain = chain
        self.progress.reset(self.task_id)

    def __enter__(self):
        self.live.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.live.__exit__(exc_type, exc_val, exc_tb)

    def print_start(self):
        start_table = Table(show_header=False, border_style='milling')
        start_table.add_column('key', justify='right')
        start_table.add_column('value', justify='left')
        start_table.add_row('Block', str(self.block.idx))
        start_table.add_row(
            'Chain', self.chain.block_hash if self.chain else 'GENESIS'
        )
        start_table.add_row('Target', self.block.target)
        start_table.add_row('Started', now_iso())
        self.console.print(start_table)

    def print_stop(self, milled_block):
        stop_table = Table(show_header=False)
        stop_table.add_column('key', justify='right')
        stop_table.add_column('value', justify='left')
        stop_table.add_row('Stopped', now_iso())
        stop_table.add_row('Elapsed', self.elapsed)
        stop_table.add_row(
            'Hashes', f'{self.hash_count} @ {self.hps} hps'
        )
        label_text = Text('POW')
        style = 'milling.milled'
        if milled_block:
            pofw = milled_block.proof_of_work
            value_text = Text(f'{pofw} ({human_bignum(pofw)})', style=style)
            stop_table.add_row(label_text, value_text)
            stop_table.add_row('Block', f'{milled_block.block_hash}')
        elif self.block.proof_of_work is not None:
            style = 'milling.close'
            value_text = Text('SCOOPED (but so close)', style=style)
            stop_table.add_row(label_text, value_text)
        else:
            style = 'milling.scooped'
            value_text = Text('SCOOPED', style=style)
            stop_table.add_row(label_text, value_text)
        stop_table.border_style = style
        self.console.print(stop_table)
        self.console.print(Rule(style=style))


@click.command('init', help='Initialize the database.')
@with_appcontext
def init_db_command():
    try:
        db.create_all()
        console.print('Initialized the database.', style='success')
    except Exception as e:
        console.print(f'Initialization failed: {e}', style='error')


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
        for latest_block, peer in node.request_latest_blocks():
            try:
                progress_bar = BlockSyncProgress(peer=peer, console=console)
                with progress_bar as progress:
                    node.fill_chain(latest_block, progress=progress)
                    progress.finish()
            except requests.HTTPError as e:
                console.print(
                    f'Synchronization failed: {http_error_message(e)}',
                    style='error'
                )
            except Exception:
                console.print_exception()
    except Exception as e:
        console.print(f'Synchronization failed: {e}', style='error')


@click.command('validate', help="Validate the node's block chain.")
@with_appcontext
def validate_chain_command():
    try:
        node = Node(logger=current_app.logger)
        lc = node.longest_chain
        progress_bar = ProgressBar(
            'Validating Chain', console=console, total=lc.length
        )
        with progress_bar as progress:
            lc.validate(progress=progress)
    except Exception as e:
        console.print(f'The block chain is invalid: {e}', style='error')


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
        progress = None
        last_block = None
        append_blocks = False
        if os.path.isfile(file) and (last_line := read_last_line(file)):
            last_block = Block.from_json(last_line)
            if lc_dao.get_block(last_block.block_hash) is None:
                raise Exception(CHAIN_MISMATCH_MSG)
            append_blocks = True
        last_idx = last_block.idx if last_block is not None else -1
        progress_bar = ProgressBar(
            "Exporting Blocks",
            console=console,
            total=lc_dao.block.idx+1,
            completed=last_block.idx+1 if last_block is not None else 0
        )
        with open(
            file, 'a' if append_blocks else 'w', encoding='utf-8'
        ) as f, progress_bar as progress:
            block_dao = lc_dao.get_block(idx=last_idx+1)
            while block_dao is not None:
                block = Block.from_dao(block_dao)
                f.write(block.to_json())
                f.write('\n')
                progress.next()
                block_dao = lc_dao.next_block(block_dao)
    except Exception:
        console.print_exception()
        console.print('Export failed', style='error')


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
        with open(file, encoding='utf-8') as f:
            progress_bar = ProgressBar(
                "Importing Blocks",
                console=console,
                total=sum(1 for line in f)
            )
        with open(file, encoding='utf-8') as f, progress_bar as progress:
            for line in f:
                block = Block.from_json(line)
                if Block.from_db(block.block_hash) is None:
                    node.add_block(block)
                progress.next()
    except Exception:
        console.print_exception()
        console.print('Import failed', style='error')


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
    if peer:
        try:
            progress_bar = BlockSyncProgress(peer=peer, console=console)
            with progress_bar as progress:
                miller.poll_latest_blocks(progress=progress)
                progress.finish()
        except Exception:
            console.print_exception()
            db.session.rollback()
    block_count = 0
    milling_progress = MillingProgress(console=console)
    with milling_progress as progress:
        progress.console.print()
        progress.console.print(
            Rule(
                title=f'Milling as address [bold]{milling_wallet.address}',
                align='left',
                style='milling'
            )
        )
        while (not blocks) or (block_count < blocks):
            try:
                chain = miller.longest_chain
                block = miller.create_block()
                progress.next_block(block, chain)
                progress.print_start()
                try:
                    milled_block = miller.mill_block(
                        block,
                        mp=multi,
                        rounds=rounds,
                        worksize=worksize,
                        progress=progress
                    )
                finally:
                    block_count += 1
                progress.print_stop(milled_block)
            except Exception:
                progress.console.print_exception()
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
            console.print(f'Transfer transaction created: {txn.txid}')
            confirm = Confirm.ask(
                'Do you want to sign and post the transaction?'
            )
        if confirm:
            txn.set_wallet(txn_wallet)
            txn.sign()
            client.post_transaction(txn)
            console.print('Transfer created.', style='success')
        else:
            console.print('Transfer aborted.', style='error')
    except requests.HTTPError as e:
        console.print(
            f'Transfer failed: {http_error_message(e)}', style='error'
        )
    except Exception as e:
        console.print(f'Transfer failed: {e}', style='error')


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
            console.print(f'Subject transaction created: {txn.txid}')
            confirm = Confirm.ask(
                'Do you want to sign and post the transaction?'
            )
        if confirm:
            txn.set_wallet(txn_wallet)
            txn.sign()
            client.post_transaction(txn)
            console.print(f'Subject created: {txn.txid}', style='success')
        else:
            console.print('Subject aborted.', style='error')
    except requests.HTTPError as e:
        console.print(
            f'Subject failed: {http_error_message(e)}', style='error'
        )
    except Exception as e:
        console.print(f'Subject failed: {e}', style='error')


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
            console.print(f'Subject transaction created: {txn.txid}')
            confirm = Confirm.ask(
                'Do you want to sign and post the transaction?'
            )
        if confirm:
            txn.set_wallet(txn_wallet)
            txn.sign()
            client.post_transaction(txn)
            console.print(f'Forgive created: {txn.txid}', style='success')
        else:
            console.print('Forgive aborted.', style='error')
    except requests.HTTPError as e:
        console.print(
            f'Forgive failed: {http_error_message(e)}', style='error'
        )
    except Exception as e:
        console.print(f'Forgive failed: {e} ', style='error')


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
            console.print(f'Support transaction created: {txn.txid}')
            confirm = Confirm.ask(
                'Do you want to sign and post the transaction?'
            )
        if confirm:
            txn.set_wallet(txn_wallet)
            txn.sign()
            client.post_transaction(txn)
            console.print(f'Support created: {txn.txid}', style='success')
        else:
            console.print('Support aborted.', style='error')
    except requests.HTTPError as e:
        console.print(
            f'Support failed: {http_error_message(e)}', style='error'
        )
    except Exception as e:
        console.print(f'Support failed: {e}', style='error')


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
    console.print(f'Created {filename}', style='success')


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
        console.print(f'{human_curmudgeons(balance)} CCG', style='success')
    except requests.HTTPError as e:
        console.print(
            f'Balance failed: {http_error_message(e)}', style='error'
        )
    except Exception as e:
        console.print(f'Balance failed: {e}', style='error')


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
        console.print(f'{human_curmudgeons(balance)} CCG', style='success')
    except requests.HTTPError as e:
        console.print(
            f'Subject balance failed: {http_error_message(e)}', style='error'
        )
    except Exception as e:
        console.print(f'Subject balance failed: {e}', style='error')


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
        console.print(f'{human_curmudgeons(support)} CCG', style='success')
    except requests.HTTPError as e:
        console.print(
            f'Support balance failed: {http_error_message(e)}', style='error'
        )
    except Exception as e:
        console.print(f'Support balance failed: {e}', style='error')
