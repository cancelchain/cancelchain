from base64 import urlsafe_b64encode

from cancelchain.payload import Inflow, Outflow, validate_subject


def test_outflow_data_csv(subject, wallet):
    outflow = Outflow(amount=9, address=wallet.address)
    assert outflow.data_csv == f'9,{wallet.address},,,'
    outflow = Outflow(amount=9, subject=subject)
    assert outflow.data_csv == f'9,,{subject},,'
    outflow = Outflow(amount=9, forgive=subject)
    assert outflow.data_csv == f'9,,,{subject},'
    outflow = Outflow(amount=9, support=subject)
    assert outflow.data_csv == f'9,,,,{subject}'


def test_outflow_schadenfreude(subject):
    outflow = Outflow(amount=9, subject=subject)
    assert outflow.schadenfreude == 4


def test_outflow_grace(subject):
    outflow = Outflow(amount=9, forgive=subject)
    assert outflow.grace == 4


def test_outflow_mudita(subject):
    outflow = Outflow(amount=9, support=subject)
    assert outflow.mudita == 9


def test_inflow_data_csv(txid):
    inflow = Inflow(outflow_txid=txid, outflow_idx=0)
    assert inflow.data_csv == f'{txid},0'


def test_validate_subject(subject_raw, subject):
    assert validate_subject(subject)
    subject_urlsafe = urlsafe_b64encode(subject_raw.encode()).decode()
    assert subject_urlsafe.endswith('=')
    assert not validate_subject(subject_urlsafe)
